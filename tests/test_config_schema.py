"""Startup configuration validation: shapes, required paths, and unknown names.

These tests pin the three rules in ``odessy.config.schema`` rather than the current
wording of a message: a malformed value is an error, a setting the run needs must
exist, and a name Odessy does not read is a warning that names the likely typo.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest

import odessy.cli as cli
from odessy.config.config_file import default_config_file, read_env_file
from odessy.config.schema import KNOWN_NAMES, Report, enforce_config, validate_config


def _settings(**values: str) -> dict[str, str]:
    return dict(values)


VOICE_PATHS = ("ODESSY_WHISPER_BIN", "ODESSY_WHISPER_MODEL", "ODESSY_KOKORO_MODEL",
               "ODESSY_KOKORO_VOICES")


def _touch(path: Path) -> Path:
    path.write_text("", encoding="utf-8")
    return path


def _errors(report: Report) -> dict[str, str]:
    return {issue.setting: issue.message for issue in report.errors}


def _warnings(report: Report) -> dict[str, str]:
    return {issue.setting: issue.message for issue in report.warnings}


# -- the committed defaults ---------------------------------------------------


def test_the_committed_default_config_is_declared_and_well_formed() -> None:
    """Every line the repository ships must pass the same check a user's does."""

    values = read_env_file(default_config_file())

    report = validate_config(values)

    assert report.errors == ()
    assert not [issue for issue in report.issues if issue.setting not in KNOWN_NAMES]


# -- shapes -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "value", "expected"),
    [
        ("ODESSY_DEPLOYMENT", "singel", "single, split"),
        ("ODESSY_MODEL_BACKEND", "gpt4", "lfm"),
        ("ODESSY_TOOL_CONCURRENCY", "9", "at most 8"),
        ("ODESSY_TOOL_CONCURRENCY", "many", "whole number"),
        ("ODESSY_MODEL_PROGRESS_TIMEOUT", "2 min", "whole number"),
        ("ODESSY_MODEL_ENDPOINT", "localhost:11434", "http(s) URL"),
        ("ODESSY_MODEL_ENDPOINT", "http://[::1", "http(s) URL"),
        ("ODESSY_TOOL_SERVER_URL", "10.1.1.1:8765/mcp", "http(s) URL"),
        ("ODESSY_CLOUD_PRICE_INPUT", "-1", "at least 0"),
        ("ODESSY_WAKE_THRESHOLD", "1.5", "at most 1"),
        ("ODESSY_VOICE_SETTLING_MS", "10001", "at most 10000"),
        ("ODESSY_AUTO_CLOUD_FALLBACK", "true", "on, off"),
        ("ODESSY_MODEL_NAME", "two words", "without spaces"),
        ("ODESSY_CLOUD_RETRIES", "1.5", "whole number"),
    ],
)
def test_a_value_that_does_not_fit_its_shape_is_an_error(
    name: str, value: str, expected: str
) -> None:
    report = validate_config(_settings(**{name: value}))

    assert expected in _errors(report)[name]
    assert report.ok is False


@pytest.mark.parametrize(
    "values",
    [
        {"ODESSY_DEPLOYMENT": "split"},
        {"ODESSY_EGRESS_FILE": ""},          # documented: empty turns the log off
        {"ODESSY_AUDIT_FILE": ""},
        {"ODESSY_CLOUD_EFFORT": ""},         # documented: empty keeps the default
        {"ODESSY_TOOL_SERVER_URL": ""},      # documented: empty means not configured
        {"ODESSY_AUTO_CLOUD_FALLBACK": "off"},
        {"ODESSY_CLOUD_PROMPT_CACHE": "false"},   # the runtime reads false as off
        {"ODESSY_CLOUD_FALLBACKS": "true"},
        {"ODESSY_VOICE_STREAM_SPEECH": "0"},
        {"ODESSY_MODEL_PROGRESS_TIMEOUT": "120"},
        {"ODESSY_MODEL_BACKEND": "deepseek"},
        {"ODESSY_ISOLATION": "not-required"},
    ],
)
def test_documented_values_are_accepted(values: dict[str, str]) -> None:
    assert validate_config(values).errors == ()


