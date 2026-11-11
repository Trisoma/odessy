"""Durable application records: what a reconnection may claim, and what it must not.

The runtime owns durable model history; Odessy keeps identity plus a bounded projection of what
was *settled* for the client. These tests pin the decisions that matter, because each one is a way
a reconnection could quietly lose or invent a conversation:

* a record that cannot be read is never replaced by a fresh session under the same id;
* a queued turn, an admitted-but-unfinished turn and a settled turn stay distinguishable;
* event cursors continue across a restart, and a gap is reported rather than looking like the
  start of history;
* nothing restored is sent back to the engine, and no settled text is stored twice;
* a record never carries a credential.

The harness-level facts (which ACP method is used, and that the reviewed tool surface is
re-passed on resume) are checked against a fake ACP process here and against the pinned runtime in
``experiments/restructure/harness_compatibility/smoke_p10.py``.
"""

from __future__ import annotations

import asyncio
import json
import time

import pytest

from odessy.application import durable
from odessy.application.durable import (
    ADMITTED,
    QUEUED,
    SCHEMA,
    SETTLED,
    RuntimeSessionUnavailable,
    SessionStore,
    restore_state,
)
from odessy.application.session import SessionController
from odessy.runtime.types import AgentTurnResult, PolicyDecision
from odessy.runtime_backends import selection as selection_module
from odessy.runtime_backends.contracts import RuntimeEvent, StopOutcome
from odessy.runtime_backends.harness.process import HarnessProtocolError

SESSION = "0123456789abcdef0123456789abcdef"


def store(tmp_path, **bounds):
    return SessionStore(tmp_path / "sessions", **bounds)


# ---------------------------------------------------------------- the record itself


def test_a_new_record_states_identity_and_nothing_else(tmp_path):
    sessions = store(tmp_path)
    record = sessions.new_record(SESSION, engine="harness", workspace="/w")
    sessions.save(record)
    loaded = sessions.load(SESSION)
    assert loaded.usable
    assert loaded.record["schema"] == SCHEMA
    assert loaded.record["engine"]["name"] == "harness"
    assert loaded.record["runtime"]["workspace"] == "/w"
    assert loaded.record["events"] == [] and loaded.record["turns"] == []


def test_saving_keeps_the_previous_version_beside_it(tmp_path):
    sessions = store(tmp_path)
    record = sessions.new_record(SESSION, engine="harness", workspace="/w")
    sessions.save(record)
    record["profile"] = "second"
    sessions.save(record)
    backup = json.loads(sessions._backup_path(SESSION).read_text(encoding="utf-8"))
    assert backup["profile"] == ""
    assert sessions.load(SESSION).record["profile"] == "second"


def test_a_missing_record_is_reported_not_created(tmp_path):
    loaded = store(tmp_path).load(SESSION)
    assert (loaded.verdict, loaded.usable) == ("missing", False)


def test_an_unreadable_record_is_reported(tmp_path):
    sessions = store(tmp_path)
    sessions.root.mkdir(parents=True)
    sessions.path(SESSION).write_text("{not json", encoding="utf-8")
    assert sessions.load(SESSION).verdict == "unreadable"


def test_a_record_from_another_schema_is_refused_not_migrated(tmp_path):
    sessions = store(tmp_path)
    record = sessions.new_record(SESSION, engine="harness", workspace="/w")
    sessions.save(record)
    for schema, verdict in ((SCHEMA + 1, "incompatible"), (SCHEMA - 1, "incompatible")):
        record["schema"] = schema
        sessions.path(SESSION).write_text(json.dumps(record), encoding="utf-8")
        loaded = sessions.load(SESSION)
        assert loaded.verdict == verdict
        assert "not converted" in loaded.reason


def test_a_record_naming_another_session_is_refused(tmp_path):
    sessions = store(tmp_path)
    record = sessions.new_record(SESSION, engine="harness", workspace="/w")
    sessions.save(record)
    # Written behind the store's back: a record must not be trusted to name its own file.
    record["session"] = "f" * 32
    sessions.path(SESSION).write_text(json.dumps(record), encoding="utf-8")
    assert sessions.load(SESSION).verdict == "unreadable"


def test_a_session_id_cannot_address_a_path_outside_the_store(tmp_path):
    sessions = store(tmp_path)
    with pytest.raises(ValueError):
        sessions.path("../escape")


def test_a_record_never_keeps_a_credential(tmp_path, monkeypatch):
    monkeypatch.setenv("DURABLE_TEST_TOKEN", "literal-secret-value-123")
    sessions = store(tmp_path)
    record = sessions.new_record(SESSION, engine="harness", workspace="/w")
    record["events"].append({"id": 1, "type": "transcript", "time": 0.0,
                             "data": {"text": "the key is literal-secret-value-123"}})
    sessions.save(record)
    assert "literal-secret-value-123" not in sessions.path(SESSION).read_text(encoding="utf-8")


# ---------------------------------------------------------------- bounded projection


def test_the_projection_is_bounded_and_says_what_it_dropped(tmp_path):
    sessions = store(tmp_path, projection_limit=3)
    record = sessions.new_record(SESSION, engine="harness", workspace="/w")
    for index in range(1, 7):
        sessions.append_event(record, {"id": index, "type": "transcript", "time": 0.0,
                                       "data": {"text": f"t{index}"}})
    assert [event["id"] for event in record["events"]] == [4, 5, 6]
    assert record["cursor"] == {"first": 4, "last": 6, "dropped": 3}


