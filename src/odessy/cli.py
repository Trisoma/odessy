from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import os
import sys
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, cast

from mcp.client import Client

from odessy.runtime.agent_loop import ExecutionTarget, OdessyAgent
from odessy.models.cloud_model import CLOUD_PROVIDERS, CloudModel, CloudModelConfigError, build_cloud_model
from odessy.config.config_file import load_config_env
from odessy.config.deployment import (
    DEPLOYMENT_ENV,
    DeploymentConfigError,
    deployment_mode,
    model_tool_scope,
    remote_tool_server_urls,
)
from odessy.execution.contract import ISOLATION_REQUIREMENTS, IsolationRequirement
from odessy.execution.escalation import ApprovalRequest, EscalationDeniedError, EscalationOutcome
from odessy.models.fake_model import FakeModel
from odessy.models.lfm_model import LlamaCppLFMModel
from odessy.tools.mcp_server import build_odessy_mcp_server
from odessy.tools.external_tools import (
    MAX_EXTERNAL_TOOLS_CLOUD,
    MAX_EXTERNAL_TOOLS_LOCAL,
    ExternalSessions,
    NamespacedServer,
    ServerConfig,
    ServerConfigError,
    discover_tools,
    external_tool_scope,
    load_server_configs,
)
from odessy.tools.node_mcp import NodeToolServerHttp, node_tool_server_dependencies_installed
from odessy.models.ollama_model import OllamaModel
from odessy.authority.audit import AuditLogError, ExternalAuditLog, audit_scope
from odessy.authority.policy import OdessyPolicy
from odessy.config.commands import CommandsConfigError, load_preapproved_commands
from odessy.runtime.confirmation import TerminalConfirmer, printable
from odessy.runtime.types import PolicyDecision, ToolRequest
from odessy.voice.audio_io import CommandAudioIO
from odessy.voice.capture import CommandAudioStream, CommandPlayer
from odessy.voice.config import VoiceConfig
from odessy.voice.frames import WAKE_FRAME_BYTES
from odessy.voice.loop import VoiceLoop
from odessy.voice.memory import ConversationMemory
from odessy.voice.segmentation import SegmenterConfig, UtteranceSegmenter
from odessy.voice.session import (
    SessionOptions,
    VoiceSession,
    pcm_to_wav,
)
from odessy.voice.stt import WhisperCppSTT, blank_transcript_reason
from odessy.voice.tts import KokoroTTS
from odessy.models.routed_model import RoutedModel, RoutingConfig, model_uses_cloud
from odessy.models.usage import CloudBudgetExceeded, CloudUsageConfigError, process_ledger
from odessy.voice.vad import SileroSpeechDetector
from odessy.voice.wake import OpenWakeWordDetector, WakeGate
from odessy.application.bootstrap import ControllerAgent, controller_runtime

DEFAULT_LFM_ENDPOINT = "http://127.0.0.1:11434"
DEFAULT_LFM_MODEL = "lfm2.5-thinking:1.2b-q8_0"
LOCAL_BACKENDS = ("lfm", "ollama", "llama_cpp")
MODEL_BACKENDS = (*LOCAL_BACKENDS, "auto", *CLOUD_PROVIDERS)

Model = FakeModel | OllamaModel | LlamaCppLFMModel | CloudModel | RoutedModel


def _build_model(
    use_fake_model: bool,
    *,
    backend: str | None = None,
    model_name: str | None = None,
    ollama_model: str | None = None,
) -> Model:
    if use_fake_model:
        return FakeModel()
    backend = (backend or os.environ.get("ODESSY_MODEL_BACKEND", "lfm")).lower()
    if backend == "auto":
        return RoutedModel(RoutingConfig.from_env())
    if backend in CLOUD_PROVIDERS:
        # Cloud models ignore model_name: callers pass Ollama names, and the cloud
        # model is configured separately through ODESSY_CLOUD_MODEL.
        try:
            return build_cloud_model(backend)
        except CloudModelConfigError as error:
            raise SystemExit(str(error)) from error
    if backend == "ollama":
        name = (
            model_name
            or ollama_model
            or os.environ.get("ODESSY_MODEL_NAME")
            or os.environ.get("ODESSY_OLLAMA_MODEL", "gemma3:1b")
        )
        return OllamaModel(model=name)
    if backend == "llama_cpp":
        backend = "lfm"
    if backend != "lfm":
        raise ValueError(f"Unknown model backend: {backend}")
    name = model_name or os.environ.get("ODESSY_MODEL_NAME", DEFAULT_LFM_MODEL)
    endpoint = os.environ.get("ODESSY_MODEL_ENDPOINT", DEFAULT_LFM_ENDPOINT)
    return LlamaCppLFMModel(model=name, endpoint=endpoint)


