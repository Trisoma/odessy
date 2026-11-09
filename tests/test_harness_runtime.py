"""Deterministic lifecycle tests for the Harness backend (prompt 03).

No real runtime is involved: each test writes a small fake executable and drives the
production code paths — version pinning, spawn, handshake, framing, failure
classification, shutdown and process-tree cleanup — against it. The one smoke test that
launches the pinned binary lives in ``experiments/restructure/harness_compatibility``,
because it needs the downloaded artifact.
"""

import asyncio
import json
import os
import subprocess
import sys
import threading
import time

import pytest

from odessy.application.session import SessionController
from odessy.runtime_backends import RuntimeCapabilityUnavailable, RuntimeUnavailable
from odessy.runtime_backends import selection as selection_module
from odessy.runtime_backends.harness import (
    NARROW_PATCH_NAME,
    NARROW_PATCH_TEXT,
    HarnessConfig,
    HarnessRuntime,
)
from odessy.runtime_backends.harness.config import from_environment
from odessy.runtime_backends.harness.process import (
    HarnessProcess,
    HarnessProtocolError,
    HarnessTransportError,
)

FAKE_RUNTIME = r'''
import json, os, subprocess, sys, time

MODE = os.environ.get("FAKE_MODE", "normal")


def emit(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


if "--version" in sys.argv:
    dump = os.environ.get("FAKE_ENVFILE")
    if dump:
        with open(dump, "w", encoding="utf-8") as handle:
            handle.write("\n".join(sorted(os.environ)))
    print("9.9.9" if MODE == "badversion" else "0.2.1-rc.2")
    raise SystemExit(0)

PIDFILE = os.environ.get("FAKE_PIDFILE")
if PIDFILE:
    with open(PIDFILE, "w", encoding="utf-8") as handle:
        handle.write(str(os.getpid()))

if MODE == "crash":
    sys.stderr.write("fatal: cannot start\n")
    sys.stderr.flush()
    raise SystemExit(3)

if MODE == "garbage":
    sys.stdout.write("this is not json\n")
    sys.stdout.write("[1, 2, 3]\n")
    sys.stdout.flush()

if MODE == "child":
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
    with open(os.environ["FAKE_PIDFILE"] + ".child", "w", encoding="utf-8") as handle:
        handle.write(str(child.pid))


def answer(message):
    method = message.get("method")
    request_id = message.get("id")
    if MODE == "hang":
        return
    if MODE == "slowinit" and method == "initialize":
        time.sleep(0.8)
    if method == "initialize":
        if MODE == "badresult":
            emit({"jsonrpc": "2.0", "id": request_id, "result": 5})
            return
        if MODE == "proto2":
            emit({"jsonrpc": "2.0", "id": request_id, "result": {"protocolVersion": 2}})
            return
        if MODE == "noproto":
            emit({"jsonrpc": "2.0", "id": request_id, "result": {"agentInfo": {}}})
            return
        emit({"jsonrpc": "2.0", "id": request_id, "result": {"protocolVersion": 1}})
        return
    if method == "session/new":
        emit({"jsonrpc": "2.0", "id": request_id, "result": {"sessionId": "acp-1"}})
        return
    if method == "session/prompt":
        session_id = (message.get("params") or {}).get("sessionId")
        if MODE == "serverrequest":
            emit({"jsonrpc": "2.0", "id": "srv-1", "method": "session/request_permission",
                  "params": {"sessionId": session_id}})
        if MODE == "tool":
            emit({"jsonrpc": "2.0", "method": "session/update",
                  "params": {"sessionId": session_id,
                             "update": {"sessionUpdate": "tool_call",
                                        "toolCall": {"title": "pwsh"}}}})
        for chunk in ("Hello", " there"):
            emit({"jsonrpc": "2.0", "method": "session/update",
                  "params": {"sessionId": session_id,
                             "update": {"sessionUpdate": "agent_message_chunk",
                                        "content": {"type": "text", "text": chunk}}}})
        emit({"jsonrpc": "2.0", "id": request_id, "result": {"stopReason": "end_turn"}})
        return
    emit({"jsonrpc": "2.0", "id": request_id,
          "error": {"code": -32601, "message": "unknown method " + str(method)}})


for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        message = json.loads(line)
    except ValueError:
        continue
    if "method" not in message or "id" not in message:
        continue
    answer(message)
'''