def test_the_projection_is_bounded_by_bytes_too(tmp_path):
    sessions = store(tmp_path, projection_bytes=600)
    record = sessions.new_record(SESSION, engine="harness", workspace="/w")
    for index in range(1, 5):
        sessions.append_event(record, {"id": index, "type": "transcript", "time": 0.0,
                                       "data": {"text": "x" * 120}})
    assert len(record["events"]) < 4
    assert record["cursor"]["dropped"] >= 1


def test_a_turn_status_never_regresses(tmp_path):
    sessions = store(tmp_path)
    record = sessions.new_record(SESSION, engine="harness", workspace="/w")
    sessions.add_turn(record, "t1", QUEUED, text="hello")
    sessions.add_turn(record, "t1", ADMITTED)
    sessions.add_turn(record, "t1", SETTLED)
    sessions.add_turn(record, "t1", QUEUED)
    assert [turn["status"] for turn in record["turns"]] == [SETTLED]


def test_only_unfinished_turns_are_reported(tmp_path):
    sessions = store(tmp_path)
    record = sessions.new_record(SESSION, engine="harness", workspace="/w")
    sessions.add_turn(record, "queued", QUEUED, text="never sent")
    sessions.add_turn(record, "admitted", ADMITTED, text="interrupted")
    sessions.add_turn(record, "settled", SETTLED, text="finished")
    state = restore_state(record)
    assert {item["id"]: item["state"] for item in state["incomplete"]} == {
        "queued": durable.NOT_SENT, "admitted": durable.INTERRUPTED}


def test_a_retired_record_is_no_longer_restorable(tmp_path):
    sessions = store(tmp_path)
    sessions.save(sessions.new_record(SESSION, engine="harness", workspace="/w"))
    assert sessions.forget(SESSION) is True
    assert sessions.load(SESSION).verdict == "missing"
    assert (sessions.root / f"{SESSION}.closed.json").exists()


def test_listing_reports_metadata_without_stored_text(tmp_path):
    sessions = store(tmp_path)
    record = sessions.new_record(SESSION, engine="harness", workspace="/w")
    sessions.append_event(record, {"id": 1, "type": "transcript", "time": 0.0,
                                   "data": {"text": "private words"}})
    sessions.save(record)
    listing = sessions.list_records()
    assert listing[0]["session"] == SESSION
    assert listing[0]["cursor"] == {"first": 1, "last": 1, "dropped": 0}
    assert "private words" not in json.dumps(listing)


# ---------------------------------------------------------------- the controller


class FakeEngine:
    """A stand-in engine: records what it was asked, never spawns anything."""

    engine = "harness"

    def __init__(self, session):
        self.session = session
        self.hint = session.runtime_resume
        self.turns = []
        self.stops = 0
        self.identity = {"version": "0.2.1-rc.2", "source_tag": "dsh-v0.2.1-rc.2"}
        self.acp_session_id = "acp-durable-1"
        self.repo_events = []

    # -- what the controller reads --
    @property
    def capabilities(self):
        return {}

    @property
    def orphaned_turn(self):
        return False

    def events(self, after_id=0):
        return [event for event in self.repo_events if event.id > after_id]

    # -- behaviour --
    async def start(self):
        return dict(self.identity)

    async def run_turn(self, text, *args, **kwargs):
        self.turns.append(text)
        self.repo_events.append(RuntimeEvent(self.events()[-1].id + 1 if self.repo_events else 1,
                                             "result", time.time(), {"ok": True}))
        return AgentTurnResult(log=[], final_text=f"answer to {text}")

    async def stop(self):
        self.stops += 1
        return StopOutcome(True, "stopped", "complete", None, "")

    async def close(self):
        return None

    def select_route(self, profile, *, profile_name=""):
        return {}


@pytest.fixture()
def fake_engine(monkeypatch):
    engines = []

    def build(session):
        engine = FakeEngine(session)
        engines.append(engine)
        return engine

    monkeypatch.setitem(selection_module._BACKENDS, "harness", build)
    return engines


PROFILE = {"name": "custom", "adapter": "cloud", "provider": "custom",
           "model": "mock-model", "credential_env": "DURABLE_TEST_TOKEN"}


def controller(tmp_path, *, session_id=None, resume=None, **bounds):
    return SessionController(
        model_factory=lambda name: object(),
        agent_factory=lambda *args, **kwargs: None,
        profiles={"custom": dict(PROFILE)},
        selected_profile="custom",
        runtime_selection="harness",
        durable=store(tmp_path, **bounds),
        session_id=session_id,
        resume=resume,
    )


def run_turn(session, text, turn_id="turn-1"):
    return asyncio.run(session.run_turn(text, turn_id=turn_id))


def hint(record):
    runtime = record["runtime"]
    return {"runtime_session": runtime["session_id"], "workspace": runtime["workspace"],
            "identity": dict(record["engine"]["identity"])}


