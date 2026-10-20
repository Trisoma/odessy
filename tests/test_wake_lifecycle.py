from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
import odessy.cli as cli
from odessy.voice.session import pcm_to_wav
from odessy.voice.stt import STTResult


def setup_cli(monkeypatch, tmp_path, *, failure=None):
    cfg = SimpleNamespace(ollama_model='fake', whisper_bin='', whisper_model='',
        kokoro_model='', kokoro_voices='', onnx_provider='auto', onnx_cache_dir=None,
        audio_player='aplay', audio_output_device=None, audio_recorder='arecord',
        audio_input_device=None, tmp_dir=tmp_path, wake_model=Path('fake.onnx'),
        wake_threshold=.5, max_utterance_seconds=20, interaction='wake',
        blank_retries=2, followup_seconds=10)
    stream = MagicMock()
    stream.start.return_value = stream
    monkeypatch.setattr(cli.VoiceConfig, 'from_env', lambda: cfg)
    monkeypatch.setattr(cli, '_build_model', lambda *a, **kw: object())
    monkeypatch.setattr(cli, 'WhisperCppSTT', lambda **kw: SimpleNamespace(
        transcribe=lambda path: STTResult('', 'empty', 'no transcript')))
    monkeypatch.setattr(cli, 'KokoroTTS', lambda **kw: object())
    monkeypatch.setattr(cli, 'CommandPlayer', lambda **kw: MagicMock())
    monkeypatch.setattr(cli, 'CommandAudioStream', lambda **kw: stream)
    monkeypatch.setattr(cli, 'OpenWakeWordDetector', lambda *a, **kw: object())
    monkeypatch.setattr(cli, 'SileroSpeechDetector', lambda: SimpleNamespace(frame_bytes=1024))
    @asynccontextmanager
    async def servers(*a, **kw):
        if failure == 'server':
            raise RuntimeError('server startup failed')
        yield ()
    monkeypatch.setattr(cli, '_tool_servers', servers)
    monkeypatch.setattr(cli, '_build_agent', lambda *a: object())
    if failure == 'detector':
        def detector(*a, **kw):
            raise RuntimeError('detector startup failed')
        monkeypatch.setattr(cli, 'OpenWakeWordDetector', detector)
    return stream


@pytest.mark.anyio
@pytest.mark.parametrize('failure', ['server', 'detector'])
async def test_listen_startup_failure_closes_capture(monkeypatch, tmp_path, failure):
    stream = setup_cli(monkeypatch, tmp_path, failure=failure)
    with pytest.raises(RuntimeError, match='startup failed'):
        await cli._listen_async(False)
    stream.close.assert_called()


@pytest.mark.anyio
async def test_actual_empty_whisper_result_uses_blank_retry(monkeypatch, tmp_path):
    setup_cli(monkeypatch, tmp_path)
    from test_wake_session import Harness
    observed = []
    async def run(self):
        # Keep the actual CLI transcription callback and exercise the real controller.
        session = Harness([]).build()
        session.transcribe = self.transcribe
        await session._finish_capture()
        await session._transcription
        observed.append((session.state, session._blank_attempts))
    monkeypatch.setattr(cli.VoiceSession, 'run', run)
    await cli._listen_async(False)
    assert observed == [('RECORDING', 1)]
