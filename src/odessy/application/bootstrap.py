"""CLI and HTTP composition share the same session and participant boundaries."""
from __future__ import annotations

import asyncio
import inspect
from contextlib import asynccontextmanager
from pathlib import Path
import sys

from mcp import Client

from odessy.application.session import SessionController, parse_session_command
from odessy.authority.audit import current_audit, audit_scope
from odessy.collaboration.config import load_config
from odessy.collaboration.context import collaboration_scope
from odessy.collaboration.jobs import JobManager
from odessy.collaboration.participants import CollaborationService
from odessy.collaboration.tools import authorized_remote_targets, build_collaboration_server
from odessy.config.deployment import model_tool_scope
from odessy.runtime.agent_loop import ExecutionTarget
from odessy.runtime.types import AgentTurnResult
from odessy.tools.external_tools import NamespacedServer


class OwnedResources:
    """Enter and exit task-bound MCP/context scopes in their owning task."""
    def __init__(self, factory):
        self.ready = asyncio.get_running_loop().create_future()
        self.closing = asyncio.Event()
        self.task = asyncio.create_task(self._own(factory))

    async def _own(self, factory):
        try:
            async with factory() as servers:
                from odessy.tools.external_tools import external_tools
                self.ready.set_result((servers, tuple(external_tools()), current_audit()))
                await self.closing.wait()
        except BaseException as exc:
            if not self.ready.done():
                # CLI composition uses SystemExit for configuration/transport
                # failures. It must never escape into the HTTP event loop.
                if isinstance(exc, SystemExit):
                    failure = RuntimeError('Conversation initialization failed; inspect model and execution-host configuration')
                    failure.__cause__ = exc
                    exc = failure
                self.ready.set_exception(exc)
            else:
                raise

    async def aclose(self):
        self.closing.set()
        await self.task


class ManagedController(SessionController):
    def __init__(self, *, resources, turn_lock=None, **kwargs):
        self.resources, self.turn_lock = resources, turn_lock
        super().__init__(**kwargs)

    async def run_turn(self, *args, **kwargs):
        if self.turn_lock is None:
            return await super().run_turn(*args, **kwargs)
        # One coordinator generation pipeline per service process also keeps the
        # process ledger's turn reporting and edge GPU residency unambiguous.
        if self.turn_lock.locked():
            raise RuntimeError("Another session is using the coordinator; try again after it finishes")
        async with self.turn_lock:
            return await super().run_turn(*args, **kwargs)

    async def close(self):
        await super().close()
        if self.jobs is not None:
            await self.jobs.close()
        await self.resources.aclose()