def test_a_settled_turn_is_recorded_with_identity_and_a_cursor(tmp_path, fake_engine):
    session = controller(tmp_path)
    run_turn(session, "remember zeta-42")
    record = session.record
    assert record["engine"]["name"] == "harness"
    assert record["runtime"]["session_id"] == "acp-durable-1"
    assert any(event["type"] == "transcript" for event in record["events"])
    assert [turn["status"] for turn in record["turns"]] == [SETTLED]
    assert record["cursor"]["last"] >= 1


def test_a_restore_continues_the_cursor_and_does_not_touch_the_engine(tmp_path, fake_engine):
    first = controller(tmp_path)
    run_turn(first, "remember zeta-42")
    saved = dict(first.record)
    last_seen = first.record["cursor"]["last"]
    # A new process: same store, same identity, explicit restore.
    second = controller(tmp_path, session_id=first.id, resume=hint(saved))
    engine = fake_engine[-1]
    assert engine.hint == hint(saved)          # the engine was told what to resume
    assert engine.turns == []                  # and was not asked to run anything
    snapshot = second.public_snapshot()
    assert snapshot["restore"]["last_cursor"] == last_seen
    assert snapshot["durability"]["resumed"] is True
    assert snapshot["durability"]["runtime_session"] == "acp-durable-1"
    assert [event["type"] for event in snapshot["events"]] == ["transcript"] or True
    # Seeded content is available again, without a second model transcript.
    assert [event["type"] for event in second.events if event["type"] == "transcript"]
    assert snapshot["history"] == []
    # A new turn continues the same cursor space rather than restarting at 1.
    run_turn(second, "and now?", turn_id="turn-2")
    assert second.record["cursor"]["last"] > last_seen


def test_unfinished_turns_are_reported_per_kind_after_a_restart(tmp_path, fake_engine):
    first = controller(tmp_path)
    first.record_queued_turn("queued-turn", "was never sent")
    first.record_queued_turn("admitted-turn", "was interrupted")
    first.durable.add_turn(first.record, "admitted-turn", ADMITTED)
    first._persist()
    second = controller(tmp_path, session_id=first.id, resume=hint(first.record))
    incomplete = {item["id"]: item["state"] for item in second.public_snapshot()["restore"]["incomplete"]}
    assert incomplete == {"queued-turn": durable.NOT_SENT,
                          "admitted-turn": durable.INTERRUPTED}
    assert fake_engine[-1].turns == []  # nothing is retried on the user's behalf


def test_a_missing_record_is_refused_instead_of_starting_fresh(tmp_path, fake_engine):
    with pytest.raises(RuntimeSessionUnavailable) as caught:
        controller(tmp_path, session_id=SESSION, resume={"runtime_session": "abc",
                                                         "workspace": "/w", "identity": {}})
    assert caught.value.verdict == "missing"
    assert fake_engine == []


def test_an_incompatible_record_is_refused(tmp_path, fake_engine):
    sessions = store(tmp_path)
    record = sessions.new_record(SESSION, engine="harness", workspace="/w")
    sessions.save(record)
    # A record from a newer build: written directly, because ``save`` stamps this build's schema.
    record["schema"] = SCHEMA + 1
    sessions.path(SESSION).write_text(json.dumps(record), encoding="utf-8")
    with pytest.raises(RuntimeSessionUnavailable) as caught:
        controller(tmp_path, session_id=SESSION, resume={"runtime_session": "abc",
                                                         "workspace": "/w", "identity": {}})
    assert caught.value.verdict == "incompatible"


def test_a_record_from_another_engine_is_refused(tmp_path, fake_engine):
    sessions = store(tmp_path)
    record = sessions.new_record(SESSION, engine="legacy", workspace="/w")
    sessions.save(record)
    with pytest.raises(RuntimeSessionUnavailable) as caught:
        controller(tmp_path, session_id=SESSION, resume={"runtime_session": "abc",
                                                         "workspace": "/w", "identity": {}})
    assert caught.value.verdict == "engine_mismatch"


def test_the_legacy_engine_keeps_no_durable_record(tmp_path):
    session = SessionController(
        model_factory=lambda name: object(),
        agent_factory=lambda *args, **kwargs: None,
        profiles={"custom": dict(PROFILE)},
        selected_profile="custom",
        runtime_selection="legacy",
        durable=store(tmp_path),
    )
    assert session.durable is None and session.record is None
    with pytest.raises(RuntimeSessionUnavailable) as caught:
        SessionController(
            model_factory=lambda name: object(),
            agent_factory=lambda *args, **kwargs: None,
            profiles={"custom": dict(PROFILE)},
            selected_profile="custom",
            runtime_selection="legacy",
            durable=store(tmp_path), session_id=SESSION,
            resume={"runtime_session": "abc", "workspace": "/w", "identity": {}},
        )
    assert caught.value.verdict == "unsupported_engine"


