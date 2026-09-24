"""Kokoro session reuse.

Regression tests for the defect where ``build_kokoro`` was called on every
``synthesize()``, constructing a fresh ``ort.InferenceSession`` each time. It was
invisible in production (one bounded turn per process) and obvious in the
benchmark, where RSS climbed 56.8 MB -> ~1.44 GB -> ~1.89 GB across iterations.

These tests assert call *counts*, not timing: the property under test is "the
session is built once", which is measurable without a model or a GPU.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

from odessy.voice import tts as tts_module
from odessy.voice.tts import KokoroTTS, build_kokoro_cached, reset_kokoro_cache


class _FakeKokoro:
    def __init__(self, tag: str) -> None:
        self.tag = tag
        self.create_calls = 0

    def create(self, text: str, *, voice: str, speed: float, lang: str):
        self.create_calls += 1
        return [0.0, 0.1], 24000


class _BuildCounter:
    """Stands in for ``build_kokoro`` and counts how often it is called."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str, str | None]] = []

    def __call__(self, model_path, voices_path, *, provider="auto", cache_dir=None):
        self.calls.append((model_path, voices_path, provider, str(cache_dir)))
        return _FakeKokoro(f"{model_path}:{provider}")


@pytest.fixture(autouse=True)
def _clear_cache():
    reset_kokoro_cache()
    yield
    reset_kokoro_cache()


@pytest.fixture
def build_counter(monkeypatch: pytest.MonkeyPatch) -> _BuildCounter:
    counter = _BuildCounter()
    monkeypatch.setattr(tts_module, "build_kokoro", counter)
    return counter


def test_repeated_calls_build_once(build_counter: _BuildCounter) -> None:
    first = build_kokoro_cached("k.onnx", "v.bin")
    second = build_kokoro_cached("k.onnx", "v.bin")

    assert first is second
    assert len(build_counter.calls) == 1


@pytest.mark.parametrize(
    "changed",
    [
        {"provider": "cpu"},
        {"cache_dir": Path("/tmp/ort-cache")},
        {"model_path": "other.onnx"},
        {"voices_path": "other.bin"},
    ],
)
def test_cache_miss_when_a_key_component_changes(
    build_counter: _BuildCounter, changed: dict
) -> None:
    """Anything that changes the built object must not reuse the old one."""

    build_kokoro_cached("k.onnx", "v.bin")
    kwargs = {"model_path": "k.onnx", "voices_path": "v.bin"}
    kwargs.update(changed)

    build_kokoro_cached(**kwargs)

    assert len(build_counter.calls) == 2


def test_reset_clears_the_cache(build_counter: _BuildCounter) -> None:
    build_kokoro_cached("k.onnx", "v.bin")
    reset_kokoro_cache()
    build_kokoro_cached("k.onnx", "v.bin")

    assert len(build_counter.calls) == 2


def test_synthesize_reuses_one_session_across_calls(
    monkeypatch: pytest.MonkeyPatch, build_counter: _BuildCounter, tmp_path: Path
) -> None:
    """The real regression: two ``synthesize()`` calls, one session build."""

    writes: list[Path] = []
    fake_soundfile = types.SimpleNamespace(
        write=lambda path, samples, rate: writes.append(Path(path))
    )
    monkeypatch.setitem(sys.modules, "soundfile", fake_soundfile)

    engine = KokoroTTS(model_path="k.onnx", voices_path="v.bin", provider="cpu")

    first = engine.synthesize("one", tmp_path / "a.wav")
    second = engine.synthesize("two", tmp_path / "b.wav")

    assert first.status == "ok"
    assert second.status == "ok"
    assert len(build_counter.calls) == 1, "the session must not be rebuilt per call"
    assert writes == [tmp_path / "a.wav", tmp_path / "b.wav"]


def test_synthesize_reuses_one_session_across_instances(
    monkeypatch: pytest.MonkeyPatch, build_counter: _BuildCounter, tmp_path: Path
) -> None:
    """The cache is process-wide, because the dataclass is frozen and may be rebuilt."""

    fake_soundfile = types.SimpleNamespace(write=lambda *args: None)
    monkeypatch.setitem(sys.modules, "soundfile", fake_soundfile)

    for index in range(3):
        engine = KokoroTTS(model_path="k.onnx", voices_path="v.bin", provider="cpu")
        assert engine.synthesize("hi", tmp_path / f"{index}.wav").status == "ok"

    assert len(build_counter.calls) == 1


def test_injected_synthesizer_bypasses_the_cache(
    monkeypatch: pytest.MonkeyPatch, build_counter: _BuildCounter, tmp_path: Path
) -> None:
    """An injected synthesizer must not cause a model build at all."""

    engine = KokoroTTS(
        model_path="k.onnx",
        voices_path="v.bin",
        synthesizer=lambda text, path: path.write_bytes(b"fake"),
    )

    assert engine.synthesize("hi", tmp_path / "c.wav").status == "ok"
    assert build_counter.calls == []


def test_a_failed_build_is_not_cached(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A build failure must not poison the cache for the next attempt."""

    attempts = {"count": 0}

    def flaky(model_path, voices_path, *, provider="auto", cache_dir=None):
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise RuntimeError("provider unavailable")
        return _FakeKokoro("recovered")

    monkeypatch.setattr(tts_module, "build_kokoro", flaky)

    with pytest.raises(RuntimeError):
        build_kokoro_cached("k.onnx", "v.bin")

    assert build_kokoro_cached("k.onnx", "v.bin").tag == "recovered"
    assert attempts["count"] == 2
