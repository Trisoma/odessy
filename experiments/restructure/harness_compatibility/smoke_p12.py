"""Prompt 12 smoke: the HTTP application creates and serves a Harness session.

This drives the *application* boundary (bearer-authenticated HTTP), not the adapter
directly: create a session with an explicit runtime, run a turn, watch live deltas settle,
Stop a streaming turn, refresh and restore, and keep a second session open beside it. The
pinned runtime and the deterministic provider fixture are real; only the model is a fixture.

Usage (run from the repository root):
    python experiments/restructure/harness_compatibility/smoke_p12.py \
        --exe <pinned runtime exe> --home <fresh dsh home> --work <work dir> \
        --toolchain <node/pnpm toolchain dir>
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import mock_provider  # noqa: E402

from odessy.application.api import create_app  # noqa: E402
from odessy.application.bootstrap import create_controller  # noqa: E402
from odessy.application.durable import SessionStore  # noqa: E402
from odessy.collaboration.config import load_config  # noqa: E402

MODEL = "mock-model"

#: The external MCP server used for the consent scenarios, written into the run's own work
#: directory. It is a real stdio server with real effects; only its tools are trivial.
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


def _requests(work: Path) -> list[dict]:
    path = work / "mock" / "requests.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def _processes(match: str) -> list[int]:
    if sys.platform != "win32":
        return []
    import subprocess

    completed = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "Get-CimInstance Win32_Process | Select-Object ProcessId,CommandLine | ConvertTo-Json -Compress"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
    try:
        rows = json.loads(completed.stdout or "[]")
    except ValueError:
        return []
    if isinstance(rows, dict):
        rows = [rows]
    return [int(row["ProcessId"]) for row in rows
            if match in str(row.get("CommandLine") or "")]


def _install_bundle(exe: str, home: Path, toolchain: Path) -> tuple[bool, str]:
    import subprocess

    completed = subprocess.run(
        [sys.executable, str(HERE / "install_plugin.py"), "--exe", exe,
         "--home", str(home), "--toolchain", str(toolchain)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
    detail = (completed.stdout or completed.stderr or "").strip().splitlines()[-1:] or [""]
    return completed.returncode == 0, detail[0][:200]


async def _wait_idle(api, sid: str, timeout: float = 180.0) -> dict:
    # The turn runs in a task created after the 202; give it a beat to claim the session.
    await asyncio.sleep(0.4)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        snapshot = (await api.get(f"/v1/sessions/{sid}")).json()
        if snapshot.get("running") is not True:
            return snapshot
        await asyncio.sleep(0.2)
    raise TimeoutError(f"session {sid} never became idle")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--exe", default="")
    parser.add_argument("--home", required=True)
    parser.add_argument("--work", required=True)
    parser.add_argument("--toolchain", default="")
    parser.add_argument("--installed-root", default="",
                        help="exercise a complete installed payload instead of --exe")
    parser.add_argument("--versioned-root", default="",
                        help="exercise a packaged/versioned installation (<root>/versions/<id>)")
    args = parser.parse_args()
    if not args.exe and not args.installed_root and not args.versioned_root:
        parser.error("give --exe, --installed-root or --versioned-root")

    work = Path(args.work).resolve()
    home = Path(args.home).resolve()
    workspace = work / "ws"
    state = work / "state"
    for path in (work / "mock", workspace, home, state):
        path.mkdir(parents=True, exist_ok=True)
    (work / "mock" / "requests.jsonl").write_text("", encoding="utf-8")

    checks: list[tuple[str, bool, str]] = []

    def check(name: str, passed: bool, detail: str = "") -> None:
        checks.append((name, bool(passed), detail))

    fixture_root = work / "fixture-root"
    fixture_root.mkdir(parents=True, exist_ok=True)
    fixture = work / "fixture_server.py"
    fixture.write_text(FIXTURE_SERVER, encoding="utf-8")
    started = work / "fixture-started"
    started.unlink(missing_ok=True)
    for name in ("approved.txt", "denied.txt"):
        (fixture_root / name).unlink(missing_ok=True)

    before = set(_processes("deepseek-harness-sdk-runtime"))
    # The pinned path contains a non-ASCII directory; the plugin installer is a Python
    # subprocess whose captured stdout would otherwise be encoded with the console code
    # page on Windows and fail before writing the profile.
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    os.environ.setdefault("PYTHONUTF8", "1")
    server, port = mock_provider.serve(work / "mock")
    try:
        environment = {
            "ODESSY_HARNESS_HOME": str(home),
            "ODESSY_HARNESS_WORKSPACE": str(workspace),
            "ODESSY_CLOUD_API_KEY": "mock-key-not-a-secret",
            # A real reviewed tool surface: a restore refuses an unverified roster (prompt
            # 10's fail-closed rule), so the smoke needs one to prove resume end to end.
            "ODESSY_HARNESS_TOOLS": "on",
            "ODESSY_SESSION_STATE_DIR": str(state),
            # A consent-requiring surface: a write tool is decided by the gateway's control
            # channel, i.e. by this application's approval broker.
            "ODESSY_HARNESS_EXTERNAL_TOOLS": "fixture",
            "ODESSY_SERVERS_FILE": str(_servers_file(work, fixture)),
            "FIXTURE_ROOT": str(fixture_root),
            "FIXTURE_STARTED": str(started),
            "FIXTURE_TOKEN": "p09a-authentication-literal",
        }
        installed_payload = Path(args.installed_root).resolve() if args.installed_root else None
        versioned_root = Path(args.versioned_root).resolve() if args.versioned_root else None
        version_directory = None
        if versioned_root is not None:
            # The packaged layout: the active version owns python/desktop/node/tools/harness.
            # The environment comes from the same function the installed launcher uses, so this
            # run proves the resolver, not a hand-written path list.
            from odessy.setup.installed import harness_environment, spec as installed_spec
            info = installed_spec(versioned_root)
            version_directory = versioned_root / "versions" / info["version"]
            installed_payload = version_directory
            environment.update(harness_environment(info))
            environment.update({
                "ODESSY_HARNESS_NODE_TOOLS": "on",
                "ODESSY_HARNESS_TOOL_PYTHON": info["python"],
                "ODESSY_RUNTIME_ENGINE": "harness",
            })
        elif installed_payload is not None:
            # The installed resolver, not the developer override: the runtime directory, the
            # private Node and the staged tool server all come from the payload, and the
            # installed virtualenv is the tool python.
            environment.update({
                "ODESSY_HARNESS_RUNTIME_DIR": str(installed_payload / "runtime" / "harness-runtime"),
                "ODESSY_HARNESS_NODE": str(installed_payload / "runtime" / "node-runtime" / "node.exe"),
                "ODESSY_NODE_TOOLS_DIR": str(installed_payload / "application" / "tools"),
                "ODESSY_HARNESS_NODE_TOOLS": "on",
                "ODESSY_HARNESS_TOOL_PYTHON": str(
                    installed_payload / "dependencies" / "venv" / "Scripts" / "python.exe"),
                "ODESSY_RUNTIME_ENGINE": "harness",
            })
        else:
            environment.update({
                "ODESSY_HARNESS_EXE": str(Path(args.exe).resolve()),
                "ODESSY_HARNESS_TOOL_PYTHON": sys.executable,
            })
        os.environ.update(environment)
        installed, install_detail = (False, "no --toolchain given")
        if installed_payload is not None:
            # The packaged plugin ships inside the application and is composed at launch.
            if version_directory is not None:
                site = (version_directory / "Lib" / "site-packages" if os.name == "nt"
                        else version_directory / "lib")
                bundle = (site / "odessy" / "runtime_backends" / "harness" / "bundle"
                          / "odessy-stream-bridge" / "lib" / "index.js")
            else:
                bundle = (installed_payload / "application" / "src" / "odessy" / "runtime_backends"
                          / "harness" / "bundle" / "odessy-stream-bridge" / "lib" / "index.js")
            installed, install_detail = bundle.is_file(), str(bundle)
        elif args.toolchain:
            installed, install_detail = _install_bundle(
                str(Path(args.exe).resolve()), home, Path(args.toolchain).resolve())
        check("the packaged stream bridge is present for this run", installed,
              install_detail)

        profile = _profile(port)
        store = SessionStore(state)
        # The session's provider route comes from its model profile, exactly as in the app:
        # a collaboration participant record pointing at the deterministic provider.
        from odessy.collaboration.config import (
            CollaborationConfig, CollaborationSettings, ParticipantProfile, save_config,
        )

        collaboration_file = work / "collaboration.toml"
        save_config(CollaborationConfig(
            CollaborationSettings(),
            {profile["name"]: ParticipantProfile(
                profile["name"], "cloud", provider=profile["provider"],
                model=profile["model"], endpoint=profile["endpoint"])}),
            collaboration_file)
        os.environ["ODESSY_COLLABORATION_FILE"] = str(collaboration_file)

        async def factory(restore=None, runtime=None):
            kwargs = dict(workspace=workspace, use_fake_model=True,
                          selected_profile=profile["name"], durable=store, restore=restore)
            if installed_payload is None:
                # The committed 1497d2b controller predates the per-request selection seam;
                # an installed run selects the engine through ODESSY_RUNTIME_ENGINE instead.
                kwargs["runtime_selection"] = runtime
            return await create_controller(**kwargs)

        app = create_app(factory, token="secret", config=load_config())
        findings: dict = {}

        async def scenario(app) -> None:
            try:
                import httpx
            except ModuleNotFoundError:
                # The installed MCP closure provides the compatible httpx2 client.
                import httpx2 as httpx

            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test",
                                         headers={"Authorization": "Bearer secret"},
                                         timeout=240) as api:
                # -- create on an explicit runtime ------------------------------------
                created = await api.post("/v1/sessions", json={} if installed_payload else {"runtime": "harness"})
                findings["create_status"] = created.status_code
                body = created.json()
                sid = body["id"]
                check("an explicit runtime creates a session", created.status_code == 201,
                      str(created.status_code))
                if installed_payload is None:
                    check("the session reports the chosen backend",
                          body.get("engine", {}).get("name") == "harness"
                          and body["engine"].get("source") == "explicit",
                          json.dumps(body.get("engine", {}).get("name")))

                # -- prompt -> live delta -> result ----------------------------------
                sent = await api.post(f"/v1/sessions/{sid}/turns",
                                      json={"text": "ping the prompt twelve path"})
                check("a turn is accepted", sent.status_code == 202, str(sent.status_code))
                settled = await _wait_idle(api, sid)
                events = settled.get("events") or []
                types = [event["type"] for event in events]
                findings["turn_event_types"] = types
                check("live deltas reached the client", "delta" in types, str(types))
                check("the turn produced a committed result", "result" in types, str(types))
                assistant = [event for event in events if event["type"] == "transcript"
                             and event["data"].get("role") == "assistant"]
                check("the answer was recorded once", len(assistant) == 1,
                      str(len(assistant)))
                if installed_payload is None:
                    check("the engine identity is reported after startup",
                          bool((settled.get("engine") or {}).get("identity", {}).get("version")),
                          json.dumps((settled.get("engine") or {}).get("identity", {}))[:120])
                if installed_payload is not None:
                    selection = next((event for event in events
                                      if event["type"] == "selection"), {})
                    check("the installed launch runs on the harness engine",
                          (selection.get("data") or {}).get("engine") == "harness",
                          json.dumps(selection.get("data"))[:160])

                # -- approved then denied side-effecting tool, through the app ---------
                if not _requests(work):
                    print("NO-MODEL-REQUEST", json.dumps(
                        [(event.get("type"), str(event.get("data"))[:160])
                         for event in settled.get("events") or []])[:900],
                        file=sys.stderr, flush=True)
                roster = [str(name) for name in (_requests(work)[0].get("tools") or [])]
                write_tool = next((name for name in roster
                                   if "write" in name and "fixture" in name), None)
                check("a consent-requiring external tool is mounted", bool(write_tool),
                      str(roster))
                if write_tool:
                    async def ask_for_approval(text):
                        await api.post(f"/v1/sessions/{sid}/turns", json={"text": text})
                        deadline = time.monotonic() + 40
                        while time.monotonic() < deadline:
                            current = (await api.get(f"/v1/sessions/{sid}")).json()
                            pending = current.get("pending_approvals") or []
                            if pending:
                                return pending[0], current
                            if not current.get("running"):
                                return None, current
                            await asyncio.sleep(0.1)
                        return None, {}

                    approved_path = fixture_root / "approved.txt"
                    approval, _ = await ask_for_approval(
                        f"tool {write_tool} " + json.dumps(
                            {"path": "approved.txt", "content": "p12-approved"}))
                    check("a side-effecting tool call asks the application",
                          bool(approval), json.dumps(approval)[:160])
                    if approval:
                        await api.post(f"/v1/sessions/{sid}/approvals/{approval['id']}",
                                       json={"approved": True})
                    await _wait_idle(api, sid)
                    check("the approved call wrote through the pinned runtime",
                          approved_path.is_file()
                          and "p12-approved" in approved_path.read_text(encoding="utf-8"),
                          str(approved_path.is_file()))

                    denied_path = fixture_root / "denied.txt"
                    denial, _ = await ask_for_approval(
                        f"tool {write_tool} " + json.dumps(
                            {"path": "denied.txt", "content": "p12-denied"}))
                    check("a second side-effecting call asks again", bool(denial),
                          json.dumps(denial)[:160])
                    if denial:
                        await api.post(f"/v1/sessions/{sid}/approvals/{denial['id']}",
                                       json={"approved": False})
                    await _wait_idle(api, sid)
                    check("the denied call left no file", not denied_path.exists(),
                          str(denied_path.exists()))
                    results = [str(item.get("tool_results")) for item in _requests(work)]
                    check("the denial was reported to the model", any(
                        "not approved" in text.lower() or "denied" in text.lower()
                        or "refused" in text.lower() for text in results),
                        str(results[-2:])[:240])

                # -- bundled Node tool execution (installed node-runtime) -------------
                node_write = next((name for name in roster
                                   if "filesystem_write" in name and "odessy_node" in name), None)
                check("the bundled Node file tool is mounted", bool(node_write),
                      str([name for name in roster if "odessy_node" in name])[:200])
                if node_write:
                    node_path = workspace / "node-approved.txt"
                    node_denied = workspace / "node-denied.txt"
                    for path in (node_path, node_denied):
                        path.unlink(missing_ok=True)
                    node_approval, _ = await ask_for_approval(
                        f"tool {node_write} " + json.dumps(
                            {"path": "node-approved.txt", "content": "from bundled node"}))
                    if node_approval:
                        await api.post(f"/v1/sessions/{sid}/approvals/{node_approval['id']}",
                                       json={"approved": True})
                    await _wait_idle(api, sid)
                    check("the approved bundled Node tool wrote the file",
                          node_path.is_file() and "from bundled node" in
                          node_path.read_text(encoding="utf-8"), str(node_path.is_file()))
                    node_denial, _ = await ask_for_approval(
                        f"tool {node_write} " + json.dumps(
                            {"path": "node-denied.txt", "content": "must not exist"}))
                    if node_denial:
                        await api.post(f"/v1/sessions/{sid}/approvals/{node_denial['id']}",
                                       json={"approved": False})
                    await _wait_idle(api, sid)
                    check("the denied bundled Node tool left no file",
                          not node_denied.exists(), str(node_denied.exists()))
                # -- clarification question on a FRESH session ------------------------
                # A session that already carries tool results makes the provider skip the
                # question branch (it counts tool messages), so asking there would prove
                # nothing. A fresh session exercises the real ask/answer path.
                fresh_question = await api.post(
                    "/v1/sessions", json={} if installed_payload else {"runtime": "harness"})
                question_sid = fresh_question.json()["id"]
                await api.post(f"/v1/sessions/{question_sid}/turns",
                               json={"text": "question please"})
                await asyncio.sleep(0.4)
                question = None
                deadline = time.monotonic() + 60
                while time.monotonic() < deadline:
                    current = (await api.get(f"/v1/sessions/{question_sid}")).json()
                    pending = current.get("pending_questions") or []
                    if pending:
                        question = pending[0]
                        break
                    if not current.get("running"):
                        break
                    await asyncio.sleep(0.1)
                check("a clarification question reached the application", bool(question),
                      json.dumps(question)[:160])
                if question:
                    await api.post(f"/v1/sessions/{question_sid}/questions/{question['id']}",
                                   json={"answer": "Yes"})
                answered = await _wait_idle(api, question_sid)
                transcripts = [event["data"].get("text")
                               for event in answered.get("events") or []
                               if event["type"] == "transcript"]
                check("the same turn continued after the answer",
                      "Answer received; continuing." in str(transcripts[-1:]),
                      str(transcripts[-1:])[:200])
                await api.delete(f"/v1/sessions/{question_sid}")

                # -- Stop a streaming turn -------------------------------------------
                await api.post(f"/v1/sessions/{sid}/turns", json={"text": "slow please"})
                deadline = time.monotonic() + 30
                while time.monotonic() < deadline:
                    current = (await api.get(f"/v1/sessions/{sid}")).json()
                    if any(event["type"] == "delta" for event in current.get("events") or []):
                        break
                    await asyncio.sleep(0.2)
                stopped = await api.post(f"/v1/sessions/{sid}/stop")
                outcome = stopped.json()
                findings["stop"] = outcome
                check("Stop answers what was established",
                      outcome.get("execution") in {"stopped", "unconfirmed", "idle"},
                      json.dumps(outcome)[:160])
                await _wait_idle(api, sid)
                after_stop = (await api.get(f"/v1/sessions/{sid}")).json()
                check("the stop is visible in the event stream",
                      any(event["type"] == "stopped"
                          for event in after_stop.get("events") or []), "")

                # -- refresh, then close and restore ---------------------------------
                refreshed = (await api.get(f"/v1/sessions/{sid}")).json()
                check("a refresh returns the same session",
                      refreshed.get("id") == sid
                      and (installed_payload is not None
                           or refreshed.get("engine", {}).get("name") == "harness"), "")
                await api.delete(f"/v1/sessions/{sid}")
                restored = await api.post("/v1/sessions",
                                          json={"restore": sid} if installed_payload else {"restore": sid, "runtime": "harness"})
                findings["restore_status"] = restored.status_code
                findings["restore_detail"] = restored.text[:400]
                print("RESTORE-RESPONSE", restored.status_code, restored.text[:400],
                      file=sys.stderr, flush=True)
                check("the closed session restores", restored.status_code == 201,
                      f"{restored.status_code}: {restored.text[:240]}")
                resumed = restored.json()
                check("the restore says it resumed the runtime session",
                      (resumed.get("durability") or {}).get("resumed") is True,
                      json.dumps(resumed.get("durability"))[:160])
                await api.post(f"/v1/sessions/{sid}/turns", json={"text": "after restore"})
                await _wait_idle(api, sid)
                history = _requests(work)
                findings["model_requests"] = len(history)
                check("the model saw the restored history",
                      len(history) >= 2
                      and int(history[-1].get("n_messages") or 0)
                      > int(history[0].get("n_messages") or 0),
                      str([item.get("n_messages") for item in history]))

                # -- two sessions ----------------------------------------------------
                other = await api.post("/v1/sessions")
                if installed_payload is None:
                    check("a session without a runtime keeps the default",
                          (other.json().get("engine") or {}).get("name") == "legacy",
                          json.dumps((other.json().get("engine") or {}).get("name")))
                both = [(await api.get(f"/v1/sessions/{sid}")).json(),
                        (await api.get(f"/v1/sessions/{other.json()['id']}")).json()]
                if installed_payload is None:
                    names = [(entry.get("engine") or {}).get("name") for entry in both]
                    check("two sessions keep two backends", set(names) == {"harness", "legacy"},
                          str(names))
                else:
                    check("two sessions keep distinct identities",
                          both[0].get("id") and both[1].get("id")
                          and both[0]["id"] != both[1]["id"],
                          str([entry.get("id") for entry in both]))
                for entry in both:
                    if entry.get("id"):
                        await api.delete(f"/v1/sessions/{entry['id']}")

        asyncio.run(scenario(app))
        (work / "findings.json").write_text(
            json.dumps(findings, indent=2, ensure_ascii=False), encoding="utf-8")
        leaked = sorted(set(_processes("deepseek-harness-sdk-runtime")) - before)
        check("no runtime child is left behind", not leaked, str(leaked))
    finally:
        server.shutdown()

    passed = sum(1 for _, ok, _ in checks if ok)
    print(json.dumps({"checks": [{"check": name, "passed": ok, "detail": detail}
                                 for name, ok, detail in checks],
                      "summary": f"{passed}/{len(checks)} checks passed"},
                     indent=2, ensure_ascii=False))
    return 0 if passed == len(checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