def _build_agent(
    workspace_root: Path,
    model: Model,
    mcp_servers: tuple[object, ...] | None = None,
) -> OdessyAgent:
    from odessy.runtime.scheduler import concurrency_from_env
    try:
        preapproved = load_preapproved_commands(workspace_root)
    except CommandsConfigError as error:
        raise SystemExit(str(error)) from error
    confirmer = TerminalConfirmer(workspace_root)
    return OdessyAgent(
        tool_concurrency=concurrency_from_env(),
        model=model,
        policy=OdessyPolicy(
            workspace_root=workspace_root,
            isolation=_isolation_from_env(),
            preapproved_commands=preapproved,
        ),
        mcp_server=(
            mcp_servers
            if mcp_servers is not None
            else (ExecutionTarget(build_odessy_mcp_server(workspace_root=workspace_root)),)
        ),
        confirm=confirmer,
        confirm_batch=confirmer.batch,
        approver=TerminalEscalationApprover(),
        max_model_calls=_max_model_calls_from_env(cloud=model_uses_cloud(model)),
    )


#: Model generations allowed per turn, counting retries and follow-up steps.
MAX_MODEL_CALLS_ENV = "ODESSY_MAX_MODEL_CALLS"
DEFAULT_MAX_MODEL_CALLS = 4
DEFAULT_CLOUD_MAX_MODEL_CALLS = 8


def default_max_model_calls(*, cloud: bool) -> int:
    """8 for a cloud model, 4 for a local one.

    A cloud call takes about 2 s and a capable model explores before it answers: two
    DeepSeek multi-step attempts in the evaluation stopped one call short at 4. A local
    call takes about 30 s, so the local ceiling stays where it was.
    """

    return DEFAULT_CLOUD_MAX_MODEL_CALLS if cloud else DEFAULT_MAX_MODEL_CALLS


def _max_model_calls_from_env(*, cloud: bool = False) -> int:
    """The per-turn ceiling on model calls: ``ODESSY_MAX_MODEL_CALLS``, else the default."""

    raw = os.environ.get(MAX_MODEL_CALLS_ENV, "").strip()
    if not raw:
        return default_max_model_calls(cloud=cloud)
    try:
        value = int(raw)
    except ValueError:
        raise SystemExit(f"{MAX_MODEL_CALLS_ENV} must be a positive integer (got {raw!r})") from None
    if value < 1:
        raise SystemExit(f"{MAX_MODEL_CALLS_ENV} must be a positive integer (got {raw!r})")
    return value


class TerminalEscalationApprover:
    """Asks the user in the terminal whether one call may run with a wider sandbox.

    The model may ask for this (``sandbox_permissions`` with a justification), and the
    tool schema says the user is asked. Without an approver composed here, every such
    request failed with "no approval service is composed" and ended the turn.
    """

    async def ask(self, request: ApprovalRequest) -> EscalationOutcome:
        print(f"!! HIGH RISK: Odessy asks to run {request.tool_name} with a wider sandbox.")
        print(f"Reason: {printable(request.reason)}")
        try:
            answer = await asyncio.to_thread(input, "Type 'yes' to allow the wider sandbox for this one call: ")
        except EOFError:
            return "unavailable"
        return "allowed-once" if answer.strip().lower() == "yes" else "rejected"


