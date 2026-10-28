from __future__ import annotations
from odessy.voice.providers import create_stt, create_tts, close_providers, cancel_provider

import argparse
import asyncio
import contextlib
import functools
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
from odessy.execution.providers import default_execution_providers
from odessy.execution.registry import ProviderRegistry, with_unconfined_fallback
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
from odessy.runtime.hooks import HookConfigError, load_hooks
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
from odessy.models.failover import FailoverModel, build_cloud_model_with_failover
from odessy.models.routed_model import RoutedModel, RoutingConfig, model_uses_cloud
from odessy.models.usage import CloudBudgetExceeded, CloudUsageConfigError, process_ledger
from odessy.voice.vad import SileroSpeechDetector
from odessy.voice.wake import OpenWakeWordDetector, WakeGate
from odessy.application.bootstrap import ControllerAgent, controller_runtime

DEFAULT_LFM_ENDPOINT = "http://127.0.0.1:11434"
DEFAULT_LFM_MODEL = "lfm2.5-thinking:1.2b-q8_0"
LOCAL_BACKENDS = ("lfm", "ollama", "llama_cpp")
MODEL_BACKENDS = (*LOCAL_BACKENDS, "auto", *CLOUD_PROVIDERS)

Model = FakeModel | OllamaModel | LlamaCppLFMModel | CloudModel | FailoverModel | RoutedModel


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
        # model is configured separately through ODESSY_CLOUD_MODEL. A local fallback
        # in ODESSY_CLOUD_FAILOVER is built as that backend would be on its own.
        try:
            return build_cloud_model_with_failover(
                backend,
                local_factory=lambda name: _build_model(
                    False, backend=name, model_name=model_name, ollama_model=ollama_model
                ),
            )
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
    *,
    mode: str = "voice",
) -> OdessyAgent:
    from odessy.runtime.scheduler import concurrency_from_env
    try:
        preapproved = load_preapproved_commands(workspace_root)
    except CommandsConfigError as error:
        raise SystemExit(str(error)) from error
    try:
        hooks = load_hooks(workspace_root)
    except HookConfigError as error:
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
            else (ExecutionTarget(build_odessy_mcp_server(workspace_root=workspace_root, registry=execution_registry())),)
        ),
        confirm=confirmer,
        confirm_batch=confirmer.batch,
        approver=TerminalEscalationApprover(),
        max_model_calls=_max_model_calls_from_env(cloud=model_uses_cloud(model), mode=mode),
        hooks=hooks,
    )


def _paired_host():
    """The configured paired host, or ``None``.

    Wrapped so tests and the doctor can substitute it, and so a bad configuration is
    reported as a configuration error rather than an import failure.
    """

    from odessy.tools.paired import PairedConfigError, paired_host

    try:
        return paired_host()
    except PairedConfigError as error:
        raise SystemExit(str(error)) from error


#: Model generations allowed per turn, counting retries and follow-up steps.
MAX_MODEL_CALLS_ENV = "ODESSY_MAX_MODEL_CALLS"
DEFAULT_MAX_MODEL_CALLS = 4
DEFAULT_CLOUD_MAX_MODEL_CALLS = 8
DEFAULT_CLOUD_TEXT_MAX_MODEL_CALLS = 20
#: ``voice``: someone is waiting to hear the answer. ``text``: typed turns and the
#: session API, where a longer multi-step task is expected.
TURN_MODES = ("voice", "text")


def default_max_model_calls(*, cloud: bool, mode: str = "voice") -> int:
    """8 for a spoken cloud turn, 20 for a typed one, 4 for a local model.

    A cloud call takes about 2 s and a capable model explores before it answers: two
    DeepSeek multi-step attempts in the evaluation stopped one call short at 4. A
    spoken turn stays at 8 because someone is listening for the answer; a typed task
    (find, change, test, fix) often needs 10 to 20 steps, and the cloud budget and
    context compaction bound what the longer turn costs. A local call takes about
    30 s, so the local ceiling stays where it was. The evaluation suite uses the
    spoken default, so its results stay comparable.
    """

    if mode not in TURN_MODES:
        raise ValueError(f"Unknown turn mode {mode!r}; expected one of: {', '.join(TURN_MODES)}")
    if not cloud:
        return DEFAULT_MAX_MODEL_CALLS
    return DEFAULT_CLOUD_TEXT_MAX_MODEL_CALLS if mode == "text" else DEFAULT_CLOUD_MAX_MODEL_CALLS


