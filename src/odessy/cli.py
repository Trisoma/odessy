from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path
from typing import Any, cast

from odessy.agent_loop import ExecutionTarget, OdessyAgent
from odessy.cloud_model import CLOUD_PROVIDERS, CloudModel, CloudModelConfigError, build_cloud_model
from odessy.config_file import load_config_env
from odessy.execution.contract import ISOLATION_REQUIREMENTS, IsolationRequirement
from odessy.fake_model import FakeModel
from odessy.lfm_model import LlamaCppLFMModel
from odessy.mcp_server import build_odessy_mcp_server
from odessy.ollama_model import OllamaModel
from odessy.policy import OdessyPolicy
from odessy.types import PolicyDecision, ToolRequest
from odessy.voice.audio_io import CommandAudioIO
from odessy.voice.config import VoiceConfig
from odessy.voice.loop import VoiceLoop
from odessy.voice.stt import WhisperCppSTT
from odessy.voice.tts import KokoroTTS

DEFAULT_LFM_ENDPOINT = "http://127.0.0.1:11434"
DEFAULT_LFM_MODEL = "lfm2.5-thinking:1.2b-q8_0"
LOCAL_BACKENDS = ("lfm", "ollama", "llama_cpp")
MODEL_BACKENDS = (*LOCAL_BACKENDS, *CLOUD_PROVIDERS)

Model = FakeModel | OllamaModel | LlamaCppLFMModel | CloudModel


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
) -> OdessyAgent:
    return OdessyAgent(
        model=model,
        policy=OdessyPolicy(
            workspace_root=workspace_root,
            isolation=_isolation_from_env(),
        ),
        mcp_server=_build_mcp_servers(workspace_root),
        confirm=_confirm_tool_call,
    )


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
    print(f"Odessy needs confirmation to run {request.name}.")
    print(f"Risk: {decision.risk}")
    print(f"Reason: {decision.reason}")
    print(f"Arguments: {_summarize_arguments(request.arguments)}")
    answer = input("Allow this tool call? [y/N] ")
    return answer.strip().lower() in {"y", "yes"}


def _summarize_arguments(arguments: dict[str, Any]) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    for key, value in arguments.items():
        if key == "content" and isinstance(value, str):
            summary[key] = f"<{len(value.encode('utf-8'))} bytes>"
        else:
            summary[key] = value
    return summary


async def _text_async(user_text: str, use_fake_model: bool, backend: str | None = None) -> None:
    workspace_root = Path.cwd()
    model = _build_model(use_fake_model, backend=backend)
    _announce_cloud(model)
    agent = _build_agent(workspace_root, model)
    result = await agent.run_turn(user_text)
    print(result.final_text)


async def _voice_async(use_fake_model: bool, backend: str | None = None) -> None:
    config = VoiceConfig.from_env()
    workspace_root = Path.cwd()
    model = _build_model(use_fake_model, backend=backend, model_name=config.ollama_model)
    _announce_cloud(model)
    agent = _build_agent(workspace_root, model)
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
    )
    result = await loop.run_once()
    if result.transcript:
        print(f"Transcript: {result.transcript}")
    if result.final_text:
        print(f"Odessy: {result.final_text}")
    if result.status != "ok":
        raise SystemExit(f"Voice turn failed with status {result.status}: {result.error}")


def _announce_cloud(model: object) -> None:
    """Say plainly, once per run, that this turn leaves the machine."""
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


def _build_mcp_servers(workspace_root: Path) -> tuple[object, ...] | object:
    # Wrapped in `ExecutionTarget` because this IS the authorization act: the composition
    # root names the servers it means to execute on. A server that is merely reachable --
    # discovered, or supplied by a future dynamic source -- is not executable, so trust is
    # never inherited from construction.
    servers: list[object] = [
        ExecutionTarget(build_odessy_mcp_server(workspace_root=workspace_root))
    ]
    extra_urls = os.environ.get("ODESSY_MCP_SERVER_URLS") or os.environ.get("ODESSY_TOOL_SERVER_URL")
    if extra_urls:
        for raw_url in extra_urls.replace(";", ",").split(","):
            url = raw_url.strip()
            if url:
                servers.append(ExecutionTarget(url))
    return tuple(servers) if len(servers) > 1 else servers[0]


def main(argv: list[str] | None = None) -> None:
    load_config_env()
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["voice"]:
        parser = argparse.ArgumentParser(description="Run one bounded local Odessy voice turn.")
        parser.add_argument(
            "--fake-model",
            action="store_true",
            help="Use the deterministic fake model instead of Ollama/Gemma.",
        )
        _model_backend_argument(parser)
        args = parser.parse_args(argv[1:])
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