def _isolation_from_env() -> IsolationRequirement:
    """How strict this deployment is about isolation.

    Defaults to ``preferred``, which is the behaviour every existing caller already has.
    It is deliberately **not** ``required`` by default: a host with no confinement backend
    would then refuse every process-spawning command, including on this machine, where
    the Python side has no Windows backend. Raising it is a deployment decision, so it is
    configuration rather than a constant.
    """

    raw = (os.environ.get("ODESSY_ISOLATION") or "preferred").strip().lower()
    if raw not in ISOLATION_REQUIREMENTS:
        allowed = ", ".join(ISOLATION_REQUIREMENTS)
        raise SystemExit(f"ODESSY_ISOLATION must be one of: {allowed} (got {raw!r})")
    return cast(IsolationRequirement, raw)


def _confirm_tool_call(decision: PolicyDecision, request: ToolRequest) -> bool:
    """One confirmation with no session memory; the agent gets a ``TerminalConfirmer``."""

    return TerminalConfirmer(Path.cwd())(decision, request)


async def _text_async(user_text: str, use_fake_model: bool, backend: str | None = None) -> None:
    workspace_root = Path.cwd()
    model = _build_model(use_fake_model, backend=backend)
    _announce_model(model)
    async with controller_runtime(workspace=workspace_root, model=model, interactive=True,
                                  use_fake_model=use_fake_model) as controller:
        agent = ControllerAgent(controller, isolate_turn=True)
        try:
            with _usage_report(reset=False):
                result = await agent.run_turn(user_text)
        except (PermissionError, EscalationDeniedError) as error:
            # A denial, a declined confirmation or a refused escalation is an outcome,
            # not a crash: say what was not run instead of printing a traceback.
            raise SystemExit(f"Odessy did not run it: {error}") from error
        except CloudBudgetExceeded as error:
            raise SystemExit(str(error)) from error
    print(result.final_text)


async def _voice_async(use_fake_model: bool, backend: str | None = None) -> None:
    config = VoiceConfig.from_env()
    workspace_root = Path.cwd()
    model = _build_model(use_fake_model, backend=backend, model_name=config.ollama_model)
    _announce_model(model)
    async with controller_runtime(workspace=workspace_root, model=model, interactive=True,
                                  use_fake_model=use_fake_model) as controller:
        agent = ControllerAgent(controller, isolate_turn=True)
        with _usage_report(reset=False):
            await _run_voice_turn(agent, config)


async def _run_voice_turn(agent: OdessyAgent, config: VoiceConfig) -> None:
    controller = getattr(agent, 'controller', None)
    broker = controller.interactions if controller is not None else None
    loop = VoiceLoop(
        agent=agent,
        audio_io=CommandAudioIO(
            recorder=config.audio_recorder,
            player=config.audio_player,
            input_device=config.audio_input_device,
            output_device=config.audio_output_device,
        ),
        stt=WhisperCppSTT(
            whisper_bin=config.whisper_bin,
            model_path=config.whisper_model,
        ),
        tts=KokoroTTS(
            model_path=config.kokoro_model,
            voices_path=config.kokoro_voices,
            provider=config.onnx_provider,
            cache_dir=config.onnx_cache_dir,
        ),
        tmp_dir=config.tmp_dir,
        seconds=config.voice_seconds,
        status_callback=print,
        interactions=broker,
        stop_turn=controller.stop if controller is not None else None,
        settling_ms=config.settling_ms,
    )
    if broker is not None:
        from odessy.voice.session import write_cue_wav
        config.tmp_dir.mkdir(parents=True, exist_ok=True)
        cue_path = config.tmp_dir / "odessy-interaction-cue.wav"
        write_cue_wav(cue_path)
        async def interaction_cue():
            await loop._blocking(loop.audio_io.play, cue_path)
        object.__setattr__(loop, 'cue', interaction_cue)
        from odessy.interaction.terminal import TerminalInteractionHandler, race_handlers
        broker.handler = race_handlers(loop.handle_interaction,
            TerminalInteractionHandler(broker, stop_turn=controller.stop, mark_ready=False))
    result = await loop.run_once()
    attempts = 1 + config.blank_retries
    attempt = 1
    while result.status == "blank" and attempt < attempts:
        attempt += 1
        # The model was never called for the blank turn, so retrying costs a
        # recording and nothing else. It is not a retry of a request.
        print(
            f"No usable speech in that recording ({result.error}). "
            f"Recording again ({attempt}/{attempts})."
        )
        result = await loop.run_once()
    if result.transcript:
        print(f"Transcript: {result.transcript}")
    if result.final_text:
        print(f"Odessy: {result.final_text}")
    if result.status == "blank":
        raise SystemExit(
            f"Heard no usable speech after {attempt} recording(s): {result.error}. "
            "Nothing was sent to the model."
        )
    if result.status != "ok":
        raise SystemExit(f"Voice turn failed with status {result.status}: {result.error}")


