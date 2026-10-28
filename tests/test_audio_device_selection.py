import subprocess
from types import SimpleNamespace


def test_linux_inventory_reads_alsa_names_without_opening_audio(monkeypatch):
    from odessy.voice import native_audio
    calls=[]
    def run(argv, **kwargs):
        calls.append(argv)
        return SimpleNamespace(returncode=0, stdout='default\n    OS default\npulse\n    PulseAudio sound server\n')
    monkeypatch.setattr(subprocess, 'run', run)
    result=native_audio.inventory('command')
    assert any(d['id']=='pulse' and d['input'] and d['output'] for d in result['devices'])
    assert calls == [['arecord','-L'],['aplay','-L']]


def test_whisper_expands_home_paths_before_execution(tmp_path, monkeypatch):
    from pathlib import Path
    from odessy.voice.stt import WhisperCppSTT
    monkeypatch.setattr(Path, 'home', lambda:tmp_path)
    monkeypatch.setenv('HOME',str(tmp_path));monkeypatch.setenv('USERPROFILE',str(tmp_path))
    calls=[]
    def run(argv, timeout):
        calls.append(argv)
        return SimpleNamespace(returncode=0, stdout='[00:00:00.000 --> 00:00:01.000] hello',stderr='')
    result=WhisperCppSTT('~/whisper/whisper-cli','~/models/model.bin',runner=run).transcribe(tmp_path/'input.wav')
    assert calls[0][0]==str(tmp_path/'whisper/whisper-cli')
    assert calls[0][2]==str(tmp_path/'models/model.bin')
    assert result.text=='hello'


def test_stt_capture_failure_reports_safe_stage(monkeypatch):
    import pytest
    from odessy.setup import diagnostics
    from odessy.setup.contracts import SetupSelection
    from odessy.voice import native_audio
    class Broken:
        def start(self):raise RuntimeError('raw-private-detail')
        def close(self):pass
    monkeypatch.setattr(native_audio,'recorder',lambda _:Broken())
    with pytest.raises(diagnostics.ProviderTestError,match='microphone capture') as failure:
        diagnostics.probe(SetupSelection(role='combined'),'stt',run=True,consent=True)
    assert 'raw-private-detail' not in str(failure.value)