class FakeSession:
    """The parts of SessionController a runtime is allowed to touch."""

    def __init__(self):
        self.id = "sess-1"
        self._events = []

    def events(self):
        return list(self._events)

    def emit(self, event_type, data=None):
        self._events.append({"id": len(self._events) + 1, "type": event_type,
                             "time": time.time(), "data": data or {}})

    def public_snapshot(self):
        return {"id": self.id}


@pytest.fixture()
def fake(tmp_path):
    """The fake runtime as an explicit argv prefix.

    ``command`` is a tuple precisely so a runtime need not be a single file: here the
    interpreter and the script are both named, and nothing is discovered.
    """
    script = tmp_path / "fake_runtime.py"
    script.write_text(FAKE_RUNTIME, encoding="utf-8")
    return (sys.executable, str(script))


def make_config(tmp_path, fake, **overrides):
    home = tmp_path / "home"
    workspace = tmp_path / "ws"
    home.mkdir(exist_ok=True)
    workspace.mkdir(exist_ok=True)
    overlay = home / NARROW_PATCH_NAME
    overlay.write_text(NARROW_PATCH_TEXT, encoding="utf-8")
    settings = dict(
        command=tuple(fake),
        home=home,
        workspace=workspace,
        patches=(),
        narrow_patch=overlay,
        startup_timeout=4.0,
        turn_timeout=6.0,
        shutdown_timeout=3.0,
        # The fake is an interpreter plus a script, not the pinned artifact; the digest
        # gate itself is proven separately against the real pin and in the pinned smoke.
        verify_artifact=False,
    )
    settings.update(overrides)
    return HarnessConfig(**settings)


def run(coroutine):
    return asyncio.run(coroutine)


def _pid_alive(pid):
    if sys.platform == "win32":
        completed = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                                   capture_output=True, text=True, check=False)
        return str(pid) in completed.stdout
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


# -- configuration -----------------------------------------------------------

def test_from_environment_requires_an_explicit_executable(monkeypatch):
    monkeypatch.delenv("ODESSY_HARNESS_EXE", raising=False)
    with pytest.raises(RuntimeUnavailable) as caught:
        from_environment()
    assert caught.value.public()["reason"].startswith("not configured")
    assert "ODESSY_HARNESS_EXE" in str(caught.value)


def test_from_environment_never_falls_back_to_path_or_a_sibling(monkeypatch, tmp_path):
    monkeypatch.setenv("ODESSY_HARNESS_EXE", "harness-on-path")
    with pytest.raises(RuntimeUnavailable) as caught:
        from_environment()
    assert "absolute path" in caught.value.public()["reason"]

    monkeypatch.setenv("ODESSY_HARNESS_EXE", str(tmp_path / "missing.exe"))
    with pytest.raises(RuntimeUnavailable) as caught:
        from_environment()
    assert caught.value.public()["reason"] == "the configured executable does not exist"


def test_child_environment_is_an_allowlist(monkeypatch, tmp_path, fake):
    config = make_config(tmp_path, fake, passthrough=("FAKE_MODE",))
    parent = {"PATH": "C:/bin", "SYSTEMROOT": "C:/Windows", "ODESSY_SECRET_TOKEN": "nope",
              "FAKE_MODE": "normal", "LOCALAPPDATA": str(tmp_path)}
    env = config.child_env(parent)
    assert env["DSH_HOME"] == str(config.home)
    assert env["FAKE_MODE"] == "normal"
    assert "ODESSY_SECRET_TOKEN" not in env
    assert "HOME" not in env  # the child does not inherit a user's whole identity


