from __future__ import annotations

import functools
import re
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from odessy.execution.contract import (
    ESCALATION_TARGETS,
    WIDER_MODES,
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
from odessy.authority.command_rules import (
    CHAINING,
    FILE_READERS,
    classify_command,
    matches_allow,
    matches_confirm,
    matches_install,
)
from odessy.config.commands import matches_preapproved
from odessy.config.sandbox import DEFAULT_SETTINGS, SandboxSettings
from odessy.tools.external_tools import ExternalTool, external_tool
from odessy.runtime.types import PolicyDecision, ToolRequest
from odessy.authority.workspace_authority import (
    WorkspaceAuthority,
    WorkspaceAuthorityError,
    is_later_effect_path,
)

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
    """Deny-by-default v0.1.0 policy gate for model-proposed tool calls.

    Every decision names its ``rule``: a stable identifier for the turn log, the audit
    log and ``python -m odessy permissions check``, unlike the reason, which may quote
    arguments.
    """

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
    #: Command prefixes the user approved in advance for this workspace, from
    #: ``commands.toml`` (``odessy.config.commands``). Only ``terminal.run``.
    preapproved_commands: tuple[tuple[str, ...], ...] = ()
    #: Files and folders the agent may not change: the hooks file, its folder and the
    #: hook scripts (``odessy.runtime.hooks.protected_paths``), ``sandbox.toml`` and
    #: ``commands.toml``. A hook runs as the user outside the sandbox and the other two
    #: decide what is asked, so writing any of them would be a way out.
    protected_paths: tuple[Path, ...] = ()
    #: Isolation for the paired laptop's commands when it differs from ``isolation``
    #: (``sandbox.toml`` ``[targets.laptop]``, or ``auto`` resolved per target).
    target_isolation: Mapping[str, IsolationRequirement] = field(default_factory=dict)
    #: How commands are confined beyond their mode (``sandbox.toml``): network,
    #: hidden paths, extra writable folders, limits, and whether escalation is asked.
    sandbox: SandboxSettings = DEFAULT_SETTINGS

    def identity(self) -> "OdessyPolicy":
        """What an approval binds to. A wrapper delegates this to the policy it wraps."""

        return self

    def isolation_for(self, tool_name: str) -> IsolationRequirement:
        """The isolation requirement for a command on the tool's target."""

        return self.target_isolation.get(_target(tool_name), self.isolation)

    def execution_arguments(self, request: ToolRequest, mode: Mode) -> dict[str, Any]:
        """The rest of the sandbox decision for one command, sent with its mode.

        A command the policy ran without asking gets ``network.read_only`` (deny by
        default); one you confirmed gets ``network.workspace_write``, except that a
        package install always keeps the network it exists to use.
        """

        network = self.sandbox.network_for(mode)
        command = request.arguments.get("command")
        if network == "deny" and mode == "workspace-write" and isinstance(command, str):
            if matches_install(command.strip().lower().replace("\\", "/").split()):
                network = "allow"
        arguments: dict[str, Any] = {"network": network, "hidden_paths": list(self.sandbox.hidden_paths)}
        if self.sandbox.extra_writable:
            arguments["extra_writable"] = list(self.sandbox.extra_writable)
        if self.sandbox.limits is not None:
            arguments["limits"] = self.sandbox.limits.to_arguments()
        return arguments

    @property
    def authority(self) -> WorkspaceAuthority:
        return WorkspaceAuthority(Path(self.workspace_root))

    def evaluate(self, request: ToolRequest) -> PolicyDecision:
        external = external_tool(request.name)
        if external is not None:
            return self._evaluate_external(external, request)
        allowed_tools = self.allowed_tools if self.allowed_tools is not None else {
            "time.now",
            "user.ask",
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
            "model.consult", "agent.delegate", "agent.job_status", "agent.cancel",
        }
        if request.name not in allowed_tools:
            return PolicyDecision(
                action="deny",
                reason=f"Tool is not allowed: {request.name}",
                risk="high",
                rule="tool_not_allowed",
            )
        if request.name == "user.ask":
            from odessy.interaction.context import current_interactions
            if _ESCALATION_ARGUMENT_NAMES & set(request.arguments):
                return PolicyDecision("deny", "Clarification cannot request sandbox permissions", "high", rule="sandbox_arguments")
            if current_interactions() is None:
                return PolicyDecision("deny", "No clarification interaction is available", "low", rule="no_interaction")
            return PolicyDecision("allow", "Ask for information; answers grant no authority", "low", rule="user_ask")
        if request.name in {"model.consult", "agent.delegate", "agent.job_status", "agent.cancel"}:
            if _ESCALATION_ARGUMENT_NAMES & set(request.arguments):
                return PolicyDecision("deny", "Collaboration tools do not accept sandbox escalation arguments", "high", rule="sandbox_arguments")
            from odessy.collaboration.context import current_service
            service = current_service()
            if service is None or not service.config.settings.enabled:
                return PolicyDecision("deny", "Collaboration is not enabled", "medium", rule="collaboration_off")
            if request.name in {"agent.job_status", "agent.cancel"}:
                return PolicyDecision("allow", "Inspect or cancel an authorized collaboration job", "low", rule="collaboration_job")
            participant = request.arguments.get("participant") or service.config.settings.default_participant
            profile = service.config.participants.get(participant)
            capability = "consult" if request.name == "model.consult" else "delegate"
            if profile is None or capability not in profile.capabilities:
                return PolicyDecision("deny", "Participant or capability is not configured", "medium", rule="collaboration_participant")
            if capability == "consult":
                if not service.config.settings.automatic:
                    return PolicyDecision("deny", "Automatic consultation is disabled; request it explicitly", "medium", rule="collaboration_consult")
                return PolicyDecision("allow", "Consultation enabled by user settings; advice only", "low", rule="collaboration_consult")
            mode = request.arguments.get("mode", "advice")
            if mode not in profile.allowed_modes:
                return PolicyDecision("deny", "Delegation mode is not allowed for this participant", "high", rule="collaboration_mode")
            return PolicyDecision("confirm", "Delegate work using the configured child-agent permissions", "medium" if mode == "advice" else "high", rule="collaboration_delegate")
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
                    rule="sandbox_arguments",
                )
        if request.name == "time.now" and request.arguments:
            return PolicyDecision(
                action="deny",
                reason="time.now does not accept arguments",
                risk="low",
                rule="tool_arguments",
            )
        if request.name == "calculator.eval":
            expression = request.arguments.get("expression")
            if set(request.arguments) != {"expression"}:
                return PolicyDecision(
                    action="deny",
                    reason="calculator.eval accepts only an expression argument",
                    risk="low",
                    rule="tool_arguments",
                )
            if not isinstance(expression, str) or not expression.strip():
                return PolicyDecision(
                    action="deny",
                    reason="calculator.eval requires a non-empty expression",
                    risk="low",
                    rule="tool_arguments",
                )
            return PolicyDecision(
                action="allow",
                reason="calculator.eval allowed as a read-only arithmetic tool",
                risk="low",
                rule="read_only_tool",
            )
        if request.name in _READ_ONLY_TOOL_NAMES:
            return self._evaluate_read_only_tool(request)
        if request.name in {"terminal.run", "laptop.terminal.run"}:
            return self._evaluate_terminal_run(request)
        if request.name == "filesystem.write":
            return self._evaluate_filesystem_write(request)
        if request.name == "log.append":
            return self._evaluate_log_append(request)
        if request.name == "time.now":
            return PolicyDecision("allow", "time.now reads the clock", "low", rule="read_only_tool")
        # A tool on the list that no rule above knows has no decision to give it.
        return PolicyDecision("deny", f"No policy rule covers {request.name}", "high", rule="tool_without_rule")

    def _evaluate_terminal_run(self, request: ToolRequest) -> PolicyDecision:
        """The command's decision, then the timeout, which can only make it stricter.

        The timeout comes from the model. Judged first, it decided a call before any
        other rule looked at it, so `rm -rf .` with a timeout of 31 was only confirmed.
        """

        decision = self._judge_command(request)
        timeout = request.arguments.get("timeout", self.normal_timeout)
        if decision.action == "deny" or timeout <= self.max_timeout:
            return decision
        reason = f"{request.name} timeout {timeout} exceeds the normal execution scope"
        if decision.action == "allow":
            # Confirming a long read must not let it write.
            return PolicyDecision("confirm", reason, "medium", rule="command_timeout", mode_outcome="allow")
        return replace(decision, reason=f"{decision.reason}; {reason}", preapproved=False)

    def _judge_command(self, request: ToolRequest) -> PolicyDecision:
        tool_name = request.name
        command = request.arguments.get("command")
        cwd = request.arguments.get("cwd", ".")
        timeout = request.arguments.get("timeout", self.normal_timeout)

        if not isinstance(command, str) or not command.strip():
            return PolicyDecision("deny", f"{tool_name} requires a non-empty command", "high", rule="tool_arguments")
        if not isinstance(cwd, str) or not cwd.strip():
            return PolicyDecision("deny", f"{tool_name} requires a valid cwd", "high", rule="tool_arguments")
        if not isinstance(timeout, int):
            return PolicyDecision("deny", f"{tool_name} timeout must be an integer", "high", rule="tool_arguments")
        if timeout < 1:
            return PolicyDecision(
                "deny",
                f"{tool_name} timeout must be between 1 and the maximum execution scope",
                "high",
                rule="tool_arguments",
            )

        escalation_denial = self._check_escalation_arguments(request.arguments)
        if escalation_denial is not None:
            return escalation_denial

        cwd_decision = self._check_workspace_cwd(cwd, tool_name)
        if cwd_decision is not None:
            return cwd_decision

        lowered = command.strip().lower()
        tokens = lowered.replace("\\", "/").split()

        # The laptop has its own files; these paths are this machine's.
        if tool_name == "terminal.run" and _names_protected_path(lowered, self.protected_paths):
            return PolicyDecision(
                "deny",
                f"{tool_name} denied because it names Odessy's own configuration, which the agent may not change",
                "high",
                rule="protected_config",
            )

        finding = classify_command(command)
        if finding is not None:
            if finding.action == "deny":
                return PolicyDecision("deny", f"{tool_name} denied because {finding.reason}", "high", rule=finding.rule)
            return PolicyDecision(
                "confirm", f"{tool_name} requires confirmation because {finding.reason}", "high", rule=finding.rule
            )
        read_only = matches_allow(lowered, tokens) or self._reads_workspace_files(command, cwd, tokens)
        if (
            tool_name == "terminal.run"
            and not read_only
            and matches_preapproved(command, self.preapproved_commands)
        ):
            return PolicyDecision(
                "confirm",
                f"{tool_name} approved in advance for this workspace in commands.toml",
                "medium",
                rule="command_preapproved",
                preapproved=True,
            )
        if matches_confirm(lowered, tokens):
            return PolicyDecision(
                "confirm",
                f"{tool_name} requires confirmation because the command may modify state",
                "medium",
                rule="command_write",
            )
        if read_only:
            return PolicyDecision(
                "allow",
                f"{tool_name} allowed as a read-only workspace command",
                "low",
                rule="command_read_only",
            )
        return PolicyDecision(
            "confirm",
            f"{tool_name} requires confirmation because the command is outside the low-risk allow rules",
            "medium",
            rule="command_default",
        )

    def _reads_workspace_files(self, command: str, cwd: str, tokens: list[str]) -> bool:
        """A file-printing command (``cat``, ``Get-Content``, ...) whose files are all here.

        Listing a directory reveals names; printing a file reveals its contents, so a
        reader runs unasked only when every argument resolves inside the workspace and
        names no credential file. Anything else (a path outside, ``~``, a variable) is
        left to the confirmation rule, not denied.
        """

        if not tokens or tokens[0] not in FILE_READERS or CHAINING.search(command.lower()):
            return False
        arguments = [part.strip("'\"") for part in command.split()[1:]]
        paths = [part for part in arguments if part and not part.startswith("-")]
        if not paths or any(mark in part for part in paths for mark in ("$", "%", "~")):
            return False
        for part in paths:
            try:
                resolved = self.authority.resolve_path(str(Path(cwd) / part), must_exist=False)
            except WorkspaceAuthorityError:
                return False
            if resolved.sensitive:
                return False
        return True

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
                "deny", "invalid escalation: sandbox_permissions must be a mode string", "medium", rule="sandbox_arguments"
            )
        if justification is not None and not isinstance(justification, str):
            return PolicyDecision(
                "deny", "invalid escalation: justification must be a sentence", "medium", rule="sandbox_arguments"
            )
        try:
            validate_escalation_args(target, justification)
        except EscalationArgumentError as exc:
            return PolicyDecision("deny", str(exc), "medium", rule="sandbox_arguments")
        if target not in ESCALATION_TARGETS:
            # The schema advertises the closed vocabulary; this is the per-call truth.
            return PolicyDecision(
                "deny",
                f"unknown sandbox_permissions target: {target}",
                "medium",
                rule="sandbox_arguments",
            )
        return None

    def _effective_mode(self, request: ToolRequest, decision: PolicyDecision) -> Mode | None:
        """The mode this action's process runs under, or ``None`` if it spawns none.

        ``None`` is scoped to the execution axis, never read as "no boundary needed";
        see :meth:`requirement_for`.
        """

        if request.name not in _PROCESS_TOOL_NAMES:
            return None
        if decision.action == "deny":
            return None
        return _MODE_BY_OUTCOME.get(decision.mode_outcome or decision.action)

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
            isolation=self.isolation_for(request.name),
        )

    def preflight_execution_mode(
        self, request: ToolRequest, decision: PolicyDecision
    ) -> Mode | None:
        """Validate the final candidate mode without contacting an approval channel."""
        effective = self._effective_mode(request, decision)
        target = request.arguments.get("sandbox_permissions")
        justification = request.arguments.get("justification")
        if target is not None and not isinstance(target, str):
            raise EscalationArgumentError("invalid escalation: sandbox_permissions must be a mode string")
        if justification is not None and not isinstance(justification, str):
            raise EscalationArgumentError("invalid escalation: justification must be a sentence")
        validate_escalation_args(target, justification)
        if target is None:
            return effective
        if effective is None:
            raise EscalationDeniedError("This action has no execution mode to escalate")
        if target not in ESCALATION_TARGETS:
            raise EscalationArgumentError(f"unknown sandbox_permissions target: {target}")
        setting = self.sandbox.escalation_for(_target(request.name))
        if setting == "never":
            raise EscalationDeniedError("sandbox escalation is turned off in sandbox.toml (escalation = \"never\")")
        if setting == "workspace-write-only" and target == "danger-full-access":
            raise EscalationDeniedError(
                'sandbox escalation to "danger-full-access" is turned off in sandbox.toml '
                '(escalation = "workspace-write-only")'
            )
        if target == "danger-full-access" and self.isolation_for(request.name) == "required":
            raise EscalationDeniedError(
                f'sandbox escalation to "{target}" is refused because isolation is '
                "required and this mode provides no confinement"
            )
        if target not in WIDER_MODES.get(effective, ()):
            raise EscalationDeniedError(
                f'sandbox escalation to "{target}" is not strictly wider than this call\'s current "{effective}" mode'
            )
        return target

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

        candidate = self.preflight_execution_mode(request, decision)
        effective = self._effective_mode(request, decision)
        if effective is None:
            raise ValueError(
                f"{request.name} does not spawn a process, so it has no isolation mode"
            )

        target = request.arguments.get("sandbox_permissions")
        if target is None:
            return candidate

        justification = request.arguments.get("justification")
        return await approve_escalation(
            EscalationRequest(
                requested_mode=target,
                justification=justification if justification is not None else "",
                effective_mode=effective,
                subject="command",
            ),
            approval,
        )

    def _evaluate_external(self, tool: ExternalTool, request: ToolRequest) -> PolicyDecision:
        """A tool from a configured third-party server.

        Only the user's config can relax a decision. What the server says about its
        own tools (its annotations) is used in one way only: on a server the user
        marked ``trusted``, a tool annotated read-only and not destructive runs
        without confirmation. Everything else needs the user's yes.
        """

        name = request.name
        server = tool.server
        stray = _ESCALATION_ARGUMENT_NAMES & set(request.arguments)
        if stray and not ({"properties"} <= set(tool.input_schema) and stray <= set(tool.input_schema["properties"])):
            return PolicyDecision(
                "deny",
                f"{name} does not accept {sorted(stray)[0]}: sandbox modes govern Odessy's own "
                "command tools, not third-party tools",
                "medium",
                rule="sandbox_arguments",
            )

        configured = server.tool_policies.get(tool.remote_name)
        if configured == "deny":
            return PolicyDecision("deny", f"{name} is denied by the {server.name} server's config", "high", rule="config_deny")

        # Path arguments the user declared are held to the workspace, whatever the
        # tool-level policy says: an `allow` covers the tool, not where it reaches.
        for argument in server.workspace_paths:
            value = request.arguments.get(argument)
            if isinstance(value, str):
                decision = self._check_workspace_path(value, name)
                if decision is not None:
                    return replace(decision, rule="workspace_path")

        if configured == "allow":
            return PolicyDecision("allow", f"{name} is allowed by the {server.name} server's config", "low", rule="config_allow")
        if configured == "confirm":
            return PolicyDecision("confirm", f"{name} requires confirmation by the {server.name} server's config", "medium", rule="config_confirm")
        if server.trust != "trusted":
            return PolicyDecision(
                "confirm",
                f"{name} requires confirmation because the {server.name} server is not marked trusted",
                "medium",
                rule="untrusted_server",
            )
        if tool.read_only is True and tool.destructive is not True:
            return PolicyDecision(
                "allow",
                f"{name} is annotated read-only by the trusted {server.name} server",
                "low",
                rule="trusted_read_only",
            )
        why = "annotated destructive" if tool.destructive else "not annotated read-only"
        return PolicyDecision(
            "confirm",
            f"{name} requires confirmation because it is {why}",
            "medium" if not tool.destructive else "high",
            rule="default_confirm",
        )

    def _check_workspace_cwd(self, cwd: str, tool_name: str) -> PolicyDecision | None:
        try:
            self.authority.resolve_cwd(cwd)
        except WorkspaceAuthorityError:
            return PolicyDecision(
                "deny",
                f"{tool_name} denied because cwd is outside the allowed workspace",
                "high",
                rule="outside_workspace",
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
            rule="read_only_tool",
        )

    def _check_workspace_path(self, candidate_path: str, tool_name: str) -> PolicyDecision | None:
        try:
            resolved = self.authority.resolve_path(candidate_path, must_exist=False)
        except WorkspaceAuthorityError:
            return PolicyDecision(
                "deny",
                f"{tool_name} denied because path is outside the allowed workspace",
                "high",
                rule="outside_workspace",
            )
        if resolved.sensitive:
            return PolicyDecision(
                "deny",
                f"{tool_name} denied because the path targets credentials or secrets",
                "high",
                rule="sensitive_path",
            )
        return None

    def _evaluate_filesystem_write(self, request: ToolRequest) -> PolicyDecision:
        if set(request.arguments) != {"path", "content"}:
            return PolicyDecision(
                "deny",
                "filesystem.write accepts only path and content arguments",
                "high",
                rule="tool_arguments",
            )
        path_value = request.arguments.get("path")
        content = request.arguments.get("content")
        if not isinstance(path_value, str) or not path_value.strip():
            return PolicyDecision("deny", "filesystem.write requires a valid path", "high", rule="tool_arguments")
        if not isinstance(content, str):
            return PolicyDecision("deny", "filesystem.write requires string content", "high", rule="tool_arguments")

        path_decision = self._check_workspace_path(path_value, "filesystem.write")
        if path_decision is not None:
            return path_decision
        if _is_protected_path(Path(self.workspace_root), path_value, self.protected_paths):
            return PolicyDecision(
                "deny",
                "filesystem.write denied because the path is Odessy's own configuration",
                "high",
                rule="protected_config",
            )
        if is_later_effect_path(self.authority.resolve_path(path_value, must_exist=False).relative):
            return PolicyDecision(
                "confirm",
                "filesystem.write changes a file that runs code later (git hooks, CI, editor tasks)",
                "high",
                rule="write_later_effect",
            )

        return PolicyDecision(
            "confirm",
            "filesystem.write modifies the workspace and requires confirmation",
            "medium",
            rule="workspace_write",
        )

    def _evaluate_log_append(self, request: ToolRequest) -> PolicyDecision:
        if set(request.arguments) != {"content"}:
            return PolicyDecision(
                "deny",
                "log.append accepts only a content argument",
                "high",
                rule="tool_arguments",
            )
        content = request.arguments.get("content")
        if not isinstance(content, str) or not content.strip():
            return PolicyDecision("deny", "log.append requires non-empty string content", "high", rule="tool_arguments")
        return PolicyDecision(
            "allow",
            "log.append allowed because its destination is fixed and internal",
            "low",
            rule="internal_log",
        )

