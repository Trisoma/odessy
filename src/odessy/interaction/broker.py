"""One-use human interactions shared by trusted transports on the session loop."""
from __future__ import annotations

import asyncio
import math
import os
from collections import OrderedDict
from copy import deepcopy
import time
import uuid

from .context import human_wait


class InteractionError(KeyError):
    def __init__(self, message, status_code=400):
        super().__init__(message)
        self.status_code = status_code


class InteractionBroker:
    def __init__(self, emit, redact, config=None):
        self.emit, self.redact = emit, redact
        self.handler = None
        self.generation_deadline = None
        def option(name, default):
            if config is None:
                return os.environ.get('ODESSY_INTERACTION_' + name.upper(), default)
            return config.get(name, default) if isinstance(config, dict) else getattr(config, name, default)
        self.prompt_timeout = float(option('prompt_timeout', 30))
        self.response_timeout = float(option('response_timeout', 30))
        self.strong_response_timeout = float(option('strong_response_timeout', 120))
        self.max_questions = int(option('max_questions', 3))
        if (not all(math.isfinite(value) and 0 < value <= maximum for value, maximum in (
                (self.prompt_timeout, 120), (self.response_timeout, 300),
                (self.strong_response_timeout, 600))) or not 1 <= self.max_questions <= 20):
            raise ValueError('Interaction bounds must be positive')
        self._pending = {}
        self._signals = {}
        self._tombstones = OrderedDict()
        self._turn_id = None
        self._questions = 0
        self._closed = False

    @property
    def pending(self):
        return [deepcopy(entry) for entry, future in self._pending.values() if not future.done()]

    @property
    def pending_approvals(self):
        return [entry for entry in self.pending if entry['kind'] == 'approval']

    @property
    def pending_questions(self):
        return [entry for entry in self.pending if entry['kind'] == 'question']

    def start_turn(self, turn_id=None):
        self.end_turn()
        self._turn_id = turn_id or uuid.uuid4().hex
        self._questions = 0
        return self._turn_id

    def end_turn(self, reason='superseded'):
        self.cancel_pending(reason)
        self._turn_id = None

    def close(self):
        self._closed = True
        self.cancel_pending('closed')

    def _remember(self, identifier, reason, channel=None):
        self._tombstones[identifier] = {'reason': reason, 'channel': channel}
        while len(self._tombstones) > 256:
            self._tombstones.popitem(last=False)

    def _finish(self, identifier, value, reason, channel=None):
        entry, future = self._pending.pop(identifier)
        self._remember(identifier, reason, channel)
        if not future.done():
            future.set_result(value)
        self._signals[identifier].set()
        self.emit(entry['kind'] + ('_answered' if reason == 'resolved' else '_expired'),
                  {'id': identifier, 'reason': reason, 'channel': channel, **({'approved': value} if entry['kind'] == 'approval' else {})})

    def cancel_pending(self, reason='cancelled'):
        for identifier, (entry, _) in tuple(self._pending.items()):
            self._finish(identifier, False if entry['kind'] == 'approval' else
                         self._question_result(identifier, 'cancelled', reason=reason), reason)

    def _lookup(self, identifier):
        pending = self._pending.get(identifier)
        if pending is None:
            tombstone = self._tombstones.get(identifier)
            reason = tombstone['reason'] if tombstone else None
            raise InteractionError('Interaction ' + (reason or 'not found'),
                                   409 if reason == 'resolved' else 410 if reason else 404)
        entry, future = pending
        if time.time() >= entry['expires_at']:
            self._finish(identifier, False if entry['kind'] == 'approval' else
                         self._question_result(identifier, 'expired', reason='timeout'), 'expired')
            raise InteractionError('Interaction expired', 410)
        return entry, future

    def is_pending(self, identifier):
        try:
            self._lookup(identifier)
            return True
        except InteractionError:
            return False

    def mark_ready(self, identifier):
        entry, _ = self._lookup(identifier)
        if not entry['ready']:
            entry['ready'] = True
            duration = self.strong_response_timeout if 'voice' not in entry['allowed_channels'] else self.response_timeout
            entry['expires_at'] = time.time() + duration
            self._signals[identifier].set()
            self.emit('interaction_ready', deepcopy(entry))

    def _validate(self, identifier, kind, channel):
        entry, _ = self._lookup(identifier)
        if entry['kind'] != kind:
            raise InteractionError('Wrong interaction kind')
        if channel not in entry['allowed_channels']:
            raise InteractionError('Channel is not allowed')
        if channel == 'voice' and not entry['ready']:
            raise InteractionError('Voice prompt is not ready')
        return entry

    def resolve_approval(self, identifier, approved, *, channel):
        self._validate(identifier, 'approval', channel)
        if not isinstance(approved, bool):
            raise InteractionError('approved must be a boolean')
        self._finish(identifier, approved, 'resolved', channel)

    def resolve_question(self, identifier, answer, *, channel):
        entry = self._validate(identifier, 'question', channel)
        if not isinstance(answer, str) or not answer.strip() or len(answer) > 8192:
            raise InteractionError('Nonempty answer of at most 8192 characters required')
        answer = answer.strip()
        if not entry['allow_free_text'] and answer not in entry['choices']:
            raise InteractionError('Answer must match a choice')
        self._finish(identifier, self._question_result(identifier, 'ok', answer=self.redact(answer)), 'resolved', channel)

    @staticmethod
    def _question_result(identifier, status, **values):
        return {'status': status, 'source': 'human_information', 'question_id': identifier, **values}

    async def ask(self, question, choices=None, allow_free_text=True):
        if not isinstance(question, str) or not question.strip() or len(question) > 8192:
            raise ValueError('Nonempty question of at most 8192 characters required')
        if choices is not None and (not isinstance(choices, list) or not choices or len(choices) > 32 or
                any(not isinstance(choice, str) or not choice.strip() or len(choice) > 1024 for choice in choices)):
            raise ValueError('choices must be a nonempty list of strings')
        if not isinstance(allow_free_text, bool) or (not allow_free_text and not choices):
            raise ValueError('A choice or free text must be allowed')
        if self._questions >= self.max_questions:
            return self._question_result(None, 'limit_reached', reason='Clarification limit reached')
        self._questions += 1
        return await self._request('question', {'question': question.strip(), 'prompt': question.strip(),
            'choices': choices or [], 'allow_free_text': allow_free_text, 'risk': 'low'})

    async def request_approval(self, details):
        return await self._request('approval', details)

    async def _request(self, kind, details):
        if self._closed:
            return False if kind == 'approval' else self._question_result(None, 'cancelled', reason='closed')
        if any(entry['turn_id'] == self._turn_id for entry, _ in self._pending.values()):
            return False if kind == 'approval' else self._question_result(None, 'busy', reason='Another interaction is pending')
        identifier = uuid.uuid4().hex
        entry = deepcopy(self.redact(details))
        risk = entry.get('risk', 'high' if kind == 'approval' else 'low')
        scopes = entry.get('scopes', [])
        if isinstance(scopes, str):
            scopes = [scopes]
        strong = risk not in {'low', 'medium'} or any('escalat' in str(scope).lower() for scope in scopes)
        now = time.time()
        entry.update(id=identifier, turn_id=self._turn_id, kind=kind, risk=risk,
            prompt=entry.get('authoritative_prompt', entry.get('prompt', 'Approve this action?')),
            allowed_channels=['terminal', 'authenticated_ui'] + ([] if strong else ['voice']),
            created_at=now, expires_at=now + self.prompt_timeout, ready=False)
        future = asyncio.get_running_loop().create_future()
        signal = asyncio.Event()
        self._pending[identifier] = (entry, future)
        self._signals[identifier] = signal
        self.emit(kind + '_required', deepcopy(entry))
        handler_task = None
        if self.handler is None:
            self.mark_ready(identifier)
        else:
            async def handle():
                try:
                    await self.handler(deepcopy(entry))
                except asyncio.CancelledError:
                    raise
                except Exception as error:
                    if identifier in self._pending:
                        diagnostic = {'error_type': type(error).__name__, 'error': self.redact(str(error)[:500])}
                        self.emit('interaction_unavailable', {'id': identifier, **diagnostic})
                        self._finish(identifier, False if kind == 'approval' else
                            self._question_result(identifier, 'cancelled', reason='handler_failed', **diagnostic), 'handler_failed')
            handler_task = asyncio.create_task(handle())
        try:
            with human_wait(self.generation_deadline):
                while not future.done():
                    signal.clear()
                    try:
                        await asyncio.wait_for(signal.wait(), max(0, entry['expires_at'] - time.time()))
                    except asyncio.TimeoutError:
                        if identifier in self._pending:
                            self._finish(identifier, False if kind == 'approval' else
                                self._question_result(identifier, 'expired', reason='timeout'), 'expired')
                return future.result()
        finally:
            if identifier in self._pending:
                self._finish(identifier, False if kind == 'approval' else
                             self._question_result(identifier, 'cancelled', reason='interrupted'), 'interrupted')
            if handler_task is not None:
                handler_task.cancel()
                try:
                    await handler_task
                except (asyncio.CancelledError, Exception):
                    pass
            self._signals.pop(identifier, None)