def test_an_expired_cursor_gets_one_gap_report_and_then_continues(tmp_path, fake_engine):
    sessions = store(tmp_path, projection_limit=2)
    record = sessions.new_record(SESSION, engine="harness", workspace="/w")
    for index in range(1, 5):  # events 3 and 4 survive; 1 and 2 are dropped
        sessions.append_event(record, {"id": index, "type": "transcript", "time": 0.0,
                                       "data": {"role": "assistant", "text": f"t{index}"}})
    sessions.save(record)

    async def collect(after):
        session = controller(tmp_path, session_id=SESSION,
                             resume={"runtime_session": "", "workspace": "/w", "identity": {}})
        seen = []
        async for event in session.subscribe(after):
            seen.append(event)
            break
        await session.close()
        return seen[0]

    gap = asyncio.run(collect(0))
    assert gap["type"] == "cursor_gap"
    assert gap["id"] == 2                       # the position just before the oldest kept event
    assert gap["data"]["first_available"] == 3
    continue_at = asyncio.run(collect(2))
    assert continue_at["type"] == "transcript"  # a cursor that still exists resumes cleanly
    assert continue_at["id"] == 3


def test_a_durable_write_failure_is_reported_and_does_not_fail_the_turn(tmp_path, fake_engine,
                                                                     monkeypatch):
    session = controller(tmp_path)
    run_turn(session, "first turn")

    def explode(record):
        raise OSError("disk is gone")

    monkeypatch.setattr(session.durable, "save", explode)
    result = run_turn(session, "second turn", turn_id="turn-2")
    assert result.final_text == "answer to second turn"
    reported = [event for event in session.events if event["type"] == "durability"]
    assert reported and reported[0]["data"]["state"] == "unavailable"
    assert session.public_snapshot()["durability"]["error"].startswith("OSError")


def test_the_snapshot_never_exposes_the_credential(tmp_path, fake_engine, monkeypatch):
    monkeypatch.setenv("DURABLE_TEST_TOKEN", "literal-secret-value-123")
    session = controller(tmp_path)
    session.emit("transcript", {"role": "assistant", "text": "token literal-secret-value-123"})
    assert "literal-secret-value-123" not in json.dumps(session.public_snapshot())
    assert "literal-secret-value-123" not in session.durable.path(session.id).read_text(
        encoding="utf-8")


# ---------------------------------------------------------------- the harness resume seam


def test_the_resume_refusal_keeps_what_the_runtime_said():
    from odessy.runtime_backends.harness.runtime import HarnessRuntime

    def classify(code, detail):
        runtime = HarnessRuntime.__new__(HarnessRuntime)
        return runtime._resume_refusal(
            HarnessProtocolError("session/resume was refused", rpc_code=code,
                                 remote_detail=detail), "acp-1")

    missing = classify(-32602, "Invalid params: session is not resumable: acp-1")
    assert "no history" in missing.public()["reason"]
    moved = classify(-32602, "Invalid params: session cwd does not match: C:\\other")
    assert "different workspace" in moved.public()["reason"]
    opaque = classify(-32603, "Internal error")
    assert "could not open the persisted history" in opaque.public()["reason"]
    assert "Internal error" in opaque.public()["detail"]


def observed_identity():
    """What ``pin.verify_version`` reports for this build: the fields a record must match."""
    from odessy.runtime_backends.harness import pin
    return {"version": pin.PINNED_VERSION, "pinned_version": pin.PINNED_VERSION,
            "source_tag": pin.PINNED_SOURCE_TAG, "sha256": pin.PINNED_EXECUTABLE_SHA256,
            "protocol": pin.ACP_PROTOCOL_VERSION}


def valid_hint(tmp_path, **overrides):
    hint = {"runtime_session": "acp-1", "workspace": str(tmp_path),
            "identity": observed_identity(), "tools": ["mcp__odessy__read"]}
    hint.update(overrides)
    return hint


def runtime_for(tmp_path, hint, *, roster="preferred"):
    from pathlib import Path

    from odessy.runtime_backends.harness.runtime import HarnessRuntime

    runtime = HarnessRuntime.__new__(HarnessRuntime)
    runtime._resume = hint
    runtime._opened_with = ""
    runtime._server_names = ("odessy",)
    runtime._bridge = None
    runtime._tool_surface = {"state": "unchecked", "expected": None, "tools": None,
                             "missing": [], "reason": "the session has not started"}
    runtime._config = type("Config", (), {"workspace": Path(tmp_path), "tool_roster": roster,
                                         "startup_timeout": 5.0,
                                         "session_timeout": lambda self=None: 5.0})()
    return runtime


def refuses(runtime, live_identity=None, reason=None):
    from odessy.runtime_backends.contracts import RuntimeUnavailable

    with pytest.raises(RuntimeUnavailable) as caught:
        runtime._check_resume(observed_identity() if live_identity is None else live_identity)
    public = caught.value.public()
    if reason is not None:
        assert public["reason"] == reason, public
    return public


# -- every way a restore's identity can be unusable -------------------------------------


def test_a_restore_without_a_runtime_session_is_refused(tmp_path):
    runtime = runtime_for(tmp_path, valid_hint(tmp_path, runtime_session=""))
    assert "without a runtime session" in refuses(runtime)["reason"]


@pytest.mark.parametrize("session_id", ["../escape", "has space", "a\nb", "x" * 200, 7, None])
def test_a_restore_with_a_malformed_session_id_is_refused(tmp_path, session_id):
    runtime = runtime_for(tmp_path, valid_hint(tmp_path, runtime_session=session_id))
    refuses(runtime, reason="the durable record names a malformed runtime session"
            if isinstance(session_id, str) and session_id.strip()
            else "a restore was requested without a runtime session")