@pytest.mark.parametrize("row", ["tool-pwsh", "mcp-resources", "plan-mode", "tool-not-in-the-list"])
def test_an_extra_overlay_cannot_take_part_in_the_tool_surface(tmp_path, row):
    from odessy.runtime_backends.harness.config import from_environment

    override = tmp_path / "override.yml"
    override.write_text(f"- id: {row}\n  disabled: false\n", encoding="utf-8")
    environ = {"ODESSY_HARNESS_EXE": sys.executable, "ODESSY_HARNESS_PATCH": str(override)}
    with pytest.raises(RuntimeUnavailable) as caught:
        from_environment(environ, workspace=tmp_path)
    assert caught.value.public()["reason"] == \
        "an extra profile overlay may not modify a tool row"
    assert row in caught.value.public()["detail"]


def test_an_extra_overlay_that_configures_a_route_is_allowed_and_loses_to_the_narrow_one(tmp_path):
    route = tmp_path / "route.yml"
    route.write_text("- id: acp\n  config:\n    provider: deepseek-official\n"
                     "    model: mock-model\n", encoding="utf-8")
    environ = {"ODESSY_HARNESS_EXE": sys.executable, "ODESSY_HARNESS_PATCH": str(route)}
    config = from_environment(environ, workspace=tmp_path)
    ordered = config.ordered_patches()
    # Patch layers apply in argv order, so the reviewed overlay must come last: a later
    # layer could otherwise re-enable what it disabled.
    assert ordered[-1].name == NARROW_PATCH_NAME
    assert ordered[0] == route
    assert ordered[-1].read_text(encoding="utf-8") == NARROW_PATCH_TEXT
    argv = config.argv()
    emitted = [argv[index + 1] for index, item in enumerate(argv) if item == "--patch"]
    assert emitted[-1] == str(ordered[-1])


def test_the_narrow_overlay_is_always_emitted_last(tmp_path):
    extras = [tmp_path / "a.yml", tmp_path / "b.yml"]
    for path in extras:
        path.write_text("- id: acp\n", encoding="utf-8")
    narrow = tmp_path / NARROW_PATCH_NAME
    narrow.write_text(NARROW_PATCH_TEXT, encoding="utf-8")
    config = HarnessConfig(command=(sys.executable,), home=tmp_path, workspace=tmp_path,
                           patches=tuple(extras), narrow_patch=narrow)
    assert config.ordered_patches() == [*extras, narrow]


# -- version pinning ---------------------------------------------------------

def test_a_different_version_is_refused_before_any_session_exists(monkeypatch, tmp_path, fake):
    monkeypatch.setenv("FAKE_MODE", "badversion")
    config = make_config(tmp_path, fake, passthrough=("FAKE_MODE",))
    runtime = HarnessRuntime(FakeSession(), config=config)
    with pytest.raises(RuntimeUnavailable) as caught:
        run(runtime.start())
    assert caught.value.public()["reason"] == "the configured executable is not the pinned build"
    assert "9.9.9" in caught.value.public()["detail"]
    assert runtime.diagnostics() == []  # nothing was spawned


def test_a_mismatched_artifact_is_refused_even_with_the_right_version(tmp_path, fake):
    from odessy.runtime_backends.harness import pin

    env = {"PATH": os.environ.get("PATH", "")}
    wrong = "0" * 64
    with pytest.raises(RuntimeUnavailable) as caught:
        pin.verify_version(fake, "0.2.1-rc.2", env=env, expected_sha256=wrong)
    assert caught.value.public()["reason"] == "the configured executable is not the pinned artifact"
    assert "does not match the pinned" in caught.value.public()["detail"]


def test_the_artifact_digest_is_enforced_by_default(tmp_path, fake):
    # No override: the production default demands the reviewed digest, and an interpreter
    # wrapped around a script is not it.
    config = make_config(tmp_path, fake, verify_artifact=True)
    runtime = HarnessRuntime(FakeSession(), config=config)
    with pytest.raises(RuntimeUnavailable) as caught:
        run(runtime.start())
    assert caught.value.public()["reason"] == "the configured executable is not the pinned artifact"
    assert runtime._process is None


