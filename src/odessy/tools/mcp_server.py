from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, TypedDict

from mcp.server import MCPServer

from odessy.tools.calculator import CalculatorEvalResult, evaluate_arithmetic_expression
from odessy.authority.command_bridge import CommandBridge, TerminalRunResult
from odessy.execution.contract import ExecutionPolicy, ExecutionProvider, ProviderError
from odessy.execution.registry import ProviderRegistry


class TimeNowResult(TypedDict):
    iso_time: str
    timezone: str


Clock = Callable[[], str]


def _refusal_result(*, command: str, cwd: str, reason: str) -> TerminalRunResult:
    """A result for a command the execution layer refused to run.

    ``status`` is ``"refused"`` rather than ``"failed"`` on purpose: the command never
    started, and reporting it as a failure would make "the command failed" and "the
    command never ran" indistinguishable to everything downstream.

    ``exit_code`` is ``None`` for the same reason it is ``None`` on a timeout: no process
    exit status exists, because no process ran.
    """

    return {
        "stdout": "",
        "stderr": reason,
        "exit_code": None,
        "duration": 0.0,
        "status": "refused",
        "command": command,
        "cwd": cwd,
        "timed_out": False,
        "stdout_truncated": False,
        "stderr_truncated": False,
        "sandbox": {"provider_id": None, "enforcement": "none", "limitations": []},
    }


def _local_clock() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


@dataclass(frozen=True)
class SessionPolicy:
    """The confinement one *session* runs under, chosen before the model speaks.

    When a server is built with this, the confinement arguments stop being tool arguments:
    a call cannot set the mode, the network policy or the hidden paths, because there is no
    agent loop on that path to strip what the model supplied. The values come from Odessy's
    resolved ``SandboxPlan`` and are fixed for the process's lifetime.
    """

    mode: str = "read-only"
    isolation: str = "preferred"
    network: str = "allow"
    hidden_paths: tuple[str, ...] = ()
    extra_writable: tuple[str, ...] = ()

    def confinement(self) -> dict[str, Any]:
        """The keyword arguments ``CommandBridge.run`` takes for these values."""

        values: dict[str, Any] = {
            "mode": self.mode,
            "isolation": self.isolation,
            "network": self.network,
            "hidden_paths": list(self.hidden_paths),
        }
        if self.extra_writable:
            values["extra_writable"] = list(self.extra_writable)
        return values