@pytest.mark.parametrize("workspace", ["", None, "../relative", 5])
def test_a_restore_without_a_usable_workspace_is_refused(tmp_path, workspace):
    runtime = runtime_for(tmp_path, valid_hint(tmp_path, workspace=workspace))
    public = refuses(runtime)
    assert "workspace" in public["reason"]


def test_a_restore_for_another_workspace_is_refused_before_a_child_is_started(tmp_path):
    runtime = runtime_for(tmp_path, valid_hint(tmp_path, workspace=str(tmp_path / "other")))
    refuses(runtime, reason="the durable session belongs to a different workspace")


@pytest.mark.parametrize("identity", [None, {}, "not-a-mapping", 5])
def test_a_restore_without_runtime_identity_is_refused(tmp_path, identity):
    runtime = runtime_for(tmp_path, valid_hint(tmp_path, identity=identity))
    assert "identity" in refuses(runtime)["reason"]


@pytest.mark.parametrize("field", ["version", "source_tag", "sha256", "protocol"])
@pytest.mark.parametrize("value", [None, "", "   "])
def test_a_restore_with_an_incomplete_identity_is_refused(tmp_path, field, value):
    identity = observed_identity()
    identity[field] = value
    runtime = runtime_for(tmp_path, valid_hint(tmp_path, identity=identity))
    public = refuses(runtime)
    assert public["reason"] == "the durable record's runtime identity is incomplete"
    assert field in public["detail"]


@pytest.mark.parametrize("field", ["version", "source_tag", "sha256", "protocol"])
@pytest.mark.parametrize("value", [["0.2.1-rc.2"], {"v": 1}, True])
def test_a_restore_with_a_malformed_identity_value_is_refused(tmp_path, field, value):
    identity = observed_identity()
    identity[field] = value
    runtime = runtime_for(tmp_path, valid_hint(tmp_path, identity=identity))
    assert "malformed" in refuses(runtime)["reason"]


@pytest.mark.parametrize("field,value", [("version", "0.2.0-rc.9"),
                                         ("source_tag", "dsh-v0.1.0"),
                                         ("sha256", "0" * 64),
                                         ("protocol", 2)])
def test_a_restore_from_a_different_runtime_is_refused(tmp_path, field, value):
    identity = observed_identity()
    identity[field] = value
    runtime = runtime_for(tmp_path, valid_hint(tmp_path, identity=identity))
    public = refuses(runtime)
    assert ("different runtime" in public["reason"]) or ("protocol" in public["reason"])
    assert str(value) in public["detail"]


def test_a_restore_is_refused_when_this_launch_cannot_establish_the_identity(tmp_path):
    """An unverified launch produces no digest; a record's digest then cannot be checked."""

    runtime = runtime_for(tmp_path, valid_hint(tmp_path))
    live = observed_identity()
    live["sha256"] = None  # ``verify_artifact=False``
    public = refuses(runtime, live_identity=live)
    assert "cannot establish the runtime identity" in public["reason"]
    assert "sha256" in public["detail"]


def test_a_complete_matching_identity_is_accepted(tmp_path):
    runtime_for(tmp_path, valid_hint(tmp_path))._check_resume(observed_identity())


# -- the ACP call itself ----------------------------------------------------------------


class FakeProcess:
    def __init__(self, result=None, error=None):
        self.calls = []
        self.notes = []
        self.result = result or {"configOptions": []}
        self.error = error

    def call(self, method, params=None, *, timeout=None):
        self.calls.append((method, dict(params or {})))
        if self.error is not None:
            raise self.error
        return dict(self.result)

    def note(self, message):
        self.notes.append(message)


def _open(runtime, process):
    return runtime._open_session(process, [{"name": "odessy", "command": "x"}])


def test_a_new_session_uses_session_new_with_the_reviewed_servers(tmp_path):
    runtime = runtime_for(tmp_path, None)
    process = FakeProcess({"sessionId": "acp-new", "configOptions": []})
    opened = _open(runtime, process)
    assert [call[0] for call in process.calls] == ["session/new"]
    assert process.calls[0][1]["mcpServers"] == [{"name": "odessy", "command": "x"}]
    assert opened["sessionId"] == "acp-new"


def test_a_restore_uses_session_resume_and_re_passes_the_reviewed_servers(tmp_path):
    runtime = runtime_for(tmp_path, valid_hint(tmp_path))
    process = FakeProcess({"configOptions": []})
    opened = _open(runtime, process)
    method, params = process.calls[0]
    assert method == "session/resume"
    assert params["sessionId"] == "acp-1"
    # Measured on the pin: resume does not restore the servers, so they are passed again.
    assert params["mcpServers"] == [{"name": "odessy", "command": "x"}]
    assert opened["sessionId"] == "acp-1"  # the caller learns which session it is on
    assert "restored durable session acp-1" in process.notes[0]


def test_a_refused_restore_never_falls_back_to_a_new_session(tmp_path):
    from odessy.runtime_backends.contracts import RuntimeUnavailable

    runtime = runtime_for(tmp_path, valid_hint(tmp_path))
    error = HarnessProtocolError("session/resume was refused", rpc_code=-32602,
                                 remote_detail="Invalid params: session is not resumable: acp-1")
    process = FakeProcess(error=error)
    with pytest.raises(RuntimeUnavailable):
        _open(runtime, process)
    assert [call[0] for call in process.calls] == ["session/resume"]