async def _listen_async(
    use_fake_model: bool,
    backend: str | None = None,
    interaction: str | None = None,
) -> None:
    """Continuous wake listening: the composition root for ``voice --listen``.

    Everything hardware-specific is bound here and nowhere else: the session receives
    plain callables, so it can be tested with recorded PCM and no device. Tool servers
    stay open for the whole session rather than per turn — starting the local Node
    server once per request would add seconds between the cue and the answer.
    """

    from odessy.voice.session import write_cue_wav

    config = VoiceConfig.from_env()
    workspace_root = Path.cwd()
    model = _build_model(use_fake_model, backend=backend, model_name=config.ollama_model)
    _announce_model(model)

    stt = WhisperCppSTT(whisper_bin=config.whisper_bin, model_path=config.whisper_model)
    tts = KokoroTTS(
        model_path=config.kokoro_model,
        voices_path=config.kokoro_voices,
        provider=config.onnx_provider,
        cache_dir=config.onnx_cache_dir,
    )
    player = CommandPlayer(player=config.audio_player, output_device=config.audio_output_device)
    stream = CommandAudioStream(
        recorder=config.audio_recorder, input_device=config.audio_input_device
    )
    session: VoiceSession | None = None
    try:
        stream.start()
        config.tmp_dir.mkdir(parents=True, exist_ok=True)
        cue_path = config.tmp_dir / "odessy-cue.wav"
        if not cue_path.exists():
            write_cue_wav(cue_path)

        async def next_frame() -> bytes:
            return await asyncio.to_thread(stream.read, WAKE_FRAME_BYTES)

        async def transcribe(pcm: bytes) -> str:
            path = config.tmp_dir / f"odessy-utterance-{uuid.uuid4().hex}.wav"
            path.write_bytes(pcm_to_wav(pcm))
            try:
                result = await asyncio.to_thread(stt.transcribe, path)
            finally:
                with contextlib.suppress(OSError):
                    path.unlink(missing_ok=True)
            if result.status == "empty":
                return ""
            if result.status != "ok":
                raise RuntimeError(result.error or result.status)
            return result.text

        async def speak(text: str) -> None:
            turn_id = session._turn_id
            generation = player.generation
            path = config.tmp_dir / f"odessy-reply-{uuid.uuid4().hex}.wav"
            result = await asyncio.to_thread(tts.synthesize, text, path)
            try:
                if result.status != "ok":
                    raise RuntimeError(result.error or result.status)
                if session._closing or turn_id != session._turn_id:
                    return
                await asyncio.to_thread(player.play, path, generation=generation)
            finally:
                with contextlib.suppress(OSError):
                    path.unlink(missing_ok=True)

        async def cue() -> None:
            generation = player.generation
            await asyncio.to_thread(player.play, cue_path, generation=generation)

        async with controller_runtime(workspace=workspace_root, model=model, interactive=True,
                                      use_fake_model=use_fake_model) as controller:
            agent = ControllerAgent(controller, isolate_turn=True)

            async def run_turn(text: str, stop: asyncio.Event, context: Any) -> str:
                with _usage_report(reset=False):
                    result = await agent.run_turn(text, stop_event=stop, context=context, remember=False)
                return result.final_text

            session = VoiceSession(
                frames=next_frame,
                close_frames=stream.close,
                wake=OpenWakeWordDetector(str(config.wake_model)),
                gate=WakeGate(threshold=config.wake_threshold),
                vad=SileroSpeechDetector(),
                transcribe=transcribe,
                speak=speak,
                stop_speaking=player.stop,
                stop_turn=controller.stop,
                run_turn=run_turn,
                classify_blank=blank_transcript_reason,
                segmenter=UtteranceSegmenter(
                    SegmenterConfig(max_utterance_ms=config.max_utterance_seconds * 1000)
                ),
                options=SessionOptions(
                    interaction=cast(Any, interaction or config.interaction),
                    settling_ms=config.settling_ms,
                    blank_retries=config.blank_retries,
                    followup_ms=config.followup_seconds * 1000,
                ),
                memory=ConversationMemory(),
                status=print,
                cue=cue,
            )
            from odessy.interaction.terminal import TerminalInteractionHandler, race_handlers
            controller.interactions.handler = race_handlers(session.interaction_handler(controller.interactions),
                TerminalInteractionHandler(controller.interactions, stop_turn=controller.stop, mark_ready=False))
            try:
                await session.run()
            except KeyboardInterrupt:
                pass
            finally:
                if model_uses_cloud(model):
                    line = process_ledger().session_line()
                    if line:
                        print(line, file=sys.stderr, flush=True)
            if session.error is not None:
                raise SystemExit(f"Voice session stopped: {session.error}")
    finally:
        try:
            if session is not None:
                await session.aclose()
        finally:
            try:
                player.stop()
            finally:
                stream.close()