@pytest.mark.parametrize("name", ["ODESSY_TOOL_CONCURRENCY", "ODESSY_CLOUD_TIMEOUT",
                                  "ODESSY_MODEL_NAME", "ODESSY_VOICE_SECONDS"])
def test_an_empty_typed_value_is_refused_where_the_runtime_needs_a_value(name: str) -> None:
    report = validate_config(_settings(**{name: ""}))

    assert "empty" in _errors(report)[name]


# -- unknown names ------------------------------------------------------------


def test_a_misspelled_setting_is_a_warning_that_names_the_real_one() -> None:
    """Silently dropping configuration is the failure mode this rule exists for."""

    report = validate_config(_settings(ODESSY_WHISPER_MODLE="/tmp/model.bin"))

    assert report.errors == ()
    (issue,) = report.warnings
    assert issue.setting == "ODESSY_WHISPER_MODLE"
    assert "ODESSY_WHISPER_MODEL" in (issue.fix or "")


def test_an_unknown_name_without_a_close_match_says_to_remove_it() -> None:
    report = validate_config(_settings(ODESSY_ZORP="1"))

    (issue,) = report.warnings
    assert "remove" in (issue.fix or "")


@pytest.mark.parametrize(
    "name",
    [
        "ODESSY_WORKSPACE_ROOT",
        "ODESSY_MCP_PORT",
        "ODESSY_PROBE_READONLY",
        "ODESSY_BENCHMARK_OLLAMA_NUM_GPU",
        "ODESSY_OK_abcdef1234",
    ],
)
def test_names_written_by_odessy_or_tooling_never_warn(name: str) -> None:
    assert validate_config({name: "1"}).issues == ()


# -- required paths per feature ----------------------------------------------


def test_voice_paths_are_required_only_by_the_voice_command(tmp_path: Path) -> None:
    env = {name: str(tmp_path / "missing") for name in VOICE_PATHS}

    text_run = validate_config(env)
    voice_run = validate_config(env, contexts=("voice",))

    assert text_run.errors == ()
    # A path that is set but missing is still worth a warning for a text run.
    assert set(_warnings(text_run)) == set(env)
    assert set(_errors(voice_run)) == set(env)


def test_a_voice_path_that_exists_is_accepted(tmp_path: Path) -> None:
    env = {name: str(_touch(tmp_path / name.lower())) for name in VOICE_PATHS}

    report = validate_config(env, contexts=("voice",))

    assert report.errors == ()


def test_an_unset_voice_setting_falls_back_to_the_runtime_default() -> None:
    """The check reports what the voice run would actually use, not only what is set."""

    report = validate_config({}, contexts=("voice",))

    # "whisper-cli" is a bare command name looked up on PATH; the other three are
    # relative paths that do not exist relative to the test's working directory.
    assert "ODESSY_WHISPER_BIN" not in _errors(report)
    assert {"ODESSY_WHISPER_MODEL", "ODESSY_KOKORO_MODEL", "ODESSY_KOKORO_VOICES"} <= set(_errors(report))