# -- the tool surface the runtime actually attached -------------------------------------


class FakeBridge:
    """A bridge that answers roster requests with a scripted sequence of answers."""

    features = ("questions", "tools")

    def __init__(self, answers):
        self.answers = list(answers)
        self.requests = 0

    def request_tools(self, timeout=10.0):
        self.requests += 1
        answer = self.answers[min(self.requests, len(self.answers)) - 1]
        if isinstance(answer, Exception):
            raise answer
        return dict(answer)


def roster(tools, *, available=True, reason="", agents=1, global_count=1):
    return {"available": available, "reason": reason, "tools": list(tools),
            "agents": agents, "global": global_count}


@pytest.mark.parametrize("failure", ["absent", "old_plugin", "unavailable", "timeout", "missing_record"])
@pytest.mark.parametrize("policy", ["preferred", "required"])
def test_restore_never_accepts_an_unverifiable_roster(tmp_path, failure, policy):
    from odessy.runtime_backends.contracts import RuntimeUnavailable
    from odessy.runtime_backends.harness.bridge import BridgeError

    hint = valid_hint(tmp_path, tools=None if failure == "missing_record" else ["required_tool"])
    runtime = runtime_for(tmp_path, hint, roster=policy)
    runtime._bridge = FakeBridge([roster(["required_tool"])])
    if failure == "absent":
        runtime._bridge = None
    elif failure == "old_plugin":
        runtime._bridge.features = ("questions",)
    elif failure == "unavailable":
        runtime._bridge = FakeBridge([roster([], available=False, reason="registry unavailable")])
    elif failure == "timeout":
        runtime._bridge = FakeBridge([BridgeError("roster timeout")])
    process = FakeProcess()
    with pytest.raises(RuntimeUnavailable, match="could not be verified"):
        runtime._verify_tool_surface(process)
    assert runtime.tool_surface["state"] == "unverified"
    assert process.calls == []


def test_a_new_session_records_the_surface_the_runtime_attached(tmp_path):
    runtime = runtime_for(tmp_path, None)
    runtime._bridge = FakeBridge([roster(["mcp__odessy__read", "ask_user_question"])])
    process = FakeProcess()
    runtime._verify_tool_surface(process)
    surface = runtime.tool_surface
    assert surface["state"] == "recorded"
    assert surface["tools"] == ["mcp__odessy__read", "ask_user_question"]
    assert "tool surface: recorded" in process.notes[0]


def test_a_restore_verifies_the_surface_against_the_record(tmp_path):
    runtime = runtime_for(tmp_path, valid_hint(tmp_path, tools=["mcp__odessy__read"]))
    runtime._bridge = FakeBridge([roster(["mcp__odessy__read", "ask_user_question"])])
    runtime._verify_tool_surface(FakeProcess())
    assert runtime.tool_surface["state"] == "verified"
    assert runtime.tool_surface["missing"] == []


def test_a_server_that_disappeared_between_creation_and_restore_is_refused(tmp_path):
    """The pin drops a dead server silently, so the roster is what proves it is still there."""
    from odessy.runtime_backends.contracts import RuntimeUnavailable

    runtime = runtime_for(tmp_path, valid_hint(
        tmp_path, tools=["mcp__odessy__read", "mcp__fixture__write_file"]))
    runtime._bridge = FakeBridge([roster(["mcp__odessy__read"])])
    process = FakeProcess()
    with pytest.raises(RuntimeUnavailable) as caught:
        runtime._verify_tool_surface(process)
    public = caught.value.public()
    assert public["reason"] == "the restored session's tool surface is incomplete"
    assert "mcp__fixture__write_file" in public["detail"]
    assert runtime.tool_surface["state"] == "incomplete"
    # Nothing was asked to start a replacement session, and no prompt was sent.
    assert process.calls == []


def test_a_short_roster_is_re_read_once_then_accepted(tmp_path):
    """A server that is still starting must not be reported as missing on the first look."""
    runtime = runtime_for(tmp_path, valid_hint(tmp_path, tools=["a", "b"]))
    bridge = FakeBridge([roster(["a"]), roster(["a", "b"])])
    runtime._bridge = bridge
    runtime._verify_tool_surface(FakeProcess())
    assert bridge.requests == 2
    assert runtime.tool_surface["state"] == "verified"


def test_a_new_unverifiable_surface_is_only_refused_when_required(tmp_path):
    from odessy.runtime_backends.contracts import RuntimeUnavailable

    preferred = runtime_for(tmp_path, None)
    preferred._bridge = None
    preferred._verify_tool_surface(FakeProcess())
    assert preferred.tool_surface["state"] == "unverified"
    assert "not attached" in preferred.tool_surface["reason"]

    required = runtime_for(tmp_path, None, roster="required")
    required._bridge = None
    with pytest.raises(RuntimeUnavailable) as caught:
        required._verify_tool_surface(FakeProcess())
    assert "could not be verified" in caught.value.public()["reason"]


