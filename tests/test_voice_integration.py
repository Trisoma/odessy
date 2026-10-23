from __future__ import annotations

from pathlib import Path

import pytest

from odessy.runtime.agent_loop import ExecutionTarget, OdessyAgent
from odessy.models.fake_model import FakeModel
from odessy.tools.mcp_server import build_odessy_mcp_server
from odessy.authority.policy import OdessyPolicy
from odessy.voice.loop import VoiceLoop
from odessy.voice.stt import WhisperCppSTT, blank_transcript_reason, parse_whisper_transcript
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


@pytest.mark.parametrize(
    "text",
    ["[BLANK_AUDIO]", "[BLANK_AUDIO].", "(silence)", "[ Silence ]", "[MUSIC]",
     "[inaudible]", "[BLANK_AUDIO] (silence)", "...", "   "],
)
def test_a_silence_marker_is_not_a_request(text: str) -> None:
    """Whisper returns a marker for silence, not an empty string.

    ``[BLANK_AUDIO]`` is non-empty text, so a status check alone passed it to the
    model, which then proposed a tool for a request nobody made.
    """

    assert blank_transcript_reason(text) is not None


@pytest.mark.parametrize(
    "text",
    [
        "what time is it?",
        "[inaudible] hello",
        "calculate 12 * 8",
        "silence is golden",
        "music please",
        "(turn off the light)",
        "[restart the server]",
        "[BLANK_AUDIO] [restart the server]",
        "[silence)",
    ],
)
def test_real_speech_is_never_treated_as_blank(text: str) -> None:
    """Only annotation-only or punctuation-only text is blank.

    Any word outside the annotation means the recording contained speech, so a
    transcript that merely *starts* with a marker must still reach the model.
    """

    assert blank_transcript_reason(text) is None


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


@pytest.mark.anyio
async def test_a_blank_recording_never_reaches_the_model(tmp_path: Path) -> None:
    """No speech means no request: no model call, no tool call, no authorization.

    The assertion is on the agent because that is the boundary that matters. A
    turn that returns ``blank`` must not have entered the loop at all.
    """

    loop = VoiceLoop(
        agent=_ExplodingAgent(),
        audio_io=FakeAudioIO(),
        stt=FakeSTT("[BLANK_AUDIO]"),
        tts=FakeTTS(),
        tmp_dir=tmp_path,
    )

    result = await loop.run_once()

    assert result.status == "blank"
    assert result.transcript == "[BLANK_AUDIO]"
    assert result.final_text == ""
    assert "non-speech annotation" in (result.error or "")


@pytest.mark.anyio
@pytest.mark.parametrize("empty_status", ["ok", "empty"])
async def test_empty_transcript_is_blank_without_calling_agent(
    tmp_path: Path, empty_status: str,
) -> None:
    from odessy.voice.stt import STTResult

    class EmptySTT:
        def transcribe(self, audio_path: Path) -> STTResult:
            return STTResult("", empty_status, "no transcript")

    result = await VoiceLoop(
        agent=_ExplodingAgent(), audio_io=FakeAudioIO(), stt=EmptySTT(),
        tts=FakeTTS(), tmp_dir=tmp_path,
    ).run_once()

    assert result.status == "blank"
    assert result.transcript == ""
    assert not result.input_audio_path.exists()


@pytest.mark.anyio
async def test_stt_failure_is_not_reclassified_as_blank(tmp_path: Path) -> None:
    from odessy.voice.stt import STTResult

    class FailedSTT:
        def transcribe(self, audio_path: Path) -> STTResult:
            return STTResult("", "failed", "recorder unavailable")

    result = await VoiceLoop(
        agent=_ExplodingAgent(), audio_io=FakeAudioIO(), stt=FailedSTT(),
        tts=FakeTTS(), tmp_dir=tmp_path,
    ).run_once()

    assert result.status == "failed"
    assert result.error == "recorder unavailable"


