"""Pinned evidence for prompts 08 (closure) and 09: a real tool call through the pipeline.

What runs here, with nothing faked but the model:

    pinned runtime (0.2.1rc2) + real bundle
      -> the runtime spawns Odessy's own stdio tool server (session/new -> mcpServers)
        -> a model tool call arrives in that server
          -> Odessy's authority policy decides
            -> Odessy's CommandBridge confines with a real provider
              -> the command really runs (or really does not)

Checks:

1. the tool roster the model is offered is Odessy's own server (plus the clarification tool),
   with no built-in execution row;
2. an allowed read-only command executes and the **tool result the model receives** carries
   Odessy's `sandbox` block naming the provider that confined it — this is what closes 08:
   the retained enforcement is applied at the actual execution boundary;
3. an unapproved write is refused and leaves no file on disk (real OS check);
4. a call that tries to choose its own sandbox mode is refused before any confinement;
5. `close()` leaves no runtime or tool-server process behind.

    python smoke_p09.py --exe <pinned exe> --home <DSH_HOME> --work <dir> [--toolchain <dir>]
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
MARKER = "odessy-pipeline-ok"


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
    """The tool names the provider was offered in the most recent request."""

    for request in reversed(_requests(work)):
        names = [str(name) for name in request.get("tools") or [] if isinstance(name, str)]
        if names:
            return sorted(set(names))
    return []


def _tool_turns(work: Path) -> list[dict]:
    """What the model was shown after each tool ran, newest last.

    The fixture records the tool messages verbatim; each tool prints one JSON object, so a
    line that parses is a result and the rest is prose.
    """

    results: list[dict] = []
    for request in _requests(work):
        for text in request.get("tool_results") or []:
            try:
                results.append(json.loads(text))
            except (TypeError, json.JSONDecodeError):
                results.append({"raw": str(text)})
    return results


def _alive(pid: int) -> bool:
    out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                         capture_output=True, text=True, check=False).stdout
    return str(pid) in out


async def _approve_once(session, approved=True) -> None:
    """Answer the first approval the session raises, through its own broker.

    This is the same broker an interactive client uses (`pending_approvals` /
    `answer_approval`), so the consent travels the authenticated path, not a test-only
    shortcut inside the tool server.
    """

    for _ in range(2400):
        pending = session.pending_approvals
        if pending:
            session.answer_approval(pending[0]["id"], approved)
            return
        await asyncio.sleep(0.05)


def _sleeping_pids() -> list[int]:
    """Processes running the probe's long command, to prove Stop reached the tool."""

    script = ("Get-CimInstance Win32_Process | "
              "Where-Object { $_.ProcessId -ne $PID -and $_.CommandLine -like '*Start-Sleep 25*' } | "
              "ForEach-Object { $_.ProcessId }")
    out = subprocess.run(["powershell", "-NoProfile", "-Command", script],
                         capture_output=True, text=True, check=False).stdout
    return [int(line) for line in out.split() if line.strip().isdigit()]


