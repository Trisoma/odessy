from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from odessy.execution.contract import (
    ESCALATION_TARGETS,
    ExecutionPolicy,
    IsolationRequirement,
    Mode,
    Requirement,
)
from odessy.execution.escalation import (
    EscalationApproval,
    EscalationArgumentError,
    EscalationDeniedError,
    EscalationRequest,
    approve_escalation,
    validate_escalation_args,
)
from odessy.types import PolicyDecision, ToolRequest
from odessy.workspace_authority import WorkspaceAuthority, WorkspaceAuthorityError

#: The tools whose action spawns a process, and which therefore have an *execution*
#: isolation mode at all.
#:
#: The scoping is deliberate. What this excludes is "execution isolation does not apply
#: to this action", NOT "this action needs no security boundary". Capability classes
#: outside process execution carry their own requirement semantics: a managed-device
#: write is not a process and is still a privileged capability, so it must bring its
#: own answer rather than being waved through by the ``None`` this produces.
_PROCESS_TOOL_NAMES = frozenset({"terminal.run", "laptop.terminal.run"})

#: The two arguments that together ask for a wider sandbox mode. They travel as a
#: pair; see :func:`validate_escalation_args`.
_ESCALATION_ARGUMENT_NAMES = frozenset({"sandbox_permissions", "justification"})

#: The mode each authority outcome implies for a process-spawning action.
#:
#: The mapping is derived from the authority decision rather than from a *second*
#: pass over the command rules. Re-evaluating the command could disagree with the
#: first pass, and the authority answer and the isolation answer would then be
#: describing different actions.
#:
#: ``deny`` is absent on purpose: a denied action never reaches a mode.
_MODE_BY_OUTCOME: dict[str, Mode] = {
    "allow": "read-only",
    "confirm": "workspace-write",
}

@dataclass(frozen=True)
class OdessyPolicy:
    """Deny-by-default v0.1.0 policy gate for model-proposed tool calls."""

    workspace_root: Path
    allowed_tools: set[str] | None = None
    normal_timeout: int = 10
    max_timeout: int = 30
    #: How strict this policy is about isolation, in the execution contract's
    #: vocabulary. ``required`` means an absolute boundary was demanded, so a mode
    #: providing no confinement can never be granted -- not even by an approved
    #: escalation. Defaults to ``preferred`` because that accepts a weaker boundary,
    #: which is the behaviour every existing caller already has.
    isolation: IsolationRequirement = "preferred"

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
        if request.name not in _PROCESS_TOOL_NAMES:
            # A silent no-op would be worse than a refusal: a model that asks to widen
            # the sandbox on an action the sandbox does not govern would otherwise be
            # told nothing. Note the message is scoped to sandbox modes, which are the
            # process-execution vocabulary, and says nothing about whether the action
            # is privileged by some other means.
            stray = _ESCALATION_ARGUMENT_NAMES & set(request.arguments)
            if stray:
                name = sorted(stray)[0]
                return PolicyDecision(
                    "deny",
                    f"{request.name} does not accept {name}: sandbox modes govern "
                    "process-spawning actions, and this action spawns no process",
                    "medium",
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

        escalation_denial = self._check_escalation_arguments(request.arguments)
        if escalation_denial is not None:
            return escalation_denial

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

    def _check_escalation_arguments(
        self, arguments: dict[str, Any]
    ) -> PolicyDecision | None:
        """Deny a malformed escalation ask, or return None when there is none.

        :meth:`evaluate` must stay *total* -- the agent loop treats a raised error from
        it as a bug rather than an outcome -- so the pairing rule is enforced here as a
        denial. :func:`validate_escalation_args` remains the single source of truth for
        what the rule is, so the two boundaries cannot drift apart.
        """

        target = arguments.get("sandbox_permissions")
        justification = arguments.get("justification")
        if target is None and justification is None:
            return None
        if target is not None and not isinstance(target, str):
            return PolicyDecision(
                "deny", "invalid escalation: sandbox_permissions must be a mode string", "medium"
            )
        if justification is not None and not isinstance(justification, str):
            return PolicyDecision(
                "deny", "invalid escalation: justification must be a sentence", "medium"
            )
        try:
            validate_escalation_args(target, justification)
        except EscalationArgumentError as exc:
            return PolicyDecision("deny", str(exc), "medium")
        if target not in ESCALATION_TARGETS:
            # The schema advertises the closed vocabulary; this is the per-call truth.
            return PolicyDecision(
                "deny",
                f"unknown sandbox_permissions target: {target}",
                "medium",
            )
        return None

    def _effective_mode(self, request: ToolRequest, decision: PolicyDecision) -> Mode | None:
        """The mode this action's process runs under, or ``None`` if it spawns none.

        ``None`` is scoped to the execution axis, never read as "no boundary needed";
        see :meth:`requirement_for`.
        """

        if request.name not in _PROCESS_TOOL_NAMES:
            return None
        return _MODE_BY_OUTCOME.get(decision.action)

    def requirement_for(
        self, request: ToolRequest, decision: PolicyDecision
    ) -> Requirement | None:
        """The *execution* isolation requirement for one already evaluated action.

        The decision is passed in rather than recomputed so the requirement describes
        the same action the authority layer just judged.

        Returns ``None`` when the execution axis does not govern the action -- that is,
        when it spawns no process. ``None`` means "this axis of control does not apply
        here", not "no boundary is needed". A managed-device write is not a process and
        is still a privileged capability, so a device provider brings its own
        requirement semantics instead of inheriting this function's answer.
        """

        mode = self._effective_mode(request, decision)
        if mode is None:
            return None
        return Requirement(
            policy=ExecutionPolicy(
                mode=mode,
                workspace_root=str(self.workspace_root),
            ),
            isolation=self.isolation,
        )

    async def resolve_execution_mode(
        self,
        request: ToolRequest,
        decision: PolicyDecision,
        *,
        approval: EscalationApproval,
    ) -> Mode:
        """Resolve the mode one process-spawning action runs under, before it runs.

        Without an escalation ask this is just the effective mode. With one, the
        request goes through the full choreography in
        :func:`odessy.execution.escalation.approve_escalation`: strict widening against
        the *effective* mode, then the approval channel, then the outcome. An approved
        escalation is a new call under a wider policy, never a mutated one.
        """

        effective = self._effective_mode(request, decision)
        if effective is None:
            raise ValueError(
                f"{request.name} does not spawn a process, so it has no isolation mode"
            )

        target = request.arguments.get("sandbox_permissions")
        if target is None:
            return effective

        justification = request.arguments.get("justification")
        validate_escalation_args(target, justification)

        # The invariant this phase exists for. `required` means an absolute boundary
        # was demanded, and `danger-full-access` provides none -- so granting it here
        # would let the escalation path hand out exactly what the requirement exists
        # to refuse. The registry refuses the same combination, and these two layers
        # must not disagree about it.
        if target == "danger-full-access" and self.isolation == "required":
            raise EscalationDeniedError(
                f'sandbox escalation to "{target}" is refused because isolation is '
                "required and this mode provides no confinement"
            )

        return await approve_escalation(
            EscalationRequest(
                requested_mode=target,
                justification=justification if justification is not None else "",
                effective_mode=effective,
                subject="command",
            ),
            approval,
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
