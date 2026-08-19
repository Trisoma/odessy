from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from odessy.types import PolicyDecision, ToolRequest
from odessy.workspace_authority import WorkspaceAuthority, WorkspaceAuthorityError


@dataclass(frozen=True)
class OdessyPolicy:
    """Deny-by-default v0.1.0 policy gate for model-proposed tool calls."""

    workspace_root: Path
    allowed_tools: set[str] | None = None
    normal_timeout: int = 10
    max_timeout: int = 30

    @property
    def authority(self) -> WorkspaceAuthority:
        return WorkspaceAuthority(Path(self.workspace_root))

    def evaluate(self, request: ToolRequest) -> PolicyDecision:
        allowed_tools = self.allowed_tools or {
            "time.now",
            "terminal.run",
            "laptop.terminal.run",
            "calculator.eval",
            "filesystem.read",
            "filesystem.list",
            "filesystem.write",
            "log.append",
            "project.find",
            "project.grep",
            "git.status",
            "git.diff",
            "git.changed_files",
            "git.log",
        }
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
        if request.name in _READ_ONLY_TOOL_NAMES:
            return self._evaluate_read_only_tool(request)
        if request.name in {"terminal.run", "laptop.terminal.run"}:
            return self._evaluate_terminal_run(request)
        if request.name == "filesystem.write":
            return self._evaluate_filesystem_write(request)
        if request.name == "log.append":
            return self._evaluate_log_append(request)
        return PolicyDecision(
            action="allow",
            reason=f"Tool is allowed: {request.name}",
            risk="low",
        )

    def _evaluate_terminal_run(self, request: ToolRequest) -> PolicyDecision:
        tool_name = request.name
        command = request.arguments.get("command")
        cwd = request.arguments.get("cwd", ".")
        timeout = request.arguments.get("timeout", self.normal_timeout)

        if not isinstance(command, str) or not command.strip():
            return PolicyDecision("deny", f"{tool_name} requires a non-empty command", "high")
        if not isinstance(cwd, str) or not cwd.strip():
            return PolicyDecision("deny", f"{tool_name} requires a valid cwd", "high")
        if not isinstance(timeout, int):
            return PolicyDecision("deny", f"{tool_name} timeout must be an integer", "high")
        if timeout < 1:
            return PolicyDecision(
                "deny",
                f"{tool_name} timeout must be between 1 and the maximum execution scope",
                "high",
            )
        if timeout > self.max_timeout:
            return PolicyDecision(
                "confirm",
                f"{tool_name} timeout {timeout} exceeds the normal execution scope",
                "medium",
            )

        cwd_decision = self._check_workspace_cwd(cwd, tool_name)
        if cwd_decision is not None:
            return cwd_decision

        lowered = command.strip().lower()
        tokens = lowered.replace("\\", "/").split()

        if _matches_background_or_detach_rule(lowered, tokens):
            return PolicyDecision(
                "deny",
                f"{tool_name} denied because background or detached execution is outside the request lifecycle",
                "high",
            )
        if _matches_deny_rule(lowered, tokens):
            return PolicyDecision(
                "deny",
                f"{tool_name} denied because the command is destructive or credential-seeking",
                "high",
            )
        if _matches_confirm_rule(lowered, tokens):
            return PolicyDecision(
                "confirm",
                f"{tool_name} requires confirmation because the command may modify state",
                "medium",
            )
        if _matches_allow_rule(lowered, tokens):
            return PolicyDecision(
                "allow",
                f"{tool_name} allowed as a read-only workspace command",
                "low",
            )
        return PolicyDecision(
            "confirm",
            f"{tool_name} requires confirmation because the command is outside the low-risk allow rules",
            "medium",
        )

    def _check_workspace_cwd(self, cwd: str, tool_name: str) -> PolicyDecision | None:
        try:
            self.authority.resolve_cwd(cwd)
        except WorkspaceAuthorityError:
            return PolicyDecision(
                "deny",
                f"{tool_name} denied because cwd is outside the allowed workspace",
                "high",
            )
        return None

    def _evaluate_read_only_tool(self, request: ToolRequest) -> PolicyDecision:
        for key, value in request.arguments.items():
            if key in {"path", "file", "cwd"} and isinstance(value, str):
                path_decision = self._check_workspace_path(value, request.name)
                if path_decision is not None:
                    return path_decision
        return PolicyDecision(
            "allow",
            f"{request.name} allowed as a read-only workspace tool",
            "low",
        )

    def _check_workspace_path(self, candidate_path: str, tool_name: str) -> PolicyDecision | None:
        try:
            resolved = self.authority.resolve_path(candidate_path, must_exist=False)
        except WorkspaceAuthorityError:
            return PolicyDecision(
                "deny",
                f"{tool_name} denied because path is outside the allowed workspace",
                "high",
            )
        if resolved.sensitive:
            return PolicyDecision(
                "deny",
                f"{tool_name} denied because the path targets credentials or secrets",
                "high",
            )
        return None

    def _evaluate_filesystem_write(self, request: ToolRequest) -> PolicyDecision:
        if set(request.arguments) != {"path", "content"}:
            return PolicyDecision(
                "deny",
                "filesystem.write accepts only path and content arguments",
                "high",
            )
        path_value = request.arguments.get("path")
        content = request.arguments.get("content")
        if not isinstance(path_value, str) or not path_value.strip():
            return PolicyDecision("deny", "filesystem.write requires a valid path", "high")
        if not isinstance(content, str):
            return PolicyDecision("deny", "filesystem.write requires string content", "high")

        path_decision = self._check_workspace_path(path_value, "filesystem.write")
        if path_decision is not None:
            return path_decision

        return PolicyDecision(
            "confirm",
            "filesystem.write modifies the workspace and requires confirmation",
            "medium",
        )

    def _evaluate_log_append(self, request: ToolRequest) -> PolicyDecision:
        if set(request.arguments) != {"content"}:
            return PolicyDecision(
                "deny",
                "log.append accepts only a content argument",
                "high",
            )
        content = request.arguments.get("content")
        if not isinstance(content, str) or not content.strip():
            return PolicyDecision("deny", "log.append requires non-empty string content", "high")
        return PolicyDecision(
            "allow",
            "log.append allowed because its destination is fixed and internal",
            "low",
        )


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


def _matches_background_or_detach_rule(command: str, tokens: list[str]) -> bool:
    if not tokens:
        return True
    detached_terms = {
        "disown",
        "nohup",
        "schtasks",
        "setsid",
        "start-job",
        "start-process",
    }
    if tokens[0] in detached_terms:
        return True
    padded = f" {command} "
    if tokens[:2] in (["cmd", "/c"], ["cmd.exe", "/c"]) and " start " in padded:
        return True
    if " --detach" in command or " -d " in padded:
        return True
    if command.endswith(" &") or " & " in command or " 2>&1 &" in command:
        return True
    return False


_READ_ONLY_TOOL_NAMES = {
    "filesystem.read",
    "filesystem.list",
    "project.find",
    "project.grep",
    "git.status",
    "git.diff",
    "git.changed_files",
    "git.log",
}


def _looks_like_secret_path(value: str) -> bool:
    normalized = value.replace("\\", "/").lower()
    secret_terms = {
        ".aws",
        ".env",
        ".git-credentials",
        ".gnupg",
        ".kube",
        ".netrc",
        ".npmrc",
        ".pypirc",
        ".ssh",
        "credentials.json",
        "id_rsa",
        "id_ed25519",
        "secrets.json",
        "token.json",
    }
    return any(term in normalized for term in secret_terms)