def _target(tool_name: str) -> str:
    return "laptop" if tool_name.startswith("laptop.") else "local"


#: Files named on their own in a command are matched by name as well as by path.
_CONFIG_NAMES = frozenset({"hooks.toml", "sandbox.toml", "commands.toml"})


@functools.lru_cache(maxsize=32)
def _protected_spellings(paths: tuple[Path, ...]) -> tuple[str, ...]:
    """How a command might name each protected path: absolute, and relative to home.

    A command is text, so this is a screen, not a boundary: the fingerprint check in
    ``odessy.runtime.hooks`` catches a change made some other way.
    """

    try:
        home = Path.home().resolve()
    except (OSError, RuntimeError):
        home = None
    spellings: set[str] = set()
    for path in paths:
        try:
            absolute = Path(path).expanduser().resolve()
        except OSError:
            absolute = Path(path)
        spellings.add(absolute.as_posix().lower())
        if absolute.name.lower() in _CONFIG_NAMES:
            spellings.add(absolute.name.lower())
        if home is not None:
            try:
                relative = absolute.relative_to(home).as_posix().lower()
            except ValueError:
                continue
            if "/" in relative:
                spellings.add(relative)
    return tuple(sorted(spellings))


def _names_protected_path(command: str, paths: tuple[Path, ...]) -> bool:
    if not paths:
        return False
    text = command.replace("\\", "/").replace('"', "").replace("'", "")
    return any(spelling in text for spelling in _protected_spellings(tuple(paths)))


def _is_protected_path(workspace_root: Path, candidate: str, paths: tuple[Path, ...]) -> bool:
    if not paths:
        return False
    target = Path(candidate).expanduser()
    if not target.is_absolute():
        target = workspace_root / target
    try:
        target = target.resolve()
    except OSError:
        return False
    for path in paths:
        try:
            protected = Path(path).expanduser().resolve()
        except OSError:
            continue
        if target == protected or protected in target.parents:
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
