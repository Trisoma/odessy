"""External Piper contract: real child lifecycle, atomic WAV and passive discovery."""
import json
import sys
import threading
import time
import wave
from pathlib import Path

import pytest

from odessy.voice.config import VoiceConfig
from odessy.voice.providers import create_tts


def config(tmp_path):
    model = tmp_path / 'voice.onnx'
    model.write_bytes(b'model')
    Path(str(model) + '.json').write_text(json.dumps({'sample_rate': 22050}))
    return VoiceConfig('', '', '', '', tts_provider='piper',
                       tts_executable=sys.executable, tts_model=str(model))


def test_external_piper_creates_valid_wav_without_shell(tmp_path, monkeypatch):
    provider = create_tts(config(tmp_path))
    script = ("import sys,wave; text=sys.stdin.buffer.read().decode('utf-8'); "
              "assert text=='Hello; $literal\\n'; "
              "w=wave.open(sys.argv[1],'wb'); w.setparams((1,2,22050,0,'NONE','')); "
              "w.writeframes(b'\\x00\\x00'*2205); w.close()")
    monkeypatch.setattr(provider, '_command', lambda model, output:
                        [sys.executable, '-c', script, str(output)])
    path = tmp_path / 'result.wav'
    assert provider.synthesize('Hello; $literal', path).status == 'ok'
    with wave.open(str(path)) as wav:
        assert wav.getframerate() == 22050 and wav.getnframes() == 2205
    provider.close()
    assert provider.synthesize('closed', path).status == 'failed'


def test_python_piper_receives_unicode_text_under_a_windows_legacy_locale(tmp_path, monkeypatch):
    """Keep the real interpreter flags and pipe; replace only the external model CLI."""
    from odessy.voice import piper
    real_popen = piper.subprocess.Popen
    script = (
        "import sys,wave; "
        "sys.stdin.reconfigure(encoding='utf-8' if sys.flags.utf8_mode else 'cp1252'); "
        "assert sys.stdin.read() == 'You\\u2019re ready \\u2014 caf\\u00e9.\\n'; "
        "w=wave.open(sys.argv[-1],'wb'); w.setparams((1,2,22050,0,'NONE','')); "
        "w.writeframes(b'\\x00\\x00'*2205); w.close()"
    )

    def launch(command, **kwargs):
        module = command.index('-m')
        assert command[module + 1] == 'piper'
        return real_popen(command[:module] + ['-c', script] + command[module + 2:], **kwargs)

    monkeypatch.setattr(piper.subprocess, 'Popen', launch)
    provider = create_tts(config(tmp_path))
    try:
        result = provider.synthesize('You\u2019re ready \u2014 caf\u00e9.', tmp_path / 'unicode.wav')
        assert result.status == 'ok', result.error
    finally:
        provider.close()


@pytest.mark.parametrize('kind', ['missing_config', 'invalid_config', 'failed_child', 'invalid_wav'])
def test_piper_failure_preserves_previous_output(tmp_path, monkeypatch, kind):
    cfg = config(tmp_path)
    sidecar = Path(cfg.tts_model + '.json')
    if kind == 'missing_config':
        sidecar.unlink()
    elif kind == 'invalid_config':
        sidecar.write_text('{}')
    provider = create_tts(cfg)
    code = 'raise SystemExit(2)' if kind == 'failed_child' else "import pathlib,sys; pathlib.Path(sys.argv[1]).write_bytes(b'bad')"
    monkeypatch.setattr(provider, '_command', lambda model, output:
                        [sys.executable, '-c', code, str(output)])
    path = tmp_path / 'previous.wav'
    path.write_bytes(b'keep')
    assert provider.synthesize('hello', path).status == 'failed'
    assert path.read_bytes() == b'keep'


def test_cancel_terminates_owned_process_and_late_output_is_not_published(tmp_path, monkeypatch):
    provider = create_tts(config(tmp_path))
    ready = tmp_path / 'ready'
    code = "import pathlib,sys,time; pathlib.Path(sys.argv[1]).touch(); time.sleep(30)"
    monkeypatch.setattr(provider, '_command', lambda model, output:
                        [sys.executable, '-c', code, str(ready)])
    results = []
    path = tmp_path / 'cancelled.wav'
    thread = threading.Thread(target=lambda: results.append(provider.synthesize('hello', path)))
    thread.start()
    deadline = time.monotonic() + 5
    while not ready.exists() and time.monotonic() < deadline:
        time.sleep(.02)
    assert ready.exists()
    provider.cancel()
    thread.join(5)
    assert not thread.is_alive() and results[0].status == 'failed'
    assert not path.exists()
    provider.close()


def test_detection_finds_piper_pair_and_keeps_it_out_of_wake_models(tmp_path, monkeypatch):
    from odessy.setup import voice_assets
    root = tmp_path / 'voice'
    model = root / 'piper' / 'en_US-voice-medium.onnx'
    model.parent.mkdir(parents=True)
    model.write_bytes(b'model')
    Path(str(model) + '.json').write_text('{"sample_rate":22050}')
    executable = root / 'piper/runtime/python.exe'
    executable.parent.mkdir()
    executable.touch()
    monkeypatch.setenv('ODESSY_VOICE_ROOT', str(root))
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    result = voice_assets.discover_voice_assets({'voice': {'tts_provider': 'piper'}})
    assert str(model.resolve()) in result['candidates']['tts_model']
    assert str(executable.resolve()) in result['candidates']['tts_executable']
    assert str(model.resolve()) not in result['candidates']['wake_model']
    assert 'kokoro_model' not in result['missing']


def test_piper_timeout_drains_child_and_preserves_existing_file(tmp_path, monkeypatch):
    provider = create_tts(config(tmp_path))
    provider.timeout = .1
    monkeypatch.setattr(provider, '_command', lambda model, output:
                        [sys.executable, '-c', 'import time; time.sleep(30)'])
    output = tmp_path / 'previous.wav'
    output.write_bytes(b'keep')
    assert provider.synthesize('hello', output).status == 'failed'
    assert provider._process is None
    assert output.read_bytes() == b'keep'