def _announce_model(model: object) -> None:
    """Show explicit models at startup; auto announces each actual selection."""
    if not isinstance(model, RoutedModel):
        backend = (
            model.provider if isinstance(model, CloudModel)
            else "lfm" if isinstance(model, LlamaCppLFMModel)
            else "ollama" if isinstance(model, OllamaModel)
            else "fake" if isinstance(model, FakeModel)
            else type(model).__name__
        )
        name = getattr(model, "model", type(model).__name__)
        print(f"[odessy] model: {name} (backend={backend})", file=sys.stderr, flush=True)
    _announce_cloud(model)
    if model_uses_cloud(model):
        # Read prices and budget now, so a typo stops the run before anything is sent.
        try:
            ledger = process_ledger()
        except CloudUsageConfigError as error:
            raise SystemExit(str(error)) from error
        if ledger.budget_tokens is not None or ledger.budget_cost is not None:
            print(f"[odessy] cloud budget for this run: {ledger.budget_line()}", file=sys.stderr, flush=True)


@contextlib.contextmanager
def _usage_report(*, reset: bool = True):
    """Print the cloud tokens (and estimated cost) one turn used, if it used any."""

    try:
        ledger = process_ledger()
    except CloudUsageConfigError:
        # Reported at startup for cloud models; a local run has nothing to report.
        yield
        return
    if reset:
        ledger.start_turn()
    try:
        yield
    finally:
        line = ledger.turn_line()
        if line:
            print(line, file=sys.stderr, flush=True)


def _announce_cloud(model: object) -> None:
    """Say plainly, once per run, that this turn leaves the machine."""
    if isinstance(model, RoutedModel) and model.uses_cloud:
        print("[odessy] auto cloud fallback enabled: eligible requests and redacted tool output may be sent to " + str(model.config.cloud_backend), file=sys.stderr)
    if isinstance(model, CloudModel):
        print(
            f"[odessy] cloud model {model.provider}/{model.model}: your request and "
            "redacted tool output are sent to the provider.",
            file=sys.stderr,
        )


def _model_backend_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--model-backend",
        choices=MODEL_BACKENDS,
        default=None,
        help=(
            "Select the model backend (defaults to ODESSY_MODEL_BACKEND, then lfm). "
            "Cloud backends: " + ", ".join(CLOUD_PROVIDERS) + "."
        ),
    )


#: Printed when ``single`` mode runs without the Node tool server.
_NO_NODE_WARNING = (
    "[odessy] Node tool server is not installed, so only time.now, calculator.eval and "
    "terminal.run are available (no file, search or git tools), and commands run with "
    "no confinement backend. Run `python -m odessy doctor` for the setup steps."
)


