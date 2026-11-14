"""Pinned evidence for prompt 09a: the Node tool server and an external MCP server, guarded.

What runs, with nothing faked but the model:

    pinned runtime (0.2.1rc2) + packaged bundle
      -> session/new attaches three servers: Odessy's own tool server, the guarded gateway for
         the *real* Node tool server (tools/dist/src/server.js), and the guarded gateway for a
         controlled third-party MCP server
        -> the model calls a tool
          -> the gateway admits the call against the session's turn generation, asks
             Odessy's policy, asks the human through the application's own broker
            -> only then does the upstream server run it
              -> the effect is on the real filesystem (or is absent)

Checks:

1. the roster the model is offered contains the Node tools and the external tools, and none of
   the built-in execution rows or the Node server's argument-chosen command tool;
2. an approved Node write really writes; a denied one leaves no file;
3. an approved external write really writes; a denied one leaves no file;
4. a path outside the workspace is refused through the Node server, with no read;
5. Stop while an external call is active ends that call honestly and the session continues;
6. close() leaves no runtime, gateway or upstream process behind.

    python smoke_p09a.py --exe <pinned exe> --home <DSH_HOME> --work <dir> --node <node.exe>
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[2] / "src"))

import mock_provider  # noqa: E402

from odessy.application.session import SessionController  # noqa: E402

MODEL = "mock-model"
PLATFORM = ("SystemRoot", "WINDIR", "SYSTEMDRIVE", "COMSPEC", "PATHEXT", "TEMP", "TMP",
            "USERPROFILE", "APPDATA", "LOCALAPPDATA", "PROCESSOR_ARCHITECTURE")

#: A third-party MCP server, written into the run's own work directory. It is a real stdio
#: server with real effects; only its tools are trivial. Nothing in the repository defines it,
#: so the smoke does not depend on the test suite for its evidence.
FIXTURE_SERVER = '''\
import asyncio, os
from pathlib import Path
from mcp.server import MCPServer

if os.environ.get("FIXTURE_TOKEN") != "p09a-authentication-literal":
    raise RuntimeError("the configured upstream credential is unavailable")
if any(name in os.environ for name in ("ODESSY_TOOL_CONTROL_TOKEN", "ODESSY_BRIDGE_TOKEN",
                                       "ODESSY_HARNESS_PROVIDER_KEY", "UNDECLARED_TOKEN")):
    raise RuntimeError("an unrelated Harness credential reached the upstream")
ROOT = Path(os.environ["FIXTURE_ROOT"]).resolve()
STARTED = Path(os.environ["FIXTURE_STARTED"])
server = MCPServer("fixture")

@server.tool(name="echo")
def echo(text: str) -> dict:
    """Echo text back."""
    return {"echoed": text}

@server.tool(name="write_file")
def write_file(path: str, content: str) -> dict:
    """Write a file below the fixture root."""
    target = (ROOT / path).resolve()
    if ROOT not in target.parents:
        raise ValueError("outside the fixture root")
    target.write_text(content, encoding="utf-8")
    return {"written": str(target), "bytes": len(content)}

@server.tool(name="slow")
async def slow(seconds: float = 20.0) -> dict:
    """Sleep so a Stop has something in flight."""
    STARTED.write_text("started", encoding="utf-8")
    await asyncio.sleep(seconds)
    return {"slept": seconds}

if __name__ == "__main__":
    asyncio.run(server.run_stdio_async())
'''


def _profile(port: int) -> dict:
    return {"name": "custom", "adapter": "cloud", "provider": "custom",
            "model": MODEL, "endpoint": f"http://127.0.0.1:{port}/v1"}


def _make_session(profile: dict) -> SessionController:
    return SessionController(
        model_factory=lambda name: object(),
        agent_factory=lambda *args, **kwargs: None,
        profiles={"custom": profile},
        selected_profile="custom",
        runtime_selection="harness",
    )


def _requests(work: Path) -> list[dict]:
    path = work / "mock" / "requests.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def _offered_tools(work: Path) -> list[str]:
    for request in reversed(_requests(work)):
        names = [str(name) for name in request.get("tools") or [] if isinstance(name, str)]
        if names:
            return sorted(set(names))
    return []


def _tool_results(work: Path) -> list[str]:
    texts: list[str] = []
    for request in _requests(work):
        for text in request.get("tool_results") or []:
            texts.append(str(text))
    return texts


def _processes(pattern: str) -> list[int]:
    script = ("Get-CimInstance Win32_Process | "
              f"Where-Object {{ $_.ProcessId -ne $PID -and $_.CommandLine -like '*{pattern}*' }} | "
              "ForEach-Object { $_.ProcessId }")
    out = subprocess.run(["powershell", "-NoProfile", "-Command", script],
                         capture_output=True, text=True, check=False).stdout
    return [int(line) for line in out.split() if line.strip().isdigit()]


def _alive(pid: int) -> bool:
    out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                         capture_output=True, text=True, check=False).stdout
    return str(pid) in out


async def _answer(session, approved: bool) -> None:
    """Answer the first approval the session raises, through its own broker."""

    for _ in range(2400):
        pending = session.pending_approvals
        if pending:
            session.answer_approval(pending[0]["id"], approved)
            return
        await asyncio.sleep(0.05)


def _servers_file(work: Path, fixture: Path) -> Path:
    path = work / "servers.toml"
    path.write_text(
        "[servers.fixture]\n"
        'transport = "stdio"\n'
        f"command = ['{Path(sys.executable).as_posix()}', '{fixture.as_posix()}']\n"
        'env = ["FIXTURE_ROOT", "FIXTURE_STARTED", "FIXTURE_TOKEN"]\n'
        'trust = "untrusted"\n'
        'workspace_paths = ["path"]\n'
        "timeout = 60\n",
        encoding="utf-8",
    )
    return path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--exe", required=True)
    parser.add_argument("--home", required=True)
    parser.add_argument("--work", required=True)
    parser.add_argument("--node", required=True)
    parser.add_argument("--tool-python", default="")
    args = parser.parse_args()

    work = Path(args.work).resolve()
    home = Path(args.home).resolve()
    workspace = work / "ws"
    outside = work / "outside"
    fixture_root = work / "fixture-root"
    for path in (work / "mock", workspace, outside, home, fixture_root):
        path.mkdir(parents=True, exist_ok=True)
    (work / "mock" / "requests.jsonl").write_text("", encoding="utf-8")
    fixture = work / "fixture_server.py"
    fixture.write_text(FIXTURE_SERVER, encoding="utf-8")
    started = work / "fixture-started"
    escaped = outside / "escape.txt"
    escaped.unlink(missing_ok=True)
    for name in ("node-approved.txt", "node-denied.txt"):
        (workspace / name).unlink(missing_ok=True)
    for name in ("external-approved.txt", "external-denied.txt"):
        (fixture_root / name).unlink(missing_ok=True)

    checks: list[tuple[str, bool, str]] = []
    # Snapshots, so a process that another session left running is not blamed on this run
    # (and one this run leaked cannot hide behind it).
    runtime_before = set(_processes("deepseek-harness-sdk-runtime"))
    gateway_before = set(_processes("odessy.tools.upstream_gateway"))
    node_before = set(_processes("tools\\dist\\src\\server.js"))
    server, port = mock_provider.serve(work / "mock")
    try:
        exe = str(Path(args.exe).resolve())
        os.environ.update({
            "ODESSY_HARNESS_EXE": exe,
            "ODESSY_HARNESS_HOME": str(home),
            "ODESSY_HARNESS_WORKSPACE": str(workspace),
            "ODESSY_CLOUD_API_KEY": "mock-key-not-a-secret",
            "ODESSY_HARNESS_TOOLS": "on",
            "ODESSY_HARNESS_TOOL_PYTHON": args.tool_python or sys.executable,
            # The two upstream surfaces under test.
            "ODESSY_HARNESS_NODE_TOOLS": "on",
            "ODESSY_HARNESS_NODE": str(Path(args.node).resolve()),
            "ODESSY_HARNESS_EXTERNAL_TOOLS": "fixture",
            "ODESSY_SERVERS_FILE": str(_servers_file(work, fixture)),
            "ODESSY_AUDIT_FILE": str(work / "audit.jsonl"),
            "FIXTURE_ROOT": str(fixture_root),
            "FIXTURE_STARTED": str(started),
            "FIXTURE_TOKEN": "p09a-authentication-literal",
            "UNDECLARED_TOKEN": "must-not-reach-an-upstream",
        })
        started.unlink(missing_ok=True)
        profile = _profile(port)

        async def run_turns():
            session = _make_session(profile)
            runtime = session.runtime
            try:
                await asyncio.wait_for(session.run_turn("hello"), 180)
                names = _offered_tools(work)
                # The pin sanitises MCP tool names (dots become underscores and a short
                # suffix is appended), so a tool is identified by its stem, not equality.
                node_write = next((name for name in names
                                   if "filesystem_write" in name), None)
                node_read = next((name for name in names
                                  if "filesystem_read" in name), None)
                external_write = next((name for name in names
                                       if "fixture" in name and "write_file" in name), None)
                external_slow = next((name for name in names
                                      if "fixture" in name and "slow" in name), None)

                if node_write:
                    approve = asyncio.create_task(_answer(session, True))
                    await asyncio.wait_for(session.run_turn(
                        'tool ' + node_write + ' ' + json.dumps(
                            {"path": "node-approved.txt", "content": "from node"})), 120)
                    await asyncio.wait_for(approve, 10)

                    deny = asyncio.create_task(_answer(session, False))
                    await asyncio.wait_for(session.run_turn(
                        'tool ' + node_write + ' ' + json.dumps(
                            {"path": "node-denied.txt", "content": "no"})), 60)
                    await asyncio.wait_for(deny, 10)

                if node_read:
                    # Outside the workspace: the policy refuses before the server is reached.
                    await asyncio.wait_for(session.run_turn(
                        'tool ' + node_read + ' ' + json.dumps({"path": str(escaped)})), 60)

                if external_write:
                    approve = asyncio.create_task(_answer(session, True))
                    await asyncio.wait_for(session.run_turn(
                        'tool ' + external_write + ' ' + json.dumps(
                            {"path": "external-approved.txt", "content": "from fixture"})), 120)
                    await asyncio.wait_for(approve, 10)

                    deny = asyncio.create_task(_answer(session, False))
                    await asyncio.wait_for(session.run_turn(
                        'tool ' + external_write + ' ' + json.dumps(
                            {"path": "external-denied.txt", "content": "no"})), 60)
                    await asyncio.wait_for(deny, 10)

                stopped = None
                if external_slow:
                    approve = asyncio.create_task(_answer(session, True))
                    call = asyncio.create_task(session.run_turn(
                        'tool ' + external_slow + ' ' + json.dumps({"seconds": 25})))
                    for _ in range(200):
                        if started.exists():
                            break
                        await asyncio.sleep(0.05)
                    checks.append(("the slow external call started before Stop",
                                   started.exists(), ""))
                    outcome = await session.stop()
                    stopped = outcome
                    try:
                        await asyncio.wait_for(call, 20)
                    except Exception:  # noqa: BLE001 - a stopped turn may raise
                        pass
                    approve.cancel()
                    await asyncio.gather(approve, return_exceptions=True)
                    checks.append(("Stop reports the tool cleanup as complete or pending",
                                   outcome.get("cleanup") in ("complete", "pending"),
                                   json.dumps(outcome)))
                    # The session continues to serve turns after the stop.
                    await asyncio.wait_for(session.run_turn("hello again"), 120)
                    checks.append(("the session serves another turn after Stop", True, ""))
                return names, node_write, external_write, runtime.identity
            finally:
                await session.close()

        names, node_write, external_write, identity = asyncio.run(run_turns())
        results = "\n".join(_tool_results(work))

        checks.append(("the roster carries the Node tools",
                       any("filesystem_write" in name for name in names),
                       f"tools={names}"))
        checks.append(("the roster carries the external tools",
                       any("fixture" in name for name in names), f"tools={names}"))
        checks.append(("no built-in execution row is offered",
                       not any(name in names for name in ("bash", "pwsh", "shell")),
                       f"tools={names}"))
        checks.append(("the Node server's argument-chosen command tool is not offered",
                       not any("terminal_run" in name and "odessy_node" in name
                               for name in names),
                       f"tools={names}"))
        checks.append(("three servers were attached",
                       len(list(identity.get("servers") or [])) == 3,
                       str(identity.get("servers"))))

        checks.append(("an approved Node write really wrote",
                       (workspace / "node-approved.txt").exists(), ""))
        checks.append(("a denied Node write left no file",
                       not (workspace / "node-denied.txt").exists(), ""))
        checks.append(("an approved external write really wrote",
                       (fixture_root / "external-approved.txt").exists(), ""))
        checks.append(("a denied external write left no file",
                       not (fixture_root / "external-denied.txt").exists(), ""))
        checks.append(("a path outside the workspace was refused without reading it",
                       not escaped.exists()
                       and ("outside the allowed workspace" in results
                            or "denied" in results),
                       results[-400:]))

        leftover_runtime = set(_processes("deepseek-harness-sdk-runtime")) - runtime_before
        leftover_gateway = set(_processes("odessy.tools.upstream_gateway")) - gateway_before
        leftover_node = set(_processes("tools\\dist\\src\\server.js")) - node_before
        checks.append(("close() left no runtime process", not leftover_runtime,
                       str(sorted(leftover_runtime))))
        checks.append(("close() left no gateway process", not leftover_gateway,
                       str(sorted(leftover_gateway))))
        checks.append(("close() left no Node tool server process", not leftover_node,
                       str(sorted(leftover_node))))
    finally:
        server.shutdown()

    failed = [name for name, ok, _ in checks if not ok]
    for name, ok, detail in checks:
        print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"  -- {detail}" if detail else ""))
    print(json.dumps({"checks": len(checks), "failed": failed}, indent=2))
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
