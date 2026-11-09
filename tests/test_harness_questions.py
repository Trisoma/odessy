"""Prompt 07 clarification: the question half of the runtime interaction bridge.

The runtime owns the model-facing side (its own ``ask_user_question`` tool and the
``user-questions`` seam); the packaged bundle mounts the tool and registers one answerer.
These tests cover Odessy's half: frame validation and binding, the broker round trip, the
answer returned to the exact waiting invocation, and every path that must withdraw a
question rather than invent an answer.

``HarnessQuestionBridge`` is exercised directly (deterministic, no child process) and once
through ``HarnessRuntime`` with a fake plugin socket, to prove the production wiring.
"""

import asyncio
import json
import sys
import time

import pytest

from odessy.runtime_backends.harness import (
    NARROW_PATCH_NAME,
    NARROW_PATCH_TEXT,
    HarnessConfig,
    HarnessQuestionBridge,
    HarnessRuntime,
)

SESSION = "acp-questions"


class Recorder:
    """Collects client-to-plugin messages; the plugin would write them to the socket."""

    def __init__(self):
        self.sent = []

    def __call__(self, message):
        self.sent.append(dict(message))
        return True

    def of(self, kind):
        return [item for item in self.sent if item.get("type") == kind]


def question_frame(*, request_id="req-1", turn=1, session=SESSION, questions=None):
    return {
        "type": "question",
        "sessionId": session,
        "turn": turn,
        "requestId": request_id,
        "questions": questions if questions is not None else [{
            "id": "q1", "question": "Proceed with the cleanup?",
            "labels": ["Yes", "No"], "header": "Confirm",
        }],
    }


def make_bridge(*, answers=None, send=None, epoch=None, asker=None):
    sent = send or Recorder()
    asked = []

    async def default_asker(question):
        asked.append({"id": question.id, "question": question.text,
                      "choices": list(question.choices), "detail": question.detail,
                      "header": question.header})
        result = (answers or {}).get(question.text, {"status": "ok", "answer": "Yes",
                                                     "source": "human_information"})
        return result

    loop = asyncio.get_running_loop()
    bridge = HarnessQuestionBridge(
        send=sent,
        asker=asker or default_asker,
        loop=lambda: loop,
        epoch=epoch or (lambda: 0),
    )
    bridge.begin_turn(1, session_id=SESSION, turn_floor=0)
    return bridge, sent, asked


async def settle(cycles=8):
    for _ in range(cycles):
        await asyncio.sleep(0.01)


# --------------------------------------------------------------------- answers

def test_a_choice_answer_returns_to_the_exact_request():
    async def flow():
        bridge, sent, asked = make_bridge()
        bridge.observe(question_frame())
        await settle()
        assert asked == [{"id": "q1", "question": "Proceed with the cleanup?",
                          "choices": ["Yes", "No"], "detail": None, "header": "Confirm"}]
        answers = sent.of("answer")
        assert len(answers) == 1
        assert answers[0]["requestId"] == "req-1"
        assert answers[0]["sessionId"] == SESSION
        assert answers[0]["turn"] == 1
        assert answers[0]["answers"] == [{"id": "q1", "selected": ["Yes"]}]
        assert not sent.of("cancel")
        assert bridge.stats.answered == 1

    asyncio.run(flow())


def test_free_text_is_carried_as_custom_not_as_a_choice():
    async def flow():
        bridge, sent, _ = make_bridge(answers={
            "Which file?": {"status": "ok", "answer": "notes/todo.md"}})
        bridge.observe(question_frame(questions=[{
            "id": "q1", "question": "Which file?"}]))
        await settle()
        assert sent.of("answer")[0]["answers"] == [
            {"id": "q1", "selected": [], "custom": "notes/todo.md"}]

    asyncio.run(flow())


def test_several_questions_are_asked_in_order_and_answered_together():
    async def flow():
        bridge, sent, asked = make_bridge(answers={
            "First?": {"status": "ok", "answer": "A"},
            "Second?": {"status": "ok", "answer": "typed"},
        })
        bridge.observe(question_frame(questions=[
            {"id": "a", "question": "First?", "labels": ["A", "B"]},
            {"id": "b", "question": "Second?"},
        ]))
        await settle()
        assert [item["question"] for item in asked] == ["First?", "Second?"]
        assert sent.of("answer")[0]["answers"] == [
            {"id": "a", "selected": ["A"]},
            {"id": "b", "selected": [], "custom": "typed"},
        ]

    asyncio.run(flow())