@pytest.mark.anyio
@pytest.mark.parametrize("transcripts", [
    ["[BLANK_AUDIO]", "(silence)", "calculate 12 * 8"],
    ["", "[BLANK_AUDIO]", "calculate 12 * 8"],
])
async def test_a_blank_recording_is_retried_without_a_second_model_call(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    transcripts: list[str],
) -> None:
    """The retry re-records; it never re-runs the previous request."""

    from odessy import cli as odessy_cli
    config, audio, tts = _voice_cli_components(monkeypatch, tmp_path, transcripts, 2)

    class CountingModel(FakeModel):
        requests: list[str] = []

        def propose(self, user_text: str):
            self.requests.append(user_text)
            return super().propose(user_text)

    model = CountingModel()
    agent = OdessyAgent(
        model=model, policy=OdessyPolicy(workspace_root=tmp_path),
        mcp_server=ExecutionTarget(build_odessy_mcp_server(workspace_root=tmp_path)),
    )

    await odessy_cli._run_voice_turn(agent, config)

    assert len(audio.recorded_paths) == 3
    assert len(set(audio.recorded_paths)) == 3
    assert all(not path.exists() for path in audio.recorded_paths)
    assert model.requests == ["calculate 12 * 8"]
    assert tts.spoken_text == "12 * 8 = 96"


@pytest.mark.anyio
@pytest.mark.parametrize("blank_retries,transcript", [
    (0, ""), (0, "[BLANK_AUDIO]"), (1, ""), (1, "[BLANK_AUDIO]"),
])
async def test_blank_retries_are_exhausted_with_a_clear_exit(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    blank_retries: int,
    transcript: str,
) -> None:
    from odessy import cli as odessy_cli
    attempts = blank_retries + 1
    config, audio, tts = _voice_cli_components(
        monkeypatch, tmp_path, [transcript] * attempts, blank_retries,
    )

    with pytest.raises(SystemExit) as raised:
        await odessy_cli._run_voice_turn(_ExplodingAgent(), config)  # type: ignore[arg-type]

    assert len(audio.recorded_paths) == attempts
    assert tts.spoken_text == ""
    assert audio.played_path is None
    assert "no usable speech" in str(raised.value).lower()
    assert "nothing was sent to the model" in str(raised.value).lower()


class _ExplodingAgent:
    """Fails the test if anything asks it to run a turn."""

    async def run_turn(self, *args: object, **kwargs: object) -> object:
        raise AssertionError("a blank transcript must never reach the model")


class FakeAudioIO:
    def __init__(self) -> None:
        self.played_path: Path | None = None
        self.recorded_paths: list[Path] = []

    def record(self, output_path: Path, seconds: int) -> None:
        self.recorded_paths.append(output_path)
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


def _voice_cli_components(monkeypatch, tmp_path, transcripts, blank_retries):
    """Keep the real CLI, voice loop, and Whisper parser; replace hardware I/O."""
    from odessy import cli
    from odessy.voice.config import VoiceConfig

    audio, tts = FakeAudioIO(), FakeTTS()
    remaining = iter(transcripts)

    def transcribe(argv, timeout):
        text = next(remaining)
        return _completed(argv, stdout=f"[00:00:00.000 --> 00:00:01.000] {text}" if text else "")

    stt = WhisperCppSTT("whisper-cli", "tiny.bin", runner=transcribe)
    monkeypatch.setattr(cli, "CommandAudioIO", lambda **kwargs: audio)
    monkeypatch.setattr(cli, "WhisperCppSTT", lambda **kwargs: stt)
    monkeypatch.setattr(cli, "KokoroTTS", lambda **kwargs: tts)
    config = VoiceConfig("whisper-cli", "tiny.bin", "kokoro.onnx", "voices.bin",
                         blank_retries=blank_retries, tmp_dir=tmp_path)
    return config, audio, tts


def _completed(argv, returncode: int = 0, stdout: str = "", stderr: str = ""):
    import subprocess

    return subprocess.CompletedProcess(argv, returncode, stdout=stdout, stderr=stderr)