def test_the_version_probe_runs_in_the_launch_environment(monkeypatch, tmp_path, fake):
    from odessy.runtime_backends.harness import pin

    envfile = tmp_path / "probe-env.txt"
    monkeypatch.setenv("FAKE_ENVFILE", str(envfile))
    monkeypatch.setenv("ODESSY_SECRET_TOKEN", "must-not-be-seen")
    config = make_config(tmp_path, fake, passthrough=("FAKE_ENVFILE",))
    identity = pin.verify_version(fake, "0.2.1-rc.2", env=config.child_env(),
                                  expected_sha256=None)
    assert identity["version"] == "0.2.1-rc.2"
    seen = set(envfile.read_text(encoding="utf-8").splitlines())
    assert "DSH_HOME" in seen
    assert "FAKE_ENVFILE" in seen
    assert "ODESSY_SECRET_TOKEN" not in seen  # the probe cannot rely on the parent's secrets


def test_a_missing_executable_fails_at_session_creation(tmp_path, fake):
    from dataclasses import replace

    config = replace(make_config(tmp_path, fake), command=(str(tmp_path / "missing.exe"),))
    with pytest.raises(RuntimeUnavailable):
        HarnessRuntime(FakeSession(), config=config)
    # The validator is what the production path calls, so it is exercised directly too.
    narrow = tmp_path / NARROW_PATCH_NAME
    narrow.write_text(NARROW_PATCH_TEXT, encoding="utf-8")
    with pytest.raises(RuntimeUnavailable):
        HarnessConfig(command=(str(tmp_path / "missing.exe"),), home=tmp_path,
                      workspace=tmp_path, narrow_patch=narrow).validate()
    # A narrow overlay that is not the reviewed one is refused as well.
    with pytest.raises(RuntimeUnavailable):
        HarnessConfig(command=(sys.executable,), home=tmp_path, workspace=tmp_path,
                      narrow_patch=narrow.with_name("something-else.yml")).validate()


# -- handshake ---------------------------------------------------------------

def test_handshake_captures_identity_and_is_idempotent(tmp_path, fake):
    runtime = HarnessRuntime(FakeSession(), config=make_config(tmp_path, fake))

    async def flow():
        first = await runtime.start()
        second = await runtime.start()
        assert first == second
        assert first["version"] == "0.2.1-rc.2"
        assert first["pinned_version"] == "0.2.1-rc.2"
        assert runtime.acp_session_id == "acp-1"
        process = runtime._process
        pid = process.pid
        await runtime.close()
        await runtime.close()  # idempotent
        return pid

    pid = run(flow())
    assert not _pid_alive(pid)
    assert runtime.diagnostics()[-1].startswith("closed")


def test_malformed_frames_are_diagnosed_but_do_not_break_the_stream(monkeypatch, tmp_path, fake):
    monkeypatch.setenv("FAKE_MODE", "garbage")
    config = make_config(tmp_path, fake, passthrough=("FAKE_MODE",))
    process = HarnessProcess(config)
    process.start()
    try:
        reply = process.call("initialize", {"protocolVersion": 1}, timeout=4.0)
        assert reply == {"protocolVersion": 1}
        assert process.malformed_frames >= 2
        assert any("unparseable frame" in line for line in process.diagnostics())
        assert any("non-object frame" in line for line in process.diagnostics())
    finally:
        process.close()


def test_a_non_object_result_is_a_protocol_error(monkeypatch, tmp_path, fake):
    monkeypatch.setenv("FAKE_MODE", "badresult")
    config = make_config(tmp_path, fake, passthrough=("FAKE_MODE",))
    process = HarnessProcess(config)
    process.start()
    try:
        with pytest.raises(HarnessProtocolError) as caught:
            process.call("initialize", {}, timeout=4.0)
        assert "expected an object" in str(caught.value)
    finally:
        process.close()


def test_diagnostics_are_bounded(tmp_path, fake):
    config = make_config(tmp_path, fake, diagnostic_lines=3)
    process = HarnessProcess(config)
    process.start()
    try:
        for index in range(20):
            process.note(f"line {index}")
        assert len(process.diagnostics()) == 3
    finally:
        process.close()