def test_the_askers_header_and_detail_travel_with_the_question():
    async def flow():
        bridge, sent, asked = make_bridge(answers={
            "Review this plan?": {"status": "ok", "answer": "Approve"}})
        bridge.observe(question_frame(questions=[{
            "id": "q1", "question": "Review this plan?", "header": "Plan",
            "detail": "# Plan\nDo the thing.", "labels": ["Approve"]}]))
        await settle()
        assert asked[0]["header"] == "Plan"
        assert asked[0]["detail"] == "# Plan\nDo the thing."
        assert sent.of("answer")[0]["answers"] == [{"id": "q1", "selected": ["Approve"]}]

    asyncio.run(flow())

    asyncio.run(flow())


# ----------------------------------------------------------- withdrawal paths

@pytest.mark.parametrize("questions", [
    None,                                                              # missing
    [],                                                                # empty
    [{"id": "a", "question": "x"}] * 9,                                # too many
    [{"id": "a", "question": "x"}, {"id": "a", "question": "y"}],      # duplicate id
    [{"id": "a", "question": ""}],                                     # empty text
    [{"id": "a", "question": "x" * 8193}],                             # over text bound
    [{"id": "a", "question": "x", "multiSelect": True}],               # unsupported shape
    [{"id": "a", "question": "x", "labels": []}],                      # empty options
    [{"id": "a", "question": "x", "labels": ["y", "y"]}],              # duplicate labels
    ["not an object"],
])
def test_malformed_requests_are_withdrawn_never_answered(questions):
    async def flow():
        bridge, sent, asked = make_bridge()
        frame = question_frame()
        if questions is not None:
            frame["questions"] = questions
        else:
            frame.pop("questions")
        bridge.observe(frame)
        await settle()
        assert not asked
        assert not sent.of("answer")
        assert sent.of("cancel")[0]["requestId"] == "req-1"
        assert bridge.stats.rejected == 1

    asyncio.run(flow())


def test_a_frame_from_another_session_is_withdrawn():
    async def flow():
        bridge, sent, asked = make_bridge()
        bridge.observe(question_frame(session="another-session"))
        await settle()
        assert not asked
        assert sent.of("cancel")[0]["reason"] == "rejected"

    asyncio.run(flow())


def test_a_frame_from_a_finished_turn_is_withdrawn():
    async def flow():
        bridge, sent, asked = make_bridge()
        bridge.begin_turn(3, session_id=SESSION, turn_floor=2)
        bridge.observe(question_frame(turn=2))
        await settle()
        assert not asked
        assert sent.of("cancel")

    asyncio.run(flow())


def test_a_turn_without_a_runtime_turn_number_is_withdrawn():
    async def flow():
        bridge, sent, asked = make_bridge()
        frame = question_frame()
        frame["turn"] = None
        bridge.observe(frame)
        await settle()
        assert not asked
        assert sent.of("cancel")

    asyncio.run(flow())


def test_a_duplicate_question_id_is_not_asked_twice():
    async def flow():
        bridge, sent, asked = make_bridge()
        bridge.observe(question_frame())
        await settle()
        bridge.observe(question_frame())
        await settle()
        assert len(asked) == 1
        assert len(sent.of("answer")) == 1
        assert bridge.stats.duplicate == 1

    asyncio.run(flow())


@pytest.mark.parametrize("status", ["cancelled", "expired", "busy", "limit_reached"])
def test_a_broker_that_did_not_answer_withdraws_the_question(status):
    async def flow():
        bridge, sent, _ = make_bridge(answers={
            "Proceed with the cleanup?": {"status": status, "reason": status}})
        bridge.observe(question_frame())
        await settle()
        assert not sent.of("answer")
        cancel = sent.of("cancel")[0]
        assert cancel["requestId"] == "req-1"
        assert cancel["reason"] in ("cancelled", "timeout")
        assert bridge.stats.answered == 0

    asyncio.run(flow())


