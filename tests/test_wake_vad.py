"""Silero adapter contract without downloading or loading ONNX models."""

import sys
from array import array
from types import ModuleType

import pytest

from odessy.voice.frames import VAD_FRAME_BYTES
from odessy.voice.vad import SileroSpeechDetector


@pytest.mark.parametrize("probability,expected", [(0.04, False), (0.8, True)])
def test_silero_uses_512_sample_chunks_instead_of_upstream_480_default(
    monkeypatch, probability, expected,
):
    class UpstreamVAD:
        # Match openWakeWord's default, which is incompatible with the deployed
        # Silero model when a 512-sample frame is split into 480 + 32 samples.
        def __init__(self, *, model_path=None):
            # The adapter passes the deployed Silero model when it is present on this
            # computer and falls back to the pinned default when it is not. Both are
            # the same upstream seam; only the inference chunk size is under test.
            self.model_path = model_path

        def predict(self, samples, frame_size=480):
            assert samples.typecode == "h"
            assert len(samples) == 512
            if frame_size != 512:
                raise ValueError("Silero received an unsupported inference chunk size")
            return probability

    package = ModuleType("openwakeword")
    vad_module = ModuleType("openwakeword.vad")
    vad_module.VAD = UpstreamVAD
    numpy = ModuleType("numpy")
    numpy.int16 = object()

    def frombuffer(pcm, dtype):
        assert dtype is numpy.int16
        return array("h", pcm)

    numpy.frombuffer = frombuffer
    monkeypatch.setitem(sys.modules, "numpy", numpy)
    monkeypatch.setitem(sys.modules, "openwakeword", package)
    monkeypatch.setitem(sys.modules, "openwakeword.vad", vad_module)

    detector = SileroSpeechDetector()
    assert detector.is_speech(bytes(VAD_FRAME_BYTES)) is expected


def test_wake_uses_vad_beside_selected_model_with_an_isolated_profile(tmp_path, monkeypatch):
    import asyncio
    from odessy.voice.config import VoiceConfig
    from odessy.voice.hybrid import HybridAudioAdapter
    from odessy.voice import hybrid, assets
    resources = tmp_path / 'selected-models'
    resources.mkdir()
    (resources / 'hey_jarvis.onnx').write_bytes(b'wake')
    (resources / 'silero_vad.onnx').write_bytes(b'vad')
    monkeypatch.setattr(assets, 'asset_dir', lambda: tmp_path / 'isolated-empty-profile')
    package = ModuleType('openwakeword')
    module = ModuleType('openwakeword.vad')
    class VAD:
        def __init__(self, model_path=None):
            if model_path != str(resources / 'silero_vad.onnx'):
                raise FileNotFoundError('VAD must use selected wake resources')
    module.VAD = VAD
    monkeypatch.setitem(sys.modules, 'openwakeword', package)
    monkeypatch.setitem(sys.modules, 'openwakeword.vad', module)
    # This initialization test must not depend on the optional voice extra.
    monkeypatch.setitem(sys.modules, 'numpy', ModuleType('numpy'))
    monkeypatch.setattr(hybrid, '_recognition_missing', lambda c: None)
    async def run():
        adapter = HybridAudioAdapter(VoiceConfig('', '', '', '', wake_model=resources / 'hey_jarvis.onnx'),
            lambda *a: None, stream_factory=lambda: object(), wake_factory=lambda: object(),
            stt_factory=lambda: object())
        try:
            assert await adapter._ensure_components()
        finally:
            await adapter.aclose()
    asyncio.run(run())
