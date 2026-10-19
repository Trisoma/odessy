from __future__ import annotations

from pathlib import Path

import pytest

from odessy.agent_loop import ExecutionTarget, OdessyAgent
from odessy.fake_model import FakeModel
from odessy.mcp_server import build_odessy_mcp_server
from odessy.policy import OdessyPolicy
from odessy.voice.loop import VoiceLoop
from odessy.voice.stt import WhisperCppSTT, parse_whisper_transcript
from odessy.voice.tts import KokoroTTS


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def test_parse_whisper_transcript_extracts_timestamped_text() -> None:
    output = """
whisper_model_load: loading model
[00:00:00.000 --> 00:00:02.000]   calculate 12 * 8
[00:00:02.000 --> 00:00:03.000]   please
"""

    assert parse_whisper_transcript(output) == "calculate 12 * 8 please"


def test_parse_whisper_transcript_ignores_diagnostics_without_transcript() -> None:
    output = "whisper_print_timings: total time = 1000 ms"

    assert parse_whisper_transcript(output) == ""


def test_whisper_stt_reports_command_failure(tmp_path: Path) -> None:
    stt = WhisperCppSTT(
        whisper_bin="missing-whisper",
        model_path="models/tiny.bin",
        runner=lambda argv, timeout: _completed(argv, returncode=2, stderr="failed"),
    )

    result = stt.transcribe(tmp_path / "input.wav")

    assert result.status == "failed"
    assert result.text == ""
    assert "failed" in result.error


def test_kokoro_tts_failure_keeps_text_available(tmp_path: Path) -> None:
    tts = KokoroTTS(
        model_path="missing.onnx",
        voices_path="voices.bin",
        synthesizer=lambda text, output_path: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    result = tts.synthesize("hello", tmp_path / "reply.wav")

    assert result.status == "failed"
    assert result.output_path == tmp_path / "reply.wav"
    assert "boom" in result.error


@pytest.mark.anyio
async def test_voice_loop_cleans_temp_files_and_returns_text(tmp_path: Path) -> None:
    audio = FakeAudioIO()
    stt = FakeSTT("what time is it?")
    tts = FakeTTS()
    agent = OdessyAgent(
        model=FakeModel(),
        policy=OdessyPolicy(workspace_root=tmp_path),
        mcp_server=ExecutionTarget(
            build_odessy_mcp_server(
                workspace_root=tmp_path,
                clock=lambda: "2026-08-17T15:00:00+08:00",
            )
        ),
    )
    loop = VoiceLoop(agent=agent, audio_io=audio, stt=stt, tts=tts, tmp_dir=tmp_path)

    result = await loop.run_once()

    assert result.status == "ok"
    assert result.transcript == "what time is it?"
    assert result.final_text == "The current time is 2026-08-17T15:00:00+08:00."
    assert tts.spoken_text == result.final_text
    assert audio.played_path is not None
    assert not result.input_audio_path.exists()
    assert not result.output_audio_path.exists()


@pytest.mark.anyio
async def test_voice_loop_can_drive_existing_tool_path(tmp_path: Path) -> None:
    loop = VoiceLoop(
        agent=OdessyAgent(
            model=FakeModel(),
            policy=OdessyPolicy(workspace_root=tmp_path),
            mcp_server=ExecutionTarget(build_odessy_mcp_server(workspace_root=tmp_path)),
        ),
        audio_io=FakeAudioIO(),
        stt=FakeSTT("calculate 12 * 8"),
        tts=FakeTTS(),
        tmp_dir=tmp_path,
    )

    result = await loop.run_once()

    assert result.status == "ok"
    assert result.final_text == "12 * 8 = 96"


@pytest.mark.anyio
async def test_voice_loop_reports_progress_phases(tmp_path: Path) -> None:
    progress: list[str] = []
    loop = VoiceLoop(
        agent=OdessyAgent(
            model=FakeModel(),
            policy=OdessyPolicy(workspace_root=tmp_path),
            mcp_server=ExecutionTarget(build_odessy_mcp_server(workspace_root=tmp_path)),
        ),
        audio_io=FakeAudioIO(),
        stt=FakeSTT("calculate 12 * 8"),
        tts=FakeTTS(),
        tmp_dir=tmp_path,
        status_callback=progress.append,
    )

    result = await loop.run_once()

    assert result.status == "ok"
    assert progress == [
        "Recording started",
        "Recording stopped",
        "Transcribing speech",
        "Model is thinking",
        "Generating action proposal",
        "Validating action",
        "Running tool",
        "Generating response",
        "Synthesizing speech",
        "Playing response",
    ]


class FakeAudioIO:
    def __init__(self) -> None:
        self.played_path: Path | None = None

    def record(self, output_path: Path, seconds: int) -> None:
        output_path.write_bytes(b"fake wav")

    def play(self, audio_path: Path) -> None:
        self.played_path = audio_path


class FakeSTT:
    def __init__(self, text: str) -> None:
        self.text = text

    def transcribe(self, audio_path: Path):
        from odessy.voice.stt import STTResult

        return STTResult(text=self.text, status="ok", error=None)


class FakeTTS:
    def __init__(self) -> None:
        self.spoken_text = ""

    def synthesize(self, text: str, output_path: Path):
        from odessy.voice.tts import TTSResult

        self.spoken_text = text
        output_path.write_bytes(b"fake speech")
        return TTSResult(output_path=output_path, status="ok", error=None)


def _completed(argv, returncode: int = 0, stdout: str = "", stderr: str = ""):
    import subprocess

    return subprocess.CompletedProcess(argv, returncode, stdout=stdout, stderr=stderr)