def test_a_stop_withdraws_a_waiting_question_and_no_answer_is_sent():
    async def flow():
        release = asyncio.Event()

        async def slow_asker(question):
            await release.wait()
            return {"status": "ok", "answer": "Yes"}

        bridge, sent, _ = make_bridge(asker=slow_asker)
        bridge.observe(question_frame())
        await settle()
        bridge.cancel_all("stopped")  # what HarnessRuntime.stop() does
        await settle()
        assert sent.of("cancel")[0]["reason"] == "stopped"
        release.set()
        await settle()
        assert not sent.of("answer")
        assert bridge.stats.answered == 0

    asyncio.run(flow())


def test_a_stop_epoch_change_withdraws_an_answer_that_arrived_meanwhile():
    async def flow():
        state = {"epoch": 0}

        async def asker(question):
            state["epoch"] += 1  # a Stop landed while the person was answering
            return {"status": "ok", "answer": "Yes"}

        bridge, sent, _ = make_bridge(asker=asker, epoch=lambda: state["epoch"])
        bridge.observe(question_frame())
        await settle()
        assert not sent.of("answer")
        assert sent.of("cancel")[0]["reason"] == "stale"

    asyncio.run(flow())


def test_a_turn_that_ends_while_the_person_answers_is_not_resumed():
    async def flow():
        bridge = None

        async def asker(question):
            bridge.end_turn()  # the turn ended before the answer was ready
            return {"status": "ok", "answer": "Yes"}

        bridge, sent, _ = make_bridge(asker=asker)
        bridge.observe(question_frame())
        await settle()
        assert not sent.of("answer")
        assert sent.of("cancel")

    asyncio.run(flow())


def test_an_empty_answer_is_not_reported_as_a_human_answer():
    async def flow():
        bridge, sent, _ = make_bridge(answers={
            "Proceed with the cleanup?": {"status": "ok", "answer": "   "}})
        bridge.observe(question_frame())
        await settle()
        assert not sent.of("answer")
        assert sent.of("cancel")

    asyncio.run(flow())


def test_too_many_pending_questions_are_refused():
    async def flow():
        release = asyncio.Event()

        async def slow_asker(question):
            await release.wait()
            return {"status": "ok", "answer": "Yes"}

        bridge, sent, _ = make_bridge(asker=slow_asker)
        for index in range(5):
            bridge.observe(question_frame(request_id=f"req-{index}"))
        await settle()
        assert len(bridge.pending) == 4
        assert any(item["requestId"] == "req-4" for item in sent.of("cancel"))
        release.set()
        await settle()

    asyncio.run(flow())


def test_frames_that_are_not_questions_are_ignored():
    async def flow():
        bridge, sent, asked = make_bridge()
        bridge.observe({"type": "frame", "frame": {"type": "start"}})
        bridge.observe({"type": "dropped", "frames": 3})
        bridge.observe("not an object")
        await settle()
        assert not asked and not sent.sent

    asyncio.run(flow())


# ------------------------------------------------- production runtime wiring

def test_a_frame_sent_with_the_greeting_is_not_lost():
    """Regression: the greeting read must not swallow the plugin's first real frame."""
    import socket
    import threading

    from odessy.runtime_backends.harness.bridge import LiveBridge

    server = socket.socket()
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = server.getsockname()[1]
    payload = (
        json.dumps({"type": "hello", "pid": 1, "protocol": 1, "features": ["questions"]})
        + "\n"
        + json.dumps({"type": "question", "sessionId": "s", "turn": 1,
                      "requestId": "r", "questions": []})
        + "\n"
    ).encode()

    def serve():
        conn, _ = server.accept()
        conn.recv(4096)
        conn.sendall(payload)
        time.sleep(0.5)
        conn.close()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    bridge = LiveBridge(port, "token")
    try:
        frame = bridge.next_frame(2.0)
        assert frame is not None and frame.get("type") == "question"
        assert bridge.features == ("questions",)
    finally:
        bridge.close()
        server.close()

