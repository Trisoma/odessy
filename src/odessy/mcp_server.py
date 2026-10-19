from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import TypedDict

from mcp.server import MCPServer

from odessy.calculator import CalculatorEvalResult, evaluate_arithmetic_expression
from odessy.command_bridge import CommandBridge, TerminalRunResult
from odessy.execution.contract import ExecutionPolicy, ExecutionProvider, ProviderError


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


def build_odessy_mcp_server(
    *,
    clock: Clock | None = None,
    workspace_root: Path | str | None = None,
    provider: ExecutionProvider | None = None,
    policy: ExecutionPolicy | None = None,
    include_terminal: bool = True,
) -> MCPServer:
    """Build the Odessy tool server.

    ``include_terminal=False`` leaves out ``terminal.run``, for a deployment where
    another server (the local Node tool server, in ``single`` mode) provides it.

    ``provider`` and ``policy`` are threaded through to the `CommandBridge` rather
    than built here: which backend this host can offer and which mode an action
    resolves to are decisions that belong to the caller's composition root, not to
    the tool surface. Omitting both is legal and runs every command unconfined,
    with each result reporting ``enforcement: "none"`` so the absence is visible
    rather than implied.
    """

    server = MCPServer("odessy-v0.1.0")
    now = clock or _local_clock
    bridge = CommandBridge(
        Path(workspace_root or ".").resolve(),
        provider=provider,
        policy=policy,
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
    ) -> TerminalRunResult:
        """Run a workspace-bound command and return structured output.

        ``mode`` and ``isolation`` are the sandbox decision the *authority layer* already
        resolved for this call, and they travel as a pair. Neither is a model-settable
        argument: the agent loop injects them after policy and escalation, which is what
        keeps the mode out of the model's reach and stops this side inferring a
        requirement the authority layer never expressed.
        """

        try:
            return bridge.run(
                command=command,
                cwd=cwd,
                timeout=timeout,
                mode=mode,  # type: ignore[arg-type]
                isolation=isolation,  # type: ignore[arg-type]
            )
        except ProviderError as exc:
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
