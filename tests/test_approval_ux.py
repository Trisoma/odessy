"""Fewer, better confirmations: read-only commands, session allow, tiers, write diffs."""

from __future__ import annotations

from pathlib import Path

import pytest

from odessy.authority.policy import OdessyPolicy
from odessy.runtime.confirmation import TerminalConfirmer, printable, session_key, write_preview
from odessy.runtime.types import PolicyDecision, ToolRequest
from odessy.tools.external_tools import ExternalTool, ServerConfig, external_tool_scope


def _decide(tmp_path: Path, command: str, cwd: str = ".") -> PolicyDecision:
    return OdessyPolicy(workspace_root=tmp_path).evaluate(
        ToolRequest("terminal.run", {"command": command, "cwd": cwd})
    )


# --- read-only commands run unasked ----------------------------------------------------


@pytest.mark.parametrize(
    "command",
    [
        "cat README.md",
        "type notes.txt",
        "Get-Content -Tail 5 src/app.py",
        "head -n 20 a.txt",
        "wc -l a.txt b.txt",
        "which python",
        "where.exe node",
        "Get-Command git",
        "git log --oneline -5",
        "git diff HEAD~1",
        "git show HEAD:README.md",
        "git branch -a",
        "git branch --show-current",
        "git --version",
        "python --version",
        "node --help",
        "echo hello",
    ],
)
def test_read_only_commands_are_allowed(tmp_path: Path, command: str) -> None:
    assert _decide(tmp_path, command).action == "allow"


def test_a_reader_follows_the_working_directory(tmp_path: Path) -> None:
    (tmp_path / "sub").mkdir()
    assert _decide(tmp_path, "cat ../README.md", cwd="sub").action == "allow"
    assert _decide(tmp_path, "cat ../../README.md", cwd="sub").action == "confirm"


@pytest.mark.parametrize(
    "command",
    [
        "cat ../outside.txt",  # outside the workspace
        "cat /etc/hosts",
        "type C:\\Windows\\win.ini",
        "cat ~/.bashrc",  # the shell expands ~
        "cat $HOME/x",
        "cat",  # no file named
        "git branch new-feature",  # creates a branch
        "git branch --delete old",
        "git diff --output=patch.txt",  # writes a file
        "git diff --no-index ../a ../b",  # reads outside the repository
        "git diff --ext-diff",  # runs an external program
        "git -c core.pager=less log",  # the subcommand must come first
        "git commit -m x",
        "echo $env:OPENAI_API_KEY",  # prints a variable
        "echo %PATH%",
        "./build.sh --version",  # a workspace program runs its own code
        "npm version",  # bumps package.json
        "cat a.txt && rm b.txt",
    ],
)
def test_lookalikes_still_need_confirmation(tmp_path: Path, command: str) -> None:
    assert _decide(tmp_path, command).action == "confirm"


@pytest.mark.parametrize("command", ["cat .env", "cat .ssh/id_rsa", "type secrets/token.txt", "rm -rf build"])
def test_denials_are_unchanged(tmp_path: Path, command: str) -> None:
    assert _decide(tmp_path, command).action == "deny"


@pytest.mark.parametrize(
    "command",
    ["pip install requests", "npm install", "python -m pip install x", "uv pip install x", "yarn add lodash", "cargo add serde"],
)
def test_installing_packages_is_high_risk(tmp_path: Path, command: str) -> None:
    decision = _decide(tmp_path, command)
    assert (decision.action, decision.risk) == ("confirm", "high")


def test_ordinary_state_changes_stay_medium(tmp_path: Path) -> None:
    for command in ("mkdir build", "pytest", "npm test", "git commit -m x"):
        decision = _decide(tmp_path, command)
        assert (decision.action, decision.risk) == ("confirm", "medium"), command


# --- the confirmer ---------------------------------------------------------------------


class _Terminal:
    def __init__(self, *answers: str) -> None:
        self.answers = list(answers)
        self.prompts: list[str] = []
        self.lines: list[str] = []

    def ask(self, prompt: str) -> str:
        self.prompts.append(prompt)
        if not self.answers:
            raise AssertionError("asked more often than expected")
        return self.answers.pop(0)

    def confirmer(self, root: Path) -> TerminalConfirmer:
        return TerminalConfirmer(root, ask=self.ask, say=self.lines.append)


MEDIUM = PolicyDecision("confirm", "may modify state", "medium")
HIGH = PolicyDecision("confirm", "installs packages", "high")


def _command(text: str, cwd: str = ".") -> ToolRequest:
    return ToolRequest("terminal.run", {"command": text, "cwd": cwd})


def test_always_allows_the_same_command_until_exit(tmp_path: Path) -> None:
    terminal = _Terminal("a")
    confirm = terminal.confirmer(tmp_path)
    assert confirm(MEDIUM, _command("npm test"))
    assert confirm(MEDIUM, _command("npm   test"))  # whitespace does not matter
    assert len(terminal.prompts) == 1
    assert "allowed for this session" in terminal.lines[-1]


