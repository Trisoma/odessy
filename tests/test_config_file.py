from __future__ import annotations

import pytest

from pathlib import Path

from odessy.config.config_file import load_config_env, read_env_file, update_env_values
from odessy.voice.config import VoiceConfig


def test_read_env_file_supports_quotes_comments_and_home_expansion(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", "/home/jaimef")
    path = tmp_path / "odessy.env"
    path.write_text(
        "\n".join(
            [
                "# local config",
                'ODESSY_WHISPER_BIN="$HOME/whisper.cpp/build/bin/whisper-cli"',
                "ODESSY_VOICE_SECONDS=3",
                "",
            ]
        ),
        encoding="utf-8",
    )

    values = read_env_file(path)

    assert values["ODESSY_WHISPER_BIN"] == "/home/jaimef/whisper.cpp/build/bin/whisper-cli"
    assert values["ODESSY_VOICE_SECONDS"] == "3"


def test_load_config_env_does_not_override_existing_environment(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ODESSY_OLLAMA_MODEL", "manual-model")
    path = tmp_path / "odessy.env"
    path.write_text("ODESSY_OLLAMA_MODEL=file-model\nODESSY_TOOL_SERVER_URL=http://10.1.1.1:8765/mcp\n", encoding="utf-8")

    load_config_env(path)

    import os

    assert os.environ["ODESSY_OLLAMA_MODEL"] == "manual-model"
    assert os.environ["ODESSY_TOOL_SERVER_URL"] == "http://10.1.1.1:8765/mcp"


def test_voice_config_reads_from_config_file(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "odessy.env"
    path.write_text(
        "\n".join(
            [
                "ODESSY_WHISPER_BIN=/home/jaimef/whisper.cpp/build/bin/whisper-cli",
                "ODESSY_WHISPER_MODEL=/home/jaimef/whisper.cpp/models/ggml-tiny.en.bin",
                "ODESSY_KOKORO_MODEL=/home/jaimef/odessy-voice-test/kokoro-v1.0.int8.onnx",
                "ODESSY_KOKORO_VOICES=/home/jaimef/odessy-voice-test/voices-v1.0.bin",
                "ODESSY_OLLAMA_MODEL=lfm2.5-thinking:1.2b-q8_0",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("ODESSY_CONFIG_FILE", str(path))

    config = VoiceConfig.from_env()

    assert config.whisper_bin.endswith("whisper-cli")
    assert config.ollama_model == "lfm2.5-thinking:1.2b-q8_0"

def test_update_env_values_rejects_newline_injection(tmp_path: Path) -> None:
    path = tmp_path / "odessy.env"
    path.write_text("# keep me\nODESSY_OTHER=1\n", encoding="utf-8")
    with pytest.raises(ValueError):
        update_env_values(path, {"ODESSY_SSH_DISPLAY_NAME": "ok\nODESSY_SSH_COMPUTER=on"})
    assert path.read_text(encoding="utf-8") == "# keep me\nODESSY_OTHER=1\n"


def test_update_env_values_rejects_unsupported_keys_and_control_characters(tmp_path: Path) -> None:
    path = tmp_path / "odessy.env"
    for values in ({"BAD KEY": "x"}, {"ODESSY_X": "a\x00b"},
                   {"ODESSY_X": "a\rb"}, {"ODESSY_X": " a "},
                   {"ODESSY_X": "\"quoted\""}, {"ODESSY_X": "$HOME"}):
        with pytest.raises(ValueError):
            update_env_values(path, values)
    assert not path.exists()


def test_update_env_values_preserves_comments_and_serializes_concurrent_writes(tmp_path: Path) -> None:
    import concurrent.futures
    path = tmp_path / "odessy.env"
    path.write_text("# header\nODESSY_KEEP=yes\n", encoding="utf-8")

    def write(index: int) -> None:
        update_env_values(path, {f"ODESSY_TEST_{index}": str(index)})

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(write, range(24)))

    values = read_env_file(path)
    assert values["ODESSY_KEEP"] == "yes"
    assert {values[f"ODESSY_TEST_{i}"] for i in range(24)} == {str(i) for i in range(24)}
    assert "# header" in path.read_text(encoding="utf-8")
    assert not list(tmp_path.glob("*.tmp"))