def _tool_server_processes() -> list[int]:
    """PIDs whose command line is this pipeline's tool server.

    The tool server is the runtime's MCP child, so "the session ended cleanly" means the
    child is gone too — not only that the runtime exited.
    """

    script = ("Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
              "Where-Object { $_.CommandLine -like '*odessy.tools.stdio_server*' } | "
              "ForEach-Object { $_.ProcessId }")
    out = subprocess.run(["powershell", "-NoProfile", "-Command", script],
                         capture_output=True, text=True, check=False).stdout
    return [int(line) for line in out.split() if line.strip().isdigit()]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--exe", required=True)
    parser.add_argument("--home", required=True)
    parser.add_argument("--work", required=True)
    parser.add_argument("--tool-python", default="",
                        help="interpreter that can import odessy (default: this one)")
    args = parser.parse_args()

    work = Path(args.work).resolve()
    home = Path(args.home).resolve()
    workspace = work / "ws"
    outside = work / "outside"
    for path in (work / "mock", workspace, outside, home):
        path.mkdir(parents=True, exist_ok=True)
    (work / "mock" / "requests.jsonl").write_text("", encoding="utf-8")
    escaped = outside / "escape.txt"
    escaped.unlink(missing_ok=True)

    checks: list[tuple[str, bool, str]] = []
    server, port = mock_provider.serve(work / "mock")
    try:
        exe = str(Path(args.exe).resolve())
        for name in PLATFORM:
            if name in os.environ:
                os.environ.setdefault(name, os.environ[name])
        os.environ.update({
            "ODESSY_HARNESS_EXE": exe,
            "ODESSY_HARNESS_HOME": str(home),
            "ODESSY_HARNESS_WORKSPACE": str(workspace),
            "ODESSY_CLOUD_API_KEY": "mock-key-not-a-secret",
            # The tool surface is opt-in; this is the session that opts in.
            "ODESSY_HARNESS_TOOLS": "on",
            # The spike imports the checkout by path, so this process's interpreter is not
            # one that can import `odessy`. Point the tool server at one that can.
            "ODESSY_HARNESS_TOOL_PYTHON": args.tool_python or sys.executable,
        })
        profile = _profile(port)

        async def run_turns():
            session = _make_session(profile)
            try:
                names: list[str] = []
                seen: list[dict] = []
                await asyncio.wait_for(session.run_turn("hello"), 120)
                names = _offered_tools(work)
                tools = [name for name in names if name.startswith("mcp__")]
                call = next((name for name in tools if "terminal" in name), None)
                if call is None:
                    return names, [], None, []
                # 2: an allowed read-only command, through the real pipeline.
                await asyncio.wait_for(
                    session.run_turn(f'tool {call} {{"command": "echo {MARKER}"}}'), 120)
                deny_task = asyncio.create_task(_approve_once(session, False))
                # 3: an unapproved write outside the workspace.
                await asyncio.wait_for(session.run_turn('tool ' + call + ' ' + json.dumps(
                    {'command': "Set-Content -LiteralPath '" + str(escaped) + "' -Value x"})), 30)
                await asyncio.wait_for(deny_task, 5)
                # 4: a call that tries to choose its own sandbox mode.
                await asyncio.wait_for(
                    session.run_turn(
                        f'tool {call} {{"command": "echo hi", "mode": "danger-full-access"}}'),
                    120)
                approved_task = asyncio.create_task(_approve_once(session))
                await asyncio.wait_for(session.run_turn(
                    'tool ' + call + ' ' + json.dumps({'command': 'echo confirmed > confirmed.txt'})), 30)
                await asyncio.wait_for(approved_task, 5)
                checks.append(("approved operation executed", (workspace / "confirmed.txt").exists(), "broker approval"))
                approve_sleep = asyncio.create_task(_approve_once(session))
                sleeping = asyncio.create_task(session.run_turn(
                    'tool ' + call + ' ' + json.dumps({'command': 'Start-Sleep 25', 'timeout': 30})))
                running = []
                for _ in range(80):
                    running = list(session.runtime._control.running)
                    if running:
                        break
                    await asyncio.sleep(.1)
                checks.append(("long command started before Stop", bool(running), str(running)))
                outcome = await session.stop()
                checks.append(("Stop confirms cleanup before returning",
                               session.runtime._control.clean.is_set()
                               and outcome.get("cleanup") == "complete", str(outcome)))
                for _ in range(40):
                    left = [pid for pid in running if await asyncio.to_thread(_alive, pid)]
                    if not left:
                        break
                    await asyncio.sleep(.1)
                checks.append(("Stop killed active tool command", bool(running) and not left, str(left)))
                try:
                    await asyncio.wait_for(sleeping, 10)
                except Exception:
                    pass
                approve_sleep.cancel()
                await asyncio.gather(approve_sleep, return_exceptions=True)
                await asyncio.wait_for(session.run_turn('tool ' + call + ' ' + json.dumps(
                    {'command': 'echo after-stop'})), 30)
                checks.append(("session accepts a new tool turn after Stop", True, "turn completed"))
                seen = _tool_turns(work)
                pid = session.runtime._process.pid if session.runtime._process else 0
                return names, seen, call, (pid, dict(session.runtime.identity))
            finally:
                await session.close()

        names, seen, call, tail = asyncio.run(run_turns())
        checks.append(("the model is offered Odessy's own tool server",
                       call is not None, f"tools={names}"))
        checks.append(("no built-in execution row is offered",
                       not any(name in names for name in ("bash", "pwsh", "shell")),
                       f"tools={names}"))

        ok_result = next((item for item in seen
                          if MARKER in str(item.get("stdout") or "")), None)
        checks.append(("the allowed command really ran", ok_result is not None,
                       json.dumps(ok_result)[:300] if ok_result else "no result with the marker"))
        sandbox = (ok_result or {}).get("sandbox") or {}
        checks.append(("the result names the provider that confined it",
                       sandbox.get("provider_id") not in (None, "", "unconfined"),
                       json.dumps(sandbox)[:300]))
        # The block reports what the *session's* policy asked for, not what the call asked.
        checks.append(("the confinement is the session's, not the call's",
                       sandbox.get("network") in ("open", "denied")
                       and sandbox.get("hidden_paths") == 0,
                       json.dumps({k: sandbox.get(k) for k in ("network", "hidden_paths")})))

        refused = [item for item in seen if any(word in str(item.get("stderr") or "") for word in ("confirmation", "not approved"))]
        checks.append(("the unapproved write was refused", bool(refused),
                       json.dumps([item.get("stderr") for item in seen])[:300]))
        checks.append(("the unapproved write left no file", not escaped.exists(), str(escaped)))

        mode_refusals = [item for item in seen
                         if "fixed by the session" in json.dumps(item)]
        checks.append(("a call cannot choose its own sandbox mode", bool(mode_refusals),
                       json.dumps([item.get("stderr") for item in seen])[:300]))

        if tail:
            pid, identity = tail
            checks.append(("the runtime reports the tool server it launched",
                           identity.get("tools") == "odessy", str(identity.get("tools"))))
            # The tool server is the runtime's child; closing the session must end both.
            checks.append(("close() left no runtime process", pid == 0 or not _alive(pid),
                           f"pid={pid}"))
            leftover = _tool_server_processes()
            checks.append(("close() left no tool-server process", not leftover,
                           f"pids={leftover}"))
    finally:
        server.shutdown()

    failed = [name for name, ok, _ in checks if not ok]
    for name, ok, detail in checks:
        print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"  — {detail}" if detail else ""))
    print(json.dumps({"checks": len(checks), "failed": failed}, indent=2))
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