@asynccontextmanager
async def _tool_servers(workspace_root: Path, *, cloud: bool = False) -> AsyncIterator[tuple[object, ...]]:
    """Built-in servers for this deployment, plus the third-party servers in servers.toml.

    A configured server is listed once at startup; its tools become model-facing for
    the run as ``<server>.<tool>``. A server that does not answer is reported and left
    out; it does not stop the run or affect the built-in tools. A stdio server keeps one
    session for the run, and every third-party call is recorded in the audit log.
    """

    try:
        configs = load_server_configs()
    except ServerConfigError as error:
        raise SystemExit(str(error)) from error
    try:
        audit = ExternalAuditLog.open() if configs else None
    except AuditLogError as error:
        raise SystemExit(str(error)) from error

    async with _builtin_tool_servers(workspace_root) as builtins:
        if not configs:
            yield builtins
            return
        async with ExternalSessions() as sessions:
            servers = {config.name: NamespacedServer(config, sessions) for config in configs}

            async def list_tools(config: ServerConfig) -> list[object]:
                return await servers[config.name].list_tools()

            limit = MAX_EXTERNAL_TOOLS_CLOUD if cloud else MAX_EXTERNAL_TOOLS_LOCAL
            tools, reachable, problems = await discover_tools(configs, limit=limit, list_tools=list_tools)
            for problem in problems:
                print(f"[odessy] external server {problem}", file=sys.stderr)
            # Being configured is the authorization: only listed servers that answered
            # are execution targets. Nothing found at runtime is ever added here.
            externals = tuple(ExecutionTarget(servers[config.name]) for config in reachable)
            with external_tool_scope(tools), audit_scope(audit):
                yield (*builtins, *externals)


@asynccontextmanager
async def _builtin_tool_servers(workspace_root: Path) -> AsyncIterator[tuple[object, ...]]:
    """The MCP servers for this deployment, kept running for the duration of the block.

    Each is wrapped in `ExecutionTarget` because this IS the authorization act: the
    composition root names the servers it means to execute on. A server that is merely
    reachable -- discovered, or supplied by a future dynamic source -- is not executable,
    so trust is never inherited from construction.
    """

    try:
        mode = deployment_mode()
    except DeploymentConfigError as error:
        raise SystemExit(str(error)) from error

    if mode == "split":
        yield (
            ExecutionTarget(build_odessy_mcp_server(workspace_root=workspace_root)),
            *(ExecutionTarget(url) for url in remote_tool_server_urls()),
        )
        return

    if not node_tool_server_dependencies_installed():
        print(_NO_NODE_WARNING, file=sys.stderr)
        server = build_odessy_mcp_server(workspace_root=workspace_root)
        async with Client(server) as client:
            tools = await client.list_tools()
        with model_tool_scope(tool.name for tool in getattr(tools, "tools", tools)):
            yield (ExecutionTarget(server),)
        return

    # Loopback only, on a free port, for this run only. The Node server registers its
    # command tool as `terminal.run` in single mode, so the Python server leaves it out.
    node = NodeToolServerHttp(workspace_root, host="127.0.0.1", extra_env={DEPLOYMENT_ENV: "single"})
    try:
        url = await node.__aenter__()
    except (RuntimeError, TimeoutError) as error:
        raise SystemExit(
            f"Could not start the local Node tool server: {error}\n"
            "Run `python -m odessy doctor` to check the setup."
        ) from error
    try:
        yield (
            ExecutionTarget(
                build_odessy_mcp_server(workspace_root=workspace_root, include_terminal=False)
            ),
            ExecutionTarget(url),
        )
    finally:
        await node.__aexit__(None, None, None)


def _quiet_transport_logs() -> None:
    """Keep per-request HTTP lines out of the console.

    Building the Python MCP server sets the root logger to INFO, after which httpx and
    the MCP client log every request ("HTTP Request: POST ...", session ids). The
    levels are set on those loggers, so they hold whatever the root config becomes.
    ``ODESSY_DEBUG_HTTP=1`` keeps them.
    """

    if os.environ.get("ODESSY_DEBUG_HTTP") == "1":
        return
    # The MCP SDK's HTTP client is `httpx2`; plain `httpx` is listed for older SDKs.
    for name in ("httpx", "httpx2", "mcp.client"):
        logging.getLogger(name).setLevel(logging.WARNING)


