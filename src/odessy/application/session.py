"""Transport-neutral, bounded in-memory agent sessions."""
from __future__ import annotations

import asyncio
import inspect
import os
import re
import threading
import time
import uuid
from collections import deque
from dataclasses import asdict, is_dataclass
from typing import Any

from odessy.authority.redaction import redact_text, looks_like_secret_name
from odessy.runtime.hooks import public_event
from odessy.runtime.types import ToolObservation, ToolRequest
from odessy.voice.memory import ConversationMemory
from odessy.collaboration.context import collaboration_scope
from odessy.interaction.broker import InteractionBroker
from odessy.interaction.context import interaction_scope


# These are public measurement counters, not credential-bearing token fields.
_TOKEN_METRICS = frozenset({
    'prompt_tokens', 'completion_tokens', 'cached_prompt_tokens', 'total_tokens',
    'input_tokens', 'output_tokens', 'cache_creation_input_tokens',
    'cache_read_input_tokens', 'generation_tokens_per_sec',
    'generation_tokens_per_second',
})


def public(value):
    if is_dataclass(value):
        value = asdict(value)
    if isinstance(value, dict):
        return {str(k): '<redacted>' if looks_like_secret_name(str(k)) and not (
            str(k) in _TOKEN_METRICS and (v is None or type(v) in (int, float))
        ) else public(v) for k, v in value.items()}
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
        #: Events raised on a worker thread, waiting for the loop to deliver them.
        #: The loop is the only thread allowed to touch ``_events`` or a subscriber
        #: queue, so a cross-thread announcement is queued here first.
        self._thread_events = deque()
        self._thread_events_lock = threading.Lock()
        self._loop = None
        self._subscriber_limit = subscriber_limit
        self.max_subscribers = max_subscribers
        self._sequence = 0
        self.interactions = InteractionBroker(self.emit, self._public)
        self._pending = self.interactions._pending
        self._task = None
        self._stop = asyncio.Event()
        self._closed = False
        self.monitoring_sink = None
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
        return self.interactions.pending_approvals

    @property
    def pending_questions(self):
        return self.interactions.pending_questions

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
            models = [self._model, self._active_model]
            for model in tuple(models):
                # A failover model holds one adapter (and key) per provider.
                models.extend(child for _, child in getattr(model, 'candidates', ()))
            for model in models:
                key = getattr(model, 'api_key', '')
                if key:
                    value = value.replace(key, '<redacted>')
        return value

    def emit(self, event_type, data=None):
        if self.monitoring_sink is not None and event_type == 'usage':
            from odessy.monitoring.runtime import sanitize
            values = asdict(data) if is_dataclass(data) else data if isinstance(data, dict) else {}
            safe = sanitize(values)
            rate = values.get('generation_tokens_per_sec')
            safe.update(sanitize({'generation_tokens_per_second': rate}))
            # Selected is not necessarily used or resident; never label it loaded.
            safe['selected_model'] = self.selected_profile
            try:
                self.monitoring_sink.publish(safe)
            except Exception:
                pass
        self._sequence += 1
        event = {'id': self._sequence, 'type': event_type, 'time': time.time(), 'data': self._public(data or {})}
        self._events.append(event)
        for queue in tuple(self._subscribers):
            if queue.full():
                queue.get_nowait()
            queue.put_nowait(event)
        return event

    def _emit_from_thread(self, event_type, data=None, *, loop=None):
        """Queue an event raised off the loop thread.

        Model calls, consultations, and coding jobs run in worker threads. They must
        not call ``emit`` themselves: it mutates the replay deque and feeds
        ``asyncio.Queue`` objects, and an ``asyncio.Queue`` is not safe to feed from
        another thread. The event is queued here and delivered by the loop -- at the
        turn's flush when it was raised during the turn, or by the callback scheduled
        now when it arrives later.

        ``loop`` is the loop captured when the sink was installed, so an event that
        arrives after its turn has finished is still delivered rather than stranded in
        the queue waiting for the next turn.
        """

        with self._thread_events_lock:
            self._thread_events.append((event_type, data))
        loop = loop if loop is not None else self._loop
        if loop is not None and not loop.is_closed():
            try:
                loop.call_soon_threadsafe(self._flush_thread_events)
            except RuntimeError:  # the loop closed between the check and the call
                pass

    def _flush_thread_events(self):
        """Deliver queued off-loop events in the order they were raised. Loop only."""

        while True:
            with self._thread_events_lock:
                if not self._thread_events:
                    return
                event_type, data = self._thread_events.popleft()
            self.emit(event_type, data)

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

    async def confirm_action(self, details):
        if self._stop.is_set() or self._closed:
            return False
        return await self.interactions.request_approval(details)

    async def _screen_direct(self, name, arguments, risk):
        """Screen a request that does not pass through the agent loop (a desktop job).

        The same rules and pre-tool hooks apply. A hooks file that cannot be read, or
        that changed since this process read it, fails closed.
        """
        from pathlib import Path
        from odessy.runtime.hooks import HookConfigError, load_hooks, screen_request
        from odessy.runtime.types import PolicyDecision
        try:
            hooks = load_hooks(Path.cwd())
        except HookConfigError as error:
            raise PermissionError(f'{name} refused: {error}') from error
        decision = PolicyDecision('confirm', f'{name} requested directly', risk)
        screened = await screen_request(hooks, ToolRequest(name, dict(arguments)), decision)
        if screened.action == 'deny':
            raise PermissionError(f'{name} refused: {screened.reason}')

    async def confirm(self, decision, request):
        # Direct job/API callers do not have runtime's canonical approval plan.
        # Describe their actual request rather than accepting a generated summary.
        decision_value = decision if isinstance(decision, dict) else vars(decision)
        request_value = request if isinstance(request, dict) else asdict(request)
        name = request_value.get('name', 'unknown action')
        arguments = request_value.get('arguments', {})
        risk = decision_value.get('risk', 'high')
        if name == 'agent.delegate':
            await self._screen_direct(name, arguments, risk)
        if name == 'agent.delegate' and hasattr(self.jobs, 'preflight_submission'):
            from pathlib import Path
            from odessy.authority.action_approval import build_action_approval
            from odessy.authority.policy import OdessyPolicy
            from odessy.runtime.types import PolicyDecision
            def target():
                return self.jobs.preflight_submission(arguments.get('participant'), arguments.get('task'),
                    mode=arguments.get('mode', 'advice'), workspace=arguments.get('workspace'))
            identity = target()
            policy = OdessyPolicy(Path.cwd())
            bound_decision = PolicyDecision('confirm', 'Delegate using configured child-agent permissions', risk)
            bound_request = ToolRequest(name, arguments)
            plan = build_action_approval(bound_request, bound_decision, policy,
                call_id=uuid.uuid4().hex, execution_target=identity)
            if not await self.confirm_action(plan.public_dict()):
                return False
            if not plan.matches(bound_request, bound_decision, policy, execution_target=target()):
                raise PermissionError('Delegation action, profile or execution target changed; new approval required')
            return True
        scopes = ['authorization']
        if arguments.get('execution_mode') == 'escalated' or arguments.get('mode') == 'escalated':
            scopes.append('escalation')
        import json
        prompt = 'Approve ' + str(name) + ' with arguments ' + json.dumps(self._public(arguments), sort_keys=True, ensure_ascii=False) + '?'
        return await self.confirm_action({'decision': decision, 'request': request,
            'risk': risk, 'scopes': scopes, 'prompt': prompt})

    def answer_approval(self, approval_id, approved, *, channel='authenticated_ui'):
        return self.interactions.resolve_approval(approval_id, approved, channel=channel)

    def answer_question(self, question_id, answer, *, channel='authenticated_ui'):
        return self.interactions.resolve_question(question_id, answer, channel=channel)

    async def run_turn(self, text, participant=None, *, context=None, stop_event=None,
                       status_callback=None, remember=True, turn_id=None):
        if self._closed or self.running:
            raise RuntimeError('Session is closed or a turn is already running')
        self.interactions.start_turn(turn_id)
        self._task = asyncio.current_task()
        self._stop = stop_event if stop_event is not None else asyncio.Event()
        self.active_profile = self.selected_profile
        model = self._model
        self._active_model = model
        loop = self._loop = asyncio.get_running_loop()
        announce = getattr(model, '_announce', None)
        if announce is not None:
            def report_route(message):
                announce(message)
                self._emit_from_thread('routing', {'message': message}, loop=loop)
            model._announce = report_route
        sink = getattr(self.collaboration, 'event_sink', None)
        if self.collaboration is not None:
            def report_consultation(event):
                if sink is not None:
                    sink(event)
                self._emit_from_thread(event.get('type', 'consultation'), event, loop=loop)
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
            if _accepts(agent.run_turn, 'observer'):
                # Tool, policy and hook events as they happen, with names and
                # outcomes only (public_event); the full entries stay in the turn log.
                def observe(entry):
                    event = public_event(entry)
                    if event is not None:
                        self.emit(*event)
                kwargs['observer'] = observe
            with collaboration_scope(self.collaboration), interaction_scope(self.interactions):
                result = await agent.run_turn(text, **kwargs)
            # Routing notes and peer results were raised on a worker thread, so they
            # are still queued. Deliver them before the turn's closing events: a
            # client must see them in the order they happened, and the turn must not
            # return with its own announcements missing from its events.
            self._flush_thread_events()
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
            self._flush_thread_events()
            self._loop = None
            if announce is not None:
                model._announce = announce
            if self.collaboration is not None:
                self.collaboration.event_sink = sink
            self.interactions.end_turn()
            self.active_profile = None
            self._active_model = None
            self._task = None

    async def stop(self):
        self._stop.set()
        self.interactions.cancel_pending('stopped')
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
        self.interactions.close()
        self.memory.clear()
        # A worker thread that outlives the session can still raise an event; with the
        # loop gone it would queue forever, so drop what is left.
        with self._thread_events_lock:
            self._thread_events.clear()
        for queue in tuple(self._subscribers):
            if queue.full():
                queue.get_nowait()
            queue.put_nowait(None)


def _accepts(function, name):
    try:
        parameters = inspect.signature(function).parameters.values()
    except (TypeError, ValueError):
        return False
    return any(p.name == name or p.kind == inspect.Parameter.VAR_KEYWORD for p in parameters)