def test_the_installed_default_wake_model_is_accepted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`voice --listen` has a default asset; requiring the variable rejected it."""

    monkeypatch.setattr("odessy.voice.assets.DEFAULT_WAKE_ASSET_DIR", tmp_path / "wake")
    (tmp_path / "wake").mkdir()
    _touch(tmp_path / "wake" / "hey_jarvis_v0.1.onnx")
    env = {name: str(_touch(tmp_path / name.lower())) for name in VOICE_PATHS}

    assert validate_config(env, contexts=("voice", "wake")).errors == ()


def test_a_missing_default_wake_model_names_the_setup_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("odessy.voice.assets.DEFAULT_WAKE_ASSET_DIR", tmp_path / "wake")
    env = {name: str(_touch(tmp_path / name.lower())) for name in VOICE_PATHS}

    report = validate_config(env, contexts=("voice", "wake"))

    (issue,) = report.errors
    assert issue.setting == "ODESSY_WAKE_MODEL"
    assert "--setup-wake" in (issue.fix or "")
    # The single-turn voice command does not open the wake gate.
    assert validate_config(env, contexts=("voice",)).errors == ()


def test_a_directory_where_a_file_is_expected_is_an_error_even_optional(tmp_path: Path) -> None:
    report = validate_config({"ODESSY_SERVERS_FILE": str(tmp_path)})

    assert "not a file" in _errors(report)["ODESSY_SERVERS_FILE"]


# -- values are never echoed -----------------------------------------------------------------


def test_a_secret_is_never_echoed() -> None:
    report = validate_config({"ODESSY_CLOUD_API_KEY": "sk-abcdefghijklmnopqrstuvwxyz"})

    assert report.issues == ()
    assert "abcdefghijklmnopqrstuvwxyz" not in report.describe()


def test_credentials_in_a_url_are_not_echoed() -> None:
    report = validate_config({"ODESSY_MODEL_ENDPOINT": "ftp://user:verysecret@example.test"})

    (issue,) = report.errors
    assert "verysecret" not in issue.message
    assert "example.test" in issue.message


# -- startup enforcement ------------------------------------------------------


def test_enforce_config_prints_warnings_and_starts_when_only_warnings_exist() -> None:
    stream = io.StringIO()

    report = enforce_config({"ODESSY_ZORP": "1"}, stream=stream)

    assert report.ok
    assert "ODESSY_ZORP" in stream.getvalue()


def test_enforce_config_refuses_to_start_and_names_the_fix() -> None:
    with pytest.raises(SystemExit) as raised:
        enforce_config({"ODESSY_TOOL_CONCURRENCY": "9"}, stream=io.StringIO())

    message = str(raised.value)
    assert "ODESSY_TOOL_CONCURRENCY" in message
    assert "fix: use a number 1 to 8" in message


def test_cli_startup_refuses_a_broken_setting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ODESSY_TOOL_CONCURRENCY", "99")
    # `doctor` is exempt from startup enforcement; keep its own checks out of this
    # test, which is about the exemption and not about this machine's setup.
    monkeypatch.setattr("odessy.config.doctor.main", lambda backend=None, *, voice=False: 0)

    with pytest.raises(SystemExit) as raised:
        cli.main(["doctor"])

    assert "ODESSY_TOOL_CONCURRENCY" not in str(raised.value)

    with pytest.raises(SystemExit) as raised:
        cli.main(["text run"])

    assert "ODESSY_TOOL_CONCURRENCY" in str(raised.value)

    # Help must remain reachable with a broken config.
    with pytest.raises(SystemExit) as raised:
        cli.main(["--help"])

    assert "ODESSY_TOOL_CONCURRENCY" not in str(raised.value)


def test_cli_voice_requires_the_voice_assets(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    for name in ("ODESSY_WHISPER_BIN", "ODESSY_WHISPER_MODEL", "ODESSY_KOKORO_MODEL",
                 "ODESSY_KOKORO_VOICES"):
        monkeypatch.setenv(name, str(tmp_path / "missing"))

    with pytest.raises(SystemExit) as raised:
        cli.main(["voice"])

    assert "ODESSY_KOKORO_MODEL" in str(raised.value)


def test_doctor_reports_the_same_issues_as_checks() -> None:
    """``doctor`` must explain a configuration the CLI would refuse."""

    from odessy.config.doctor import _settings_checks

    checks = _settings_checks({"ODESSY_TOOL_CONCURRENCY": "9", "ODESSY_ZORP": "1"})

    assert [check.status for check in checks] == ["fail", "warn"]
    assert any("ODESSY_TOOL_CONCURRENCY" in check.summary for check in checks)
    assert any("ODESSY_ZORP" in check.summary for check in checks)


def test_doctor_says_when_the_configuration_is_clean() -> None:
    from odessy.config.doctor import _settings_checks

    (check,) = _settings_checks({"ODESSY_DEPLOYMENT": "single"})

    assert check.status == "ok"


def test_every_cloud_provider_has_a_fallback_model_setting() -> None:
    """The per-backend override names are declared, so they never warn as typos."""

    from odessy.models.cloud_model import CLOUD_PROVIDERS

    for name in CLOUD_PROVIDERS:
        assert f"ODESSY_CLOUD_{name.upper()}_MODEL" in KNOWN_NAMES