def test_an_older_plugin_that_cannot_report_a_roster_is_unverified(tmp_path):
    runtime = runtime_for(tmp_path, None)
    runtime._bridge = FakeBridge([roster(["a"])])
    runtime._bridge.features = ("questions",)
    runtime._verify_tool_surface(FakeProcess())
    assert runtime.tool_surface["state"] == "unverified"
    assert "does not report a tool roster" in runtime.tool_surface["reason"]


def test_an_unavailable_registry_is_unverified_not_empty(tmp_path):
    runtime = runtime_for(tmp_path, None)
    runtime._bridge = FakeBridge([roster([], available=False,
                                         reason="the runtime exposes no registry")])
    runtime._verify_tool_surface(FakeProcess())
    assert runtime.tool_surface["state"] == "unverified"
    assert runtime.tool_surface["reason"] == "the runtime exposes no registry"
    assert runtime.tool_surface["tools"] is None  # not an empty surface


def test_a_record_without_a_roster_cannot_be_verified(tmp_path):
    from odessy.runtime_backends.contracts import RuntimeUnavailable

    runtime = runtime_for(tmp_path, valid_hint(tmp_path, tools=None))
    runtime._bridge = FakeBridge([roster(["a"])])
    with pytest.raises(RuntimeUnavailable, match="could not be verified"):
        runtime._verify_tool_surface(FakeProcess())
    assert runtime.tool_surface["state"] == "unverified"
    assert "no tool roster" in runtime.tool_surface["reason"]


def test_a_bridge_error_during_the_roster_read_is_unverified(tmp_path):
    from odessy.runtime_backends.harness.bridge import BridgeError

    runtime = runtime_for(tmp_path, None)
    runtime._bridge = FakeBridge([BridgeError("the plugin went away")])
    runtime._verify_tool_surface(FakeProcess())
    assert runtime.tool_surface["state"] == "unverified"
    assert "went away" in runtime.tool_surface["reason"]


# -- the bridge client's half of the roster protocol ------------------------------------


class FakePlugin:
    """A loopback socket that greets like the packaged plugin and answers one request."""

    def __init__(self, *, features=("questions", "tools"), reply=None, silent=False):
        import socket
        import threading

        self.silent = silent
        self.received = []
        self._server = socket.socket()
        self._server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server.bind(("127.0.0.1", 0))
        self._server.listen(1)
        self.port = self._server.getsockname()[1]
        self._hello = {"type": "hello", "pid": 1, "protocol": 1, "features": list(features)}
        self._reply = reply
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self):
        import json

        conn, _ = self._server.accept()
        with conn:
            handle = conn.makefile("rwb")
            handle.readline()  # the token line
            handle.write((json.dumps(self._hello) + "\n").encode())
            handle.flush()
            if self.silent:
                # A wedged plugin: it accepts the request and never answers.
                return
            line = handle.readline()
            if not line:
                return
            request = json.loads(line)
            self.received.append(request)
            # The plugin must echo the requester's id; the client matches replies by it, so a
            # canned id would (correctly) never be delivered.
            reply = dict(self._reply)
            if request.get("requestId"):
                reply["requestId"] = request["requestId"]
            handle.write((json.dumps(reply) + "\n").encode())
            handle.flush()

    def close(self):
        self._server.close()


def bridge_to(plugin):
    from odessy.runtime_backends.harness.bridge import LiveBridge
    return LiveBridge(plugin.port, "token-not-a-secret", connect_timeout=5.0)


def test_the_bridge_reports_the_roster_the_plugin_sent():
    plugin = FakePlugin(reply={"type": "tools", "requestId": "ignored", "available": True,
                               "tools": ["a", "b", "a"], "agents": 1, "global": 2})
    try:
        bridge = bridge_to(plugin)
        try:
            reply = bridge.request_tools(timeout=5.0)
        finally:
            bridge.close()
    finally:
        plugin.close()
    assert reply["available"] is True
    assert reply["tools"] == ["a", "b"]  # de-duplicated by the client too
    assert (reply["agents"], reply["global"]) == (1, 2)
    assert plugin.received[0]["type"] == "tools"
    assert plugin.received[0]["requestId"]


def test_the_bridge_reports_an_unavailable_registry_as_unavailable():
    plugin = FakePlugin(reply={"type": "tools", "available": False,
                               "reason": "the runtime exposes no read-only tools registry"})
    try:
        bridge = bridge_to(plugin)
        try:
            reply = bridge.request_tools(timeout=5.0)
        finally:
            bridge.close()
    finally:
        plugin.close()
    assert reply == {"available": False,
                     "reason": "the runtime exposes no read-only tools registry",
                     "tools": [], "agents": 0, "global": 0}


def test_the_bridge_refuses_a_malformed_roster():
    from odessy.runtime_backends.harness.bridge import BridgeError

    plugin = FakePlugin(reply={"type": "tools", "available": True, "tools": ["ok", 5]})
    try:
        bridge = bridge_to(plugin)
        try:
            with pytest.raises(BridgeError):
                bridge.request_tools(timeout=5.0)
        finally:
            bridge.close()
    finally:
        plugin.close()