@pytest.mark.parametrize("mode, fragment", [
    ("proto2", "protocolVersion=2"),
    ("noproto", "protocolVersion=None"),
])
def test_an_incompatible_handshake_is_refused(monkeypatch, tmp_path, fake, mode, fragment):
    monkeypatch.setenv("FAKE_MODE", mode)
    config = make_config(tmp_path, fake, passthrough=("FAKE_MODE",))
    runtime = HarnessRuntime(FakeSession(), config=config)
    with pytest.raises(RuntimeUnavailable) as caught:
        run(runtime.start())
    assert caught.value.public()["reason"] == "the runtime did not complete the handshake"
    assert fragment in caught.value.public()["detail"]
    assert "this build speaks ACP v1" in caught.value.public()["detail"]
    assert runtime._process is None  # the incompatible child was reaped, not adopted


# -- failures ----------------------------------------------------------------

def test_startup_timeout_is_bounded_and_leaves_no_process(monkeypatch, tmp_path, fake):
    monkeypatch.setenv("FAKE_MODE", "hang")
    config = make_config(tmp_path, fake, passthrough=("FAKE_MODE",), startup_timeout=0.6)
    runtime = HarnessRuntime(FakeSession(), config=config)
    started = time.monotonic()
    with pytest.raises(RuntimeUnavailable) as caught:
        run(runtime.start())
    elapsed = time.monotonic() - started
    assert elapsed < 3.0
    assert "did not complete the handshake" in caught.value.public()["reason"]
    assert runtime._process is None  # the failed child was not adopted


def test_a_crash_during_startup_reports_the_exit_code(monkeypatch, tmp_path, fake):
    monkeypatch.setenv("FAKE_MODE", "crash")
    config = make_config(tmp_path, fake, passthrough=("FAKE_MODE",))
    runtime = HarnessRuntime(FakeSession(), config=config)
    with pytest.raises(RuntimeUnavailable) as caught:
        run(runtime.start())
    assert caught.value.public()["reason"] == "the runtime exited during startup"
    assert "exit code 3" in caught.value.public()["detail"]


def test_a_crash_during_a_turn_is_reported_not_swallowed(tmp_path, fake):
    runtime = HarnessRuntime(FakeSession(), config=make_config(tmp_path, fake))
    run(runtime.start())

    async def flow():
        process = runtime._process
        assert process is not None
        process._kill_tree()  # simulate the child dying mid-session
        try:
            with pytest.raises(RuntimeUnavailable) as caught:
                await runtime.run_turn("after the crash")
        finally:
            await runtime.close()
        return caught.value

    exc = run(flow())
    assert "exited" in str(exc) or "not running" in str(exc)
    assert exc.code == "runtime_unavailable"


# -- shutdown ----------------------------------------------------------------

def test_concurrent_close_is_idempotent(tmp_path, fake):
    config = make_config(tmp_path, fake)
    process = HarnessProcess(config)
    process.start()
    pid = process.pid
    process.call("initialize", {}, timeout=4.0)
    errors = []

    def closer():
        try:
            process.close()
        except BaseException as exc:  # noqa: BLE001 - a raise here is the failure
            errors.append(exc)

    threads = [threading.Thread(target=closer) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=15)
    assert errors == []
    assert not any(thread.is_alive() for thread in threads)
    assert not _pid_alive(pid)
    assert process.exit_code is not None


def _wait_for_pid(path, timeout=5.0):
    deadline = time.monotonic() + timeout
    while not path.exists() and time.monotonic() < deadline:
        time.sleep(0.02)
    assert path.exists(), f"{path.name} was never written; the check would be vacuous"
    return int(path.read_text(encoding="utf-8").strip())


def _wait_until_dead(pid, timeout=10.0):
    deadline = time.monotonic() + timeout
    while _pid_alive(pid) and time.monotonic() < deadline:
        time.sleep(0.05)
    return not _pid_alive(pid)


