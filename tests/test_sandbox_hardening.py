"""Sandbox hardening: network, hidden paths, extra writable folders, limits,
sandbox.toml, per-target isolation and ``auto``, the doctor, and ACE cleanup."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from odessy.authority.command_bridge import CommandBridge
from odessy.authority.policy import OdessyPolicy
from odessy.config.sandbox import (
    DEFAULT_HIDDEN_PATHS,
    SandboxConfigError,
    SandboxSettings,
    TargetSettings,
    read_sandbox_settings,
)
from odessy.execution.contract import (
    NETWORK_POLICIES,
    ConfineResult,
    Evaluation,
    ExecutionPolicy,
    ProbeReport,
)
from odessy.execution.escalation import EscalationDeniedError
from odessy.execution.limits import ResourceLimits, exceeded_limit, limit_report, windows_limit_argv
from odessy.execution.posture import host_posture, resolve_isolation
from odessy.execution.probe import (
    BwrapFacts,
    HostFacts,
    LandlockFacts,
    LandlockLauncherFacts,
    SeatbeltFacts,
    UserNamespaceFacts,
)
from odessy.execution.profiles import bwrap_profile_args, landlock_profile_args, seatbelt_profile_args
from odessy.execution.providers import BwrapProvider, LandlockProvider, SeatbeltProvider, WindowsAclProvider
from odessy.execution.registry import ProviderRegistry, with_unconfined_fallback
from odessy.execution.roots import extra_writable_roots, hidden_roots, writable_roots
from odessy.runtime.agent_loop import ExecutionTarget, OdessyAgent
from odessy.runtime.hooks import public_event
from odessy.runtime.types import FinalResponse, LogEntry, ToolRequest
from odessy.tools.mcp_server import build_odessy_mcp_server


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def facts(*, bwrap: str = "available_userns", seatbelt: bool = True) -> HostFacts:
    return HostFacts(
        platform="linux", system="Linux", release="6.7.0", machine="aarch64",
        landlock=LandlockFacts(abi=5, detail="stub"),
        landlock_launcher=LandlockLauncherFacts(path="/usr/bin/landlock-run", enforcement="full", detail="stub"),  # type: ignore[arg-type]
        bwrap=BwrapFacts(availability=bwrap, path="/usr/bin/bwrap", is_setuid_root=False, version="0.8", detail="stub"),  # type: ignore[arg-type]
        seatbelt=SeatbeltFacts(available=seatbelt, path="/usr/bin/sandbox-exec", detail="stub"),
        user_namespaces=UserNamespaceFacts(None, None, None),
        device_nodes={},
    )


def policy(mode: str = "read-only", **fields) -> ExecutionPolicy:
    return ExecutionPolicy(mode=mode, workspace_root="/work", **fields)  # type: ignore[arg-type]


# --- contract and profiles ------------------------------------------------------


def test_the_policy_defaults_change_nothing():
    plain = policy()
    assert (plain.network, plain.hidden_paths, plain.extra_writable) == ("allow", (), ())
    assert NETWORK_POLICIES == ("allow", "deny")
    assert "--unshare-net" not in bwrap_profile_args(plain)


def test_bwrap_cuts_the_network_hides_paths_and_binds_extra_folders(tmp_path):
    folder, file = tmp_path / "secrets", tmp_path / "token"
    folder.mkdir()
    file.write_text("x")
    args = bwrap_profile_args(policy("workspace-write", network="deny", hidden_paths=(str(folder), str(file)),
                                     extra_writable=("/cache",)))
    assert "--unshare-net" in args
    joined = " ".join(args)
    assert f"--tmpfs {folder}" in joined and f"--ro-bind /dev/null {file}" in joined
    assert joined.index(str(folder)) < joined.index("--bind /work /work")  # hidden before the writable binds
    assert "--bind /cache /cache" in joined
    assert "--bind /cache" not in " ".join(bwrap_profile_args(policy("read-only", extra_writable=("/cache",))))


def test_seatbelt_and_landlock_shapes(tmp_path):
    profile = seatbelt_profile_args(policy("read-only", network="deny", hidden_paths=(str(tmp_path),)))[1]
    from odessy.execution.profiles import _sbpl_string

    assert "(deny network*)" in profile and f"(deny file-read* (subpath {_sbpl_string(str(tmp_path))}))" in profile
    assert landlock_profile_args(policy("workspace-write", extra_writable=("/cache",)))[-2:] == ["--rw", "/cache"]


def test_limitations_follow_what_the_backend_enforced(tmp_path):
    hidden = policy(network="deny", hidden_paths=(str(tmp_path),))
    assert "cannot_restrict_network" not in BwrapProvider(facts=facts()).limitations(hidden)
    assert "cannot_restrict_network" in BwrapProvider(facts=facts()).limitations(policy())
    assert "cannot_restrict_network" not in SeatbeltProvider(facts=facts()).limitations(hidden)
    landlock = LandlockProvider(facts=facts()).limitations(hidden)
    assert {"cannot_hide_paths", "cannot_restrict_network"} <= landlock
    assert "cannot_hide_paths" not in LandlockProvider(facts=facts()).limitations(policy())


def test_writable_roots_include_extra_folders_only_when_writing(tmp_path):
    assert str(tmp_path) in writable_roots(policy("workspace-write", extra_writable=(str(tmp_path),)))
    assert writable_roots(policy("read-only", extra_writable=(str(tmp_path),))) == []


def test_hidden_roots_expand_home_and_keep_clear_of_the_workspace(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".ssh").mkdir(parents=True)
    workspace = home / "projects" / "app"
    (workspace / "inner").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    roots = hidden_roots(("~/.ssh", "~/.aws", str(home / "projects"), str(workspace / "inner"), "relative"), str(workspace))
    assert roots == (str((home / ".ssh").resolve()),)


def test_extra_writable_roots_refuse_home_and_filesystem_roots(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    cache = tmp_path / "cache"
    cache.mkdir()
    anchor = os.path.abspath(os.sep)
    assert extra_writable_roots(("~/cache", "~/missing", str(tmp_path), anchor)) == (str(cache.resolve()),)


# --- the Python windows-acl provider ---------------------------------------------


def test_windows_acl_needs_windows_node_and_the_built_runner(tmp_path):
    runner = tmp_path / "runner.js"
    runner.write_text("")
    assert not WindowsAclProvider(platform="posix", node="node", runner=runner).usable()[0]
    usable, _ = WindowsAclProvider(platform="nt", node="node", runner=runner).usable()
    assert usable
    provider = WindowsAclProvider(platform="nt", node="C:/node.exe", runner=runner)
    argv = provider.wrap(["powershell", "-Command", "pwd"], policy("read-only"))
    assert argv[:2] == ["C:/node.exe", str(runner)] and argv[argv.index("--mode") + 1] == "read-only"
    assert argv[argv.index("--") + 1:] == ["powershell", "-Command", "pwd"]
    assert provider.enforcement(provider.limitations(policy())) == "partial"
    assert "cannot_hide_paths" in provider.limitations(policy(hidden_paths=("C:/x",)))


# --- the command bridge ------------------------------------------------------------


class Recording:
    """A full backend that records the policy and runs a harmless command."""

    provider_id = "recording"

    def __init__(self, limitations=frozenset({"cannot_restrict_reads"})):
        self.policies = []
        self._limitations = frozenset(limitations)

    def capabilities(self):
        return frozenset({"process-exec", "fs-read", "fs-write"})

    def probe(self):
        return ProbeReport(provider_id=self.provider_id, available=True, reason="test")

    def evaluate(self, requirement):
        ok = requirement.policy.mode != "danger-full-access"
        return Evaluation(satisfied=ok, enforcement="full" if ok else "none", limitations=self._limitations, reason="test")

    def confine(self, argv, policy):
        self.policies.append(policy)
        return ConfineResult(argv=(sys.executable, "-c", "print('ran')"), enforcement="full",
                             limitations=self._limitations, denial_signatures=(), runner_failure_rules=(),
                             provider_id=self.provider_id)


def test_the_bridge_shapes_the_policy_and_reports_what_held(tmp_path):
    secrets = tmp_path / "secrets"
    secrets.mkdir()
    workspace = tmp_path / "work"
    workspace.mkdir()
    backend = Recording()
    result = CommandBridge(workspace, registry=with_unconfined_fallback([backend])).run(
        command="pwd", timeout=30, mode="read-only", isolation="required",
        network="deny", hidden_paths=[str(secrets), str(tmp_path / "missing")],
    )
    assert backend.policies[0].network == "deny"
    assert backend.policies[0].hidden_paths == (str(secrets.resolve()),)
    assert result["sandbox"]["network"] == "denied" and result["sandbox"]["hidden_paths"] == 1
    assert "limits" not in result["sandbox"]


def test_a_backend_that_cannot_cut_the_network_says_so(tmp_path):
    backend = Recording(limitations={"cannot_restrict_network", "cannot_hide_paths"})
    result = CommandBridge(tmp_path, registry=with_unconfined_fallback([backend])).run(
        command="pwd", timeout=30, mode="read-only", isolation="preferred", network="deny", hidden_paths=[str(tmp_path.parent)],
    )
    assert result["sandbox"]["network"] == "open" and result["sandbox"]["hidden_paths"] == 0


def test_an_unconfined_run_never_claims_a_cut_network(tmp_path):
    result = CommandBridge(tmp_path, registry=with_unconfined_fallback([])).run(
        command="echo hi", timeout=30, mode="read-only", isolation="preferred", network="deny",
    )
    assert result["sandbox"]["provider_id"] == "unconfined" and result["sandbox"]["network"] == "open"


def test_limits_are_validated_clamped_and_reported():
    limits = ResourceLimits.from_arguments({"memory_mb": 10 ** 9, "processes": 0, "cpu_seconds": True, "file_size_mb": 5})
    assert limits == ResourceLimits(memory_mb=1 << 20, processes=None, file_size_mb=5, cpu_seconds=None)
    assert ResourceLimits.from_arguments(None) is None
    report = limit_report(ResourceLimits(memory_mb=1, processes=2, file_size_mb=3, cpu_seconds=4))
    assert report["not_applied"] == (["file_size_mb"] if os.name == "nt" else ["processes"])


@pytest.mark.skipif(os.name == "nt", reason="POSIX exit statuses")
def test_a_limit_signal_is_named():
    import signal

    assert exceeded_limit(128 + signal.SIGXCPU) == "cpu_seconds"
    assert exceeded_limit(-signal.SIGXFSZ) == "file_size_mb"
    assert exceeded_limit(1) is None and exceeded_limit(0) is None


@pytest.mark.skipif(os.name != "nt", reason="Windows job objects")
def test_windows_limits_run_the_command_inside_a_job(tmp_path):
    argv = windows_limit_argv(ResourceLimits(memory_mb=512, processes=16, file_size_mb=None, cpu_seconds=30), ["cmd"])
    assert argv[2].endswith("_limit_host.py") and argv[3:7] == ["512", "16", "30", "--"]
    result = CommandBridge(tmp_path).run(command="Write-Output limited", timeout=30,
                                         limits={"memory_mb": 512, "processes": 16, "cpu_seconds": 30})
    assert result["exit_code"] == 0 and "limited" in result["stdout"]
    assert result["sandbox"]["limits"]["applied"] == {"memory_mb": 512, "processes": 16, "cpu_seconds": 30}


# --- sandbox.toml ----------------------------------------------------------------


def write(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "sandbox.toml"
    path.write_text(body, encoding="utf-8")
    return path


def test_no_file_means_the_defaults(tmp_path):
    settings = read_sandbox_settings(tmp_path / "none.toml")
    assert settings.isolation is None and settings.escalation == "ask"
    assert settings.network_for("read-only") == "deny" and settings.network_for("workspace-write") == "allow"
    assert settings.hidden_paths == DEFAULT_HIDDEN_PATHS and settings.limits.memory_mb == 4096


def test_a_full_file_is_read(tmp_path):
    settings = read_sandbox_settings(write(tmp_path, '''
isolation = "required"
escalation = "workspace-write-only"
extra_writable = ["~/.cache/pip"]
hidden_paths = ["~/work/other"]
default_hidden = false

[network]
workspace_write = "deny"

[limits]
memory_mb = 0
cpu_seconds = 30

[targets.laptop]
isolation = "preferred"
escalation = "never"
'''))
    assert settings.isolation == "required" and settings.hidden_paths == ("~/work/other",)
    assert settings.network_for("workspace-write") == "deny" and settings.network_for("danger-full-access") == "allow"
    assert settings.limits == ResourceLimits(memory_mb=None, processes=256, file_size_mb=4096, cpu_seconds=30)
    assert settings.escalation_for("laptop") == "never" and settings.escalation_for("local") == "workspace-write-only"
    assert settings.isolation_setting_for("laptop") == "preferred" and settings.isolation_setting_for("local") is None


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ('shell = "bash"\n', "unknown keys"),
        ('isolation = "maybe"\n', "isolation must be one of"),
        ('extra_writable = ["~"]\n', "cannot include"),
        ('extra_writable = ["~/.ssh/keys"]\n', "overlaps the hidden path"),
        ('extra_writable = ["relative/dir"]\n', "absolute or start with ~/"),
        ('[limits]\nmemory_mb = -1\n', "whole number"),
        ('[network]\nread_only = "maybe"\n', "network.read_only"),
        ('[targets.desktop]\nisolation = "required"\n', r"\[targets\] takes"),
        ('isolation = "required\n', "cannot read"),
    ],
)
def test_malformed_files_are_rejected(tmp_path, body, message):
    with pytest.raises(SandboxConfigError, match=message):
        read_sandbox_settings(write(tmp_path, body))


# --- the policy ------------------------------------------------------------------


def test_isolation_is_per_target():
    rules = OdessyPolicy(workspace_root=Path("."), isolation="required", target_isolation={"laptop": "preferred"})
    assert rules.isolation_for("terminal.run") == "required"
    assert rules.isolation_for("laptop.terminal.run") == "preferred"
    run = ToolRequest("laptop.terminal.run", {"command": "pwd", "timeout": 10})
    assert rules.requirement_for(run, rules.evaluate(run)).isolation == "preferred"


def test_execution_arguments_follow_the_mode_and_the_settings():
    settings = SandboxSettings(network_workspace_write="deny", hidden_paths=("~/.ssh",), extra_writable=("~/.npm",),
                               limits=ResourceLimits(memory_mb=1024, processes=None, file_size_mb=None, cpu_seconds=None))
    rules = OdessyPolicy(workspace_root=Path("."), sandbox=settings)
    build = ToolRequest("terminal.run", {"command": "make"})
    install = ToolRequest("terminal.run", {"command": "pip install requests"})
    assert rules.execution_arguments(build, "read-only") == {
        "network": "deny", "hidden_paths": ["~/.ssh"], "extra_writable": ["~/.npm"], "limits": {"memory_mb": 1024},
    }
    assert rules.execution_arguments(build, "workspace-write")["network"] == "deny"
    assert rules.execution_arguments(install, "workspace-write")["network"] == "allow"
    assert rules.execution_arguments(build, "danger-full-access")["network"] == "allow"


def escalate(name: str, target: str) -> ToolRequest:
    return ToolRequest(name, {"command": "mkdir build", "timeout": 10, "sandbox_permissions": target,
                              "justification": "needs the network"})


def test_the_escalation_setting_refuses_before_anyone_is_asked(tmp_path):
    settings = SandboxSettings(escalation="workspace-write-only", targets={"laptop": TargetSettings(escalation="never")})
    rules = OdessyPolicy(workspace_root=tmp_path, sandbox=settings)
    full = escalate("terminal.run", "danger-full-access")
    with pytest.raises(EscalationDeniedError, match="workspace-write-only"):
        rules.preflight_execution_mode(full, rules.evaluate(full))
    laptop = escalate("laptop.terminal.run", "danger-full-access")
    with pytest.raises(EscalationDeniedError, match='"never"'):
        rules.preflight_execution_mode(laptop, rules.evaluate(laptop))
    asked = OdessyPolicy(workspace_root=tmp_path)
    assert asked.preflight_execution_mode(full, asked.evaluate(full)) == "danger-full-access"


class _Pwd:
    def propose(self, user_text):
        return ToolRequest("terminal.run", {"command": "pwd", "timeout": 30})

    def respond_to_observation(self, user_text, observation):
        self.observation = observation
        return FinalResponse("done")


@pytest.mark.anyio
async def test_the_agent_sends_the_sandbox_decision_with_the_call(tmp_path):
    secrets = tmp_path / "secrets"
    secrets.mkdir()
    workspace = tmp_path / "work"
    workspace.mkdir()
    backend, model = Recording(), _Pwd()
    settings = SandboxSettings(hidden_paths=(str(secrets),), limits=None)
    agent = OdessyAgent(
        model=model,
        policy=OdessyPolicy(workspace_root=workspace, isolation="required", sandbox=settings),
        mcp_server=ExecutionTarget(build_odessy_mcp_server(workspace_root=workspace,
                                                           registry=with_unconfined_fallback([backend]))),
    )
    await agent.run_turn("pwd")
    sent = backend.policies[0]
    assert sent.mode == "read-only" and sent.network == "deny" and sent.hidden_paths == (str(secrets.resolve()),)
    assert model.observation.result["sandbox"]["network"] == "denied"


def test_the_model_cannot_send_sandbox_arguments():
    from odessy.tools.tool_contracts import validate_tool_arguments

    with pytest.raises(ValueError, match="Unexpected argument"):
        validate_tool_arguments("terminal.run", {"command": "curl x", "network": "allow"})


# --- auto, the doctor, the CLI ------------------------------------------------------


def test_auto_is_required_only_where_both_modes_are_fully_confined():
    full = with_unconfined_fallback([Recording()])
    nothing = with_unconfined_fallback([])
    assert resolve_isolation("auto", full, ".") == "required"
    assert resolve_isolation("auto", nothing, ".") == "preferred"
    assert resolve_isolation("auto", full, ".", target="laptop") == "preferred"
    assert resolve_isolation("not-required", full, ".") == "not-required"
    assert [p.provider_id for p in host_posture(nothing, ".")] == ["unconfined", "unconfined"]


def test_the_doctor_shows_the_sandbox(tmp_path, monkeypatch):
    from odessy.config.doctor import _sandbox_checks

    monkeypatch.chdir(tmp_path)
    env = {"ODESSY_SANDBOX_FILE": str(tmp_path / "none.toml")}
    (warn,) = _sandbox_checks(env, ProviderRegistry([*with_unconfined_fallback([]).providers()]))
    assert warn.status == "warn" and "read-only: no sandbox" in warn.summary and warn.fix
    bwrap = BwrapProvider(facts=facts())
    (ok,) = _sandbox_checks(env, with_unconfined_fallback([bwrap]))
    assert ok.status == "ok" and "local commands required, laptop commands preferred" in ok.summary
    assert "read-only: bwrap (full; network cut, credentials hidden)" in ok.summary
    (fail,) = _sandbox_checks({"ODESSY_SANDBOX_FILE": str(write(tmp_path, "nope = 1\n"))})
    assert fail.status == "fail"


def test_the_cli_resolves_isolation_and_protects_the_sandbox_file(tmp_path, monkeypatch):
    from odessy import cli
    from odessy.config.sandbox import reset_sandbox_cache
    from odessy.models.fake_model import FakeModel

    path = tmp_path / "cfg" / "sandbox.toml"
    monkeypatch.setenv("ODESSY_SANDBOX_FILE", str(path))
    monkeypatch.setenv("ODESSY_ISOLATION", "required")
    reset_sandbox_cache()
    agent = cli._build_agent(tmp_path, FakeModel(), ())
    assert agent._policy.isolation == "required" and agent._policy.isolation_for("laptop.terminal.run") == "required"
    assert path in agent._policy.protected_paths
    path.parent.mkdir()
    path.write_text("isolation = 3\n", encoding="utf-8")
    reset_sandbox_cache()
    with pytest.raises(SystemExit, match="isolation must be one of"):
        cli._build_agent(tmp_path, FakeModel(), ())
    reset_sandbox_cache()


def test_public_tool_events_name_the_sandbox():
    entry = LogEntry("tool_observation", {"name": "terminal.run", "result": {
        "status": "ok", "stdout": "secret", "sandbox": {"provider_id": "bwrap", "enforcement": "full",
                                                         "network": "denied", "limitations": ["x"]}}})
    _, data = public_event(entry)
    assert data["sandbox"] == {"provider_id": "bwrap", "enforcement": "full", "network": "denied"}
    assert "stdout" not in data


# --- ACE cleanup -----------------------------------------------------------------


def test_cleanup_lists_only_workspace_sids(monkeypatch, tmp_path, capsys):
    from odessy.execution import acl_cleanup

    calls = []

    def fake(*arguments):
        calls.append(arguments)
        import subprocess

        out = f"{tmp_path} *S-1-4-11-22:(OI)(CI)(M)\n  *S-1-4-33-44-1:(OI)(CI)(M)\n  BUILTIN\\Users:(RX)\n"
        return subprocess.CompletedProcess(arguments, 0, out, "")

    monkeypatch.setattr(acl_cleanup, "_icacls", fake)
    monkeypatch.setattr(acl_cleanup.os, "name", "nt")
    assert acl_cleanup.list_workspace_aces(tmp_path) == ["S-1-4-11-22"]
    assert acl_cleanup.main(tmp_path, apply=False) == 0 and "--apply" in capsys.readouterr().out
    assert acl_cleanup.main(tmp_path, apply=True) == 0
    assert calls[-1][1:3] == ("/remove:g", "*S-1-4-11-22")