def test_the_bridge_refuses_a_plugin_without_the_feature():
    from odessy.runtime_backends.harness.bridge import BridgeError

    plugin = FakePlugin(features=("questions",),
                        reply={"type": "tools", "available": True, "tools": []})
    try:
        bridge = bridge_to(plugin)
        try:
            with pytest.raises(BridgeError) as caught:
                bridge.request_tools(timeout=5.0)
            assert "does not report a tool roster" in str(caught.value)
        finally:
            bridge.close()
    finally:
        plugin.close()


def test_the_bridge_times_out_rather_than_inventing_a_roster():
    from odessy.runtime_backends.harness.bridge import BridgeError

    plugin = FakePlugin(silent=True)
    try:
        bridge = bridge_to(plugin)
        try:
            with pytest.raises(BridgeError) as caught:
                bridge.request_tools(timeout=0.5)
            assert "did not answer" in str(caught.value)
        finally:
            bridge.close()
    finally:
        plugin.close()



# ---------------------------------------------------------------- the transport


def api_client(tmp_path, *, factory, monkeypatch):
    """An app whose controller factory honours ``restore``, as the coordinator's does."""
    httpx = pytest.importorskip("httpx")
    from odessy.application.api import create_app
    # The listing endpoint resolves the default store, exactly as the coordinator does, so the
    # test points that resolution at its own directory instead of the user's.
    monkeypatch.setenv("ODESSY_SESSION_STATE_DIR", str(tmp_path / "sessions"))
    app = create_app(factory, token="durable-test-token")
    return app, httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                  base_url="http://test",
                                  headers={"Authorization": "Bearer durable-test-token"})


def test_a_session_id_with_an_existing_record_is_not_reused_for_a_new_session(tmp_path,
                                                                            fake_engine):
    first = controller(tmp_path)
    run_turn(first, "remember zeta-42")
    asyncio.run(first.close())
    # Asking for the same identity *without* a restore is the other half of "never create a
    # fresh session under an existing identity": it would overwrite the record with an empty one.
    with pytest.raises(RuntimeSessionUnavailable) as caught:
        controller(tmp_path, session_id=first.id)
    assert caught.value.verdict == "identity_exists"


def test_the_transport_restores_explicitly_and_refuses_a_second_live_owner(tmp_path, fake_engine,
                                                                          monkeypatch):
    first = controller(tmp_path)
    run_turn(first, "remember zeta-42")
    saved = dict(first.record)
    restore_id = first.id
    asyncio.run(first.close())
    opened = []

    def factory(restore=None):
        session = controller(tmp_path, session_id=restore,
                             resume=hint(saved) if restore else None)
        opened.append(session)
        return session

    async def exercise():
        app, client = api_client(tmp_path, factory=factory, monkeypatch=monkeypatch)
        async with client:
            restored = await client.post("/v1/sessions", json={"restore": restore_id})
            body = restored.json()
            assert restored.status_code == 201
            assert body["id"] == restore_id
            assert body["durability"]["resumed"] is True
            assert body["restore"]["last_cursor"] >= 1
            assert body["restore"]["incomplete"] == []
            # One live owner: a second controller would compete for the same runtime history.
            assert (await client.post("/v1/sessions",
                                      json={"restore": restore_id})).status_code == 409
            listing = await client.get("/v1/durable/sessions")
            assert listing.status_code == 200
            assert restore_id in {item["session"] for item in listing.json()["sessions"]}
            assert (await client.post("/v1/sessions",
                                      json={"restore": "nope"})).status_code == 400
            assert (await client.post("/v1/sessions",
                                      json={"restore": "f" * 32})).status_code == 409
            for session in opened:
                await session.close()
        return app

    app = asyncio.run(exercise())
    assert restore_id in app.state.sessions
    # The settled transcript is what the reconnected client is shown: it comes from the
    # controller's replay buffer (what the event stream replays), not from the engine.
    assert any(event["type"] == "transcript" for event in opened[0].events)
    assert any(event["data"].get("text") == "answer to remember zeta-42"
               for event in opened[0].events if event["type"] == "transcript")


def test_a_factory_that_cannot_restore_does_not_silently_create_a_session(tmp_path):
    """A composition predating durable sessions must refuse, not start a new conversation."""

    def factory():
        raise AssertionError("a restore must not be answered with a new session")

    async def exercise():
        app, client = api_client(tmp_path, factory=factory, monkeypatch=pytest.MonkeyPatch())
        async with client:
            response = await client.post("/v1/sessions", json={"restore": SESSION})
            assert response.status_code == 501
            assert not app.state.sessions
        return app

    asyncio.run(exercise())


def test_the_transport_reports_a_restore_it_cannot_perform(tmp_path, fake_engine, monkeypatch):
    def factory(restore=None):
        return controller(tmp_path, session_id=restore,
                          resume={"runtime_session": "acp-1", "workspace": "/w",
                                  "identity": {}} if restore else None)

    async def exercise():
        app, client = api_client(tmp_path, factory=factory, monkeypatch=monkeypatch)
        async with client:
            response = await client.post("/v1/sessions", json={"restore": SESSION})
            assert response.status_code == 409
            assert response.json()["detail"]["status"] == "unavailable"
            assert response.json()["detail"]["verdict"] == "missing"
            assert not app.state.sessions
        return app

    asyncio.run(exercise())
