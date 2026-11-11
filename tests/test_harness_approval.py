"""Prompt 07: Harness permission requests are bound to Odessy approvals.

Two kinds of evidence live here:

* deterministic unit tests of :class:`HarnessApprovalBridge` — the projection, the
  one-shot rules, and every path that must *not* authorize (wrong session, stale turn,
  changed operation, a Stop that raced an allow, a malformed option list, a question);
* end-to-end tests against a small fake runtime, which emits the runtime's own
  ``session/update`` ``tool_call`` frame and then asks ``session/request_permission``.
  The fake executes only after it receives an answer, so the test can show that nothing
  was executed while the approval was pending.

No real runtime is involved. The pinned smoke that launches the real binary is prompt
01/05/06's; this file proves the Odessy side of the binding.
"""

import asyncio
import json
import sys
import threading
import time

import pytest

from odessy.interaction.broker import InteractionBroker, InteractionError
from odessy.runtime_backends import RuntimeCapabilityUnavailable
from odessy.runtime_backends.harness import (
    NARROW_PATCH_NAME,
    NARROW_PATCH_TEXT,
    HarnessConfig,
    HarnessRuntime,
)
from odessy.runtime_backends.harness.approvals import (
    ApprovalBindError,
    HarnessApprovalBridge,
    allowed_response,
    rejected_response,
)

FAKE_RUNTIME = r'''
import json, os, sys, threading

MODE = os.environ.get("FAKE_MODE", "permission")
ANSWER_FILE = os.environ.get("FAKE_ANSWER_FILE")
EXEC_FILE = os.environ.get("FAKE_EXEC_FILE")
STATE = {"session": None, "answered": threading.Event(), "answer": None}

def emit(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()

if "--version" in sys.argv:
    print("0.2.1-rc.2")
    raise SystemExit(0)

def model_options():
    value = json.dumps(["odessy-deepseek", "mock-model"], separators=(",", ":"))
    return [
        {"id": "model", "type": "select", "currentValue": value, "options": [
            {"group": "odessy-deepseek", "name": "Odessy",
             "options": [{"value": value}]}]},
        {"id": "reasoning_effort", "type": "select", "currentValue": "high",
         "options": [{"value": name} for name in ("off", "low", "high", "max")]},
    ]

def permission_options():
    return [
        {"optionId": "allow-once", "name": "Allow once", "kind": "allow_once"},
        {"optionId": "reject-once", "name": "Reject", "kind": "reject_once"},
    ]

def prompt(request_id):
    session = STATE["session"]
    if MODE in ("permission", "unbound", "question", "durable"):
        if MODE != "question":
            emit({"jsonrpc": "2.0", "method": "session/update", "params": {
                "sessionId": session, "update": {
                    "sessionUpdate": "tool_call", "toolCallId": "call-1",
                    "title": "pwsh", "kind": "other", "status": "in_progress",
                    "rawInput": {"command": "echo hi",
                                 "sandbox_permissions": "workspace-write",
                                 "justification": "the model needs the network"}}}})
        if MODE == "permission" or MODE == "durable":
            options = permission_options()
            if MODE == "durable":
                options.insert(0, {"optionId": "allow-always", "name": "Always",
                                   "kind": "allow_always"})
            emit({"jsonrpc": "2.0", "id": "srv-1", "method": "session/request_permission",
                  "params": {"sessionId": session, "toolCall": {"toolCallId": "call-1"},
                             "options": options}})
            STATE["answered"].wait(20)
            answer = STATE["answer"] or {}
            if ANSWER_FILE:
                with open(ANSWER_FILE, "w", encoding="utf-8") as handle:
                    handle.write(json.dumps(answer))
            outcome = (answer.get("result") or {}).get("outcome") or {}
            if outcome.get("optionId") == "allow-once" and EXEC_FILE:
                with open(EXEC_FILE, "w", encoding="utf-8") as handle:
                    handle.write("executed\n")
        elif MODE == "unbound":
            emit({"jsonrpc": "2.0", "id": "srv-2", "method": "session/request_permission",
                  "params": {"sessionId": session,
                             "toolCall": {"toolCallId": "call-unknown"},
                             "options": permission_options()}})
        else:
            emit({"jsonrpc": "2.0", "id": "srv-3", "method": "elicitation/create",
                  "params": {"sessionId": session, "message": "which file?"}})
    for chunk in ("Hello", " there"):
        emit({"jsonrpc": "2.0", "method": "session/update", "params": {
            "sessionId": session, "update": {"sessionUpdate": "agent_message_chunk",
                                             "content": {"type": "text", "text": chunk}}}})
    emit({"jsonrpc": "2.0", "id": request_id, "result": {"stopReason": "end_turn"}})

for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        message = json.loads(line)
    except ValueError:
        continue
    if "method" not in message:
        if message.get("id") == "srv-1":
            STATE["answer"] = message
            STATE["answered"].set()
        continue
    method, request_id = message.get("method"), message.get("id")
    if method == "initialize":
        emit({"jsonrpc": "2.0", "id": request_id, "result": {"protocolVersion": 1}})
    elif method == "session/new":
        STATE["session"] = "acp-approval"
        emit({"jsonrpc": "2.0", "id": request_id,
              "result": {"sessionId": "acp-approval", "configOptions": model_options()}})
    elif method == "session/set_config_option":
        emit({"jsonrpc": "2.0", "id": request_id,
              "result": {"configOptions": model_options()}})
    elif method == "session/prompt":
        threading.Thread(target=prompt, args=(request_id,), daemon=True).start()
    elif method == "session/cancel":
        pass
    else:
        emit({"jsonrpc": "2.0", "id": request_id,
              "error": {"code": -32601, "message": "unknown method " + str(method)}})
'''


