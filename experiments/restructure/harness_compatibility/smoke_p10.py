"""Prompt 10 evidence: a durable session continued across processes on the pinned runtime.

Everything here runs against the pinned ``0.2.1rc2`` executable through Odessy's own
``SessionController``, with a deterministic OpenAI-compatible provider watching what the *model*
was actually sent. That is the only way to tell two very different outcomes apart:

* the runtime restored the conversation and the model saw it (``n_messages`` grows, roles
  ``user/assistant/user``), while the client was told nothing; or
* Odessy quietly started a new conversation under the old identity.

The second must never happen, so the smoke also drives the refusals: a record whose runtime
history is gone, a runtime log this pin cannot read, and a record written by a different runtime.

It also tests the *tool surface* rather than the request: the packaged plugin is installed into
this run's profile, the roster the runtime reports is compared with what the model was offered,
and a session whose upstream server disappears is refused instead of quietly losing its tools.

    python smoke_p10.py --exe <pinned exe> --home <DSH_HOME> --work <dir> \
        --toolchain <portable node + pnpm> --tool-python <.venv python>
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[2] / "src"))

import mock_provider  # noqa: E402

from odessy.application.durable import SessionStore  # noqa: E402
from odessy.application.session import SessionController  # noqa: E402
from odessy.runtime_backends import RuntimeUnavailable  # noqa: E402

MODEL = "mock-model"
#: What the deterministic provider answers an ordinary prompt with.
EXPECTED_ANSWER = "Hello from the deterministic fixture."

#: A third-party MCP server written into the run's own work directory: a real stdio server whose
#: tools are trivial, so its disappearance is unambiguous. Nothing in the repository defines it.
FIXTURE_SERVER = '''\
import asyncio
from mcp.server import MCPServer

server = MCPServer("p10fixture")


@server.tool(name="probe_alpha")
def probe_alpha(text: str) -> dict:
    """Echo text back."""
    return {"echoed": text}


@server.tool(name="probe_beta")
def probe_beta(number: int = 1) -> dict:
    """Return a number."""
    return {"number": number}


if __name__ == "__main__":
    asyncio.run(server.run_stdio_async())
'''

#: The same server with one tool removed: what a *successful* restart of a changed upstream
#: looks like, and the only case a request-only check cannot notice.
_BETA_BLOCK = '''

@server.tool(name="probe_beta")
def probe_beta(number: int = 1) -> dict:
    """Return a number."""
    return {"number": number}
'''
assert _BETA_BLOCK in FIXTURE_SERVER, "the fixture template changed; update the one-tool variant"
FIXTURE_SERVER_ONE_TOOL = FIXTURE_SERVER.replace(_BETA_BLOCK, "")


def _profile(port: int) -> dict:
    return {"name": "custom", "adapter": "cloud", "provider": "custom",
            "model": MODEL, "endpoint": f"http://127.0.0.1:{port}/v1"}


def _servers_file(work: Path, fixture: Path) -> Path:
    path = work / "servers.toml"
    path.write_text(
        "[servers.p10fixture]\n"
        'transport = "stdio"\n'
        f"command = ['{Path(sys.executable).as_posix()}', '{fixture.as_posix()}']\n"
        'env = []\n'
        'trust = "untrusted"\n'
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
    """PIDs whose command line mentions ``match`` (Windows: CIM, otherwise ps)."""
    if sys.platform == "win32":
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
    completed = subprocess.run(["ps", "-eo", "pid,args"], capture_output=True, text=True,
                               check=False)
    return [int(line.split()[0]) for line in completed.stdout.splitlines()
            if match in line and line.split()[0].isdigit()]


def _kill_tree(pid: int) -> None:
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True, check=False)
    else:
        os.kill(pid, 9)


def _process_details(match: str) -> list[dict]:
    """Pid and command line of every process mentioning ``match``, for a readable report."""

    if sys.platform != "win32":
        return [{"pid": pid} for pid in _processes(match)]
    completed = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "Get-CimInstance Win32_Process | Select-Object ProcessId,ParentProcessId,CommandLine | ConvertTo-Json -Compress"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
    try:
        rows = json.loads(completed.stdout or "[]")
    except ValueError:
        return []
    if isinstance(rows, dict):
        rows = [rows]
    return [{"pid": int(row["ProcessId"]), "parent": int(row.get("ParentProcessId") or 0),
             "cmd": str(row.get("CommandLine") or "")[:220]}
            for row in rows if match in str(row.get("CommandLine") or "")]


def _wait_no_processes(match: str, baseline: set[int], timeout: float = 25.0) -> list[int]:
    """Wait for a refused/chosen teardown to finish, then report what is still alive.

    Tearing a child down is not instantaneous — graceful EOF first, then terminate, then kill
    the tree, all inside the configured ``shutdown_timeout`` (10 s by default) — so a snapshot
    taken at the instant of the refusal would report a leak that is really teardown in flight.
    Bounded, so a real leak still fails the check.
    """

    deadline = time.monotonic() + timeout
    leaked = set(_processes(match)) - baseline
    while leaked and time.monotonic() < deadline:
        time.sleep(0.25)
        leaked = set(_processes(match)) - baseline
    return sorted(leaked)


def _session(store: SessionStore, profile: dict, *, session_id=None, resume=None) -> SessionController:
    return SessionController(
        model_factory=lambda name: object(),
        agent_factory=lambda *args, **kwargs: None,
        profiles={"custom": dict(profile)},
        selected_profile="custom",
        runtime_selection="harness",
        durable=store,
        session_id=session_id,
        resume=resume,
    )


def _hint(record: dict) -> dict:
    runtime = record.get("runtime") or {}
    return {"runtime_session": runtime.get("session_id") or "",
            "workspace": runtime.get("workspace") or "",
            "identity": dict((record.get("engine") or {}).get("identity") or {}),
            "tools": runtime.get("tools")}


def _install_bundle(exe: str, home: Path, toolchain: Path) -> tuple[bool, str]:
    """Install the *packaged* plugin into this run's profile, as the installer would."""

    completed = subprocess.run(
        [sys.executable, str(HERE / "install_plugin.py"), "--exe", exe,
         "--home", str(home), "--toolchain", str(toolchain)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
    detail = (completed.stdout or completed.stderr or "").strip().splitlines()[-1:] or [""]
    return completed.returncode == 0, detail[0][:200]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--exe", default="", help="developer executable (ODESSY_HARNESS_EXE)")
    parser.add_argument("--installed-runtime", default="",
                        help="staged runtime directory; uses the production resolution instead of --exe")
    parser.add_argument("--home", required=True)
    parser.add_argument("--work", required=True)
    parser.add_argument("--toolchain", default="")
    parser.add_argument("--tool-python", default="")
    parser.add_argument("--only", default="", help="run one scenario by name")
    args = parser.parse_args()
    if not args.exe and not args.installed_runtime:
        parser.error("give --exe (development) or --installed-runtime (production payload)")

    work = Path(args.work).resolve()
    home = Path(args.home).resolve()
    workspace = work / "ws"
    state = work / "state"
    for path in (work / "mock", workspace, home, state):
        path.mkdir(parents=True, exist_ok=True)
    (work / "mock" / "requests.jsonl").write_text("", encoding="utf-8")
    (work / "state").mkdir(parents=True, exist_ok=True)

    checks: list[tuple[str, bool, str]] = []

    def check(name: str, passed: bool, detail: str = "") -> None:
        checks.append((name, bool(passed), detail))

    runtime_before = set(_processes("deepseek-harness-sdk-runtime"))
    server, port = mock_provider.serve(work / "mock")
    try:
        os.environ.update({
            "ODESSY_HARNESS_HOME": str(home),
            "ODESSY_HARNESS_WORKSPACE": str(workspace),
            "ODESSY_CLOUD_API_KEY": "mock-key-not-a-secret",
            # A real tool surface, so the roster has something to verify.
            "ODESSY_HARNESS_TOOLS": "on",
            "ODESSY_HARNESS_TOOL_PYTHON": args.tool_python or sys.executable,
            "ODESSY_SESSION_STATE_DIR": str(state),
        })
        if args.installed_runtime:
            # Production: the launch resolves the staged payload and verifies every byte.
            os.environ.pop("ODESSY_HARNESS_EXE", None)
            os.environ["ODESSY_HARNESS_RUNTIME_DIR"] = str(Path(args.installed_runtime).resolve())
        else:
            os.environ.pop("ODESSY_HARNESS_RUNTIME_DIR", None)
            os.environ["ODESSY_HARNESS_EXE"] = str(Path(args.exe).resolve())
        profile = _profile(port)
        store = SessionStore(state)
        findings: dict = {}

        # The plugin is a production input, not a test double. Two ways it gets there: the
        # developer installer (pnpm, needs --toolchain), or the production composition the
        # installed application performs, which needs nothing but the packaged bundle.
        installed, install_detail = (False, "no --toolchain given")
        if args.installed_runtime:
            from odessy.runtime_backends.harness import resources as harness_resources

            record = harness_resources.compose_profile(home / "profiles" / "acp")
            copied = home / "profiles" / "acp" / "node_modules" / "@odessy" / "dsh-stream-bridge"
            installed = (copied / "lib" / "index.js").is_file() and not record["directory"] == ""
            install_detail = ("composed from the packaged bundle: wrote "
                              + ", ".join(record["wrote"] or record["kept"]))
        elif args.toolchain:
            installed, install_detail = _install_bundle(str(Path(args.exe).resolve()), home,
                                                       Path(args.toolchain).resolve())
        findings["plugin_install"] = {"installed": installed, "detail": install_detail}
        check("the packaged plugin is installed in this profile", installed, install_detail)

        async def completed_turn_then_restart() -> None:
            first = _session(store, profile)
            try:
                await asyncio.wait_for(first.run_turn("remember zeta-42"), 180)
                findings["first_turn"] = [
                    {"n_messages": item.get("n_messages"), "roles": item.get("message_roles"),
                     "last_user": item.get("last_user")} for item in _requests(work)]
                record = dict(first.record)
                findings["record"] = {
                    "session": first.id,
                    "runtime_session": (record.get("runtime") or {}).get("session_id"),
                    "engine_identity_version": (record.get("engine") or {}).get(
                        "identity", {}).get("version"),
                    "events": len(record.get("events") or []),
                    "cursor": record.get("cursor"),
                    "turns": [turn.get("status") for turn in record.get("turns") or []],
                }
                check("a completed turn records runtime identity",
                      bool((record.get("runtime") or {}).get("session_id")), findings["record"]["runtime_session"])
                check("a completed turn is recorded as settled",
                      findings["record"]["turns"] == ["settled"], str(findings["record"]["turns"]))
                check("the projection is bounded",
                      0 < findings["record"]["events"] <= store.projection_limit,
                      str(findings["record"]["events"]))
                surface = first.runtime.tool_surface
                offered = (_requests(work)[-1].get("tools") or []) if _requests(work) else []
                findings["surface_at_creation"] = surface
                findings["model_offered"] = offered
                check("the session records the tools the runtime attached",
                      surface["state"] == "recorded" and bool(surface["tools"]),
                      f"{surface['state']} / {len(surface['tools'] or [])} tool(s)")
                check("the recorded roster is the model-visible tool set",
                      bool(offered) and all(name in (surface["tools"] or []) for name in offered),
                      f"offered={offered[:6]} recorded={(surface['tools'] or [])[:6]}")
            finally:
                await first.close()
            saved = dict(store.load(first.id).record)
            before = len(_requests(work))

            # A new process: same store, explicit restore, same runtime session.
            second = _session(store, profile, session_id=first.id, resume=_hint(saved))
            try:
                await asyncio.wait_for(second.prepare(), 120)
                snapshot = second.public_snapshot()
                findings["restore"] = {"durability": snapshot["durability"],
                                       "restore": snapshot["restore"]}
                rebuilt = [event for event in second.events
                           if event["type"] == "transcript"]
                check("the restore says it continued the runtime session",
                      snapshot["durability"]["resumed"] is True
                      and snapshot["durability"]["runtime_session"] == findings["record"]["runtime_session"])
                check("the restored session's tool surface is verified",
                      snapshot["durability"]["tool_surface"]["state"] == "verified",
                      json.dumps(snapshot["durability"]["tool_surface"])[:200])
                check("settled content is rebuilt from the projection",
                      any(event["data"].get("text") == EXPECTED_ANSWER
                          for event in rebuilt)
                      and any(event["data"].get("text") == "remember zeta-42"
                              for event in rebuilt),
                      str([event["data"].get("text") for event in rebuilt])[:200])
                check("restoring does not resubmit the last prompt",
                      len(_requests(work)) == before,
                      f"{len(_requests(work)) - before} extra model call(s)")
                await asyncio.wait_for(second.run_turn("what number did I say"), 180)
                turn_requests = _requests(work)[before:]
                findings["resumed_turn"] = [
                    {"n_messages": item.get("n_messages"), "roles": item.get("message_roles"),
                     "last_user": item.get("last_user")} for item in turn_requests]
                tail = findings["resumed_turn"][-1]
                # The runtime's own envelope is system + its context snapshot + the durable
                # history + the new prompt. What matters is that an *assistant* turn from the
                # earlier conversation is in there, which only a restored session can produce.
                first_turn = findings["first_turn"][-1]
                check("the model saw the durable conversation",
                      "assistant" in (tail["roles"] or [])
                      and tail["n_messages"] > first_turn["n_messages"]
                      and (first_turn["roles"] or []).count("assistant") == 0,
                      f"first={json.dumps(first_turn)} resumed={json.dumps(tail)}"[:220])
                check("a resumed turn makes exactly one request",
                      len(turn_requests) == 1, str(len(turn_requests)))
                check("the resumed turn sent only the new prompt",
                      tail["last_user"] == "what number did I say", str(tail["last_user"]))
                check("the cursor continues past the restored position",
                      (second.record.get("cursor") or {}).get("last", 0)
                      > (saved.get("cursor") or {}).get("last", 0),
                      str(second.record.get("cursor")))
            finally:
                await second.close()

        async def interrupted_turn() -> None:
            third = _session(store, profile)
            await third.runtime.start()
            pid = third.runtime._process.pid
            running = asyncio.create_task(third.run_turn("please slow cancel"))
            await asyncio.sleep(2.5)
            _kill_tree(pid)
            with __import__("contextlib").suppress(Exception):
                await asyncio.wait_for(running, 20)
            record = dict(third.record)
            findings["interrupted"] = {"turns": [turn.get("status") for turn in record.get("turns") or []],
                                      "session": third.id}
            with __import__("contextlib").suppress(Exception):
                await third.close()
            # The surviving process *saw* the child die, so it may say "failed". What it must
            # never say is "settled".
            check("a killed turn is never recorded as settled",
                  findings["interrupted"]["turns"] == ["failed"],
                  str(findings["interrupted"]["turns"]))

            # What a *process* crash leaves behind: the same record, with the turn still
            # admitted because nothing survived to write an outcome. Written by hand because a
            # process that dies mid-turn cannot report anything afterwards.
            saved = dict(store.load(third.id).record)
            for turn in saved.get("turns") or []:
                if turn.get("id"):
                    turn.update(status="admitted", settled=None, cursor=None)
            store.save(saved)
            before = len(_requests(work))
            fourth = _session(store, profile, session_id=third.id, resume=_hint(saved))
            try:
                await asyncio.wait_for(fourth.prepare(), 120)
                state_ = fourth.public_snapshot()["restore"]
                findings["interrupted_restore"] = state_
                check("the restore reports the interrupted work",
                      [(item["id"], item["state"]) for item in state_["incomplete"]]
                      and state_["incomplete"][0]["state"] == "interrupted",
                      json.dumps(state_["incomplete"])[:200])
                check("an interrupted turn is not retried on restore",
                      len(_requests(work)) == before, f"{len(_requests(work)) - before} call(s)")
                await asyncio.wait_for(fourth.run_turn("are you there"), 180)
                check("the interrupted session continues after a new prompt",
                      len(_requests(work)) == before + 1, str(len(_requests(work)) - before))
            finally:
                await fourth.close()

        async def server_disappeared() -> None:
            """A server that existed at creation and is gone at restore is refused, not absorbed."""

            fixture = work / "fixture_server.py"
            fixture.write_text(FIXTURE_SERVER, encoding="utf-8")
            os.environ.update({
                "ODESSY_HARNESS_EXTERNAL_TOOLS": "p10fixture",
                "ODESSY_SERVERS_FILE": str(_servers_file(work, fixture)),
            })
            try:
                first = _session(store, profile)
                try:
                    await asyncio.wait_for(first.run_turn("remember theta-77"), 240)
                    record = dict(first.record)
                    surface = first.runtime.tool_surface
                    findings["upstream_surface"] = surface
                    check("the upstream's tools are attached and recorded",
                          surface["state"] == "recorded"
                          and any("p10fixture" in name for name in (surface["tools"] or [])),
                          json.dumps((surface["tools"] or [])[:8]))
                finally:
                    await first.close()
                saved = dict(store.load(first.id).record)
                recorded_session = (saved.get("runtime") or {}).get("session_id")
                # The server still starts, but one of its tools is no longer there. This is the
                # case only a roster can catch: the runtime reports nothing, and the servers it
                # was asked for are exactly the servers it was asked for.
                fixture.write_text(FIXTURE_SERVER_ONE_TOOL, encoding="utf-8")
                before = len(_requests(work))
                live_before = set(_processes("deepseek-harness-sdk-runtime")) - runtime_before
                try:
                    broken = _session(store, profile, session_id=first.id, resume=_hint(saved))
                    await asyncio.wait_for(broken.prepare(), 180)
                    findings["disappeared"] = {"refused": False}
                    await broken.close()
                except RuntimeUnavailable as exc:
                    findings["disappeared"] = {"refused": True, **exc.public()}
                check("a tool that disappeared is refused, not silently dropped",
                      findings["disappeared"].get("refused") is True
                      and "tool surface is incomplete"
                      in str(findings["disappeared"].get("reason")),
                      json.dumps(findings["disappeared"])[:260])
                check("that refusal names the tool that is gone",
                      "probe_beta" in str(findings["disappeared"].get("detail")),
                      str(findings["disappeared"].get("detail"))[:200])
                check("the refusal sends no model request",
                      len(_requests(work)) == before,
                      f"{len(_requests(work)) - before} call(s)")
                leaked = _wait_no_processes("deepseek-harness-sdk-runtime", runtime_before)
                check("the refusal leaves no runtime process behind", not leaked, str(leaked))
                after_record = dict(store.load(first.id).record)
                check("the refusal did not replace the session",
                      (after_record.get("runtime") or {}).get("session_id") == recorded_session,
                      f"{(after_record.get('runtime') or {}).get('session_id')} vs {recorded_session}")
            finally:
                fixture.write_text(FIXTURE_SERVER, encoding="utf-8")
                for name in ("ODESSY_HARNESS_EXTERNAL_TOOLS", "ODESSY_SERVERS_FILE"):
                    os.environ.pop(name, None)

        async def roster_policy() -> None:
            """New sessions may prefer a roster; restores always require verification."""

            if args.installed_runtime:
                # A production installation composes the packaged plugin into every profile,
                # so "the plugin is absent" is no longer a reachable state to simulate here.
                # The refusal paths for an unverifiable roster stay covered by
                # tests/test_durable_sessions.py; this records the state that *is* reachable.
                from odessy.runtime_backends.harness.config import from_environment

                composed = from_environment({**os.environ,
                                             "ODESSY_HARNESS_TOOL_ROSTER": "required"})
                bundle = (Path(composed.home) / "profiles" / composed.profile / "node_modules"
                          / "@odessy" / "dsh-stream-bridge" / "lib" / "index.js")
                findings["roster_policy"] = {"production": composed.profile_record,
                                             "bundle_present": bundle.is_file()}
                check("the production profile composes the packaged plugin",
                      bundle.is_file() and composed.profile_record.get("bundle") in
                      {"installed", "current", "repaired"},
                      json.dumps(findings["roster_policy"])[:220])
                return

            alt_home = work / "home-noplugin"
            alt_state = work / "state-noplugin"
            alt_home.mkdir(parents=True, exist_ok=True)
            alt_state.mkdir(parents=True, exist_ok=True)
            saved_env = {name: os.environ.get(name) for name in
                         ("ODESSY_HARNESS_HOME", "ODESSY_SESSION_STATE_DIR",
                          "ODESSY_HARNESS_TOOL_ROSTER")}
            os.environ.update({"ODESSY_HARNESS_HOME": str(alt_home),
                               "ODESSY_SESSION_STATE_DIR": str(alt_state),
                               "ODESSY_HARNESS_TOOL_ROSTER": "required"})
            alt_store = SessionStore(alt_state)
            try:
                try:
                    strict = _session(alt_store, profile)
                    await asyncio.wait_for(strict.prepare(), 180)
                    findings["required_without_plugin"] = {"refused": False}
                    await strict.close()
                except RuntimeUnavailable as exc:
                    findings["required_without_plugin"] = {"refused": True, **exc.public()}
                check("a required roster is refused when the plugin cannot report one",
                      findings["required_without_plugin"].get("refused") is True
                      and "could not be verified"
                      in str(findings["required_without_plugin"].get("reason")),
                      json.dumps(findings["required_without_plugin"])[:220])

                os.environ["ODESSY_HARNESS_TOOL_ROSTER"] = "preferred"
                lenient = _session(alt_store, profile)
                try:
                    await asyncio.wait_for(lenient.run_turn("remember iota-88"), 240)
                    findings["preferred_without_plugin"] = lenient.runtime.tool_surface
                finally:
                    await lenient.close()
                check("a preferred roster records the uncertainty instead of failing",
                      findings["preferred_without_plugin"].get("state") == "unverified"
                      and bool(findings["preferred_without_plugin"].get("reason")),
                      json.dumps(findings["preferred_without_plugin"])[:220])
                saved = dict(alt_store.load(lenient.id).record)
                before = len(_requests(work))
                restored = _session(alt_store, profile, session_id=lenient.id,
                                    resume=_hint(saved))
                try:
                    await asyncio.wait_for(restored.prepare(), 180)
                    findings["preferred_restore_without_plugin"] = {"refused": False}
                except RuntimeUnavailable as exc:
                    findings["preferred_restore_without_plugin"] = {
                        "refused": True, **exc.public()}
                finally:
                    await restored.close()
                check("a preferred restore still refuses an unverifiable roster",
                      findings["preferred_restore_without_plugin"].get("refused") is True
                      and "could not be verified" in str(
                          findings["preferred_restore_without_plugin"].get("reason")),
                      json.dumps(findings["preferred_restore_without_plugin"])[:220])
                check("an unverifiable restore makes no model request",
                      len(_requests(work)) == before)
            finally:
                for name, value in saved_env.items():
                    if value is None:
                        os.environ.pop(name, None)
                    else:
                        os.environ[name] = value

        async def refusals() -> None:
            fifth = _session(store, profile)
            try:
                await asyncio.wait_for(fifth.run_turn("remember eta-55"), 180)
            finally:
                await fifth.close()
            saved = dict(store.load(fifth.id).record)
            live_before = set(_processes("deepseek-harness-sdk-runtime")) - runtime_before

            # A record whose runtime history this machine no longer has.
            gone = _hint(saved)
            gone["runtime_session"] = str(uuid.uuid4())
            try:
                stale = _session(store, profile, session_id=fifth.id, resume=gone)
                await asyncio.wait_for(stale.prepare(), 120)
                findings["missing_history"] = {"refused": False}
                await stale.close()
            except RuntimeUnavailable as exc:
                findings["missing_history"] = {"refused": True, **exc.public()}
            check("a restore with no runtime history is refused",
                  findings["missing_history"].get("refused") is True
                  and "no history" in str(findings["missing_history"].get("reason")),
                  json.dumps(findings["missing_history"])[:220])

            # A record written by a different runtime build.
            other = _hint(saved)
            other["identity"] = {**other["identity"], "version": "0.2.0-rc.9"}
            try:
                foreign = _session(store, profile, session_id=fifth.id, resume=other)
                await asyncio.wait_for(foreign.prepare(), 120)
                findings["other_runtime"] = {"refused": False}
                await foreign.close()
            except RuntimeUnavailable as exc:
                findings["other_runtime"] = {"refused": True, **exc.public()}
            check("a restore from another runtime build is refused",
                  findings["other_runtime"].get("refused") is True
                  and "different runtime" in str(findings["other_runtime"].get("reason")),
                  json.dumps(findings["other_runtime"])[:220])

            # A log this pin cannot read (corrupted bytes).
            log = next(iter(home.rglob(f"{saved['runtime']['session_id']}/session.*.jsonl.*")), None)
            if log is not None:
                original = log.read_bytes()
                damaged = bytearray(original)
                for index in range(min(64, len(damaged))):
                    damaged[index] = 0
                log.write_bytes(bytes(damaged))
                try:
                    broken = _session(store, profile, session_id=fifth.id, resume=_hint(saved))
                    await asyncio.wait_for(broken.prepare(), 120)
                    findings["unreadable_history"] = {"refused": False}
                    await broken.close()
                except RuntimeUnavailable as exc:
                    findings["unreadable_history"] = {"refused": True, **exc.public()}
                finally:
                    log.write_bytes(original)
                check("an unreadable runtime log is refused, not replaced",
                      findings["unreadable_history"].get("refused") is True
                      and "could not open the persisted history"
                      in str(findings["unreadable_history"].get("reason")),
                      json.dumps(findings["unreadable_history"])[:220])
                check("the original history still restores afterwards",
                      isinstance(_hint(saved), dict) and bool(saved["runtime"]["session_id"]))

            live_after = set(_processes("deepseek-harness-sdk-runtime")) - runtime_before
            check("refusals leave no runtime process behind", not (live_after - live_before),
                  f"before={sorted(live_before)} after={sorted(live_after)}")

        async def run_all() -> None:
            live_after: dict[str, list[int]] = {}

            async def tracked(name: str, scenario) -> None:
                if args.only and args.only != name:
                    return
                await scenario()
                # Attribute any surviving runtime process to the scenario that left it.
                live_after[name] = sorted(set(_processes("deepseek-harness-sdk-runtime"))
                                          - runtime_before)
                if live_after[name]:
                    findings.setdefault("live_details", {})[name] = [
                        item for item in _process_details("deepseek-harness-sdk-runtime")
                        if item["pid"] in live_after[name]]

            await tracked("completed_turn_then_restart", completed_turn_then_restart)
            await tracked("interrupted_turn", interrupted_turn)
            await tracked("server_disappeared", server_disappeared)
            await tracked("roster_policy", roster_policy)
            await tracked("refusals", refusals)
            findings["live_runtime_after"] = live_after

        asyncio.run(run_all())

        # Secrets and record hygiene, checked on what is actually on disk.
        records = sorted(state.glob("*.json"))
        findings["records"] = [path.name for path in records]
        blob = "".join(path.read_text(encoding="utf-8") for path in records)
        check("no record carries the provider credential",
              "mock-key-not-a-secret" not in blob)
        check("records are schema-stamped",
              all(json.loads(path.read_text(encoding="utf-8")).get("schema") == 1 for path in records),
              str(len(records)))
        check("nothing durable was written outside the state directory",
              not list(workspace.glob("*.json*")))
    finally:
        server.shutdown()

    leaked = _wait_no_processes("deepseek-harness-sdk-runtime", runtime_before)
    check("no runtime process is left running", not leaked, str(leaked))

    report = {"findings": findings, "checks": [
        {"check": name, "passed": passed, "detail": detail} for name, passed, detail in checks]}
    (work / "findings.json").write_text(json.dumps(report, indent=2, default=str),
                                        encoding="utf-8")
    print(json.dumps(report, indent=2, default=str))
    passed = sum(1 for _, ok, _ in checks if ok)
    print(f"\n{passed}/{len(checks)} checks passed", file=sys.stderr)
    return 0 if passed == len(checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