FAKE_RUNTIME = r'''
import json, os, sys, threading, time

ANSWER_FILE = os.environ.get("FAKE_ANSWER_FILE")
PROMPT_FILE = os.environ.get("FAKE_PROMPT_FILE")

def emit(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()

if "--version" in sys.argv:
    print("0.2.1-rc.2")
    raise SystemExit(0)

def model_options():
    value = json.dumps(["odessy-deepseek", "mock-model"], separators=(",", ":"))
    return [{"id": "model", "type": "select", "currentValue": value, "options": [
        {"group": "odessy-deepseek", "name": "Odessy", "options": [{"value": value}]}]}]

def prompt(request_id):
    session = STATE["session"]
    emit({"jsonrpc": "2.0", "method": "session/update", "params": {
        "sessionId": session, "update": {
            "sessionUpdate": "tool_call", "toolCallId": "call-q",
            "title": "ask_user_question", "kind": "other", "status": "in_progress",
            "rawInput": {"questions": [{"id": "q1", "question": "Proceed?"}]}}}})
    deadline = time.time() + 20
    while time.time() < deadline:
        if ANSWER_FILE and os.path.exists(ANSWER_FILE):
            break
        time.sleep(0.02)
    emit({"jsonrpc": "2.0", "method": "session/update", "params": {
        "sessionId": session, "update": {"sessionUpdate": "tool_call_update",
                                         "toolCallId": "call-q", "status": "completed"}}})
    emit({"jsonrpc": "2.0", "method": "session/update", "params": {
        "sessionId": session, "update": {"sessionUpdate": "agent_message_chunk",
                                         "content": {"type": "text", "text": "Continuing."}}}})
    if PROMPT_FILE:
        with open(PROMPT_FILE, "w", encoding="utf-8") as handle:
            handle.write("settled")
    emit({"jsonrpc": "2.0", "id": request_id, "result": {"stopReason": "end_turn"}})

STATE = {"session": None}
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        message = json.loads(line)
    except ValueError:
        continue
    if "method" not in message:
        continue
    method, rid = message.get("method"), message.get("id")
    if method == "initialize":
        emit({"jsonrpc": "2.0", "id": rid, "result": {"protocolVersion": 1}})
    elif method == "session/new":
        STATE["session"] = "acp-q"
        emit({"jsonrpc": "2.0", "id": rid,
              "result": {"sessionId": "acp-q", "configOptions": model_options()}})
    elif method == "session/set_config_option":
        emit({"jsonrpc": "2.0", "id": rid, "result": {"configOptions": model_options()}})
    elif method == "session/prompt":
        threading.Thread(target=prompt, args=(rid,), daemon=True).start()
'''


class PluginSocket:
    """A tiny stand-in for the packaged plugin's socket: greet, ask once, record the answer."""

    def __init__(self, port, token, questions, session="acp-q", turn=1):
        import socket
        import threading
        self.answer = None
        self.error = None
        self.sent_question = False
        self.done = threading.Event()
        server = socket.socket()
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind(("127.0.0.1", int(port)))
        server.listen(1)
        self.server = server
        self.token, self.questions = token, questions
        self.session, self.turn = session, turn
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self):
        try:
            conn, _ = self.server.accept()
            data = b""
            while b"\n" not in data:
                data += conn.recv(4096)
            json.loads(data.split(b"\n", 1)[0])
            conn.sendall((json.dumps({"type": "hello", "pid": 1, "protocol": 1,
                                      "features": ["questions"]}) + "\n").encode())
            request = {"type": "question", "sessionId": self.session, "turn": self.turn,
                       "requestId": "req-1", "questions": self.questions}
            conn.sendall((json.dumps(request) + "\n").encode())
            self.sent_question = True
            buffer = b""
            while not self.done.is_set():
                chunk = conn.recv(65536)
                if not chunk:
                    break
                buffer += chunk
                while b"\n" in buffer:
                    line, buffer = buffer.split(b"\n", 1)
                    if not line.strip():
                        continue
                    message = json.loads(line)
                    if message.get("type") in ("answer", "cancel") \
                            and message.get("requestId") == "req-1":
                        self.answer = message
                        self.done.set()
            conn.close()
        except Exception as exc:  # noqa: BLE001 - surfaced in the test's diagnostic
            self.error = repr(exc)
        finally:
            self.server.close()

    def close(self):
        try:
            self.server.close()
        except OSError:
            pass


class QuestionSession:
    """The parts of a session the engine may touch, with a real interaction broker."""

    def __init__(self):
        from odessy.interaction.broker import InteractionBroker
        self.id = "sess-questions"
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
def fake_question_runtime(tmp_path):
    script = tmp_path / "fake_question_runtime.py"
    script.write_text(FAKE_RUNTIME, encoding="utf-8")
    return (sys.executable, str(script))


