"""Transport-neutral, bounded in-memory agent sessions."""
from __future__ import annotations

import asyncio
import contextlib
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
from odessy.runtime_backends import (
    RuntimeCapabilityUnavailable,
    TurnCancelled,
    normalize_engine,
    resolve_and_build,
)
from odessy.runtime_backends.contracts import RuntimeEngineLocked, StopOutcome
from odessy.voice.memory import ConversationMemory
from odessy.collaboration.context import collaboration_scope
from odessy.interaction.broker import InteractionBroker
from odessy.interaction.context import interaction_scope


# These are public measurement counters, not credential-bearing token fields.
_TOKEN_METRICS = frozenset({
    'prompt_tokens', 'completion_tokens', 'cached_prompt_tokens', 'total_tokens',
    'input_tokens', 'output_tokens', 'cache_creation_input_tokens',
    'cache_read_input_tokens', 'cached_input_tokens', 'generation_tokens_per_sec',
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
                 event_limit=256, subscriber_limit=64, max_subscribers=16,
                 runtime_selection=None):
        if not profiles or min(event_limit, subscriber_limit, max_subscribers) < 1:
            raise ValueError('Profiles and positive event bounds are required')
        self.id = uuid.uuid4().hex
        self.model_factory, self.agent_factory = model_factory, agent_factory
        self.profiles, self.collaboration, self.jobs, self.config = profiles, collaboration, jobs, config
        self.runs = None
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
        #: Incremented by every Stop. A turn admitted before the increment must not reach
        #: the engine afterwards, which is what makes Stop safe before generation.
        self._stop_epoch = 0
        #: ``(task, outcome)`` for the last Stop that addressed a running turn. A local
        #: cancellation of that task reports *this* outcome rather than inventing "stopped".
        self._last_stop: tuple[Any, dict] | None = None
        self._closed = False
        self.monitoring_sink = None
        # The engine is decided once, here, and never afterwards. ``resolve_and_build``
        # raises for an unknown or unavailable engine instead of falling back.
        self.runtime_selection, self.runtime = resolve_and_build(self, runtime_selection)
        if self.engine == 'legacy':
            self._model = self._validate_model(self.selected_profile)
        else:
            # A non-legacy engine owns its own model: building a legacy adapter here would
            # demand a key for a model the engine never uses, and would be a second source
            # of truth for the selected route.
            self._validate_profile_name(self.selected_profile)
            self._model = None

    @property
    def engine(self):
        """The engine bound to this session. Immutable for the session's lifetime."""
        return self.runtime_selection.engine

    def select_engine(self, name):
        """Reject any attempt to move an existing session to another engine.

        Selecting the current engine is a no-op, so idempotent callers keep working.
        """
        if self._closed:
            raise RuntimeError('Session is closed')
        requested = normalize_engine(name)
        if requested != self.engine:
            raise RuntimeEngineLocked(self.engine, requested)
        return self.engine

    def public_snapshot(self):
        """The transport-neutral session snapshot; the API and the runtime share one shape.

        Events go through the runtime envelope, so a different engine cannot change the
        envelope a client receives.
        """
        return {'id': self.id, 'selected_profile': self.selected_profile,
                'available_profiles': list(self.profiles),
                'active_profile': self.active_profile, 'running': self.running,
                'history': public(self.memory.turns),
                'pending_approvals': self.pending_approvals,
                'pending_questions': self.pending_questions,
                'events': [event.public() for event in self.runtime.events()]}

    def _validate_profile_name(self, name):
        if name not in self.profiles:
            raise ValueError(f'Unknown model profile: {name}')
        return name

    def _validate_model(self, name):
        self._validate_profile_name(name)
        return self.model_factory(name)

    @property
    def model_profile(self):
        """The selected profile's settings, as the engine sees them.

        Only plain data is returned: the engine must not depend on a legacy adapter or on
        this controller's internals to know which provider and model the user chose.
        """
        self._validate_profile_name(self.selected_profile)
        return self._plain_profile(self.profiles[self.selected_profile])

    @property
    def model_profiles(self):
        """Every configured profile as plain data, so the engine can declare the models
        a session may switch to without being relaunched."""
        return [self._plain_profile(value) for value in self.profiles.values()]

    @staticmethod
    def _plain_profile(value):
        if hasattr(value, 'public_dict'):
            value = value.public_dict()
        return dict(value) if isinstance(value, dict) else {}

    @property
    def events(self):
        return list(self._events)

    @property
    def running(self):
        if self._task is not None:
            return True
        # A turn the caller abandoned never ended: the engine still owns it, so the session
        # is busy and must not admit another turn behind it.
        return bool(getattr(self.runtime, 'orphaned_turn', False))

    @property
    def stop_epoch(self):
        """Changes with every Stop; capture it at admission to detect a late arrival."""
        return self._stop_epoch

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
            models = [self._model, self._active_model,
                      getattr(getattr(self, "runtime", None), "route", None)]
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
        recorder = getattr(self, '_conversation_recorder', None)
        if recorder is not None and isinstance(event['data'], dict):
            from .conversations.store import StorageError
            try:
                recorder.public_event(event_type, event['data'])
            except StorageError:
                pass  # Public progress is best effort; admission/dispatch/settlement are strict.
        self._events.append(event)
        for queue in tuple(self._subscribers):
            if queue.full():
                queue.get_nowait()
            queue.put_nowait(event)
        return event

    def emit_from_worker(self, event_type, data=None):
        """Thread-safe emit for an engine's worker threads (deltas, tool progress).

        The loop delivers the event in order; the caller never touches the replay deque or
        an asyncio queue from another thread. This is the only entry point an engine has
        for transient events, so a runtime without it simply stays settlement-only.
        """
        self._emit_from_thread(event_type, data)

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

    async def select_model_async(self, profile):
        """Apply Harness protocol changes off-loop; publish the selection on the app loop."""
        if self.engine == 'legacy':
            return self.select_model(profile)
        if self._closed:
            raise RuntimeError('Session is closed')
        self._validate_profile_name(profile)
        value = self.profiles[profile]
        if hasattr(value, 'public_dict'):
            value = value.public_dict()
        await asyncio.to_thread(self.runtime.select_route,
                                dict(value) if isinstance(value, dict) else {},
                                profile_name=profile)
        self.selected_profile = profile
        self.emit('selection', {'profile': profile, 'engine': self.engine,
                               'reason': 'User selection; applies to next turn'})
        return profile

    def select_model(self, profile):
        if self._closed:
            raise RuntimeError('Session is closed')
        if self.engine != 'legacy':
            # The engine applies the change to *future* turns through its own protocol
            # (set_config_option) and refuses what it cannot apply; this controller only
            # records the new selection once the engine accepted it.
            self._validate_profile_name(profile)
            value = self.profiles[profile]
            if hasattr(value, 'public_dict'):
                value = value.public_dict()
            self.runtime.select_route(dict(value) if isinstance(value, dict) else {},
                                      profile_name=profile)
            self.selected_profile = profile
            self.emit('selection', {'profile': profile, 'engine': self.engine,
                                    'reason': 'User selection; applies to next turn'})
            return profile
        model = self._validate_model(profile)
        self.selected_profile, self._model = profile, model
        self.emit('selection', {'profile': profile, 'reason': 'User selection; applies to next turn'})
        return profile

    async def confirm_action(self, details):
        if self._stop.is_set() or self._closed:
            return False
        approved = await self.interactions.request_approval(details)
        if not approved and self.runs is not None and details.get('operation') == 'agent.run_continue':
            # A refused handoff ends the run, which would otherwise keep its repository.
            self.runs.decline(details['target_identity']['run_id'])
        return approved

    async def _screen_direct(self, name, arguments, risk):
        """Screen a request that does not pass through the agent loop (a desktop job).

        The same rules and pre-tool hooks apply. A hooks file that cannot be read, or
        that changed since this process read it, fails closed.
        """
        from pathlib import Path
        from odessy.runtime.hooks import HookConfigError, load_hooks, screen_request
        from odessy.runtime.types import PolicyDecision
        # The job's own workspace: a rule limited to a workspace, or a path glob, means
        # the one the job will change, not the one this process started in. Callers
        # pass the workspace the job manager resolved, never the request's own field.
        # A remote job's folder is not on this machine; its rules apply as here, the
        # same choice the post-job review makes.
        workspace = Path(arguments.get('workspace') or Path.cwd())
        if not workspace.is_dir():
            workspace = Path.cwd()
        try:
            hooks = load_hooks(workspace)
        except HookConfigError as error:
            raise PermissionError(f'{name} refused: {error}') from error
        decision = PolicyDecision('confirm', f'{name} requested directly', risk)
        screened = await screen_request(hooks, ToolRequest(name, dict(arguments)), decision)
        if screened.action == 'deny':
            raise PermissionError(f'{name} refused: {screened.reason}')

    def _collaboration_rule(self, name, arguments):
        """The policy's collaboration rule for a direct request, or None without config.

        Whether the model may use collaboration on its own (``enabled``, ``automatic``)
        is not asked here: a person asked for this work directly.
        """
        from collections.abc import Mapping
        from odessy.authority.policy import collaboration_decision
        jobs = getattr(self.runs, 'jobs', None) if name != 'agent.delegate' else None
        participants = getattr(jobs or self.jobs, 'profiles', None)
        if not isinstance(participants, Mapping):
            return None
        settings = getattr(getattr(self.collaboration, 'config', None), 'settings', None)
        return collaboration_decision(ToolRequest(name, dict(arguments)), participants,
                                      default_participant=getattr(settings, 'default_participant', None))

    async def confirm(self, decision, request):
        # Direct job/API callers do not have runtime's canonical approval plan.
        # Describe their actual request rather than accepting a generated summary.
        decision_value = decision if isinstance(decision, dict) else vars(decision)
        request_value = request if isinstance(request, dict) else asdict(request)
        name = request_value.get('name', 'unknown action')
        arguments = request_value.get('arguments', {})
        risk = decision_value.get('risk', 'high')
        if name == 'agent.delegate' and not hasattr(self.jobs, 'preflight_submission'):
            await self._screen_direct(name, arguments, risk)
        handoff = name in {'agent.run', 'agent.run_continue'} and self.runs is not None
        if handoff or (name == 'agent.delegate' and hasattr(self.jobs, 'preflight_submission')):
            from pathlib import Path
            from odessy.authority.action_approval import build_action_approval
            from odessy.authority.policy import OdessyPolicy
            from odessy.runtime.types import PolicyDecision
            def target():
                if name == 'agent.run':
                    return self.runs.preflight_start(**arguments)
                if name == 'agent.run_continue':
                    return self.runs.preflight_continue(**arguments)
                return self.jobs.preflight_submission(arguments.get('participant'), arguments.get('task'),
                    mode=arguments.get('mode', 'advice'), workspace=arguments.get('workspace'))
            # The job manager's preflight first, for its specific messages; then the same
            # rule the agent's policy applies to these calls, so a request from the
            # desktop is held to exactly what a proposed one is.
            identity = target()
            rule = self._collaboration_rule(name, arguments)
            if rule is not None and rule.action == 'deny':
                # A request the config cannot carry out, reported as before (400).
                raise ValueError(f'{name} refused: {rule.reason}')
            # Screen against the workspace the job will really use: without a
            # `workspace` field a delegation runs in its participant's configured one.
            await self._screen_direct(name, {**arguments, 'workspace': identity['workspace']}, risk)
            policy = OdessyPolicy(Path.cwd())
            if rule is not None:
                order = {'low': 0, 'medium': 1, 'high': 2}
                risk = max(risk, rule.risk, key=lambda value: order.get(value, 2))
                bound_decision = PolicyDecision('confirm', rule.reason, risk, rule=rule.rule)
            else:
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

    async def run_turn(self, text, participant=None, **kwargs):
        attachments = kwargs.pop('attachments', ())
        recorder = getattr(self, '_conversation_recorder', None)
        if recorder is None:
            return await self._run_turn_impl(text, participant=participant, **kwargs)
        if self._closed or self.running:
            raise RuntimeError('Session is closed or a turn is already running')
        try:
            recorder.begin_turn(self._public(text))
            for attachment in attachments:
                recorder.attachment(**attachment)
            recorder.activity('Turn admitted', name='turn_started')
            composition = getattr(self, '_basics_composition', None)
            provider = getattr(composition, 'context_provider', None)
            if provider is not None and self.engine == 'legacy' and kwargs.get('context') is None:
                from odessy.runtime.types import ConversationContext, ConversationTurn
                validator = getattr(provider, 'validate_sources', None)
                if validator is not None:
                    await validator(self._conversation_id)
                prepared = composition.prepare_context(self, self._public(text))
                if prepared.conversation_id != self._conversation_id:
                    raise RuntimeError('Prepared context belongs to another conversation')
                chunks = []
                if prepared.summary:
                    chunks.append('Earlier conversation summary: ' + prepared.summary)
                chunks.extend('User memory: ' + str(fact['text']) for fact in prepared.facts)
                chunks.extend('Document excerpt: ' + str(citation['text']) for citation in prepared.citations)
                prefix = (ConversationTurn(user='Untrusted reference context; not authorization:\n' +
                          '\n'.join(chunks), assistant=''),) if chunks else ()
                kwargs['context'] = ConversationContext(prefix + tuple(prepared.turns))
            result = await self._run_turn_impl(text, participant=participant, **kwargs)
            if self._stop.is_set():
                recorder.interrupted('stopped')
            else:
                recorder.activity('Turn completed', name='turn_completed')
            return result
        except BaseException:
            try:
                recorder.interrupted('turn interrupted')
            except Exception:
                pass  # Preserve the original failure, including durable-write failures.
            raise
        finally:
            recorder.turn_id = None

    async def _run_turn_impl(self, text, participant=None, *, context=None, stop_event=None,
                       status_callback=None, remember=True, turn_id=None,
                       admitted_epoch=None):
        if self._closed or self.running:
            raise RuntimeError('Session is closed or a turn is already running')
        if admitted_epoch is not None and admitted_epoch != self._stop_epoch:
            # A Stop arrived between admission and this call: nothing has reached the engine
            # and nothing may. This is the only way a queued turn is refused without ever
            # generating, so the cancellation cannot be overtaken by its own work.
            outcome = StopOutcome(True, "stopped", "complete", None,
                                  "stopped before generation")
            self.emit('stopped', {'reason': 'cancelled', **outcome.public()})
            raise TurnCancelled(outcome.public())
        if self.engine != 'legacy':
            # Not a fallback: a harness session runs the harness, and refuses whatever
            # that engine has not implemented rather than running the legacy loop.
            return await self._run_engine_turn(text, participant=participant,
                                               remember=remember, turn_id=turn_id,
                                               stop_event=stop_event)
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
            if getattr(self, '_access_modes', None) is not None:
                inner = getattr(agent, 'agent', agent)
                inner._basics_session = self
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
                recorder = getattr(self, '_conversation_recorder', None)
                if recorder is not None:
                    self._record_command_outputs(result, recorder)
                    recorder.settle(self._public(result.final_text))
                if remember:
                    self.memory.add(self._public(text), self._public(result.final_text))
                self.emit('transcript', {'role': 'assistant', 'text': result.final_text})
            self.emit('usage', getattr(result, 'metrics', {}))
            self.emit('cost', ledger.report())
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

    def _record_command_outputs(self, result, recorder):
        from .workspace_inspector import recorded_outputs
        for output in recorded_outputs(result, session_id=self.id, turn_id=recorder.turn_id,
                                       sanitize=self._public):
            self.emit('command_output', recorder.command_output(self._public(output)))

    async def _run_engine_turn(self, text, *, participant=None, remember=True, turn_id=None, stop_event=None):
        """One turn on a non-legacy engine.

        The session keeps ownership of the turn's bookkeeping (pinning, history, usage,
        cost, closing events); the engine only produces the result. That way a client
        cannot tell which engine ran a turn from the event stream alone.
        """
        if participant is not None:
            raise RuntimeCapabilityUnavailable('collaboration', gate='10')
        if stop_event is not None and stop_event.is_set():
            raise TurnCancelled(StopOutcome(False, 'stopped', 'complete', 'cancelled',
                                            'input was cancelled before admission').public())
        self.interactions.start_turn(turn_id)
        self._task = asyncio.current_task()
        # Deltas arrive from the engine's worker threads; the loop must be capturable so
        # they are delivered live rather than held until the turn ends.
        self._loop = asyncio.get_running_loop()
        self.active_profile = self.selected_profile
        self._active_model = self._model
        self.emit('transcript', {'role': 'user', 'text': text})
        self.emit('selection', {'profile': self.active_profile, 'reason': 'Pinned for this turn',
                                'engine': self.engine})
        async def watch_external_stop():
            await stop_event.wait()
            await self.stop()
        watcher = asyncio.create_task(watch_external_stop()) if stop_event is not None else None
        try:
            from odessy.models.usage import process_ledger
            ledger = process_ledger()
            ledger.start_turn()
            result = await self.runtime.run_turn(text)
            # Transient deltas were queued while the turn ran. Deliver them before the
            # committed message so a consumer never sees text after the answer.
            self._flush_thread_events()
            recorder = getattr(self, '_conversation_recorder', None)
            if recorder is not None:
                self._record_command_outputs(result, recorder)
                recorder.settle(self._public(result.final_text))
            if remember:
                self.memory.add(self._public(text), self._public(result.final_text))
            self.emit('transcript', {'role': 'assistant', 'text': result.final_text})
            self.emit('usage', getattr(result, 'metrics', {}))
            self.emit('cost', ledger.report())
            self.emit('result', result)
            return result
        except TurnCancelled as exc:
            # The engine confirmed the cancellation: its partial text is not the answer, and
            # the event carries what was actually established (never a rollback claim).
            self._flush_thread_events()
            self.emit('stopped', {'reason': 'cancelled', **exc.public()})
            raise
        except asyncio.CancelledError:
            self._flush_thread_events()
            # The local await was abandoned. That is not an engine confirmation, so the event
            # says what is actually known: the Stop that caused it, or `unconfirmed`.
            self.emit('stopped', {'reason': 'local-cancel', **self._local_cancel_outcome()})
            raise
        except Exception as exc:
            self._flush_thread_events()
            self.emit('error', {'message': str(exc), 'error_type': type(exc).__name__})
            raise
        finally:
            if watcher is not None:
                watcher.cancel()
                await asyncio.gather(watcher, return_exceptions=True)
            self._flush_thread_events()
            self._loop = None
            self.interactions.end_turn()
            self.active_profile = None
            self._active_model = None
            self._task = None
            self._last_stop = None

    def _local_cancel_outcome(self):
        """What a cancelled local await may claim: the Stop that caused it, or unknown."""
        task = asyncio.current_task()
        recorded = self._last_stop
        if recorded is not None and recorded[0] is task:
            return dict(recorded[1])
        return StopOutcome(False, 'unconfirmed', 'pending', None,
                           'the local await was cancelled; engine-side termination is not '
                           'confirmed').public()

    async def stop(self):
        """Stop the running turn. Idempotent; returns what was established, not a guess."""
        # Every Stop belongs to the turn it interrupted: a turn still queued for admission is
        # refused by the epoch, and the next admitted turn captures a new one.
        self._stop_epoch += 1
        if self.engine == 'legacy':
            running = self._task is not None
            target = self._task
            self._stop.set()
            self.interactions.cancel_pending('stopped')
            if target is not None:
                self._last_stop = (target, {'requested': True, 'execution': 'stopped',
                                            'cleanup': 'complete', 'stop_reason': None,
                                            'confirmed': True, 'detail': ''})
            if self._task is not None and self._task is not asyncio.current_task():
                self._task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await self._task
            # Stop belongs to the cancelled turn, not to future standalone job approvals.
            self._stop = asyncio.Event()
            return {'status': 'stopped' if running else 'idle', 'requested': running,
                    'execution': 'stopped' if running else 'idle', 'cleanup': 'complete',
                    'stop_reason': None, 'confirmed': running,
                    'detail': 'the local engine stopped the turn; provider-side work is not '
                              'confirmed by this call'}
        # The Harness engine cancels per session through its own ACP channel, so the child -
        # and every other session - keeps running. Pending human waits are released first:
        # nothing may keep waiting on a turn that is being stopped.
        self.interactions.cancel_pending('stopped')
        target = self._task
        outcome = await self.runtime.stop()
        # Whichever local cancellation follows must report this outcome, not an empty
        # "stopped": an unconfirmed Stop leaves the child possibly still working.
        if target is not None:
            self._last_stop = (target, outcome.public())
        if not outcome.confirmed:
            # The engine did not confirm the end. Stop waiting for it locally, bounded, and
            # report that: this is not a rollback, and text already produced stays produced.
            if target is not None and target is not asyncio.current_task() and not target.done():
                target.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await asyncio.wait({target}, timeout=1.0)
        return {'status': outcome.execution, **outcome.public()}

    async def close(self):
        composition = getattr(self, '_basics_composition', None)
        if composition is not None:
            await composition.close_session(self.id)
        if self.engine == 'legacy':
            await self.stop()
        else:
            # Tear down in the order that keeps the engine honest: cancel its turn first
            # (bounded, and never claimed as more than it is), then let the engine close
            # the child it owns. Killing the child first would report the turn as an error
            # rather than the cancellation it actually was.
            with contextlib.suppress(Exception):
                await self.runtime.stop()
            task = self._task
            if task is not None and task is not asyncio.current_task():
                if not task.done():
                    task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await asyncio.wait({task}, timeout=2.0)
        self._closed = True
        modes = getattr(self, '_access_modes', None)
        if modes is not None:
            modes.close(self.id)
        if self.engine != 'legacy':
            # The engine owns a child process; closing the session must end it.
            await self.runtime.close()
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