async def create_controller(*, workspace=None, model=None, backend=None, use_fake_model=False,
                            selected_profile=None, interactive=False, turn_lock=None, mode="text"):
    """``mode`` sets the per-turn model-call default: ``voice`` for spoken turns,
    ``text`` (the default) for typed turns and the session API."""
    from odessy import cli
    workspace = Path(workspace or Path.cwd()).resolve()
    config = load_config()
    from odessy.models.usage import process_ledger
    process_ledger(allow_profile_prices=any(
        profile.adapter == 'cloud' and profile.pricing is not None
        for profile in config.participants.values()))
    service = CollaborationService(config)
    if use_fake_model:
        for name in config.participants:
            service.register_adapter(name, lambda profile, question, context:
                f"[Fake participant {profile.name}] Offline advice for: {question}")
    model = model if model is not None else cli._build_model(use_fake_model, backend=backend)
    profiles = {"current": {"name": "current", "model": getattr(model, "model", type(model).__name__)},
                "auto": {"name": "auto", "adapter": "auto"},
                "local": {"name": "local", "adapter": "lfm"}}
    profiles.update({name: profile.public_dict() for name, profile in config.participants.items()
                     if profile.adapter in {"cloud", "ollama", "lfm", "auto"}})

    def model_factory(name):
        if name == "current":
            return account_model(model, name)
        if use_fake_model:
            return cli._build_model(True)
        if name in config.participants:
            return account_model(service.build_model(name), name)
        return account_model(cli._build_model(use_fake_model, backend="auto" if name == "auto" else "lfm"), name)

    def account_model(chosen, name):
        from odessy.models.cloud_model import CloudModel
        from odessy.models.failover import FailoverModel, build_cloud_model_with_failover
        from odessy.models.routed_model import RoutedModel
        from odessy.models.usage import process_ledger
        if isinstance(chosen, CloudModel) and chosen.usage is None:
            ledger = process_ledger()
            chosen.usage = ledger.for_profile(name, chosen.provider, chosen.model, ledger.pricing)
        if isinstance(chosen, FailoverModel):
            for _, child in chosen.candidates:
                account_model(child, name)
        if isinstance(chosen, RoutedModel) and not getattr(chosen, '_profile_accounting', False):
            original = chosen._factories['cloud']
            chosen._factories['cloud'] = lambda: account_model(
                original() if original else build_cloud_model_with_failover(chosen.config.cloud_backend), name + '.cloud')
            chosen._profile_accounting = True
        return chosen

    if interactive:
        def print_consultation(event):
            print(f"[odessy] Consultation {event['participant']}: {event['status']} "
                  f"({event.get('reason') or 'participant response received'})", file=sys.stderr, flush=True)
        service.event_sink = print_consultation

    resources = OwnedResources(lambda: cli._tool_servers(workspace, cloud=cli.model_uses_cloud(model)))
    try:
        servers, externals, audit = await asyncio.wait_for(asyncio.shield(resources.ready), timeout=4)
    except (Exception, SystemExit):
        resources.task.cancel()
        await resources.aclose()
        if resources.ready.done() and not resources.ready.cancelled():
            resources.ready.exception()
        # An unavailable executor never selects a local/raw replacement.
        # The selected model can still answer with an empty execution surface.
        return _chat_only_controller(workspace=workspace, model_factory=model_factory,
            profiles=profiles, selected_profile=selected_profile, resources=resources,
            turn_lock=turn_lock, config=config, mode=mode)
    except asyncio.CancelledError:
        resources.task.cancel()
        await resources.aclose()
        if resources.ready.done() and not resources.ready.cancelled():
            resources.ready.exception()
        raise
    try:
        # Capture context-local tool admission now; callbacks may run in later API
        # tasks. Each turn re-enters these exact scopes instead of leaking trust.
        jobs = JobManager(config.participants, remote_targets=authorized_remote_targets(servers))
        loop = asyncio.get_running_loop()

        def coding_advice(profile, question, context):
            future = asyncio.run_coroutine_threadsafe(jobs.consult(profile, question, context), loop)
            try:
                return future.result(timeout=profile.timeout + 10)
            except BaseException:
                future.cancel()
                raise

        for adapter in ("codex", "claude_code", "custom_agent"):
            service.register_adapter(adapter, coding_advice)
        if config.settings.enabled:
            servers = (*servers, ExecutionTarget(build_collaboration_server(service, jobs)))
        from odessy.interaction.tools import build_interaction_server
        servers = (*servers, ExecutionTarget(build_interaction_server(lambda: controller.interactions)))
        names = []
        for target in servers:
            server = target.server if isinstance(target, ExecutionTarget) else target
            if isinstance(server, NamespacedServer):
                continue
            async with Client(server) as client:
                tools = await client.list_tools()
            names.extend(tool.name for tool in getattr(tools, "tools", tools))

        def agent_factory(chosen, confirm=None):
            agent = cli._build_agent(workspace, chosen, servers, mode=mode)
            agent._confirm_action = controller.confirm_action
            agent._delegation_jobs = jobs
            # Composed sessions authorize only through the shared broker. Legacy
            # direct OdessyAgent users retain their existing confirmation seam.
            agent._confirm = False
            agent._confirm_batch = None
            agent._approver = None
            return ScopedAgent(agent, service, names, externals, audit)

        controller = ManagedController(resources=resources, turn_lock=turn_lock, model_factory=model_factory,
            agent_factory=agent_factory, profiles=profiles, selected_profile=selected_profile or "current",
            collaboration=service, jobs=jobs, config=config)
        if interactive:
            from odessy.interaction.terminal import TerminalInteractionHandler, TerminalUnavailable
            keyboard = TerminalInteractionHandler(controller.interactions, stop_turn=controller.stop)
            async def terminal_interaction(entry):
                try:
                    await keyboard(entry)
                except TerminalUnavailable as exc:
                    print(f'[odessy] {exc}', file=sys.stderr, flush=True)
                    raise
            controller.interactions.handler = terminal_interaction
        jobs.event_sink = lambda event: controller.emit("job", event)
        return controller
    except BaseException:
        await resources.aclose()
        raise


