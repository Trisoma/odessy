from pathlib import Path
from threading import Event, Thread
import pytest

from odessy.voice.capture import AudioStreamError, CommandPlayer, WindowsNativePlayer


class PlaybackProcess:
    def __init__(self):
        self.terminated = False

    def terminate(self):
        self.terminated = True

    def wait(self, timeout=None):
        return 0

    def kill(self):
        self.terminated = True


def test_stop_during_process_start_terminates_late_process(monkeypatch):
    starting, release = Event(), Event()
    process = PlaybackProcess()

    def popen(*args, **kwargs):
        starting.set()
        assert release.wait(2)
        return process

    monkeypatch.setattr('odessy.voice.capture.subprocess.Popen', popen)
    player = CommandPlayer()
    worker = Thread(target=player.play, args=(Path('reply.wav'),))
    worker.start()
    try:
        assert starting.wait(2)
        player.stop()
    finally:
        release.set()
        worker.join(2)
    assert not worker.is_alive()
    assert process.terminated


def test_cancelled_queued_playback_cannot_replace_wake_cue(monkeypatch):
    player = CommandPlayer()
    generation = player.generation
    player.stop()
    calls = []

    def popen(argv, **kwargs):
        calls.append(argv[-1])
        return PlaybackProcess()

    monkeypatch.setattr('odessy.voice.capture.subprocess.Popen', popen)
    player.play(Path('cue.wav'), generation=player.generation)
    player.play(Path('stale.wav'), generation=generation)
    assert calls == ['cue.wav']


def test_newer_playback_owns_process_when_old_start_finishes(monkeypatch):
    starting, release = Event(), Event()
    old, cue = PlaybackProcess(), PlaybackProcess()

    def popen(argv, **kwargs):
        if argv[-1] == 'old.wav':
            starting.set()
            assert release.wait(2)
            return old
        return cue

    monkeypatch.setattr('odessy.voice.capture.subprocess.Popen', popen)
    player = CommandPlayer()
    generation = player.generation
    worker = Thread(
        target=player.play, args=(Path('old.wav'),), kwargs={'generation': generation}
    )
    worker.start()
    try:
        assert starting.wait(2)
        player.play(Path('cue.wav'), generation=generation)
    finally:
        release.set()
        worker.join(2)
    assert not worker.is_alive()
    assert old.terminated
    assert not cue.terminated


def test_native_player_uses_owned_python_process_and_reports_failure(monkeypatch):
    import sys
    calls = []
    class FailedProcess(PlaybackProcess):
        def wait(self, timeout=None):
            return 1
    def popen(argv, **kwargs):
        calls.append(argv)
        return FailedProcess()
    monkeypatch.setattr('odessy.voice.capture.subprocess.Popen', popen)
    with pytest.raises(AudioStreamError, match='playback failed'):
        WindowsNativePlayer().play(Path('reply.wav'))
    assert calls == [[sys.executable, '-m', 'odessy.voice.windows_playback', 'reply.wav']]


def test_native_playback_uses_default_device_and_propagates_errors(monkeypatch):
    import sys
    from types import SimpleNamespace
    from odessy.voice import windows_playback
    calls = []
    def play(path, flags):
        calls.append((path, flags))
    monkeypatch.setattr(sys, 'argv', ['windows_playback', 'reply.wav'])
    monkeypatch.setitem(sys.modules, 'winsound', SimpleNamespace(
        PlaySound=play, SND_FILENAME=1, SND_NODEFAULT=2))
    monkeypatch.setattr(sys, 'platform', 'win32')
    assert windows_playback.main() == 0
    assert calls == [('reply.wav', 3)]
    def fail(*args):
        raise RuntimeError('device unavailable')
    monkeypatch.setitem(sys.modules, 'winsound', SimpleNamespace(
        PlaySound=fail, SND_FILENAME=1, SND_NODEFAULT=2))
    assert windows_playback.main() == 1


def test_windows_doctor_does_not_claim_native_output_was_tested(monkeypatch):
    from types import SimpleNamespace
    from odessy.config import doctor
    monkeypatch.setattr(doctor.sys, 'platform', 'win32')
    probes = SimpleNamespace(which=lambda _: None, path_exists=lambda _: True)
    checks = doctor._voice_checks({}, probes)
    assert any('native Windows default audio output has not been tested' in c.summary for c in checks)
    assert not any('audio program not found: aplay' in c.summary for c in checks)