class ApprovalSession:
    """The parts of a session the engine and its bridge may touch."""

    def __init__(self):
        self.id = "sess-approval"
        self._events = []
        self.stop_epoch = 0
        self.audit = None
        self.model_profile = {"name": "local", "adapter": "cloud",
                              "provider": "deepseek", "model": "mock-model"}
        self.model_profiles = [self.model_profile]
        self.interactions = InteractionBroker(self.emit, lambda value: value)

    def events(self):
        return list(self._events)

    def emit(self, event_type, data=None):
        self._events.append({"id": len(self._events) + 1, "type": event_type,
                             "time": time.time(), "data": data or {}})

    def emit_from_worker(self, event_type, data=None):
        self.emit(event_type, data)

    def public_snapshot(self):
        return {"id": self.id}


@pytest.fixture()
def fake_approval(tmp_path):
    script = tmp_path / "fake_approval_runtime.py"
    script.write_text(FAKE_RUNTIME, encoding="utf-8")
    return (sys.executable, str(script))


@pytest.fixture(autouse=True)
def _credential(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "k-test-not-a-real-key")
    # The ACP permission fixture does not host the separate streaming plugin.
    monkeypatch.setenv("ODESSY_HARNESS_BRIDGE", "off")


def make_config(tmp_path, fake, **overrides):
    home, workspace = tmp_path / "home", tmp_path / "ws"
    home.mkdir(exist_ok=True)
    workspace.mkdir(exist_ok=True)
    overlay = home / NARROW_PATCH_NAME
    overlay.write_text(NARROW_PATCH_TEXT, encoding="utf-8")
    settings = dict(command=tuple(fake), home=home, workspace=workspace, patches=(),
                    narrow_patch=overlay, startup_timeout=8.0, turn_timeout=15.0,
                    shutdown_timeout=3.0, verify_artifact=False)
    settings.update(overrides)
    return HarnessConfig(**settings)


def run(coroutine):
    return asyncio.run(coroutine)