def test_close_kills_a_grandchild_the_runtime_spawned(monkeypatch, tmp_path, fake):
    pidfile = tmp_path / "runtime.pid"
    monkeypatch.setenv("FAKE_MODE", "child")
    monkeypatch.setenv("FAKE_PIDFILE", str(pidfile))
    config = make_config(tmp_path, fake, passthrough=("FAKE_MODE", "FAKE_PIDFILE"))
    process = HarnessProcess(config)
    process.start()
    try:
        process.call("initialize", {}, timeout=4.0)
        if sys.platform == "win32":
            # Name the mechanism: a pass here must not come from a lucky taskkill.
            diagnostics = process.diagnostics()
            assert any("job object" in line and "taskkill" not in line
                       for line in diagnostics), diagnostics
        grandchild = _wait_for_pid(pidfile.with_suffix(".pid.child"))
        assert _pid_alive(grandchild)  # control: the test would otherwise be vacuous
    finally:
        process.close()
    assert _wait_until_dead(grandchild), "the grandchild outlived the closed runtime"


def test_without_a_job_object_the_tree_dies_before_the_runtime_can_exit(
        monkeypatch, tmp_path, fake):
    """The fallback has exactly one ordering that can still work, and this pins it.

    ``taskkill /T`` cannot reach descendants once the parent pid is gone, so when tree
    ownership failed the supervisor must kill **before** the graceful EOF. The fake exits
    as soon as stdin closes and leaves its child behind, which is exactly the trap.
    """
    from odessy.runtime_backends.harness import process as process_module

    pidfile = tmp_path / "runtime.pid"
    monkeypatch.setenv("FAKE_MODE", "child")
    monkeypatch.setenv("FAKE_PIDFILE", str(pidfile))
    monkeypatch.setattr(process_module.JobObject, "adopt", lambda self, pid: False)
    config = make_config(tmp_path, fake, passthrough=("FAKE_MODE", "FAKE_PIDFILE"))
    process = HarnessProcess(config)
    process.start()
    try:
        process.call("initialize", {}, timeout=4.0)
        assert any("no job object" in line for line in process.diagnostics())
        runtime_pid = _wait_for_pid(pidfile)
        grandchild = _wait_for_pid(pidfile.with_suffix(".pid.child"))
    finally:
        process.close()
    assert any("killing the tree before EOF" in line for line in process.diagnostics())
    assert _wait_until_dead(runtime_pid)
    assert _wait_until_dead(grandchild), "the fallback left the descendant running"


def test_close_during_startup_leaves_no_process(monkeypatch, tmp_path, fake):
    pidfile = tmp_path / "runtime.pid"
    monkeypatch.setenv("FAKE_MODE", "slowinit")
    monkeypatch.setenv("FAKE_PIDFILE", str(pidfile))
    config = make_config(tmp_path, fake, passthrough=("FAKE_MODE", "FAKE_PIDFILE"))
    runtime = HarnessRuntime(FakeSession(), config=config)

    async def flow():
        starting = asyncio.create_task(runtime.start())
        await asyncio.sleep(0.25)  # inside the fake's 0.8 s initialize
        await runtime.close()
        with pytest.raises((RuntimeUnavailable, asyncio.CancelledError)):
            await starting
        return runtime._process

    assert run(flow()) is None
    runtime_pid = _wait_for_pid(pidfile, timeout=8.0)
    assert _wait_until_dead(runtime_pid), "close() returned while a startup was publishing"


def test_a_cancelled_start_leaves_no_process(monkeypatch, tmp_path, fake):
    pidfile = tmp_path / "runtime.pid"
    monkeypatch.setenv("FAKE_MODE", "slowinit")
    monkeypatch.setenv("FAKE_PIDFILE", str(pidfile))
    config = make_config(tmp_path, fake, passthrough=("FAKE_MODE", "FAKE_PIDFILE"))
    runtime = HarnessRuntime(FakeSession(), config=config)

    async def flow():
        starting = asyncio.create_task(runtime.start())
        await asyncio.sleep(0.25)
        starting.cancel()
        with pytest.raises(asyncio.CancelledError):
            await starting
        await runtime.close()

    run(flow())
    # The worker thread outlives the cancelled await; it must clean up after itself.
    runtime_pid = _wait_for_pid(pidfile, timeout=10.0)
    assert _wait_until_dead(runtime_pid), "a cancelled start leaked its runtime"
    assert runtime._process is None


# -- honest capabilities -----------------------------------------------------