def _max_model_calls_from_env(*, cloud: bool = False, mode: str = "voice") -> int:
    """The per-turn ceiling on model calls: ``ODESSY_MAX_MODEL_CALLS``, else the default."""

    raw = os.environ.get(MAX_MODEL_CALLS_ENV, "").strip()
    if not raw:
        return default_max_model_calls(cloud=cloud, mode=mode)
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


@functools.lru_cache(maxsize=1)
def execution_registry() -> ProviderRegistry:
    """The confinement backends this host offers to the Python ``terminal.run``.

    The shipped backends (bwrap, landlock, seatbelt), each probing whether this host
    can actually enforce it, then the unconfined fallback, which only a request that
    does not require isolation can select. Built once: the probes describe the host,
    not the call.
    """

    return with_unconfined_fallback(default_execution_providers())


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
                                  use_fake_model=use_fake_model, mode="text") as controller:
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
                                  use_fake_model=use_fake_model, mode="voice") as controller:
        agent = ControllerAgent(controller, isolate_turn=True)
        with _usage_report(reset=False):
            await _run_voice_turn(agent, config)


async def _hybrid_voice_async(use_fake_model: bool, backend: str | None = None, *,
                              listen: bool = True, interaction: str | None = None,
                              audio_factory=None) -> None:
    """Keep keyboard, session authority and history alive independently of audio."""
    from odessy.application.hybrid import HybridConsole
    config = VoiceConfig.from_env()
    if interaction is not None:
        from dataclasses import replace
        config = replace(config, interaction=interaction)
    model = _build_model(use_fake_model, backend=backend, model_name=config.ollama_model)
    _announce_model(model)
    async with controller_runtime(workspace=Path.cwd(), model=model, interactive=True,
                                  use_fake_model=use_fake_model, mode='voice') as controller:
        console = HybridConsole(controller, persistent=listen,
                                turn_context=lambda: _usage_report(reset=False))
        try:
            if audio_factory is None:
                from odessy.voice.hybrid import HybridAudioAdapter
                audio_factory = HybridAudioAdapter
            console.audio = audio_factory(config=config, submit_voice=console.submit_voice,
                stop_turn=console.stop_turn, report_audio_error=console.report_audio_error,
                report_status=console.display, memory=controller.memory,
                interactions=controller.interactions)
            console.audio.listen = listen
            console.audio.on_reply_complete = getattr(console, 'voice_reply_complete', None)
            await console.run()
        finally:
            # Idempotent even if run() already closed readers and hardware.
            close = getattr(console, 'aclose', None)
            if close is not None:
                await close()
            if model_uses_cloud(model):
                line = process_ledger().session_line()
                if line:
                    print(line, file=sys.stderr, flush=True)


async def _run_voice_turn(agent: OdessyAgent, config: VoiceConfig) -> None:
    controller = getattr(agent, 'controller', None)
    broker = controller.interactions if controller is not None else None
    stt = create_stt(config, builtin_factory=WhisperCppSTT)
    try:
        tts = create_tts(config, builtin_factory=KokoroTTS)
    except BaseException:
        close_providers(stt)
        raise
    loop = VoiceLoop(
        agent=agent,
        audio_io=CommandAudioIO(
            recorder=config.audio_recorder,
            player=config.audio_player,
            input_device=config.audio_input_device,
            output_device=config.audio_output_device,
        ),
        stt=stt,
        tts=tts,
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
    try:
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

    finally:
        cancel_provider(stt)
        cancel_provider(tts)
        await asyncio.to_thread(close_providers, stt, tts)


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

    stt = create_stt(config, builtin_factory=WhisperCppSTT)
    try:
        tts = create_tts(config, builtin_factory=KokoroTTS)
    except BaseException:
        close_providers(stt)
        raise
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
                                      use_fake_model=use_fake_model, mode="voice") as controller:
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
                stream_speech=config.stream_speech,
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
            cancel_provider(stt)
            cancel_provider(tts)
            await asyncio.to_thread(close_providers, stt, tts)


def _announce_model(model: object) -> None:
    """Show explicit models at startup; auto announces each actual selection."""
    if isinstance(model, FailoverModel):
        print(f"[odessy] model: {model.chain()} (failover in this order)", file=sys.stderr, flush=True)
    elif not isinstance(model, RoutedModel):
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