async def wait_for_approval(broker, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        entries = broker.pending_approvals
        if entries:
            return entries[0]
        await asyncio.sleep(0.01)
    raise AssertionError("no approval was raised")


# -- the projection ----------------------------------------------------------

def bridge(tmp_path, *, approver, epoch=None):
    return HarnessApprovalBridge(
        workspace=tmp_path,
        identity=lambda: {"version": "0.2.1-rc.2", "artifact": "a" * 64,
                          "odessy_session": "sess-1"},
        approver=approver,
        epoch=epoch,
    )


def observe_call(bridge, *, call="call-1", name="pwsh", arguments=None, status="in_progress"):
    bridge.observe({"jsonrpc": "2.0", "method": "session/update", "params": {
        "sessionId": "acp-1",
        "update": {"sessionUpdate": "tool_call", "toolCallId": call, "title": name,
                   "kind": "other", "status": status,
                   "rawInput": arguments if arguments is not None else {
                       "command": "echo hi", "sandbox_permissions": "workspace-write",
                       "justification": "needs the network"}}}})


def permission_params(*, session="acp-1", call="call-1", options=None):
    return {"sessionId": session, "toolCall": {"toolCallId": call},
            "options": options if options is not None else [
                {"optionId": "allow-once", "name": "Allow once", "kind": "allow_once"},
                {"optionId": "reject-once", "name": "Reject", "kind": "reject_once"},
            ]}


def bound(tmp_path, *, approver=None, options=None, epoch=None):
    async def allow(_details):
        return True, "resolved"

    bridge_ = bridge(tmp_path, approver=approver or allow, epoch=epoch)
    bridge_.begin_turn(1, session_id="acp-1")
    observe_call(bridge_)
    return bridge_, bridge_.bind("srv-1", permission_params(options=options))


def test_the_bound_operation_is_the_full_odessy_projection(tmp_path):
    bridge_, request = bound(tmp_path)
    details = request.details
    assert details["operation"] == "pwsh"
    assert details["arguments"]["command"] == "echo hi"
    assert details["risk"] == "high"
    assert details["scopes"] == ["authorization", "escalation"]
    assert "voice" not in details["allowed_channels"]
    assert details["requested_authority"] == ["allow_once"]
    assert details["workspace"] == str(tmp_path)
    assert details["runtime_identity"] == {"runtime": "harness",
                                           "version": "0.2.1-rc.2", "artifact": "a" * 64}
    assert details["session_identity"] == {"acp_session": "acp-1",
                                           "odessy_session": "sess-1"}
    assert details["request_identity"]["tool_call_id"] == "call-1"
    assert details["request_identity"]["request_id"] == "srv-1"
    assert details["request_identity"]["turn"] == 1
    assert details["model_reason"] == "needs the network"
    # The existing approval binding, not a new one: same inputs, same fingerprint.
    from odessy.authority.policy import OdessyPolicy
    from odessy.runtime.types import PolicyDecision, ToolRequest
    assert details["fingerprint"] == request.plan.fingerprint
    assert request.plan.matches(
        ToolRequest("pwsh", {"command": "echo hi",
                             "sandbox_permissions": "workspace-write",
                             "justification": "needs the network"}),
        PolicyDecision("confirm", "needs the network", "high", rule="harness_permission"),
        OdessyPolicy(tmp_path), execution_target=request.target)


def test_a_bound_allow_is_the_one_shot_option(tmp_path):
    bridge_, request = bound(tmp_path)
    response = run(bridge_.answer(request))
    assert response == allowed_response("allow-once")
    assert bridge_.stats.allowed == 1
    # One-shot: the same tool call cannot be asked about again.
    with pytest.raises(ApprovalBindError):
        bridge_.bind("srv-9", permission_params())


def test_an_explicit_denial_is_a_reject_not_a_cancel(tmp_path):
    async def deny(_details):
        return False, "resolved"

    bridge_, request = bound(tmp_path, approver=deny)
    assert run(bridge_.answer(request)) == rejected_response("reject-once")
    assert bridge_.stats.rejected == 1


@pytest.mark.parametrize("reason", ["expired", "stopped", "interrupted", "closed", "busy",
                                    "handler_failed", "no_channel", "cancelled"])
def test_anything_but_a_resolved_allow_is_cancelled(tmp_path, reason):
    async def unresolved(_details):
        return False, reason

    bridge_, request = bound(tmp_path, approver=unresolved)
    response = run(bridge_.answer(request))
    assert response["result"]["outcome"] == {"outcome": "cancelled"}
    assert bridge_.stats.allowed == 0


# -- every refusal path ------------------------------------------------------

def test_a_request_from_another_session_cannot_be_bound(tmp_path):
    bridge_ = bridge(tmp_path, approver=None)
    bridge_.begin_turn(1, session_id="acp-1")
    observe_call(bridge_)
    with pytest.raises(ApprovalBindError) as caught:
        bridge_.bind("srv-1", permission_params(session="acp-2"))
    assert "another session" in caught.value.reason
    assert bridge_.snapshot()["pending"] == []


def test_an_unobserved_tool_call_cannot_be_bound(tmp_path):
    bridge_ = bridge(tmp_path, approver=None)
    bridge_.begin_turn(1, session_id="acp-1")
    with pytest.raises(ApprovalBindError) as caught:
        bridge_.bind("srv-1", permission_params())
    assert "no observed tool call" in caught.value.reason


def test_a_finished_tool_call_cannot_be_bound(tmp_path):
    bridge_ = bridge(tmp_path, approver=None)
    bridge_.begin_turn(1, session_id="acp-1")
    observe_call(bridge_, status="completed")
    with pytest.raises(ApprovalBindError) as caught:
        bridge_.bind("srv-1", permission_params())
    assert "already ended" in caught.value.reason


def test_a_stale_turn_cannot_be_reused(tmp_path):
    bridge_, request = bound(tmp_path)
    bridge_.begin_turn(2, session_id="acp-1")
    response = run(bridge_.answer(request))
    assert response["result"]["outcome"] == {"outcome": "cancelled"}
    assert bridge_.stats.allowed == 0


@pytest.mark.parametrize("options", [
    [{"optionId": "a", "kind": "allow_once"}],                                   # no reject
    [{"optionId": "r", "kind": "reject_once"}],                                  # no allow
    [{"optionId": "a", "kind": "allow_once"}, {"optionId": "a2", "kind": "allow_once"},
     {"optionId": "r", "kind": "reject_once"}],                                  # ambiguous
    [{"optionId": "always", "kind": "allow_always"},
     {"optionId": "r", "kind": "reject_once"}],                                  # no one-shot allow
    [{"optionId": "", "kind": "allow_once"}, {"optionId": "r", "kind": "reject_once"}],
    # One id cannot mean both answers, or a denial is ambiguous on the wire.
    [{"optionId": "same", "kind": "allow_once"},
     {"optionId": "same", "kind": "reject_once"}],
    "not a list",
])
def test_malformed_options_are_refused(tmp_path, options):
    bridge_ = bridge(tmp_path, approver=None)
    bridge_.begin_turn(1, session_id="acp-1")
    observe_call(bridge_)
    with pytest.raises(ApprovalBindError):
        bridge_.bind("srv-1", permission_params(options=options))


def test_a_durable_grant_option_is_never_the_one_selected(tmp_path):
    bridge_ = bridge(tmp_path, approver=None)
    bridge_.begin_turn(1, session_id="acp-1")
    observe_call(bridge_)
    request = bridge_.bind("srv-1", permission_params(options=[
        {"optionId": "allow-always", "kind": "allow_always"},
        {"optionId": "allow-once", "kind": "allow_once"},
        {"optionId": "reject-once", "kind": "reject_once"},
    ]))
    assert request.allow_option == "allow-once"


def test_a_changed_operation_after_the_allow_is_not_authorized(tmp_path):
    async def allow(_details):
        # The runtime re-describes the same call incorrectly while the human decides.
        observe_call(bridge_, arguments={"command": "rm -rf /",
                                         "justification": "changed"})
        return True, "resolved"

    bridge_ = bridge(tmp_path, approver=allow)
    bridge_.begin_turn(1, session_id="acp-1")
    observe_call(bridge_)
    request = bridge_.bind("srv-1", permission_params())
    response = run(bridge_.answer(request))
    assert response["result"]["outcome"] == {"outcome": "cancelled"}
    assert bridge_.stats.allowed == 0


def test_a_stop_that_lands_after_the_allow_wins(tmp_path):
    state = {"epoch": 0}

    async def allow(_details):
        state["epoch"] += 1  # a Stop happened while the person was deciding
        return True, "resolved"

    bridge_ = bridge(tmp_path, approver=allow, epoch=lambda: state["epoch"])
    bridge_.begin_turn(1, session_id="acp-1")
    observe_call(bridge_)
    request = bridge_.bind("srv-1", permission_params())
    assert state["epoch"] == 0 and request.stop_epoch == 0
    response = run(bridge_.answer(request))
    assert response["result"]["outcome"] == {"outcome": "cancelled"}
    assert bridge_.stats.allowed == 0


def test_a_closed_bridge_never_writes_an_allow(tmp_path):
    bridge_, request = bound(tmp_path)
    bridge_.close()
    response = run(bridge_.answer(request))
    assert response["result"]["outcome"] == {"outcome": "cancelled"}


def test_the_wire_guard_downgrades_a_stale_allow_and_never_upgrades(tmp_path):
    state = {"epoch": 0}
    bridge_, request = bound(tmp_path, epoch=lambda: state["epoch"])
    allow = allowed_response("allow-once")
    reject = rejected_response("reject-once")

    # Current operation: the allow stands, and a reject is never turned into one.
    assert bridge_.guard_response(request, allow) == allow
    assert bridge_.guard_response(request, reject) == reject

    # A Stop after the decision: the allow must not reach the wire, but the reject still may.
    state["epoch"] += 1
    downgraded = bridge_.guard_response(request, allow)
    assert downgraded["result"]["outcome"] == {"outcome": "cancelled"}
    assert bridge_.guard_response(request, reject) == reject
    assert bridge_.stats.retracted == 1

    # A turn that ended after the decision is the same case.
    state["epoch"] = 0
    bridge_.end_turn()
    assert bridge_.guard_response(request, allow)["result"]["outcome"] == {"outcome": "cancelled"}
    assert bridge_.stats.retracted == 2


def test_a_question_method_is_counted_as_a_question_not_an_approval(tmp_path):
    bridge_ = bridge(tmp_path, approver=None)
    bridge_.begin_turn(1, session_id="acp-1")
    bridge_.note_unsupported("elicitation/create")
    assert bridge_.stats.questions == 1
    assert bridge_.take_failures() == [("question", "elicitation/create")]


def test_non_object_arguments_are_not_turned_into_a_review_view(tmp_path):
    bridge_ = bridge(tmp_path, approver=None)
    bridge_.begin_turn(1, session_id="acp-1")
    bridge_.observe({"jsonrpc": "2.0", "method": "session/update", "params": {
        "sessionId": "acp-1",
        "update": {"sessionUpdate": "tool_call", "toolCallId": "call-1",
                   "title": "pwsh", "rawInput": "{not json"}}})
    with pytest.raises(ApprovalBindError) as caught:
        bridge_.bind("srv-1", permission_params())
    assert "non-object arguments" in caught.value.reason


# -- the broker keeps questions and approvals apart --------------------------

def _broker():
    return InteractionBroker(lambda *args: None, lambda value: value)


def test_an_answer_to_a_question_cannot_resolve_an_approval():
    broker = _broker()

    async def run_flow():
        broker.start_turn("t")
        task = asyncio.create_task(broker.request_approval({"risk": "high"}))
        await asyncio.sleep(0)
        identifier = broker.pending_approvals[0]["id"]
        with pytest.raises(InteractionError) as caught:
            broker.resolve_question(identifier, "yes", channel="authenticated_ui")
        assert "Wrong interaction kind" in str(caught.value)
        assert broker.is_pending(identifier)
        broker.resolve_approval(identifier, True, channel="authenticated_ui")
        assert await task is True

    asyncio.run(run_flow())


def test_an_answer_to_an_approval_cannot_resolve_a_question():
    broker = _broker()

    async def run_flow():
        broker.start_turn("t")
        task = asyncio.create_task(broker.ask("Which file?"))
        await asyncio.sleep(0)
        identifier = broker.pending_questions[0]["id"]
        with pytest.raises(InteractionError) as caught:
            broker.resolve_approval(identifier, True, channel="authenticated_ui")
        assert "Wrong interaction kind" in str(caught.value)
        broker.resolve_question(identifier, "a.txt", channel="authenticated_ui")
        assert (await task)["answer"] == "a.txt"

    asyncio.run(run_flow())


def test_an_approval_id_from_another_session_is_unknown_there():
    first, second = _broker(), _broker()

    async def run_flow():
        first.start_turn("t1")
        task = asyncio.create_task(first.request_approval({"risk": "high"}))
        await asyncio.sleep(0)
        identifier = first.pending_approvals[0]["id"]
        second.start_turn("t2")
        with pytest.raises(InteractionError) as caught:
            second.resolve_approval(identifier, True, channel="authenticated_ui")
        assert caught.value.status_code == 404
        first.resolve_approval(identifier, True, channel="authenticated_ui")
        assert await task is True

    asyncio.run(run_flow())


# -- end to end: nothing executes before the answer --------------------------

def test_the_engine_reports_approvals_but_not_questions(tmp_path, fake_approval):
    runtime = HarnessRuntime(ApprovalSession(), config=make_config(tmp_path, fake_approval))
    assert runtime.capabilities.approvals is True
    assert runtime.capabilities.questions is False


def test_no_tool_executes_before_the_human_approves(tmp_path, fake_approval, monkeypatch):
    session = ApprovalSession()
    answer_file, exec_file = tmp_path / "answer.json", tmp_path / "executed.txt"
    monkeypatch.setenv("FAKE_MODE", "permission")
    monkeypatch.setenv("FAKE_ANSWER_FILE", str(answer_file))
    monkeypatch.setenv("FAKE_EXEC_FILE", str(exec_file))
    runtime = HarnessRuntime(session, config=make_config(
        tmp_path, fake_approval,
        passthrough=("FAKE_MODE", "FAKE_ANSWER_FILE", "FAKE_EXEC_FILE")))

    async def flow():
        try:
            await runtime.start()
            turn = asyncio.create_task(runtime.run_turn("run a command"))
            entry = await wait_for_approval(session.interactions)
            # Nothing has run: the runtime is waiting for the human, not executing.
            assert not exec_file.exists()
            assert entry["operation"] == "pwsh"
            assert entry["arguments"]["command"] == "echo hi"
            assert entry["requested_authority"] == ["allow_once"]
            assert entry["session_identity"]["acp_session"] == "acp-approval"
            assert entry["runtime_identity"]["version"] == "0.2.1-rc.2"
            session.interactions.resolve_approval(entry["id"], True,
                                                  channel="authenticated_ui")
            with pytest.raises(RuntimeCapabilityUnavailable):
                await turn
        finally:
            await runtime.close()

    run(flow())
    answer = json.loads(answer_file.read_text(encoding="utf-8"))
    assert answer["result"]["outcome"] == {"outcome": "selected", "optionId": "allow-once"}
    assert exec_file.read_text(encoding="utf-8").strip() == "executed"
    assert runtime.approvals.stats.allowed == 1


def test_a_denied_request_is_rejected_and_nothing_runs(tmp_path, fake_approval, monkeypatch):
    session = ApprovalSession()
    answer_file, exec_file = tmp_path / "answer.json", tmp_path / "executed.txt"
    monkeypatch.setenv("FAKE_MODE", "permission")
    monkeypatch.setenv("FAKE_ANSWER_FILE", str(answer_file))
    monkeypatch.setenv("FAKE_EXEC_FILE", str(exec_file))
    runtime = HarnessRuntime(session, config=make_config(
        tmp_path, fake_approval,
        passthrough=("FAKE_MODE", "FAKE_ANSWER_FILE", "FAKE_EXEC_FILE")))

    async def flow():
        try:
            await runtime.start()
            turn = asyncio.create_task(runtime.run_turn("run a command"))
            entry = await wait_for_approval(session.interactions)
            session.interactions.resolve_approval(entry["id"], False,
                                                  channel="authenticated_ui")
            with pytest.raises(RuntimeCapabilityUnavailable):
                await turn
        finally:
            await runtime.close()

    run(flow())
    answer = json.loads(answer_file.read_text(encoding="utf-8"))
    assert answer["result"]["outcome"] == {"outcome": "selected", "optionId": "reject-once"}
    assert not exec_file.exists()
    assert runtime.approvals.stats.rejected == 1


def test_a_stop_cancels_the_pending_approval_and_a_late_allow_cannot_resurrect_it(
        tmp_path, fake_approval, monkeypatch):
    session = ApprovalSession()
    answer_file = tmp_path / "answer.json"
    monkeypatch.setenv("FAKE_MODE", "permission")
    monkeypatch.setenv("FAKE_ANSWER_FILE", str(answer_file))
    runtime = HarnessRuntime(session, config=make_config(
        tmp_path, fake_approval, passthrough=("FAKE_MODE", "FAKE_ANSWER_FILE")))

    async def flow():
        try:
            await runtime.start()
            turn = asyncio.create_task(runtime.run_turn("run a command"))
            entry = await wait_for_approval(session.interactions)
            # Exactly what SessionController.stop() does first, plus the epoch it bumps.
            session.stop_epoch += 1
            session.interactions.cancel_pending("stopped")
            assert not session.interactions.pending_approvals
            with pytest.raises(InteractionError) as caught:
                session.interactions.resolve_approval(entry["id"], True,
                                                      channel="authenticated_ui")
            assert caught.value.status_code == 410
            with pytest.raises(RuntimeCapabilityUnavailable):
                await turn
        finally:
            await runtime.close()

    run(flow())
    answer = json.loads(answer_file.read_text(encoding="utf-8"))
    assert answer["result"]["outcome"] == {"outcome": "cancelled"}
    assert runtime.approvals.stats.allowed == 0


def test_close_does_not_wait_for_a_person(tmp_path, fake_approval, monkeypatch):
    session = ApprovalSession()
    monkeypatch.setenv("FAKE_MODE", "permission")
    runtime = HarnessRuntime(session, config=make_config(
        tmp_path, fake_approval, passthrough=("FAKE_MODE",)))

    async def flow():
        await runtime.start()
        turn = asyncio.create_task(runtime.run_turn("run a command"))
        await wait_for_approval(session.interactions)
        started = time.monotonic()
        await runtime.close()
        assert time.monotonic() - started < 8
        # The waiting task was cancelled and the interaction released; nothing is left
        # hanging on a person who will never answer.
        for _ in range(50):
            if not session.interactions.pending_approvals:
                break
            await asyncio.sleep(0.01)
        assert not session.interactions.pending_approvals
        assert not runtime._approval_tasks
        with pytest.raises(Exception):
            await asyncio.wait_for(asyncio.shield(turn), 10)

    run(flow())


def test_an_allow_held_for_the_wire_is_retracted_by_a_stop(
        tmp_path, fake_approval, monkeypatch):
    session = ApprovalSession()
    answer_file, exec_file = tmp_path / "answer.json", tmp_path / "executed.txt"
    monkeypatch.setenv("FAKE_MODE", "permission")
    monkeypatch.setenv("FAKE_ANSWER_FILE", str(answer_file))
    monkeypatch.setenv("FAKE_EXEC_FILE", str(exec_file))
    runtime = HarnessRuntime(session, config=make_config(
        tmp_path, fake_approval,
        passthrough=("FAKE_MODE", "FAKE_ANSWER_FILE", "FAKE_EXEC_FILE")))

    async def flow():
        try:
            await runtime.start()
            turn = asyncio.create_task(runtime.run_turn("run a command"))
            entry = await wait_for_approval(session.interactions)
            process = runtime._process
            real, entered, release = process.respond, threading.Event(), threading.Event()

            def held(request_id, payload, *, guard=None):
                # The human's allow has been produced; this is the wait before the wire
                # (a blocked pipe or a paused writer thread, indistinguishable from here).
                entered.set()
                release.wait(10)
                return real(request_id, payload, guard=guard)

            process.respond = held
            session.interactions.resolve_approval(entry["id"], True,
                                                  channel="authenticated_ui")
            assert await asyncio.to_thread(entered.wait, 5)
            assert not exec_file.exists()
            # Stop arrives while the allow is still waiting to be written.
            session.stop_epoch += 1
            session.interactions.cancel_pending("stopped")
            release.set()
            with pytest.raises(RuntimeCapabilityUnavailable):
                await turn
        finally:
            await runtime.close()

    run(flow())
    answer = json.loads(answer_file.read_text(encoding="utf-8"))
    assert answer["result"]["outcome"] == {"outcome": "cancelled"}
    assert not exec_file.exists()
    # The human did allow, and the decision was recorded as such; the wire guard is what
    # stopped it from being delivered after the Stop.
    assert runtime.approvals.stats.allowed == 1
    assert runtime.approvals.stats.retracted == 1


def test_an_allow_waiting_on_the_actual_write_lock_is_retracted_by_stop(
        tmp_path, fake_approval, monkeypatch):
    session = ApprovalSession()
    answer_file, exec_file = tmp_path / "answer.json", tmp_path / "executed.txt"
    monkeypatch.setenv("FAKE_MODE", "permission")
    monkeypatch.setenv("FAKE_ANSWER_FILE", str(answer_file))
    monkeypatch.setenv("FAKE_EXEC_FILE", str(exec_file))
    runtime = HarnessRuntime(session, config=make_config(
        tmp_path, fake_approval,
        passthrough=("FAKE_MODE", "FAKE_ANSWER_FILE", "FAKE_EXEC_FILE")))

    async def flow():
        write_lock = None
        held = False
        try:
            await runtime.start()
            turn = asyncio.create_task(runtime.run_turn("run a command"))
            entry = await wait_for_approval(session.interactions)
            process = runtime._process
            write_lock = process._write_lock
            writer_waiting = threading.Event()

            class ObservedWriteLock:
                # `close()` takes the lock with a bounded `acquire()` (that bound is what keeps
                # a blocked writer from stalling shutdown), so the double must offer both
                # interfaces and delegate to the real lock.
                def __enter__(self):
                    writer_waiting.set()
                    return write_lock.__enter__()

                def __exit__(self, *args):
                    return write_lock.__exit__(*args)

                def acquire(self, blocking=True, timeout=-1):
                    return write_lock.acquire(blocking, timeout)

                def release(self):
                    return write_lock.release()

            # Instrument entry, but hold the real production lock. Do not pause respond()
            # before its guard: that would miss the actual regression.
            write_lock.acquire()
            held = True
            process._write_lock = ObservedWriteLock()
            session.interactions.resolve_approval(
                entry["id"], True, channel="authenticated_ui")
            assert await asyncio.to_thread(writer_waiting.wait, 5.0)
            assert not exec_file.exists()
            session.stop_epoch += 1
            session.interactions.cancel_pending("stopped")
            write_lock.release()
            held = False
            with pytest.raises(RuntimeCapabilityUnavailable):
                await asyncio.wait_for(turn, 10.0)
        finally:
            if held:
                write_lock.release()
            await runtime.close()

    run(flow())
    answer = json.loads(answer_file.read_text(encoding="utf-8"))
    assert answer["result"]["outcome"] == {"outcome": "cancelled"}
    assert not exec_file.exists()
    assert runtime.approvals.stats.retracted == 1


def test_a_permission_request_that_cannot_be_bound_refuses_the_turn(
        tmp_path, fake_approval, monkeypatch):
    session = ApprovalSession()
    monkeypatch.setenv("FAKE_MODE", "unbound")
    runtime = HarnessRuntime(session, config=make_config(
        tmp_path, fake_approval, passthrough=("FAKE_MODE",)))

    async def flow():
        try:
            with pytest.raises(RuntimeCapabilityUnavailable) as caught:
                await runtime.run_turn("run a command")
            return caught.value
        finally:
            await runtime.close()

    caught = run(flow())
    assert "session/request_permission" in caught.capability
    assert "no observed tool call" in caught.capability
    assert runtime.approvals.stats.unbindable == 1
    assert not session.interactions.pending_approvals


def test_a_question_request_fails_as_a_question_never_as_an_approval(
        tmp_path, fake_approval, monkeypatch):
    session = ApprovalSession()
    monkeypatch.setenv("FAKE_MODE", "question")
    runtime = HarnessRuntime(session, config=make_config(
        tmp_path, fake_approval, passthrough=("FAKE_MODE",)))

    async def flow():
        try:
            with pytest.raises(RuntimeCapabilityUnavailable) as caught:
                await runtime.run_turn("ask me something")
            return caught.value
        finally:
            await runtime.close()

    caught = run(flow())
    assert caught.capability == "questions"
    assert runtime.approvals.stats.questions == 1
    assert not session.interactions.pending_approvals
