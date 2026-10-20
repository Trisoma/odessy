from __future__ import annotations

from odessy.cli import _build_model
from odessy.fake_model import FakeModel
from odessy.lfm_model import LlamaCppLFMModel
from odessy.ollama_model import OllamaModel


def test_text_mode_defaults_to_lfm_model() -> None:
    model = _build_model(False, ollama_model="gemma3:1b")

    assert isinstance(model, LlamaCppLFMModel)
    assert model.model == "lfm2.5-thinking:1.2b-q8_0"


def test_legacy_gemma_switch_remains_available() -> None:
    model = _build_model(False, backend="ollama", ollama_model="gemma3:1b")

    assert isinstance(model, OllamaModel)
    assert model.model == "gemma3:1b"


def test_legacy_fake_model_switch_remains_available() -> None:
    model = _build_model(True, ollama_model="gemma3:1b")

    assert isinstance(model, FakeModel)