def _print_egress(limit: int) -> None:
    from odessy.authority.egress import EGRESS_FILE_ENV, egress_file, format_record, read_records

    path = egress_file()
    if path is None:
        print(f"The egress log is off ({EGRESS_FILE_ENV} is set to an empty value).")
        return
    records = read_records(path, limit)
    if not records:
        print(f"Nothing sent to a cloud provider is recorded in {path}.")
        return
    print(f"Last {len(records)} record(s) in {path}:")
    for record in records:
        print(format_record(record))


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
    if isinstance(model, FailoverModel):
        primary = model.candidates[0][0]
        clouds = [name for name, child in model.candidates if isinstance(child, CloudModel)]
        print(
            f"[odessy] cloud model {primary}: your request and redacted tool output are sent to the "
            f"provider; while it is unavailable, to the next of: {', '.join(clouds[1:]) or 'none'}"
            + (" (then this machine's local model)" if len(clouds) < len(model.candidates) else "")
            + ".",
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
    "terminal.run are available (no file, search or git tools). Commands use bwrap, "
    "landlock or seatbelt where this host has one; on Windows they run unconfined. "
    "Run `python -m odessy doctor` for the setup steps."
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
        paired = _paired_host()
        if paired is not None:
            # Only the paired transport is offered. A paired host that cannot answer is
            # an error, never a reason to use a legacy remote URL instead.
            async with ExternalSessions() as sessions:
                server = paired.server(sessions)
                try:
                    tools = await asyncio.wait_for(
                        server.list_tools(), timeout=paired.server_config().timeout
                    )
                except Exception as error:  # noqa: BLE001 - reported, not swallowed
                    raise SystemExit(
                        f"Paired host {paired.alias!r} is unavailable: {error}\n"
                        "Run `odessy-link connect --peer " + paired.alias + "` to test it, or "
                        "`python -m odessy doctor`. No legacy transport is used instead."
                    ) from error
                print(
                    f"[odessy] paired host {paired.alias}: {len(tools)} tools over mTLS",
                    file=sys.stderr,
                )
                yield (
                    ExecutionTarget(build_odessy_mcp_server(workspace_root=workspace_root, registry=execution_registry())),
                    ExecutionTarget(server),
                )
            return
        yield (
            ExecutionTarget(build_odessy_mcp_server(workspace_root=workspace_root, registry=execution_registry())),
            *(ExecutionTarget(url) for url in remote_tool_server_urls()),
        )
        return

    if not node_tool_server_dependencies_installed():
        print(_NO_NODE_WARNING, file=sys.stderr)
        server = build_odessy_mcp_server(workspace_root=workspace_root, registry=execution_registry())
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


def _check_configuration(argv: list[str]) -> None:
    """Refuse to start on a malformed setting, and warn about names nobody reads.

    ``doctor`` is exempt: it reports the same issues as checks. Refusing to run the
    one command that explains a broken configuration would leave the user with no
    way to see what is wrong. Help is exempt for the same reason.

    The voice paths are required only by ``voice``, and the wake model only by
    ``--listen``: a text-loop run must not demand whisper.cpp on this machine.
    ``--setup-wake`` downloads the wake assets before anything uses them, so it is
    exempt as well.
    """

    if argv[:1] == ["doctor"] or "-h" in argv or "--help" in argv:
        return
    contexts: set[str] = set()
    if argv[:1] == ["voice"] and "--setup-wake" not in argv:
        contexts.add("voice")
        if "--listen" in argv:
            contexts.add("wake")
        if '--no-text-fallback' not in argv and sys.stdin.isatty():
            contexts.add('hybrid')
    from odessy.config.schema import enforce_config

    enforce_config(contexts=contexts)


def main(argv: list[str] | None = None) -> None:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ['setup']:
        from odessy.setup.cli import main as setup_main
        raise SystemExit(setup_main(argv[1:]))
    load_config_env()
    _check_configuration(argv)
    _quiet_transport_logs()
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
    if argv[:1] == ["egress"]:
        parser = argparse.ArgumentParser(
            prog="python -m odessy egress",
            description="Show what Odessy recently sent to cloud providers: sizes and tokens, never content.",
        )
        parser.add_argument("--limit", type=int, default=20, help="How many recent records to show (default 20).")
        args = parser.parse_args(argv[1:])
        _print_egress(args.limit)
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
        parser.add_argument('--no-text-fallback', action='store_true',
                            help='Disable always-available typed requests in an interactive voice terminal.')
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
        if not args.no_text_fallback and sys.stdin.isatty():
            try:
                asyncio.run(_hybrid_voice_async(use_fake_model=args.fake_model,
                    backend=args.model_backend, listen=args.listen, interaction=args.interaction))
            except KeyboardInterrupt:
                print('\nVoice session stopped.', file=sys.stderr)
            return
        if not args.no_text_fallback:
            print('[odessy] Keyboard fallback unavailable: stdin is not an interactive terminal.', file=sys.stderr)
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