def main(argv: list[str] | None = None) -> None:
    load_config_env()
    _quiet_transport_logs()
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["coding-agent-server"]:
        parser = argparse.ArgumentParser(
            description="Serve configured coding agents over trusted operator stdio MCP. "
                        "Launching this command grants its operator approval authority for execution jobs.")
        parser.parse_args(argv[1:])
        from odessy.collaboration.worker import run_worker
        import inspect
        result = run_worker(trusted_stdio_operator=True)
        if inspect.isawaitable(result):
            asyncio.run(result)
        return
    if argv[:1] == ["serve"]:
        parser = argparse.ArgumentParser(description="Serve the authenticated Odessy API on loopback.")
        parser.add_argument("--host", default="127.0.0.1")
        parser.add_argument("--port", type=int, default=8765)
        parser.add_argument("--fake-model", action="store_true",
                            help="Use the deterministic fake model without API calls.")
        _model_backend_argument(parser)
        args = parser.parse_args(argv[1:])
        from odessy.application.serve import run_server, validate_binding
        try:
            validate_binding(args.host, args.port)
            asyncio.run(run_server(host=args.host, port=args.port, use_fake_model=args.fake_model,
                                   backend=args.model_backend))
        except (ValueError, RuntimeError) as error:
            raise SystemExit(str(error)) from error
        return
    if argv[:1] == ["doctor"]:
        parser = argparse.ArgumentParser(
            prog="python -m odessy doctor",
            description="Check this machine's Odessy setup and print how to fix what is missing.",
        )
        _model_backend_argument(parser)
        parser.add_argument(
            "--voice",
            action="store_true",
            help="Also check what a wake-word voice session needs.",
        )
        args = parser.parse_args(argv[1:])
        from odessy.config.doctor import main as doctor_main

        raise SystemExit(doctor_main(args.model_backend, voice=args.voice))
    if argv[:1] == ["voice"]:
        parser = argparse.ArgumentParser(
            description="Run one bounded local Odessy voice turn, or listen for a wake phrase."
        )
        parser.add_argument(
            "--fake-model",
            action="store_true",
            help="Use the deterministic fake model instead of Ollama/Gemma.",
        )
        parser.add_argument(
            "--listen",
            action="store_true",
            help="Keep listening for the wake phrase instead of recording one bounded turn.",
        )
        parser.add_argument(
            "--interaction",
            choices=["wake", "conversation"],
            default=None,
            help=(
                "wake: require the phrase before every request (default). "
                "conversation: keep a short follow-up window after each reply."
            ),
        )
        parser.add_argument(
            "--setup-wake",
            action="store_true",
            help="Download and verify the default wake-word model, then exit.",
        )
        _model_backend_argument(parser)
        args = parser.parse_args(argv[1:])
        if args.setup_wake:
            from odessy.voice.assets import asset_dir, install

            for line in install(asset_dir()):
                print(line)
            return
        if args.listen:
            asyncio.run(
                _listen_async(
                    use_fake_model=args.fake_model,
                    backend=args.model_backend,
                    interaction=args.interaction,
                )
            )
            return
        asyncio.run(_voice_async(use_fake_model=args.fake_model, backend=args.model_backend))
        return
    parser = argparse.ArgumentParser(description="Run the Odessy text loop.")
    parser.add_argument(
        "--fake-model",
        action="store_true",
        help="Use the deterministic fake model instead of a real model backend.",
    )
    _model_backend_argument(parser)
    parser.add_argument("text", help="User text to send into the Odessy loop.")
    args = parser.parse_args(argv)
    if not args.text:
        parser.error("text is required unless using the voice subcommand")
    asyncio.run(_text_async(args.text, use_fake_model=args.fake_model, backend=args.model_backend))


if __name__ == "__main__":
    main()