def build_odessy_mcp_server(
    *,
    clock: Clock | None = None,
    workspace_root: Path | str | None = None,
    provider: ExecutionProvider | None = None,
    policy: ExecutionPolicy | None = None,
    registry: ProviderRegistry | None = None,
    include_terminal: bool = True,
    session_policy: "SessionPolicy | None" = None,
    authorize: "Callable[[str, Mapping[str, Any]], str | None] | None" = None,
    consent: "Callable[[Mapping[str, Any]], tuple[bool, str]] | None" = None,
    on_spawn: "Callable[[Any], None] | None" = None,
    before_spawn: "Callable[[], None] | None" = None,
) -> MCPServer:
    """Build the Odessy tool server.

    ``include_terminal=False`` leaves out ``terminal.run``, for a deployment where
    another server (the local Node tool server, in ``single`` mode) provides it.

    ``provider`` and ``policy`` are threaded through to the `CommandBridge` rather
    than built here: which backend this host can offer and which mode an action
    resolves to are decisions that belong to the caller's composition root, not to
    the tool surface. Omitting both is legal and runs every command unconfined,
    with each result reporting ``enforcement: "none"`` so the absence is visible
    rather than implied; a call that requires isolation is then refused.
    ``registry`` instead selects a backend per call (see `CommandBridge.registry`).

    ``session_policy`` and ``authorize`` are for the path with **no agent loop** in front of
    the call (a Harness-initiated tool call): the confinement is then fixed by the session
    rather than taken from the arguments, and every call is put to ``authorize`` first, which
    returns a refusal reason or ``None``.

    ``consent`` is how a `confirm` operation becomes *approvable* rather than refused: the
    caller supplies ``(details) -> (approved, reason)``, which the stdio pipeline wires to the
    application's own approval broker. Without it, `confirm` is refused.
    """

    server = MCPServer("odessy-v0.1.0")
    now = clock or _local_clock
    bridge = CommandBridge(
        Path(workspace_root or ".").resolve(),
        provider=provider,
        policy=policy,
        registry=registry,
    )

    @server.tool(name="time.now")
    def time_now() -> TimeNowResult:
        """Return the current local time as an ISO-8601 timestamp."""

        return {
            "iso_time": now(),
            "timezone": "local",
        }

    def terminal_run(
        command: str,
        cwd: str = ".",
        timeout: int = 10,
        mode: str | None = None,
        isolation: str | None = None,
        network: str | None = None,
        hidden_paths: list[str] | None = None,
        extra_writable: list[str] | None = None,
        limits: dict[str, int] | None = None,
    ) -> TerminalRunResult:
        """Run a workspace-bound command and return structured output.

        ``mode`` and ``isolation`` are the sandbox decision the *authority layer* already
        resolved for this call, and they travel as a pair. Neither is a model-settable
        argument: the agent loop injects them after policy and escalation, which is what
        keeps the mode out of the model's reach and stops this side inferring a
        requirement the authority layer never expressed. ``network``, ``hidden_paths``,
        ``extra_writable`` and ``limits`` come the same way, from ``sandbox.toml``.
        """

        if session_policy is not None:
            supplied = [name for name, value in (
                ("mode", mode), ("isolation", isolation), ("network", network),
                ("hidden_paths", hidden_paths), ("extra_writable", extra_writable),
                ("limits", limits),
            ) if value is not None]
            if supplied:
                return _refusal_result(
                    command=command, cwd=cwd,
                    reason=(
                        "this server runs under a policy fixed by the session, so "
                        f"{', '.join(supplied)} cannot be supplied per call"
                    ),
                )
        if authorize is not None:
            verdict = authorize("terminal.run", {
                "command": command, "cwd": cwd, "timeout": timeout,
                "mode": session_policy.mode if session_policy else mode,
                "isolation": session_policy.isolation if session_policy else isolation,
            })
            if verdict and verdict[0] == "confirm":
                # A confirmable operation is *offered*, not refused: the caller owns the
                # consent channel, and only an explicit approval lets it run.
                if consent is None:
                    return _refusal_result(
                        command=command, cwd=cwd,
                        reason=("this operation requires confirmation, and no consent "
                                "channel is attached to this tool server"))
                approved, reason = consent(verdict[1])
                if not approved:
                    return _refusal_result(
                        command=command, cwd=cwd,
                        reason=f"not approved: {reason}")
            elif verdict:
                reason = verdict if isinstance(verdict, str) else str(verdict[0])
                return _refusal_result(command=command, cwd=cwd, reason=reason)

        def dispatch_guard():
            if before_spawn is not None:before_spawn()
            if authorize is not None and isinstance(verdict, tuple):
                validate = getattr(verdict[1], 'validate', None)
                if validate is not None:validate()
        try:
            if session_policy is not None:
                # The session decided these; the call may not. A model-supplied value is
                # dropped rather than trusted, and the drop is visible in the result's
                # ``sandbox`` block, which reports what actually ran.
                return bridge.run(
                    command=command,
                    cwd=cwd,
                    timeout=timeout,
                    on_spawn=on_spawn,
                    before_spawn=dispatch_guard,
                    **session_policy.confinement(),
                )
            return bridge.run(
                command=command,
                cwd=cwd,
                timeout=timeout,
                mode=mode,  # type: ignore[arg-type]
                isolation=isolation,  # type: ignore[arg-type]
                network=network,
                hidden_paths=hidden_paths,
                extra_writable=extra_writable,
                limits=limits,
            )
        except (ProviderError, ValueError) as exc:
            # A refusal is RETURNED, not raised. Raising would let it cross the MCP
            # boundary, which flattens a typed tool exception into a generic
            # `RuntimeError("MCP tool returned an error")` and discards the reason.
            #
            # This is a diagnosability fix, not a security one: the command did not run
            # either way. What changes is that the caller can now see WHY it did not run
            # and act on it, instead of receiving an opaque failure.
            return _refusal_result(command=command, cwd=cwd, reason=str(exc))

    if include_terminal:
        server.tool(name="terminal.run")(terminal_run)

    @server.tool(name="calculator.eval")
    def calculator_eval(expression: str) -> CalculatorEvalResult:
        """Evaluate a side-effect-free arithmetic expression."""

        return evaluate_arithmetic_expression(expression)

    return server