def _chat_only_controller(*, workspace, model_factory, profiles, selected_profile,
                          resources, turn_lock, config, mode):
    from odessy import cli
    from odessy.runtime.agent_loop import OdessyAgent

    def agent_factory(chosen, confirm=None):
        # Do not load workspace hooks or attach coding agents/interaction tools.
        agent = OdessyAgent(model=chosen, policy=cli.OdessyPolicy(workspace_root=workspace),
            mcp_server=(), max_rejections=0,
            max_model_calls=cli._max_model_calls_from_env(cloud=cli.model_uses_cloud(chosen), mode=mode))
        return ScopedAgent(agent, None, [], (), None)

    controller = ManagedController(resources=resources, turn_lock=turn_lock,
        model_factory=model_factory, agent_factory=agent_factory, profiles=profiles,
        selected_profile=selected_profile or 'current', config=config,
        collaboration=None, jobs=None)
    controller.emit('execution_unavailable', {
        'message': 'Chat-only: the configured execution host is unavailable. Tools and delegated jobs are blocked. Reconnect after restoring verified access.'})
    return controller


class ScopedAgent:
    def __init__(self, agent, service, names, externals, audit=None):
        self.agent, self.service, self.names, self.externals = agent, service, names, externals
        self.audit = audit

    async def run_turn(self, *args, **kwargs):
        from odessy.tools.external_tools import external_tool_scope
        signature = inspect.signature(self.agent.run_turn)
        if not any(p.kind == inspect.Parameter.VAR_KEYWORD for p in signature.parameters.values()):
            kwargs = {key: value for key, value in kwargs.items() if key in signature.parameters}
        with collaboration_scope(self.service), model_tool_scope(self.names), external_tool_scope(self.externals), audit_scope(self.audit):
            return await self.agent.run_turn(*args, **kwargs)


@asynccontextmanager
async def controller_runtime(**kwargs):
    controller = await create_controller(**kwargs)
    try:
        yield controller
    finally:
        await controller.close()


class ControllerAgent:
    """The existing voice loops retain their microphone and utterance lifecycle."""
    def __init__(self, controller, *, isolate_turn=False):
        self.controller = controller
        self.isolate_turn = isolate_turn

    async def run_turn(self, text, *, participant=None, **kwargs):
        if not self.isolate_turn:
            return await self._run_turn(text, participant=participant, **kwargs)
        # The microphone worker outlives individual controller turns. Stop cancels
        # the child turn without taking that persistent worker down with it.
        turn = asyncio.create_task(self._run_turn(text, participant=participant, **kwargs))
        try:
            return await asyncio.shield(turn)
        except asyncio.CancelledError:
            if turn.cancelled():
                return AgentTurnResult(log=[], final_text='Stopped. No further tools were dispatched.')
            turn.cancel()
            await asyncio.gather(turn, return_exceptions=True)
            raise

    async def _run_turn(self, text, *, participant=None, **kwargs):
        command = parse_session_command(text, {**self.controller.profiles, **self.controller.config.participants})
        if command is None and text.strip().lower().startswith('consult ') and ':' in text:
            # An explicit unknown profile must not fall through to normal inference.
            participant, text = text.strip()[8:].split(':', 1)
            participant, text = participant.strip(), text.strip()
            if not text:
                raise ValueError('An explicit consultation requires a question')
        if command and command["action"] == "switch_model":
            from odessy.models.usage import process_ledger
            process_ledger().start_turn()
            name = self.controller.select_model(command["profile"])
            return AgentTurnResult(log=[], final_text=f"Model profile selected: {name}. Applies to the next request.")
        if command and command["action"] == "consult":
            participant, text = command["participant"], command["question"]
        return await self.controller.run_turn(text, participant=participant, **kwargs)
