"""Second batch of fewer confirmations: commands approved in advance, batch prompts."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from mcp.server import MCPServer

import odessy.cli as cli
from odessy.authority.policy import OdessyPolicy
from odessy.config.commands import (
    CommandsConfigError,
    load_preapproved_commands,
    matches_preapproved,
)
from odessy.models.fake_model import FakeModel
from odessy.runtime.agent_loop import ExecutionTarget, OdessyAgent
from odessy.runtime.confirmation import TerminalConfirmer
from odessy.runtime.types import AgentTurnMetrics, FinalResponse, PolicyDecision, ToolBatch, ToolCall, ToolRequest
from odessy.tools.external_tools import NamespacedServer, ServerConfig, discover_tools, external_tool_scope


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _commands_file(tmp_path: Path, workspace: Path, *commands: str) -> Path:
    path = tmp_path / "commands.toml"
    listed = ", ".join(f"'{command}'" for command in commands)
    path.write_text(f"[[workspaces]]\npath = '{workspace.as_posix()}'\ncommands = [{listed}]\n", encoding="utf-8")
    return path


# --- commands.toml ---------------------------------------------------------------------


def test_no_commands_file_approves_nothing(tmp_path: Path) -> None:
    assert load_preapproved_commands(tmp_path, tmp_path / "missing.toml") == ()


def test_only_this_workspaces_commands_apply(tmp_path: Path) -> None:
    here, there = tmp_path / "here", tmp_path / "there"
    here.mkdir(), there.mkdir()
    path = tmp_path / "commands.toml"
    path.write_text(
        f"[[workspaces]]\npath = '{here.as_posix()}'\ncommands = ['pytest', 'npm test']\n"
        f"[[workspaces]]\npath = '{there.as_posix()}'\ncommands = ['make build']\n",
        encoding="utf-8",
    )
    assert load_preapproved_commands(here, path) == (("pytest",), ("npm", "test"))
    assert load_preapproved_commands(there, path) == (("make", "build"),)


def test_the_file_location_comes_from_the_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ODESSY_COMMANDS_FILE", str(_commands_file(tmp_path, tmp_path, "pytest")))
    assert load_preapproved_commands(tmp_path) == (("pytest",),)


@pytest.mark.parametrize(
    ("command", "problem"),
    [
        ("pytest && rm x", "shell syntax"),
        ("npm test > out.txt", "shell syntax"),
        ("echo $HOME", "shell syntax"),
        ("python", "alone would approve"),
        ("npx", "alone would approve"),
        ("", "empty"),
    ],
)
def test_broad_or_compound_entries_are_refused(tmp_path: Path, command: str, problem: str) -> None:
    with pytest.raises(CommandsConfigError, match=problem):
        load_preapproved_commands(tmp_path, _commands_file(tmp_path, tmp_path, command))


def test_a_malformed_file_names_the_problem(tmp_path: Path) -> None:
    path = tmp_path / "commands.toml"
    path.write_text("[[workspaces]]\ncommands = ['pytest']\n", encoding="utf-8")
    with pytest.raises(CommandsConfigError, match="path"):
        load_preapproved_commands(tmp_path, path)
    path.write_text("allow = ['pytest']\n", encoding="utf-8")
    with pytest.raises(CommandsConfigError, match="unknown keys"):
        load_preapproved_commands(tmp_path, path)


def test_matching_is_by_whole_words_and_refuses_shell_syntax() -> None:
    approved = (("npm", "test"), ("pytest",))
    assert matches_preapproved("npm test", approved)
    assert matches_preapproved("NPM   test -- --watch", approved)
    assert matches_preapproved("pytest -q tests/test_x.py", approved)
    assert not matches_preapproved("npm testx", approved)
    assert not matches_preapproved("npm run test", approved)
    assert not matches_preapproved("pytest; rm -rf x", approved)
    assert not matches_preapproved("pytest | tee log", approved)


# --- the policy ------------------------------------------------------------------------


def _policy(tmp_path: Path, *commands: str) -> OdessyPolicy:
    approved = tuple(tuple(command.split()) for command in commands)
    return OdessyPolicy(workspace_root=tmp_path, preapproved_commands=approved)


def _run(command: str, name: str = "terminal.run", **extra: Any) -> ToolRequest:
    return ToolRequest(name, {"command": command, "cwd": ".", **extra})


def test_an_approved_command_skips_the_question_but_keeps_write_mode(tmp_path: Path) -> None:
    policy = _policy(tmp_path, "pytest")
    request = _run("pytest -q")
    decision = policy.evaluate(request)
    assert (decision.action, decision.preapproved) == ("confirm", True)
    assert policy.requirement_for(request, decision).policy.mode == "workspace-write"


def test_a_read_only_command_stays_read_only_even_if_listed(tmp_path: Path) -> None:
    decision = _policy(tmp_path, "git log").evaluate(_run("git log -3"))
    assert (decision.action, decision.preapproved) == ("allow", False)


def test_listing_never_overrides_what_is_checked_first(tmp_path: Path) -> None:
    policy = _policy(tmp_path, "npm install", "rm -rf", "cat ../secret", "pytest")
    installer = policy.evaluate(_run("npm install left-pad"))
    assert (installer.action, installer.risk, installer.preapproved) == ("confirm", "high", False)
    assert policy.evaluate(_run("rm -rf build")).action == "deny"
    assert policy.evaluate(_run("pytest", cwd="../elsewhere")).action == "deny"
    long_run = policy.evaluate(_run("pytest", timeout=600))
    assert long_run.action == "confirm" and not long_run.preapproved


def test_the_laptop_command_is_never_preapproved(tmp_path: Path) -> None:
    decision = _policy(tmp_path, "pytest").evaluate(_run("pytest", name="laptop.terminal.run"))
    assert decision.action == "confirm" and not decision.preapproved


@pytest.mark.anyio
async def test_the_agent_runs_an_approved_command_without_asking(tmp_path: Path) -> None:
    def never(decision: Any, request: Any) -> bool:
        raise AssertionError("asked")

    agent = OdessyAgent(model=FakeModel(), policy=_policy(tmp_path, "pytest"), mcp_server=(), confirm=never)
    log: list[Any] = []
    arguments = await agent._prepare_call(_run("pytest -q"), {}, log, AgentTurnMetrics())
    assert arguments["mode"] == "workspace-write"
    assert [entry.event for entry in log][:2] == ["policy_requires_confirmation", "policy_preapproved"]


def test_the_cli_loads_this_workspaces_commands(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ODESSY_COMMANDS_FILE", str(_commands_file(tmp_path, tmp_path, "pytest")))
    agent = cli._build_agent(tmp_path, FakeModel())
    assert agent._policy.preapproved_commands == (("pytest",),)


def test_the_cli_stops_on_a_broken_commands_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ODESSY_COMMANDS_FILE", str(_commands_file(tmp_path, tmp_path, "python")))
    with pytest.raises(SystemExit, match="alone would approve"):
        cli._build_agent(tmp_path, FakeModel())


# --- the batch prompt ------------------------------------------------------------------


class _Terminal:
    def __init__(self, *answers: str) -> None:
        self.answers = list(answers)
        self.prompts: list[str] = []
        self.lines: list[str] = []

    def ask(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.answers.pop(0)

    def confirmer(self, root: Path) -> TerminalConfirmer:
        return TerminalConfirmer(root, ask=self.ask, say=self.lines.append)


MEDIUM = PolicyDecision("confirm", "may modify state", "medium")
HIGH = PolicyDecision("confirm", "installs packages", "high")


def _items(*requests: tuple[PolicyDecision, ToolRequest]) -> list[tuple[PolicyDecision, ToolRequest]]:
    return list(requests)


@pytest.mark.parametrize(("answer", "expected"), [("y", True), ("all", True), ("n", False), ("", False)])
def test_one_answer_covers_the_listed_calls(tmp_path: Path, answer: str, expected: bool) -> None:
    terminal = _Terminal(answer)
    replies = terminal.confirmer(tmp_path).batch(_items((MEDIUM, _run("mkdir docs")), (MEDIUM, _run("git add docs"))))
    assert replies == [expected, expected]
    assert len(terminal.prompts) == 1
    assert "2 tool calls" in terminal.lines[0]


def test_one_by_one_defers_every_call(tmp_path: Path) -> None:
    terminal = _Terminal("s")
    assert terminal.confirmer(tmp_path).batch(_items((MEDIUM, _run("a b")), (MEDIUM, _run("c d")))) == [None, None]


def test_high_risk_is_listed_but_asked_on_its_own(tmp_path: Path) -> None:
    terminal = _Terminal("y")
    replies = terminal.confirmer(tmp_path).batch(
        _items((MEDIUM, _run("mkdir a")), (HIGH, _run("pip install x")), (MEDIUM, _run("mkdir b")))
    )
    assert replies == [True, None, True]
    assert any("HIGH RISK, asked separately" in line for line in terminal.lines)


def test_a_batch_with_fewer_than_two_listable_calls_is_not_asked(tmp_path: Path) -> None:
    terminal = _Terminal()
    assert terminal.confirmer(tmp_path).batch(_items((MEDIUM, _run("mkdir a")), (HIGH, _run("pip install x")))) == [None, None]
    assert terminal.prompts == []


def test_session_allowed_calls_are_answered_without_listing(tmp_path: Path) -> None:
    terminal = _Terminal("a", "y")
    confirmer = terminal.confirmer(tmp_path)
    assert confirmer(MEDIUM, _run("npm test"))
    replies = confirmer.batch(_items((MEDIUM, _run("npm test")), (MEDIUM, _run("mkdir a")), (MEDIUM, _run("mkdir b"))))
    assert replies == [True, True, True]
    assert any("(allowed for this session)" in line for line in terminal.lines)


def test_writes_in_a_batch_show_their_diff(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("old\n", encoding="utf-8")
    terminal = _Terminal("n")
    write = ToolRequest("filesystem.write", {"path": "a.txt", "content": "new\n"})
    terminal.confirmer(tmp_path).batch(_items((MEDIUM, write), (MEDIUM, _run("mkdir b"))))
    assert any("changes a.txt: +1 -1 line(s)" in line for line in terminal.lines)


def test_no_terminal_declines_the_batch(tmp_path: Path) -> None:
    def closed(prompt: str) -> str:
        raise EOFError

    confirmer = TerminalConfirmer(tmp_path, ask=closed, say=lambda line: None)
    assert confirmer.batch(_items((MEDIUM, _run("mkdir a")), (MEDIUM, _run("mkdir b")))) == [False, False]


# --- the agent asks once for a batch ---------------------------------------------------


def _notes() -> MCPServer:
    server = MCPServer("notes")
    calls: list[str] = []
    server.calls = calls  # type: ignore[attr-defined]

    def add(title: str) -> str:
        calls.append(title)
        return "added"

    server.add_tool(add, name="add", structured_output=False)
    return server


class _InProcess(NamespacedServer):
    def __init__(self, config: ServerConfig, server: MCPServer) -> None:
        object.__setattr__(self, "config", config)
        object.__setattr__(self, "_server", server)

    def connection(self) -> Any:
        return self._server


class _BatchModel:
    def __init__(self, batch: ToolBatch) -> None:
        self.batch = batch
        self.observations: list[Any] = []

    def propose(self, user_text: str) -> Any:
        return self.batch

    def respond_to_observation(self, user_text: str, observation: Any) -> Any:
        return FinalResponse(text="done")

    def respond_to_observations(self, user_text: str, observations: list[Any]) -> Any:
        self.observations.extend(observations)
        return FinalResponse(text="done")


async def _batch_turn(tmp_path: Path, batch_answers: list[bool | None], single: bool) -> tuple[MCPServer, list[Any], list[str]]:
    backend = _notes()
    config = ServerConfig(name="notes", transport="http", url="http://127.0.0.1:9/mcp")
    namespaced = _InProcess(config, backend)

    async def lister(server: ServerConfig) -> list[Any]:
        from mcp import Client

        async with Client(backend) as client:
            return list((await client.list_tools()).tools)

    tools, _, _ = await discover_tools([config], limit=5, list_tools=lister)
    asked: list[str] = []
    batches: list[int] = []
    model = _BatchModel(
        ToolBatch(
            (
                ToolCall("c1", ToolRequest("notes.add", {"title": "one"})),
                ToolCall("c2", ToolRequest("notes.add", {"title": "two"})),
            )
        )
    )
    with external_tool_scope(tools):
        agent = OdessyAgent(
            model=model,
            policy=OdessyPolicy(workspace_root=tmp_path),
            mcp_server=(ExecutionTarget(namespaced),),
            confirm=lambda decision, request: asked.append(request.arguments["title"]) or single,
            confirm_batch=lambda items: batches.append(len(items)) or list(batch_answers),
        )
        await agent.run_turn("add two notes")
    return backend, batches, asked


@pytest.mark.anyio
async def test_a_batch_is_asked_about_once(tmp_path: Path) -> None:
    backend, batches, asked = await _batch_turn(tmp_path, [True, True], single=False)
    assert batches == [2] and asked == []
    assert backend.calls == ["one", "two"]


@pytest.mark.anyio
async def test_a_declined_batch_runs_nothing(tmp_path: Path) -> None:
    backend, batches, asked = await _batch_turn(tmp_path, [False, False], single=True)
    assert batches == [2] and asked == [] and backend.calls == []


@pytest.mark.anyio
async def test_deferred_answers_are_asked_one_by_one(tmp_path: Path) -> None:
    backend, batches, asked = await _batch_turn(tmp_path, [None, None], single=True)
    assert batches == [2] and asked == ["one", "two"]
    assert backend.calls == ["one", "two"]


# --- doctor ----------------------------------------------------------------------------


def test_doctor_shows_this_folders_approved_commands(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from odessy.config.doctor import _commands_checks

    monkeypatch.chdir(tmp_path)
    env = {"ODESSY_COMMANDS_FILE": str(_commands_file(tmp_path, tmp_path, "pytest", "npm test"))}
    (check,) = _commands_checks(env)
    assert check.status == "ok" and "pytest, npm test" in check.summary
    assert _commands_checks({"ODESSY_COMMANDS_FILE": str(tmp_path / "missing.toml")}) == []


def test_doctor_fails_a_broken_commands_file(tmp_path: Path) -> None:
    from odessy.config.doctor import _commands_checks

    (check,) = _commands_checks({"ODESSY_COMMANDS_FILE": str(_commands_file(tmp_path, tmp_path, "npx"))})
    assert check.status == "fail" and "alone would approve" in check.summary

def test_doctor_identifies_local_only_preapproval_scope(tmp_path, monkeypatch):
    from odessy.config.doctor import _commands_checks
    monkeypatch.chdir(tmp_path)
    path = _commands_file(tmp_path, tmp_path, 'pytest')
    (check,) = _commands_checks({'ODESSY_COMMANDS_FILE': str(path)})
    assert 'local terminal.run only' in check.summary
    assert 'laptop.terminal.run still requires confirmation' in check.summary
