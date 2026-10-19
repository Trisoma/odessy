from __future__ import annotations

import os
from pathlib import Path

import pytest

from odessy.agent_loop import OdessyAgent
from odessy.command_bridge import CommandBridge, build_sanitized_environment
from odessy.fake_model import FakeModel
from odessy.policy import OdessyPolicy
from odessy.types import ToolRequest
from odessy.workspace_authority import WorkspaceAuthority, WorkspaceAuthorityError


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def test_workspace_authority_allows_relative_and_absolute_inside_paths(tmp_path: Path) -> None:
    source = tmp_path / "src" / "main.py"
    source.parent.mkdir()
    source.write_text("print('ok')\n", encoding="utf-8")
    authority = WorkspaceAuthority(tmp_path)

    assert authority.resolve_path("src/main.py", must_exist=True).relative == "src/main.py"
    assert authority.resolve_path(str(source), must_exist=True).relative == "src/main.py"


def test_workspace_authority_denies_outside_and_sibling_prefix_paths(tmp_path: Path) -> None:
    sibling = tmp_path.parent / f"{tmp_path.name}_sibling"
    sibling.mkdir(exist_ok=True)
    authority = WorkspaceAuthority(tmp_path)

    with pytest.raises(WorkspaceAuthorityError):
        authority.resolve_path("../outside.txt")
    with pytest.raises(WorkspaceAuthorityError):
        authority.resolve_path(str(sibling))


def test_workspace_authority_validates_nonexistent_target_by_existing_ancestor(
    tmp_path: Path,
) -> None:
    authority = WorkspaceAuthority(tmp_path)

    resolved = authority.resolve_path("new/note.txt", must_exist=False)

    assert resolved.relative == "new/note.txt"
    assert resolved.exists is False


def test_workspace_authority_denies_symlink_escape_when_supported(tmp_path: Path) -> None:
    outside = tmp_path.parent / f"{tmp_path.name}_outside"
    outside.mkdir(exist_ok=True)
    (outside / "secret.txt").write_text("secret", encoding="utf-8")
    link = tmp_path / "link"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks are not available in this environment")

    authority = WorkspaceAuthority(tmp_path)

    with pytest.raises(WorkspaceAuthorityError):
        authority.resolve_path("link/secret.txt", must_exist=True)


@pytest.mark.parametrize(
    "path",
    [
        ".env",
        ".env.local",
        "id_rsa",
        "deploy.pem",
        "private.key",
        "credentials.json",
        "api-token.txt",
        ".ssh/config",
        ".aws/credentials",
    ],
)
def test_policy_denies_sensitive_workspace_paths(tmp_path: Path, path: str) -> None:
    policy = OdessyPolicy(workspace_root=tmp_path)

    decision = policy.evaluate(ToolRequest("filesystem.read", {"path": path}))

    assert decision.action == "deny"
    assert "credentials or secrets" in decision.reason


def test_policy_allows_normal_workspace_source_path(tmp_path: Path) -> None:
    policy = OdessyPolicy(workspace_root=tmp_path)

    decision = policy.evaluate(ToolRequest("filesystem.read", {"path": "src/main.py"}))

    assert decision.action == "allow"


def test_policy_denies_background_or_detached_terminal_requests(tmp_path: Path) -> None:
    policy = OdessyPolicy(workspace_root=tmp_path)

    for command in ["nohup python server.py &", "Start-Process notepad", "cmd /c start calc"]:
        decision = policy.evaluate(
            ToolRequest("terminal.run", {"command": command, "cwd": ".", "timeout": 10})
        )
        assert decision.action == "deny"
        assert "background or detached" in decision.reason


