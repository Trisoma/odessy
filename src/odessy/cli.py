from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from odessy.agent_loop import OdessyAgent
from odessy.fake_model import FakeModel
from odessy.mcp_server import build_odessy_mcp_server
from odessy.ollama_model import OllamaModel
from odessy.policy import OdessyPolicy
from odessy.voice.audio_io import CommandAudioIO
from odessy.voice.config import VoiceConfig
from odessy.voice.loop import VoiceLoop
from odessy.voice.stt import WhisperCppSTT
from odessy.voice.tts import KokoroTTS


async def _main_async(user_text: str) -> None:
    agent = OdessyAgent(
        model=FakeModel(),
        policy=OdessyPolicy(workspace_root=Path.cwd()),
        mcp_server=build_odessy_mcp_server(workspace_root=Path.cwd()),
    )
    result = await agent.run_turn(user_text)
    print(result.final_text)


async def _voice_async(use_fake_model: bool) -> None:
    config = VoiceConfig.from_env()
    model = FakeModel() if use_fake_model else OllamaModel(model=config.ollama_model)
    agent = OdessyAgent(
        model=model,
        policy=OdessyPolicy(workspace_root=Path.cwd()),
        mcp_server=build_odessy_mcp_server(workspace_root=Path.cwd()),
    )
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
    parser.add_argument("text", help="User text to send into the Odessy loop.")
    args = parser.parse_args(argv)
    if not args.text:
        parser.error("text is required unless using the voice subcommand")
    asyncio.run(_main_async(args.text))


if __name__ == "__main__":
    main()
