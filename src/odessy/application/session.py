"""Transport-neutral, bounded in-memory agent sessions."""
from __future__ import annotations

import asyncio
import os
import re
import time
import uuid
from collections import deque
from dataclasses import asdict, is_dataclass
from typing import Any

from odessy.authority.redaction import redact_text, looks_like_secret_name
from odessy.runtime.types import ToolObservation, ToolRequest
from odessy.voice.memory import ConversationMemory
from odessy.collaboration.context import collaboration_scope


def public(value):
    if is_dataclass(value):
        value = asdict(value)
    if isinstance(value, dict):
        return {str(k): '<redacted>' if isinstance(v, str) and looks_like_secret_name(str(k)) else public(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [public(v) for v in value]
    if isinstance(value, str):
        return redact_text(value)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return redact_text(str(value))


def parse_session_command(text, profiles):
    aliases = {str(name).casefold(): str(name) for name in profiles}
    switch = re.fullmatch(r'\s*switch model to ([^\r\n]+?)\s*', text, re.I)
    if switch and switch[1].casefold() in aliases:
        return {'action': 'switch_model', 'profile': aliases[switch[1].casefold()]}
    consult = re.fullmatch(r'\s*consult ([^:\r\n]+):\s*(\S[^\r\n]*)\s*', text, re.I)
    if consult and consult[1].casefold() in aliases:
        return {'action': 'consult', 'participant': aliases[consult[1].casefold()], 'question': consult[2].strip()}
    return None


class SessionController:
    def __init__(self, *, model_factory, agent_factory, profiles, selected_profile=None,
                 collaboration=None, jobs=None, config=None, memory=None,
                 event_limit=256, subscriber_limit=64, max_subscribers=16):
        if not profiles or min(event_limit, subscriber_limit, max_subscribers) < 1:
            raise ValueError('Profiles and positive event bounds are required')
        self.id = uuid.uuid4().hex
        self.model_factory, self.agent_factory = model_factory, agent_factory
        self.profiles, self.collaboration, self.jobs, self.config = profiles, collaboration, jobs, config
        self.memory = memory if memory is not None else ConversationMemory()
        self.selected_profile = selected_profile or next(iter(profiles))
        self.active_profile = None
        self._active_model = None
        self._events = deque(maxlen=event_limit)
        self._subscribers = set()
        self._subscriber_limit = subscriber_limit
        self.max_subscribers = max_subscribers
        self._sequence = 0
        self._pending = {}
        self._task = None
        self._stop = asyncio.Event()
        self._closed = False
        self._model = self._validate_model(self.selected_profile)

    def _validate_model(self, name):
        if name not in self.profiles:
            raise ValueError(f'Unknown model profile: {name}')
        return self.model_factory(name)

    @property
    def events(self):
        return list(self._events)

    @property
    def running(self):
        return self._task is not None

    @property
    def pending_approvals(self):
        return [entry[0] for entry in self._pending.values()]

    def _public(self, value):
        value = public(value)
        if isinstance(value, dict):
            return {key: self._public(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self._public(item) for item in value]
        if isinstance(value, str):
            profiles = list(self.profiles.values())
            if self.config is not None:
                profiles.extend(self.config.participants.values())
            for profile in profiles:
                if isinstance(profile, dict):
                    env = profile.get('credential_env', '')
                    if not env and profile.get('adapter') == 'cloud':
                        from odessy.models.cloud_model import CLOUD_PROVIDERS
                        provider = CLOUD_PROVIDERS.get(profile.get('provider') or 'openai')
                        env = provider.api_key_env if provider else ''
                else:
                    env = getattr(profile, 'effective_credential_env', getattr(profile, 'credential_env', ''))
                secret = os.environ.get(env, '') if env else ''
                if secret:
                    value = value.replace(secret, '<redacted>')
            for model in (self._model, self._active_model):
                key = getattr(model, 'api_key', '')
                if key:
                    value = value.replace(key, '<redacted>')
        return value

    def emit(self, event_type, data=None):
        self._sequence += 1
        event = {'id': self._sequence, 'type': event_type, 'time': time.time(), 'data': self._public(data or {})}
        self._events.append(event)
        for queue in tuple(self._subscribers):
            if queue.full():
                queue.get_nowait()
            queue.put_nowait(event)
        return event

    async def subscribe(self, after_id=0):
        if len(self._subscribers) >= self.max_subscribers:
            raise RuntimeError('Session subscriber limit reached')
        queue = asyncio.Queue(maxsize=self._subscriber_limit)
        self._subscribers.add(queue)
        try:
            for event in self.events:
                if event['id'] > after_id:
                    after_id = event['id']
                    yield event
            while not self._closed:
                event = await queue.get()
                if event is None:
                    return
                if event['id'] > after_id:
                    after_id = event['id']
                    yield event
        finally:
            self._subscribers.discard(queue)

    def select_model(self, profile):
        if self._closed:
            raise RuntimeError('Session is closed')
        model = self._validate_model(profile)
        self.selected_profile, self._model = profile, model
        self.emit('selection', {'profile': profile, 'reason': 'User selection; applies to next turn'})
        return profile

    async def confirm(self, decision, request):
        if self._stop.is_set() or self._closed:
            return False
        approval_id = uuid.uuid4().hex
        future = asyncio.get_running_loop().create_future()
        entry = self._public({'id': approval_id, 'decision': decision, 'request': request})
        self._pending[approval_id] = (entry, future)
        self.emit('approval_required', entry)
        try:
            return await future
        finally:
            self._pending.pop(approval_id, None)

    def answer_approval(self, approval_id, approved):
        if not isinstance(approved, bool):
            raise ValueError('approved must be a boolean')
        entry = self._pending.get(approval_id)
        if entry is None or entry[1].done():
            raise KeyError(approval_id)
        entry[1].set_result(approved)
        self.emit('approval_answered', {'id': approval_id, 'approved': approved})

    async def run_turn(self, text, participant=None, *, context=None, stop_event=None,
                       status_callback=None, remember=True):
        if self._closed or self.running:
            raise RuntimeError('Session is closed or a turn is already running')
        self._task = asyncio.current_task()
        self._stop = stop_event if stop_event is not None else asyncio.Event()
        self.active_profile = self.selected_profile
        model = self._model
        self._active_model = model
        loop = asyncio.get_running_loop()
        announce = getattr(model, '_announce', None)
        if announce is not None:
            def report_route(message):
                announce(message)
                loop.call_soon_threadsafe(self.emit, 'routing', {'message': message})
            model._announce = report_route
        sink = getattr(self.collaboration, 'event_sink', None)
        if self.collaboration is not None:
            def report_consultation(event):
                if sink is not None:
                    sink(event)
                loop.call_soon_threadsafe(self.emit, event.get('type', 'consultation'), event)
            self.collaboration.event_sink = report_consultation
        self.emit('transcript', {'role': 'user', 'text': text})
        self.emit('selection', {'profile': self.active_profile, 'reason': 'Pinned for this turn'})
        try:
            from odessy.models.usage import process_ledger
            ledger = process_ledger()
            ledger.start_turn()
            if self.collaboration is not None:
                self.collaboration.start_turn(text)
            def progress(message):
                self.emit('progress', {'message': message})
                if status_callback is not None:
                    status_callback(message)
            kwargs = {'context': context if context is not None else self.memory.context(),
                      'stop_event': self._stop, 'status_callback': progress}
            if participant is not None:
                if self.collaboration is None:
                    raise ValueError('Collaboration is not configured')
                worker = asyncio.create_task(asyncio.to_thread(self.collaboration.consult, participant=participant,
                    question=text, context='', reason='Explicit user consultation', explicit=True))
                try:
                    response = await asyncio.shield(worker)
                except asyncio.CancelledError:
                    # Keep the session pinned until the synchronous provider exits.
                    while not worker.done():
                        try:
                            await asyncio.shield(worker)
                        except asyncio.CancelledError:
                            continue
                        except Exception:
                            break
                    if not worker.cancelled():
                        worker.exception()
                    raise
                kwargs['initial_observation'] = ToolObservation(ToolRequest('model.consult',
                    {'participant': participant, 'question': text}), response)
                self.emit('consultation', {'participant': participant, 'result': response})
            reset = getattr(model, 'reset_turn_usage', None)
            if reset:
                reset()
            agent = self.agent_factory(model, confirm=self.confirm)
            with collaboration_scope(self.collaboration):
                result = await agent.run_turn(text, **kwargs)
            if not self._stop.is_set():
                if remember:
                    self.memory.add(self._public(text), self._public(result.final_text))
                self.emit('transcript', {'role': 'assistant', 'text': result.final_text})
            self.emit('usage', getattr(result, 'metrics', {}))
            profile_costs = []
            for (participant, provider, model_name), entry in ledger.profiles.items():
                pricing, turn = entry['pricing'], entry['turn']
                turn_cost = None if pricing is None else pricing.cost(
                    prompt=turn.prompt, cached=turn.cached, completion=turn.completion)
                profile_costs.append({'participant': participant, 'provider': provider, 'model': model_name,
                    'turn': asdict(turn), 'total': asdict(entry['total']),
                    'turn_cost': turn_cost, 'total_cost': entry['cost'],
                    'pricing': asdict(pricing) if pricing is not None else None,
                    'price_status': entry['price_status']})
            self.emit('cost', {'turn': asdict(ledger.turn), 'total': asdict(ledger.total),
                               'turn_cost': ledger.cost(ledger.turn), 'total_cost': ledger.cost(),
                               'profiles': profile_costs})
            self.emit('result', result)
            return result
        except asyncio.CancelledError:
            self.emit('stopped')
            raise
        except Exception as exc:
            self.emit('error', {'message': str(exc), 'error_type': type(exc).__name__})
            raise
        finally:
            if announce is not None:
                model._announce = announce
            if self.collaboration is not None:
                self.collaboration.event_sink = sink
            for _, future in tuple(self._pending.values()):
                if not future.done():
                    future.set_result(False)
            self._pending.clear()
            self.active_profile = None
            self._active_model = None
            self._task = None

    async def stop(self):
        self._stop.set()
        for _, future in tuple(self._pending.values()):
            if not future.done():
                future.set_result(False)
        self._pending.clear()
        if self._task is not None and self._task is not asyncio.current_task():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        # Stop belongs to the cancelled turn, not to future standalone job approvals.
        self._stop = asyncio.Event()

    async def close(self):
        await self.stop()
        self._closed = True
        self.memory.clear()
        for queue in tuple(self._subscribers):
            if queue.full():
                queue.get_nowait()
            queue.put_nowait(None)