@pytest.fixture(autouse=True)
def _credential(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "k-test-not-a-real-key")


def make_config(tmp_path, fake, *, passthrough=()):
    home, workspace = tmp_path / "home", tmp_path / "ws"
    home.mkdir(exist_ok=True)
    workspace.mkdir(exist_ok=True)
    overlay = home / NARROW_PATCH_NAME
    overlay.write_text(NARROW_PATCH_TEXT, encoding="utf-8")
    return HarnessConfig(command=tuple(fake), home=home, workspace=workspace, patches=(),
                         narrow_patch=overlay, startup_timeout=8.0, turn_timeout=20.0,
                         shutdown_timeout=3.0, verify_artifact=False,
                         passthrough=tuple(passthrough))


def test_the_runtime_answers_a_question_and_resumes_the_same_turn(
        tmp_path, fake_question_runtime, monkeypatch):
    session = QuestionSession()
    answer_file, prompt_file = tmp_path / "answered.txt", tmp_path / "settled.txt"
    monkeypatch.setenv("FAKE_ANSWER_FILE", str(answer_file))
    monkeypatch.setenv("FAKE_PROMPT_FILE", str(prompt_file))
    runtime = HarnessRuntime(session, config=make_config(
        tmp_path, fake_question_runtime,
        passthrough=("FAKE_ANSWER_FILE", "FAKE_PROMPT_FILE")))
    config = runtime.config
    # The engine connects to the plugin as a client during start(), so the stand-in socket
    # must already be listening.
    plugin = PluginSocket(config.extra_env["ODESSY_BRIDGE_PORT"],
                          config.extra_env["ODESSY_BRIDGE_TOKEN"],
                          [{"id": "q1", "question": "Proceed?", "labels": ["Yes", "No"]}])

    async def flow():
        try:
            await runtime.start()
            assert runtime.capabilities.questions is True
            try:
                turn = asyncio.create_task(runtime.run_turn("ask the user"))
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline and not session.interactions.pending_questions:
                    await asyncio.sleep(0.01)
                diag = {
                    "questions": runtime._questions.stats.public(),
                    "turn": runtime._questions.snapshot()["turn"],
                    "bridge": None if runtime._bridge is None else {
                        "dropped": runtime._bridge.dropped,
                        "lines_read": runtime._bridge.lines_read,
                        "lines_queued": runtime._bridge.lines_queued,
                        "queue": runtime._bridge._queue.qsize(),
                    },
                    "events": [item["type"] for item in session.events()[-4:]],
                    "diag": runtime.diagnostics()[-3:],
                    "plugin": {"sent": plugin.sent_question, "error": plugin.error,
                               "answered": plugin.done.is_set()},
                }
                assert session.interactions.pending_questions, json.dumps(diag)
                entry = session.interactions.pending_questions[0]
                assert entry["prompt"] == "Proceed?"
                assert entry["choices"] == ["Yes", "No"]
                assert not prompt_file.exists()  # the turn cannot settle before the answer
                session.interactions.resolve_question(entry["id"], "Yes",
                                                      channel="authenticated_ui")
                await asyncio.to_thread(plugin.done.wait, 10)
                # What the plugin received is what the human answered.
                assert plugin.answer["type"] == "answer"
                assert plugin.answer["answers"] == [{"id": "q1", "selected": ["Yes"]}]
                with open(answer_file, "w", encoding="utf-8") as handle:
                    handle.write("answered")
                result = await turn
                assert result.final_text == "Continuing."
                assert prompt_file.exists()
            finally:
                plugin.close()
        finally:
            await runtime.close()

    asyncio.run(flow())


def test_a_runtime_without_the_question_feature_does_not_advertise_it(
        tmp_path, fake_question_runtime):
    session = QuestionSession()
    runtime = HarnessRuntime(session, config=make_config(tmp_path, fake_question_runtime))

    async def flow():
        try:
            await runtime.start()
            # No plugin socket was started, so the bridge never attached.
            assert runtime.capabilities.questions is False
        finally:
            await runtime.close()

    asyncio.run(flow())