def test_stop_is_refused_until_prompt_06(tmp_path, fake):
    runtime = HarnessRuntime(FakeSession(), config=make_config(tmp_path, fake))

    async def flow():
        with pytest.raises(RuntimeCapabilityUnavailable) as caught:
            await runtime.stop()
        return caught.value

    exc = run(flow())
    assert exc.capability == "per_session_cancel"
    assert exc.gate == "06"
    assert exc.code == "runtime_capability_unavailable"


def test_a_turn_returns_settled_text_and_claims_no_streaming(tmp_path, fake):
    session = FakeSession()
    runtime = HarnessRuntime(session, config=make_config(tmp_path, fake))

    async def flow():
        try:
            result = await runtime.run_turn("hello")
        finally:
            await runtime.close()
        return result

    result = run(flow())
    assert result.final_text == "Hello there"
    assert runtime.capabilities.live_text_deltas is False
    assert runtime.last_stop_reason == "end_turn"


def test_a_tool_update_is_refused_rather_than_ignored(monkeypatch, tmp_path, fake):
    monkeypatch.setenv("FAKE_MODE", "tool")
    runtime = HarnessRuntime(FakeSession(),
                             config=make_config(tmp_path, fake,
                                                passthrough=("FAKE_MODE",)))

    async def flow():
        try:
            await runtime.run_turn("use a shell")
        finally:
            await runtime.close()

    with pytest.raises(RuntimeCapabilityUnavailable) as caught:
        run(flow())
    assert caught.value.capability == "tools"


def test_a_server_request_is_refused_and_reported(monkeypatch, tmp_path, fake):
    monkeypatch.setenv("FAKE_MODE", "serverrequest")
    runtime = HarnessRuntime(FakeSession(),
                             config=make_config(tmp_path, fake,
                                                passthrough=("FAKE_MODE",)))

    async def flow():
        try:
            await runtime.run_turn("do something that needs permission")
        finally:
            await runtime.close()

    with pytest.raises(RuntimeCapabilityUnavailable) as caught:
        run(flow())
    assert "server request" in caught.value.capability
    assert "session/request_permission" in caught.value.capability


def test_readiness_never_blocks_the_event_loop(monkeypatch, tmp_path, fake):
    monkeypatch.setenv("FAKE_MODE", "slowinit")
    runtime = HarnessRuntime(FakeSession(),
                             config=make_config(tmp_path, fake,
                                                passthrough=("FAKE_MODE",)))

    async def flow():
        beats = 0
        stopped = asyncio.Event()

        async def heartbeat():
            nonlocal beats
            while not stopped.is_set():
                beats += 1
                await asyncio.sleep(0.02)

        task = asyncio.create_task(heartbeat())
        await runtime.start()
        stopped.set()
        await task
        await runtime.close()
        return beats

    # The fake stalls 0.8s inside initialize; a blocking implementation would record 0.
    assert run(flow()) >= 10


# -- session integration -----------------------------------------------------

class _Model:
    pass


def controller(**kwargs):
    return SessionController(
        model_factory=lambda name: _Model(),
        agent_factory=lambda *args, **kwargs: None,
        profiles={"local": {}},
        **kwargs,
    )


def test_a_harness_session_runs_its_turn_through_the_engine(monkeypatch, tmp_path, fake):
    config = make_config(tmp_path, fake)
    monkeypatch.setitem(selection_module._BACKENDS, "harness",
                        lambda session: HarnessRuntime(session, config=config))
    session = controller(runtime_selection="harness")

    async def flow():
        try:
            result = await session.run_turn("hello")
            types = [event_type.type for event_type in session.runtime.events()]
            snapshot = session.public_snapshot()
            with pytest.raises(RuntimeCapabilityUnavailable):
                session.select_model("local")
            with pytest.raises(RuntimeCapabilityUnavailable):
                await session.stop()
            return result, types, snapshot
        finally:
            await session.close()

    result, types, snapshot = run(flow())
    assert result.final_text == "Hello there"
    assert types[:2] == ["transcript", "selection"]
    assert types[-3:] == ["usage", "cost", "result"]
    assert snapshot["history"][-1]["assistant"] == "Hello there"
    assert snapshot["events"][1]["type"] == "selection"
    assert snapshot["events"][1]["data"]["engine"] == "harness"
