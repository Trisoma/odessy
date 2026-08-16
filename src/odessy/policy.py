from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from odessy.types import PolicyDecision, ToolRequest


@dataclass(frozen=True)
class OdessyPolicy:
    """Deny-by-default v0.1.0 policy gate for model-proposed tool calls."""

    workspace_root: Path
    allowed_tools: set[str] | None = None
    normal_timeout: int = 10
    max_timeout: int = 30

    def evaluate(self, request: ToolRequest) -> PolicyDecision:
        allowed_tools = self.allowed_tools or {"time.now", "terminal.run", "calculator.eval"}
        if request.name not in allowed_tools:
            return PolicyDecision(
                action="deny",
                reason=f"Tool is not allowed: {request.name}",
                risk="high",
            )
        if request.name == "time.now" and request.arguments:
            return PolicyDecision(
                action="deny",
                reason="time.now does not accept arguments",
                risk="low",
            )
        if request.name == "calculator.eval":
            expression = request.arguments.get("expression")
            if set(request.arguments) != {"expression"}:
                return PolicyDecision(
                    action="deny",
                    reason="calculator.eval accepts only an expression argument",
                    risk="low",
                )
            if not isinstance(expression, str) or not expression.strip():
                return PolicyDecision(
                    action="deny",
                    reason="calculator.eval requires a non-empty expression",
                    risk="low",
                )
            return PolicyDecision(
                action="allow",
                reason="calculator.eval allowed as a read-only arithmetic tool",
                risk="low",
            )
        if request.name == "terminal.run":
            return self._evaluate_terminal_run(request)
        return PolicyDecision(
            action="allow",
            reason=f"Tool is allowed: {request.name}",
            risk="low",
        )

    def _evaluate_terminal_run(self, request: ToolRequest) -> PolicyDecision:
        command = request.arguments.get("command")
        cwd = request.arguments.get("cwd", ".")
        timeout = request.arguments.get("timeout", self.normal_timeout)

        if not isinstance(command, str) or not command.strip():
            return PolicyDecision("deny", "terminal.run requires a non-empty command", "high")
        if not isinstance(cwd, str) or not cwd.strip():
            return PolicyDecision("deny", "terminal.run requires a valid cwd", "high")
        if not isinstance(timeout, int):
            return PolicyDecision("deny", "terminal.run timeout must be an integer", "high")
        if timeout < 1 or timeout > self.max_timeout:
            return PolicyDecision(
                "confirm",
                f"terminal.run timeout {timeout} exceeds the normal execution scope",
                "medium",
            )

        cwd_decision = self._check_workspace_cwd(cwd)
        if cwd_decision is not None:
            return cwd_decision

        lowered = command.strip().lower()
        tokens = lowered.replace("\\", "/").split()

        if _matches_deny_rule(lowered, tokens):
            return PolicyDecision(
                "deny",
                "terminal.run denied because the command is destructive or credential-seeking",
                "high",
            )
        if _matches_confirm_rule(lowered, tokens):
            return PolicyDecision(
                "confirm",
                "terminal.run requires confirmation because the command may modify state",
                "medium",
            )
        if _matches_allow_rule(lowered, tokens):
            return PolicyDecision(
                "allow",
                "terminal.run allowed as a read-only workspace command",
                "low",
            )
        return PolicyDecision(
            "confirm",
            "terminal.run requires confirmation because the command is outside the low-risk allow rules",
            "medium",
        )

    def _check_workspace_cwd(self, cwd: str) -> PolicyDecision | None:
        root = self.workspace_root.resolve()
        candidate = Path(cwd)
        if not candidate.is_absolute():
            candidate = root / candidate
        resolved = candidate.resolve()
        if resolved != root and root not in resolved.parents:
            return PolicyDecision(
                "deny",
                "terminal.run denied because cwd is outside the allowed workspace",
                "high",
            )
        return None


def _matches_allow_rule(command: str, tokens: list[str]) -> bool:
    if not tokens:
        return False
    if tokens[0] in {"pwd", "ls", "dir"}:
        return True
    return len(tokens) >= 2 and tokens[:2] == ["git", "status"]


def _matches_confirm_rule(command: str, tokens: list[str]) -> bool:
    if not tokens:
        return False
    write_commands = {
        "touch",
        "mkdir",
        "copy",
        "cp",
        "move",
        "mv",
        "set-content",
        "new-item",
    }
    installers = {
        "pip",
        "npm",
        "pnpm",
        "yarn",
        "uv",
        "cargo",
        "go",
    }
    if ">" in command or ">>" in command:
        return True
    if tokens[0] in installers and "install" in tokens:
        return True
    if tokens[:3] == ["python", "-m", "pip"] and "install" in tokens:
        return True
    if tokens[0] == "git" and not (len(tokens) >= 2 and tokens[:2] == ["git", "status"]):
        return True
    return tokens[0] in write_commands


def _matches_deny_rule(command: str, tokens: list[str]) -> bool:
    if not tokens:
        return True
    secret_terms = {
        ".env",
        ".ssh",
        "id_rsa",
        "credentials",
        "credential",
        "secret",
        "token",
        "password",
        ".aws",
        ".netrc",
    }
    destructive_patterns = {
        "rm -rf",
        "rm -fr",
        "del /s",
        "erase /s",
        "rmdir /s",
        "rd /s",
        "format ",
        "shutdown",
        "reboot",
        "git reset --hard",
        "git clean",
        "remove-item -recurse",
    }
    if any(term in command for term in secret_terms):
        return True
    if any(pattern in command for pattern in destructive_patterns):
        return True
    return False
