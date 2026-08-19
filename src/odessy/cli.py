from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path
from typing import Any

from odessy.agent_loop import OdessyAgent
from odessy.fake_model import FakeModel
from odessy.mcp_server import build_odessy_mcp_server
from odessy.ollama_model import OllamaModel
from odessy.policy import OdessyPolicy
from odessy.types import PolicyDecision, ToolRequest
from odessy.voice.audio_io import CommandAudioIO
from odessy.voice.config import VoiceConfig
from odessy.voice.loop import VoiceLoop
from odessy.voice.stt import WhisperCppSTT
from odessy.voice.tts import KokoroTTS


def _build_model(use_fake_model: bool, *, ollama_model: str) -> FakeModel | OllamaModel:
    if use_fake_model:
        return FakeModel()
    return OllamaModel(model=ollama_model)


def _build_agent(workspace_root: Path, model: FakeModel | OllamaModel) -> OdessyAgent:
    return OdessyAgent(
        model=model,
        policy=OdessyPolicy(workspace_root=workspace_root),
        mcp_server=_build_mcp_servers(workspace_root),
        confirm=_confirm_tool_call,
    )


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


async def _text_async(user_text: str, use_fake_model: bool) -> None:
    workspace_root = Path.cwd()
    model = _build_model(
        use_fake_model,
        ollama_model=os.environ.get("ODESSY_OLLAMA_MODEL", "gemma3:1b"),
    )
    agent = _build_agent(workspace_root, model)
    result = await agent.run_turn(user_text)
    print(result.final_text)


async def _voice_async(use_fake_model: bool) -> None:
    config = VoiceConfig.from_env()
    workspace_root = Path.cwd()
    model = _build_model(use_fake_model, ollama_model=config.ollama_model)
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
        ),
        tmp_dir=config.tmp_dir,
        seconds=config.voice_seconds,
    )
    result = await loop.run_once()
    if result.transcript:
        print(f"Transcript: {result.transcript}")
    if result.final_text:
        print(f"Odessy: {result.final_text}")
    if result.status != "ok":
        raise SystemExit(f"Voice turn failed with status {result.status}: {result.error}")


def _build_mcp_servers(workspace_root: Path) -> tuple[object, ...] | object:
    servers: list[object] = [build_odessy_mcp_server(workspace_root=workspace_root)]
    extra_urls = os.environ.get("ODESSY_MCP_SERVER_URLS") or os.environ.get("ODESSY_TOOL_SERVER_URL")
    if extra_urls:
        for raw_url in extra_urls.replace(";", ",").split(","):
            url = raw_url.strip()
            if url:
                servers.append(url)
    return tuple(servers) if len(servers) > 1 else servers[0]


def main(argv: list[str] | None = None) -> None:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["voice"]:
        parser = argparse.ArgumentParser(description="Run one bounded local Odessy voice turn.")
        parser.add_argument(
            "--fake-model",
            action="store_true",
            help="Use the deterministic fake model instead of Ollama/Gemma.",
        )
        args = parser.parse_args(argv[1:])
        asyncio.run(_voice_async(use_fake_model=args.fake_model))
        return
    parser = argparse.ArgumentParser(description="Run the Odessy text loop.")
    parser.add_argument(
        "--fake-model",
        action="store_true",
        help="Use the deterministic fake model instead of Ollama/Gemma.",
    )
    parser.add_argument("text", help="User text to send into the Odessy loop.")
    args = parser.parse_args(argv)
    if not args.text:
        parser.error("text is required unless using the voice subcommand")
    asyncio.run(_text_async(args.text, use_fake_model=args.fake_model))


if __name__ == "__main__":
    main()
