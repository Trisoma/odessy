from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import TypedDict

from mcp.server import MCPServer

from odessy.calculator import CalculatorEvalResult, evaluate_arithmetic_expression
from odessy.command_bridge import CommandBridge, TerminalRunResult


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
) -> MCPServer:
    server = MCPServer("odessy-v0.1.0")
    now = clock or _local_clock
    bridge = CommandBridge(Path(workspace_root or ".").resolve())

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