def test_always_covers_only_that_exact_command(tmp_path: Path) -> None:
    terminal = _Terminal("a", "n", "n")
    confirm = terminal.confirmer(tmp_path)
    assert confirm(MEDIUM, _command("npm test"))
    assert not confirm(MEDIUM, _command("npm test -- --watch"))
    assert not confirm(MEDIUM, _command("npm test", cwd="sub"))
    assert len(terminal.prompts) == 3


def test_a_new_confirmer_remembers_nothing(tmp_path: Path) -> None:
    _Terminal("a").confirmer(tmp_path)(MEDIUM, _command("npm test"))
    terminal = _Terminal("n")
    assert not terminal.confirmer(tmp_path)(MEDIUM, _command("npm test"))


def test_high_risk_needs_the_typed_word_and_offers_no_shortcut(tmp_path: Path) -> None:
    terminal = _Terminal("y", "a", "yes", "yes")
    confirm = terminal.confirmer(tmp_path)
    request = _command("pip install requests")
    assert not confirm(HIGH, request)
    assert not confirm(HIGH, request)  # "a" is not an answer here
    assert confirm(HIGH, request)
    assert confirm(HIGH, request)  # still asked: nothing was remembered
    assert len(terminal.prompts) == 4
    assert all("[a]" not in prompt and "'yes'" in prompt for prompt in terminal.prompts)
    assert terminal.lines[0].startswith("!! HIGH RISK")


def test_no_terminal_means_no(tmp_path: Path) -> None:
    def closed(prompt: str) -> str:
        raise EOFError

    assert not TerminalConfirmer(tmp_path, ask=closed, say=lambda line: None)(MEDIUM, _command("npm test"))


def _external(destructive: bool = False) -> ExternalTool:
    server = ServerConfig(name="notes", transport="http", url="http://127.0.0.1:9/mcp")
    return ExternalTool("notes.search", "search", server, "d", {"type": "object"}, destructive=destructive)


def test_always_for_a_third_party_tool_covers_all_its_calls(tmp_path: Path) -> None:
    terminal = _Terminal("a")
    with external_tool_scope([_external()]):
        confirm = terminal.confirmer(tmp_path)
        assert confirm(MEDIUM, ToolRequest("notes.search", {"q": "milk"}))
        assert confirm(MEDIUM, ToolRequest("notes.search", {"q": "eggs"}))
    assert len(terminal.prompts) == 1
    assert "every notes.search call" in terminal.prompts[0]


def test_writes_are_never_allowed_for_the_session(tmp_path: Path) -> None:
    write = ToolRequest("filesystem.write", {"path": "a.txt", "content": "x"})
    assert session_key(write) is None
    terminal = _Terminal("y", "y")
    confirm = terminal.confirmer(tmp_path)
    assert confirm(MEDIUM, write) and confirm(MEDIUM, write)
    assert all("[a]" not in prompt for prompt in terminal.prompts)


# --- write previews --------------------------------------------------------------------


def test_a_new_file_shows_its_lines(tmp_path: Path) -> None:
    lines = write_preview(tmp_path, "notes.md", "one\ntwo\n")
    assert lines[0] == "creates notes.md: 2 line(s), 8 bytes"
    assert lines[1:] == ["  +one", "  +two"]


def test_a_changed_file_shows_a_diff(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("x = 1\ny = 2\nz = 3\n", encoding="utf-8")
    lines = write_preview(tmp_path, "a.py", "x = 1\ny = 20\nz = 3\n")
    assert lines[0] == "changes a.py: +1 -1 line(s)"
    assert "  -y = 2" in lines and "  +y = 20" in lines


def test_an_identical_write_says_so(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("same", encoding="utf-8")
    assert write_preview(tmp_path, "a.txt", "same") == ["a.txt: no change (the content is identical)"]


def test_a_long_preview_is_cut_short(tmp_path: Path) -> None:
    lines = write_preview(tmp_path, "big.txt", "\n".join(str(i) for i in range(100)), max_lines=10)
    assert len(lines) == 12 and lines[-1] == "  ... 90 more line(s)"


def test_a_binary_file_is_summarized(tmp_path: Path) -> None:
    (tmp_path / "img.bin").write_bytes(b"\xff\xfe\x00\x01")
    (line,) = write_preview(tmp_path, "img.bin", "text")
    assert "not UTF-8 text" in line


def test_escape_sequences_cannot_hide_lines(tmp_path: Path) -> None:
    lines = write_preview(tmp_path, "a.txt", "safe\x1b[2K\x1b[1Ahidden\u202e")
    assert "\x1b" not in "".join(lines) and "\u202e" not in "".join(lines)
    assert "\\x1b[2K" in lines[1]
    assert printable("tab\there") == "tab\there"


def test_the_confirmer_shows_the_diff_for_a_write(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("old\n", encoding="utf-8")
    terminal = _Terminal("n")
    terminal.confirmer(tmp_path)(MEDIUM, ToolRequest("filesystem.write", {"path": "a.txt", "content": "new\n"}))
    assert "changes a.txt: +1 -1 line(s)" in terminal.lines
    assert not any(line.startswith("Arguments:") for line in terminal.lines)
