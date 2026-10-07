from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import TypedDict

from mcp.server import MCPServer

from odessy.calculator import CalculatorEvalResult, evaluate_arithmetic_expression
from odessy.command_bridge import CommandBridge, TerminalRunResult
from odessy.execution.contract import ExecutionPolicy, ExecutionProvider


class TimeNowResult(TypedDict):
    iso_time: str
    timezone: str


Clock = Callable[[], str]


def _local_clock() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def build_odessy_mcp_server(
    *,
    clock: Clock | None = None,
    workspace_root: Path | str | None = None,
    provider: ExecutionProvider | None = None,
    policy: ExecutionPolicy | None = None,
) -> MCPServer:
    """Build the Odessy tool server.

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

    @server.tool(name="terminal.run")
    def terminal_run(command: str, cwd: str = ".", timeout: int = 10) -> TerminalRunResult:
        """Run a workspace-bound command and return structured output."""

        return bridge.run(command=command, cwd=cwd, timeout=timeout)

    @server.tool(name="calculator.eval")
    def calculator_eval(expression: str) -> CalculatorEvalResult:
        """Evaluate a side-effect-free arithmetic expression."""

        return evaluate_arithmetic_expression(expression)

    return server
