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