def test_command_bridge_uses_sanitized_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The boundary is a pure function, so assert it directly.

    This used to spawn a shell and grep its output. That made a security invariant
    depend on how fast ``powershell.exe`` starts: on a fresh Windows runner the first
    start re-analyzes every installed module -- about 25 s, per the note in
    ``command_bridge`` -- against a 10 s budget. The test then reported a timeout on a
    job where sanitization was working perfectly, and passed on a warm one.
    """

    monkeypatch.setenv("ODESSY_TEST_SECRET", "secret-value")
    monkeypatch.setenv("PATH", os.environ.get("PATH", "/usr/bin"))

    env = build_sanitized_environment(os.environ)

    assert "ODESSY_TEST_SECRET" not in env
    assert "secret-value" not in env.values()
    # The allowlisted names a child process needs to run at all.
    assert env.get("PATH") == os.environ["PATH"]
    for name in ("SYSTEMROOT", "COMSPEC", "PATHEXT"):
        if os.environ.get(name):
            assert env.get(name) == os.environ[name]


def test_command_bridge_passes_the_sanitized_environment_to_the_child(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """What the bridge spawns with, without spawning anything.

    ``Popen`` does not inherit when ``env=`` is given, so the dict recorded here is
    exactly the child's complete environment. That is a stronger check than reading a
    shell's rendering of it, and it costs no process start.
    """

    spawned: list[dict[str, object]] = []

    class _RecordingPopen:
        def __init__(self, argv: list[str], **kwargs: object) -> None:
            self.argv = argv
            self.returncode = 0
            self.pid = 0
            spawned.append(kwargs)

        def communicate(self, timeout: float | None = None) -> tuple[str, str]:
            return "", ""

    monkeypatch.setenv("ODESSY_TEST_SECRET", "secret-value")
    monkeypatch.setattr("odessy.command_bridge.subprocess.Popen", _RecordingPopen)

    result = CommandBridge(tmp_path).run(command="any command", timeout=10)

    assert result["status"] == "ok"
    child_env = spawned[0]["env"]
    assert isinstance(child_env, dict)
    assert "ODESSY_TEST_SECRET" not in child_env
    assert "secret-value" not in child_env.values()


def test_command_bridge_runs_with_the_sanitized_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One real end-to-end run, on a budget above a cold PowerShell start.

    ``max_timeout`` (30 s) is deliberately the bridge's own ceiling rather than a
    smaller number: the point of this test is that a sanitized environment still runs
    a command, not that the shell starts within an arbitrary budget.
    """

    monkeypatch.setenv("ODESSY_TEST_SECRET", "secret-value")
    bridge = CommandBridge(tmp_path)
    command = "Get-ChildItem Env:" if os.name == "nt" else "env"

    result = bridge.run(command=command, timeout=bridge.max_timeout)

    assert result["status"] == "ok"
    assert "PATH" in result["stdout"]
    assert "ODESSY_TEST_SECRET" not in result["stdout"]
    assert "secret-value" not in result["stdout"]


def test_command_bridge_bounds_stdout(tmp_path: Path) -> None:
    bridge = CommandBridge(tmp_path, max_stdout_chars=100)
    command = "python -c \"print('x' * 1000)\""

    result = bridge.run(command=command, timeout=10)

    assert result["stdout_truncated"] is True
    # Still bounded, but no longer by the old head-only cut. The inline preview
    # keeps both ends and carries a spill locator plus an exact omission count,
    # so the inline size is deliberately larger than max_stdout_chars. Asserting
    # a bound well below the original is the invariant that matters; the old
    # "< 200" encoded the previous mechanism rather than the requirement.
    assert len(result["stdout"]) < 400
    # The withheld remainder is recoverable instead of discarded.
    assert result["stdout_spill"] is not None
    assert result["stdout_spill"]["byte_count"] > 900


def test_command_bridge_times_out_request_scoped_process(tmp_path: Path) -> None:
    """The budget cuts off a running command, not a slow shell start.

    With a 1 s budget this passed whenever ``powershell.exe`` alone took longer than
    1 s to start, in which case nothing about the process lifecycle was exercised. The
    child here records that it ran and then blocks, so the marker can only exist if
    the process really started and was then killed.
    """

    marker = tmp_path / "child-started.txt"
    script = f"open(r'{marker}', 'w').write('up'); import time; time.sleep(30)"
    bridge = CommandBridge(tmp_path)

    result = bridge.run(command=f'python -c "{script}"', timeout=5)

    assert result["status"] == "timeout"
    assert result["timed_out"] is True
    assert result["exit_code"] is None
    assert marker.exists(), "the child never ran: this timed out on shell startup"


class _UnknownToolModel:
    def propose(self, user_text: str) -> ToolRequest:
        return ToolRequest("unknown.tool", {})

    def respond_to_observation(self, user_text, observation):  # pragma: no cover
        raise AssertionError("denied requests must not reach observation handling")


@pytest.mark.anyio
async def test_policy_denial_does_not_contact_mcp(tmp_path: Path) -> None:
    agent = OdessyAgent(
        model=_UnknownToolModel(),
        policy=OdessyPolicy(workspace_root=tmp_path),
        mcp_server=object(),
    )

    with pytest.raises(PermissionError, match="not allowed"):
        await agent.run_turn("try unknown")


@pytest.mark.anyio
async def test_policy_denies_sensitive_request_before_mcp(tmp_path: Path) -> None:
    agent = OdessyAgent(
        model=FakeModel(),
        policy=OdessyPolicy(workspace_root=tmp_path),
        mcp_server=object(),
    )

    with pytest.raises(PermissionError, match="credentials or secrets"):
        await agent.run_turn("read .env")


def test_policy_allows_laptop_terminal_run_for_safe_command(tmp_path: Path) -> None:
    policy = OdessyPolicy(workspace_root=tmp_path)

    decision = policy.evaluate(
        ToolRequest("laptop.terminal.run", {"command": "pwd"})
    )

    assert decision.action == "allow"
