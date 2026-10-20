from __future__ import annotations

import urllib.error
from typing import Any

import pytest

from odessy.cli import main
from odessy.config.doctor import Check, Probes, render, run_checks

ALL_PATHS = lambda path: True  # noqa: E731


def _probes(
    *,
    on_path: set[str] | None = None,
    tags: Any = None,
    ollama_down: bool = False,
    reachable: bool = True,
    node_version: str = "v22.11.0",
    node_state: tuple[bool, bool] = (True, True),
    paths_exist: bool = True,
) -> Probes:
    on_path = {"node", "npm", "whisper-cli", "arecord", "aplay"} if on_path is None else on_path

    def http_get_json(url: str, timeout: float) -> Any:
        if ollama_down:
            raise urllib.error.URLError("connection refused")
        return tags if tags is not None else {"models": [{"name": "lfm2.5-thinking:1.2b-q8_0"}]}

    return Probes(
        which=lambda name: f"/usr/bin/{name}" if name in on_path else None,
        http_get_json=http_get_json,
        tcp_reachable=lambda host, port, timeout: reachable,
        command_output=lambda argv, timeout: node_version,
        node_tools_state=lambda: node_state,
        path_exists=lambda path: paths_exist,
    )


def _by_section(checks: list[Check], section: str) -> list[Check]:
    return [check for check in checks if check.section == section]


def test_a_complete_single_machine_setup_has_no_failures() -> None:
    checks = run_checks("lfm", {"ODESSY_DEPLOYMENT": "single"}, _probes())
    assert not [check for check in checks if check.status == "fail"]
    assert "Ready to run" in render(checks)


def test_ollama_down_is_a_failure_with_the_install_step() -> None:
    checks = run_checks("lfm", {"ODESSY_DEPLOYMENT": "single"}, _probes(ollama_down=True))
    (model,) = _by_section(checks, "Model")
    assert model.status == "fail"
    assert "ollama.com" in (model.fix or "")
    assert "problem(s) to fix" in render(checks)


def test_a_model_that_is_not_pulled_gives_the_pull_command() -> None:
    checks = run_checks(
        "lfm",
        {"ODESSY_DEPLOYMENT": "single", "ODESSY_MODEL_NAME": "qwen3:4b"},
        _probes(tags={"models": [{"name": "gemma3:1b"}]}),
    )
    failed = [check for check in _by_section(checks, "Model") if check.status == "fail"]
    assert failed and failed[0].fix == "ollama pull qwen3:4b"


def test_an_untagged_model_matches_its_latest_tag() -> None:
    checks = run_checks(
        "ollama",
        {"ODESSY_DEPLOYMENT": "single", "ODESSY_OLLAMA_MODEL": "gemma3"},
        _probes(tags={"models": [{"name": "gemma3:latest"}]}),
    )
    assert all(check.status == "ok" for check in _by_section(checks, "Model"))


def test_a_cloud_backend_checks_the_key_without_printing_it() -> None:
    missing = run_checks("deepseek", {"ODESSY_DEPLOYMENT": "single"}, _probes())
    assert _by_section(missing, "Model")[0].status == "fail"

    present = run_checks(
        "deepseek", {"ODESSY_DEPLOYMENT": "single", "DEEPSEEK_API_KEY": "sk-secret-value"}, _probes()
    )
    (model,) = _by_section(present, "Model")
    assert model.status == "ok"
    assert "sk-secret-value" not in render(present)


def test_missing_node_is_a_warning_because_odessy_still_runs() -> None:
    checks = run_checks("lfm", {"ODESSY_DEPLOYMENT": "single"}, _probes(on_path=set()))
    (tool_server,) = _by_section(checks, "Tool server")
    assert tool_server.status == "warn"
    assert "nodejs.org" in (tool_server.fix or "")


def test_uninstalled_or_stale_tools_say_how_to_build() -> None:
    uninstalled = run_checks("lfm", {"ODESSY_DEPLOYMENT": "single"}, _probes(node_state=(False, False)))
    assert _by_section(uninstalled, "Tool server")[-1].fix == "cd tools && npm install && npm run build"

    stale = run_checks("lfm", {"ODESSY_DEPLOYMENT": "single"}, _probes(node_state=(True, False)))
    last = _by_section(stale, "Tool server")[-1]
    assert last.status == "warn"
    assert last.fix == "cd tools && npm run build"


def test_old_node_is_flagged() -> None:
    too_old = run_checks("lfm", {"ODESSY_DEPLOYMENT": "single"}, _probes(node_version="v18.20.0"))
    assert "too old" in _by_section(too_old, "Tool server")[0].summary

    eol = run_checks("lfm", {"ODESSY_DEPLOYMENT": "single"}, _probes(node_version="v20.18.0"))
    assert "end-of-life" in _by_section(eol, "Tool server")[0].summary


def test_split_mode_checks_the_laptop_server_instead_of_node() -> None:
    env = {"ODESSY_TOOL_SERVER_URL": "http://10.1.1.1:8765/mcp"}
    ok = run_checks("lfm", env, _probes(on_path=set()))
    (server,) = _by_section(ok, "Tool server")
    assert server.status == "ok" and "10.1.1.1:8765" in server.summary

    down = run_checks("lfm", env, _probes(reachable=False))
    (server,) = _by_section(down, "Tool server")
    assert server.status == "fail"
    assert "ODESSY_MCP_HOST=0.0.0.0" in (server.fix or "")


def test_split_mode_without_a_laptop_is_a_warning() -> None:
    checks = run_checks("lfm", {"ODESSY_DEPLOYMENT": "split"}, _probes())
    (server,) = _by_section(checks, "Tool server")
    assert server.status == "warn"


def test_an_invalid_deployment_stops_the_checks_with_a_failure() -> None:
    checks = run_checks("lfm", {"ODESSY_DEPLOYMENT": "both"}, _probes())
    assert checks[-1].section == "Deployment"
    assert checks[-1].status == "fail"


def test_voice_problems_are_only_warnings() -> None:
    checks = run_checks("lfm", {"ODESSY_DEPLOYMENT": "single"}, _probes(on_path={"node", "npm"}, paths_exist=False))
    voice = _by_section(checks, "Voice (optional)")
    assert voice and all(check.status == "warn" for check in voice)
    assert not [check for check in checks if check.status == "fail"]


def test_doctor_subcommand_exits_nonzero_on_failure(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(
        "odessy.config.doctor.run_checks",
        lambda backend, **kwargs: [Check("Model", "fail", "Ollama is not reachable", "start it")],
    )
    with pytest.raises(SystemExit) as raised:
        main(["doctor"])
    assert raised.value.code == 1
    output = capsys.readouterr().out
    assert "[FAIL] Ollama is not reachable" in output
    assert "fix: start it" in output


def test_doctor_subcommand_passes_the_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str | None] = []
    monkeypatch.setattr("odessy.config.doctor.run_checks", lambda backend, **kwargs: seen.append(backend) or [])
    with pytest.raises(SystemExit) as raised:
        main(["doctor", "--model-backend", "qwen"])
    assert raised.value.code == 0
    assert seen == ["qwen"]


def test_the_voice_flag_reaches_the_checks(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[bool] = []
    monkeypatch.setattr(
        "odessy.config.doctor.run_checks", lambda backend, *, voice=False: seen.append(voice) or []
    )
    for argv in (["doctor"], ["doctor", "--voice"]):
        with pytest.raises(SystemExit):
            main(argv)

    assert seen == [False, True]
