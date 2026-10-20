from pathlib import Path
from threading import Event, Thread

from odessy.voice.capture import CommandPlayer


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
