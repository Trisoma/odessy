"""A controlled third-party MCP server used by the gateway tests (prompt 09a).

It is a *real* MCP server over stdio — the gateway under test spawns it, lists its tools and
forwards calls to it — but its tools are trivial, so a test can assert exactly what happened:
a file written, a call that slept, a server that exited. It is a fixture for source tests
only; the real-runtime smoke uses Odessy's own Node tool server and a separate controlled
server through the production application path.

Tools are deliberately shaped like the surfaces that matter: one annotated nothing, one that
writes, one that blocks long enough to be cancelled, one that dies mid-call, and one that
returns a large payload.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

from mcp.server import MCPServer

ROOT = Path(os.environ.get("FIXTURE_ROOT") or ".").resolve()
PID_FILE = os.environ.get("FIXTURE_PID_FILE") or ""
STARTED_FILE = os.environ.get("FIXTURE_STARTED_FILE") or ""

if PID_FILE:
    Path(PID_FILE).write_text(str(os.getpid()), encoding="utf-8")

server = MCPServer("fixture")


@server.tool(name="echo")
def echo(text: str) -> dict:
    """Echo text back unchanged."""

    return {"echoed": text}


@server.tool(name="write_file")
def write_file(path: str, content: str) -> dict:
    """Write a file below the fixture root."""

    target = (ROOT / path).resolve()
    if ROOT not in target.parents and target != ROOT:
        raise ValueError("outside the fixture root")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return {"written": str(target), "bytes": len(content)}


@server.tool(name="slow")
async def slow(seconds: float = 5.0) -> dict:
    """Sleep, so a Stop has something in flight to cancel."""

    if STARTED_FILE:
        Path(STARTED_FILE).write_text("started", encoding="utf-8")
    await asyncio.sleep(seconds)
    return {"slept": seconds}


@server.tool(name="crash")
def crash() -> dict:
    """Exit the server process immediately."""

    os._exit(7)
    return {"unreachable": True}  # pragma: no cover


@server.tool(name="big")
def big(lines: int = 1) -> dict:
    """Return a payload large enough to exercise output bounds."""

    return {"text": "x" * 2000 * max(1, lines)}


if __name__ == "__main__":  # pragma: no cover - process entry point
    asyncio.run(server.run_stdio_async())
