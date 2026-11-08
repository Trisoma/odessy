"""Tiny MCP stdio server used only to prove an external tool reaches the runtime.

Implements the three calls a client needs for tool use - `initialize`,
`tools/list`, `tools/call` - over newline-delimited JSON-RPC on stdio, and appends
every request to the log file given by `--log`. Nothing else is exposed, and the
only tool echoes its input.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

TOOLS = [{
    "name": "spike_echo",
    "description": "Echo the given text. Spike-only harmless test operation.",
    "inputSchema": {
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
    },
}]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--log", required=True)
    args = parser.parse_args()
    log = Path(args.log)
    log.parent.mkdir(parents=True, exist_ok=True)

    def record(entry: dict) -> None:
        with log.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"t": time.time(), **entry}) + "\n")

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            continue
        method = message.get("method")
        request_id = message.get("id")
        params = message.get("params") or {}
        record({"method": method, "params": params, "has_id": request_id is not None})
        if method == "initialize":
            result = {
                "protocolVersion": params.get("protocolVersion") or "2025-06-18",
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "spike-mcp", "version": "0.1"},
            }
        elif method == "tools/list":
            result = {"tools": TOOLS}
        elif method == "tools/call":
            name = params.get("name")
            arguments = params.get("arguments") or {}
            if name != "spike_echo":
                result = {"content": [{"type": "text", "text": f"unknown tool {name}"}], "isError": True}
            else:
                result = {"content": [{"type": "text", "text": "echo:" + str(arguments.get("text", ""))}]}
        elif method in ("notifications/initialized", "notifications/cancelled"):
            continue
        elif method == "ping":
            result = {}
        else:
            if request_id is None:
                continue
            print(json.dumps({"jsonrpc": "2.0", "id": request_id,
                              "error": {"code": -32601, "message": f"unknown method {method}"}}), flush=True)
            continue
        if request_id is None:
            continue
        print(json.dumps({"jsonrpc": "2.0", "id": request_id, "result": result}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
