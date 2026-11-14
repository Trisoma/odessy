"""After a restart, does a resumed session still have its reviewed tool servers?

Prompt 10 must reconnect an app session to a durable runtime session, and prompt 09a made the
tool surface (Odessy's own server, the Node server, third-party gateways) part of the guarded
boundary. `session/new` carries `mcpServers`; `session/resume` has no obvious place for them, and
the durable log may well remember the servers the session was created with. Either way Odessy has
to know the answer to:

1. Does `session/resume` with `mcpServers: []` come back **tool-less** or with the persisted
   servers re-launched?
2. Does `session/resume` **accept** a fresh `mcpServers` list, and does it then take effect?
3. If the persisted server config is stale (its command no longer exists), does resume fail, or
   does it resume and fail only when a tool is called?

The fixture gateway is a real stdio MCP server, so a successful `echo` call proves the tool
surface is genuinely re-established rather than merely listed.

    python probe_resume_tools.py --exe <pinned exe> --home <DSH_HOME> --work <dir>
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import mock_provider  # noqa: E402
from acp_client import AcpClient, AcpError  # noqa: E402

PLATFORM = ("PATH", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "PATHEXT",
            "TEMP", "TMP", "USERPROFILE", "APPDATA", "LOCALAPPDATA")
FIXTURE = HERE.parents[2] / "tests" / "fixtures" / "mcp_fixture_server.py"


def environment(home: Path, base_url: str) -> dict[str, str]:
    environ = {name: os.environ[name] for name in PLATFORM if name in os.environ}
    environ.update({"DSH_HOME": str(home), "DEEPSEEK_BASE_URL": base_url,
                    "DEEPSEEK_API_KEY": "mock-key-not-a-secret"})
    return environ


def describe(error: BaseException) -> dict:
    if isinstance(error, AcpError):
        return {"ok": False, "code": error.code, "message": str(error)[:300]}
    return {"ok": False, "error": type(error).__name__, "message": str(error)[:300]}


def requests(work: Path) -> list[dict]:
    path = work / "mock" / "requests.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def tool_names(entry: dict) -> list[str]:
    names = []
    for tool in entry.get("tools") or []:
        if isinstance(tool, str):
            names.append(tool)
        elif isinstance(tool, dict):
            names.append(((tool.get("function") or {}).get("name")) or tool.get("name") or "")
    return [name for name in names if name]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--exe", required=True)
    parser.add_argument("--home", required=True)
    parser.add_argument("--work", required=True)
    args = parser.parse_args()

    work = Path(args.work).resolve()
    home = Path(args.home).resolve()
    workspace = work / "ws"
    for path in (work / "mock", workspace, home):
        path.mkdir(parents=True, exist_ok=True)
    (work / "mock" / "requests.jsonl").write_text("", encoding="utf-8")

    findings: dict = {}
    server, port = mock_provider.serve(work / "mock")
    env = environment(home, f"http://127.0.0.1:{port}/v1")
    exe = str(Path(args.exe).resolve())
    servers = [{"name": "odessy_fixture", "command": sys.executable,
                "args": [str(FIXTURE.resolve())], "env": []}]

    def last_tools() -> list[str]:
        entries = requests(work)
        return tool_names(entries[-1]) if entries else []

    try:
        # ---- 1: a session created with the fixture server -----------------------
        first = AcpClient(exe, env=env, cwd=str(workspace))
        first.initialize()
        session_id = first.new_session(cwd=str(workspace), mcp_servers=servers)["sessionId"]
        first.prompt(session_id, "hello there")
        created_tools = last_tools()
        findings["at_creation"] = {"tools": created_tools}
        echo = next((name for name in created_tools if name.endswith("__echo") or "__echo_" in name), None)
        findings["echo_name"] = echo
        if echo is not None:
            try:
                turn = first.prompt(session_id, f'tool {echo} {{"text": "before-restart"}}')
                findings["call_before_restart"] = {"ok": True, "stop_reason": turn.stop_reason}
            except BaseException as error:  # noqa: BLE001
                findings["call_before_restart"] = describe(error)
        first.close_session(session_id)
        first.close()

        # ---- 2: resume in a new process, with no servers passed -----------------
        second = AcpClient(exe, env=env, cwd=str(workspace))
        second.initialize()
        try:
            second.resume_session(session_id, cwd=str(workspace))
            findings["resume_no_servers"] = {"ok": True}
            second.prompt(session_id, "hello again")
            findings["resume_no_servers"]["tools"] = last_tools()
            if echo is not None:
                try:
                    turn = second.prompt(session_id, f'tool {echo} {{"text": "after-restart"}}')
                    findings["resume_no_servers"]["call"] = {"ok": True,
                                                             "stop_reason": turn.stop_reason}
                    findings["resume_no_servers"]["call"]["tool_results"] = [
                        str(item)[:200] for item in (requests(work)[-1].get("tool_results") or [])]
                except BaseException as error:  # noqa: BLE001
                    findings["resume_no_servers"]["call"] = describe(error)
        except BaseException as error:  # noqa: BLE001
            findings["resume_no_servers"] = describe(error)
        second.close()

        # ---- 3: resume in a new process, passing the servers again --------------
        third = AcpClient(exe, env=env, cwd=str(workspace))
        third.initialize()
        try:
            third.call("session/resume", {"sessionId": session_id, "cwd": str(workspace),
                                          "mcpServers": servers})
            findings["resume_with_servers"] = {"ok": True}
            third.prompt(session_id, "hello a third time")
            findings["resume_with_servers"]["tools"] = last_tools()
            if echo is not None:
                try:
                    turn = third.prompt(session_id, f'tool {echo} {{"text": "after-repass"}}')
                    findings["resume_with_servers"]["call"] = {"ok": True,
                                                               "stop_reason": turn.stop_reason}
                except BaseException as error:  # noqa: BLE001
                    findings["resume_with_servers"]["call"] = describe(error)
        except BaseException as error:  # noqa: BLE001
            findings["resume_with_servers"] = describe(error)
        third.close()

        # ---- 4: resume where the persisted server command no longer exists ------
        broken = AcpClient(exe, env=env, cwd=str(workspace))
        broken.initialize()
        broken_id = broken.new_session(cwd=str(workspace), mcp_servers=[{
            "name": "odessy_fixture", "command": sys.executable,
            "args": [str(FIXTURE.resolve())], "env": []}])["sessionId"]
        broken.prompt(broken_id, "hello broken")
        broken.close_session(broken_id)
        broken.close()
        FIXTURE.resolve().rename(FIXTURE.resolve().with_suffix(".moved"))
        gone = AcpClient(exe, env=env, cwd=str(workspace))
        gone.initialize()
        try:
            gone.resume_session(broken_id, cwd=str(workspace))
            findings["stale_server"] = {"resume_ok": True}
            try:
                entry = gone.prompt(broken_id, "hello with a dead server")
                findings["stale_server"]["turn"] = {"stop_reason": entry.stop_reason}
                findings["stale_server"]["tools"] = last_tools()
            except BaseException as error:  # noqa: BLE001
                findings["stale_server"]["turn"] = describe(error)
        except BaseException as error:  # noqa: BLE001
            findings["stale_server"] = describe(error)
        gone.close()
        FIXTURE.resolve().with_suffix(".moved").rename(FIXTURE.resolve())
    finally:
        server.shutdown()

    print(json.dumps(findings, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
