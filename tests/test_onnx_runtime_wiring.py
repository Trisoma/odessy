from __future__ import annotations

import importlib
import sys
import types
from pathlib import Path


def _install_fake_onnx_modules(monkeypatch):
    state: dict[str, object] = {}

    class FakeSession:
        def __init__(self, model_path, providers):
            state["model_path"] = model_path
            state["providers"] = providers

    class FakeKokoro:
        @classmethod
        def from_session(cls, session, voices_path):
            state["session"] = session
            state["voices_path"] = voices_path
            return cls()

    onnxruntime = types.ModuleType("onnxruntime")
    onnxruntime.get_available_providers = lambda: [
        "TensorrtExecutionProvider",
        "CUDAExecutionProvider",
        "CPUExecutionProvider",
    ]
    onnxruntime.InferenceSession = FakeSession

    kokoro_onnx = types.ModuleType("kokoro_onnx")
    kokoro_onnx.Kokoro = FakeKokoro

    soundfile = types.ModuleType("soundfile")
    soundfile.write = lambda output_path, samples, sample_rate: None

    monkeypatch.setitem(sys.modules, "onnxruntime", onnxruntime)
    monkeypatch.setitem(sys.modules, "kokoro_onnx", kokoro_onnx)
    monkeypatch.setitem(sys.modules, "soundfile", soundfile)
    sys.modules.pop("odessy.voice.onnx_runtime", None)
    sys.modules.pop("odessy.voice.tts", None)
    return state


def test_build_kokoro_uses_tensorrt_with_fallbacks(monkeypatch, tmp_path: Path) -> None:
    state = _install_fake_onnx_modules(monkeypatch)
    runtime = importlib.import_module("odessy.voice.onnx_runtime")

    result = runtime.build_kokoro(
        "model.onnx",
        "voices.bin",
        provider="tensorrt",
        cache_dir=tmp_path / "ort-cache",
    )

    assert result is not None
    assert state["model_path"] == "model.onnx"
    assert state["voices_path"] == "voices.bin"
    assert state["session"] is not None
    assert (tmp_path / "ort-cache").is_dir()
    assert state["providers"][0][0] == "TensorrtExecutionProvider"
    assert state["providers"][1:] == [
        "CUDAExecutionProvider",
        "CPUExecutionProvider",
    ]


def test_kokoro_tts_forwards_accelerator_settings(monkeypatch, tmp_path: Path) -> None:
    _install_fake_onnx_modules(monkeypatch)
    tts_module = importlib.import_module("odessy.voice.tts")
    captured: dict[str, object] = {}

    class FakeSynthesizer:
        def create(self, text, *, voice, speed, lang):
            captured["create"] = (text, voice, speed, lang)
            return [0.0], 16_000

    def fake_build_kokoro(model_path, voices_path, *, provider, cache_dir):
        captured["builder"] = (model_path, voices_path, provider, cache_dir)
        return FakeSynthesizer()

    monkeypatch.setattr(tts_module, "build_kokoro", fake_build_kokoro)
    cache_dir = tmp_path / "ort-cache"
    tts = tts_module.KokoroTTS(
        model_path="model.onnx",
        voices_path="voices.bin",
        voice="af_sarah",
        speed=1.1,
        lang="en-us",
        provider="cuda",
        cache_dir=cache_dir,
    )

    result = tts.synthesize("Hello", tmp_path / "reply.wav")

    assert result.status == "ok"
    assert captured["builder"] == ("model.onnx", "voices.bin", "cuda", cache_dir)
    assert captured["create"] == ("Hello", "af_sarah", 1.1, "en-us")


def test_voice_config_reads_onnx_settings(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ODESSY_CONFIG_FILE", str(tmp_path / "missing.env"))
    monkeypatch.setenv("ODESSY_ONNX_PROVIDER", "tensorrt")
    monkeypatch.setenv("ODESSY_ONNX_CACHE_DIR", str(tmp_path / "ort-cache"))

    from odessy.voice.config import VoiceConfig

    config = VoiceConfig.from_env()

    assert config.onnx_provider == "tensorrt"
    assert config.onnx_cache_dir == tmp_path / "ort-cache"
