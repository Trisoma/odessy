"""Live stream translation and transport (prompt 05).

Two layers are covered separately, then together:

- `TurnStream` (pure): ordering, identity, bounds, attempts, dedup, late input, tool progress.
- `LiveBridge` (socket): token auth, protocol check, oversized frames, a blocked consumer.
- the runtime (end to end through the fake runtime, which speaks the plugin's own wire
  format over a real loopback socket).

The pinned runtime with the real Node plugin is covered by
``experiments/restructure/harness_compatibility/smoke_p05.py``; nothing here pretends to be
that measurement.
"""

import json
import os
import socket
import sys
import threading
import time

import pytest

from odessy.runtime_backends import RuntimeCapabilityUnavailable
from odessy.runtime_backends.harness import HarnessRuntime
from odessy.runtime_backends.harness.bridge import BridgeError, LiveBridge, free_port
from odessy.runtime_backends.harness.stream import Delta, TurnStream

from test_harness_runtime import FAKE_RUNTIME, FakeSession, make_config


@pytest.fixture()
def fake(tmp_path):
    script = tmp_path / "fake_runtime.py"
    script.write_text(FAKE_RUNTIME, encoding="utf-8")
    return (sys.executable, str(script))


def run(coroutine):
    import asyncio
    return asyncio.run(coroutine)


def frame(session="acp-1", **payload):
    return {"type": "frame", "sessionId": session, "turn": 1, "t": 0, **payload}


def text_delta(text, session="acp-1"):
    return frame(session, chunk={"type": "text-delta", "text": text})


def lifecycle(kind, session="acp-1", attempt=1):
    return frame(session, frame={"type": kind, "attempt": attempt})


# -- the translator ----------------------------------------------------------

def test_deltas_are_ordered_and_carry_identity():
    """Verify deltas are ordered and carry identity."""
    stream = TurnStream("acp-1", stream_id="sess-1:abc")
    assert stream.feed(lifecycle("start")) == []
    events = []
    for text in ("Hello", " there", "!"):
        events += stream.feed(text_delta(text))
    assert [event.seq for event in events] == [1, 2, 3]
    assert [event.text for event in events] == ["Hello", " there", "!"]
    assert {event.stream for event in events} == {"sess-1:abc"}
    assert {event.attempt for event in events} == {1}
    assert all(isinstance(event, Delta) for event in events)


def test_another_sessions_frames_are_counted_not_rendered():
    """Verify another sessions frames are counted not rendered."""
    stream = TurnStream("acp-1")
    assert stream.feed(lifecycle("start", session="other")) == []
    assert stream.feed(text_delta("leaked", session="other")) == []
    assert stream.snapshot()["foreign"] == 2
    settlement = stream.settle(text="mine", stop_reason="end_turn")
    assert settlement.foreign == 2 and settlement.deltas == 0
    assert settlement.text == "mine"  # the committed answer is unaffected


@pytest.mark.parametrize("message", [
    "not a mapping",
    {"type": "nonsense"},
    {"type": "frame", "sessionId": "acp-1", "chunk": "not-a-mapping"},
    frame(chunk={"type": "text-delta", "text": 5}),
    frame(chunk={"type": "tool-call"}),
    frame(frame={"type": "unknown"}),
])
def test_malformed_input_is_counted_and_dropped(message):
    """Verify malformed input is counted and dropped."""
    stream = TurnStream("acp-1")
    assert stream.feed(message) == []
    assert stream.snapshot()["malformed"] == 1


def test_a_zero_count_drop_note_does_not_mark_the_turn_truncated():
    """The runtime reports ``note_dropped(bridge.dropped - before)``; that is usually 0."""
    stream = TurnStream("acp-1")
    stream.feed(text_delta("hello"))
    stream.note_dropped(0)
    settlement = stream.settle(text="hello", stop_reason="end_turn")
    assert settlement.dropped == 0
    assert settlement.truncated is False
    stream.note_dropped(2)
    assert stream.snapshot()["truncated"] is True


def test_a_producer_drop_notice_is_counted_as_transport_loss():
    """The plugin drops frames when a client stalls; the loss must reach the settlement."""
    stream = TurnStream("acp-1")
    stream.feed(text_delta("kept"))
    assert stream.feed({"type": "dropped", "protocol": 1, "frames": 7}) == []
    stream.feed(text_delta("also kept"))
    settlement = stream.settle(text="keptalso kept", stop_reason="end_turn")
    assert settlement.deltas == 2
    assert settlement.dropped == 7
    assert settlement.truncated is True
    assert settlement.malformed == 0        # a reported loss is not unreadable input


def test_a_frame_from_a_settled_turn_is_not_rendered_into_the_next_one():
    """A straggler can arrive after the drain at turn start; attribution has to catch it."""
    stream = TurnStream("acp-1", previous_turn=1)
    assert stream.feed(frame(turn=1, chunk={"type": "text-delta", "text": "old"})) == []
    assert stream.feed(frame(turn=1, frame={"type": "start", "attempt": 1})) == []
    assert stream.snapshot()["late"] == 2
    assert stream.snapshot()["attempts"] == []          # no attempt was opened
    fresh = stream.feed(frame(turn=2, chunk={"type": "text-delta", "text": "new"}))
    assert [event.text for event in fresh] == ["new"]
    assert stream.last_turn == 2                       # the floor the next turn inherits
    assert stream.settle(text="new", stop_reason="end_turn").deltas == 1
    # Without the floor the same frame *is* rendered, which is exactly the reported bug:
    naive = TurnStream("acp-1")
    assert [event.text for event in
            naive.feed(frame(turn=1, chunk={"type": "text-delta", "text": "old"}))] == ["old"]


def test_a_frame_without_a_turn_number_still_belongs_to_the_running_turn():
    """Verify a frame without a turn number still belongs to the running turn."""
    stream = TurnStream("acp-1", previous_turn=4)
    message = {"type": "frame", "sessionId": "acp-1",
               "chunk": {"type": "text-delta", "text": "unattributed"}}
    assert [event.text for event in stream.feed(message)] == ["unattributed"]
    assert stream.last_turn is None


def test_a_failed_turn_leaves_its_unfinished_attempt_partial():
    """Verify a failed turn leaves its unfinished attempt partial."""
    stream = TurnStream("acp-1")
    stream.feed(lifecycle("start"))
    stream.feed(text_delta("half an answer"))
    settlement = stream.settle(text="", stop_reason="error")
    assert [attempt.partial for attempt in settlement.attempts] == [True]
    assert settlement.partial_attempts == 1


@pytest.mark.parametrize("stop_reason", ["cancelled", "error", "refusal"])
def test_an_end_frame_does_not_make_an_abnormal_turn_complete(stop_reason):
    """The runtime ends every stream, including one whose turn then fails."""
    stream = TurnStream("acp-1")
    stream.feed(lifecycle("start"))
    stream.feed(text_delta("answer"))
    stream.feed(lifecycle("end"))                     # "no more text", not "committed"
    settlement = stream.settle(text="answer", stop_reason=stop_reason)
    assert [attempt.partial for attempt in settlement.attempts] == [True]


def test_a_normal_end_keeps_its_attempt_complete():
    """Verify a normal end keeps its attempt complete."""
    stream = TurnStream("acp-1")
    stream.feed(lifecycle("start"))
    stream.feed(text_delta("answer"))
    stream.feed(lifecycle("end"))
    settlement = stream.settle(text="answer", stop_reason="end_turn")
    assert [attempt.partial for attempt in settlement.attempts] == [False]
    assert settlement.partial_attempts == 0


def test_the_turn_text_bound_is_cumulative_across_attempts():
    """A retry must not get a fresh 1 MiB allowance: the bound is per turn."""
    stream = TurnStream("acp-1", max_delta_chars=16, max_turn_chars=12)
    stream.feed(lifecycle("start"))
    assert [event.text for event in stream.feed(text_delta("abcdefgh"))] == ["abcdefgh"]
    stream.feed(lifecycle("start", attempt=2))         # the provider retried
    assert stream.feed(text_delta("ijklmnop")) == []    # 8 + 8 > 12, not 8 > 12
    snapshot = stream.snapshot()
    assert snapshot["dropped"] == 1 and snapshot["truncated"] is True
    assert [event.text for event in stream.feed(text_delta("ij"))] == ["ij"]  # 10 fits
    settlement = stream.settle(text="abcdefghij", stop_reason="end_turn")
    assert settlement.deltas == 2
    assert [(a.number, a.chars) for a in settlement.attempts] == [(1, 8), (2, 2)]


def test_a_retry_leaves_the_first_attempt_visibly_partial():
    """Verify a retry leaves the first attempt visibly partial."""
    stream = TurnStream("acp-1")
    stream.feed(lifecycle("start"))
    stream.feed(text_delta("par"))
    # The provider retried before the first attempt ended: a new start.
    stream.feed(lifecycle("start", attempt=2))
    stream.feed(text_delta("tial"))
    stream.feed(lifecycle("end", attempt=2))
    settlement = stream.settle(text="partial", stop_reason="end_turn")
    assert [(a.number, a.partial) for a in settlement.attempts] == [(1, True), (2, False)]
    assert settlement.partial_attempts == 1
    assert settlement.deltas == 2


def test_settlement_is_idempotent_and_late_deltas_are_counted():
    """Verify settlement is idempotent and late deltas are counted."""
    stream = TurnStream("acp-1")
    stream.feed(lifecycle("start"))
    stream.feed(text_delta("Hello"))
    first = stream.settle(text="Hello there", stop_reason="end_turn")
    second = stream.settle(text="different", stop_reason="error")
    assert first is second
    assert second.text == "Hello there"          # one outcome, never rewritten
    assert stream.feed(text_delta("late")) == []
    assert stream.settle(text="again", stop_reason="cancelled") is first
    # A settlement is a frozen snapshot: what arrives after it cannot rewrite it. The
    # stream's live counters still record the loss, which is how the next turn attributes it.
    assert first.late == 0
    assert stream.snapshot()["late"] == 1
    stream.note_late(2)
    assert stream.settle(text="x", stop_reason="y") is first
    assert stream.snapshot()["late"] == 3


def test_text_is_bounded_per_frame_and_per_turn():
    """Verify text is bounded per frame and per turn."""
    stream = TurnStream("acp-1", max_delta_chars=8, max_turn_chars=12)
    assert stream.feed(text_delta("0123456789")) == []       # one oversized frame
    assert stream.snapshot()["malformed"] == 1
    assert stream.feed(text_delta("01234"))[0].text == "01234"
    assert stream.feed(text_delta("56789"))[0].text == "56789"
    assert stream.feed(text_delta("abc")) == []              # would exceed the turn bound
    snapshot = stream.snapshot()
    assert snapshot["dropped"] == 1 and snapshot["truncated"] is True
    settlement = stream.settle(text="x", stop_reason="end_turn")
    assert settlement.dropped == 1 and settlement.truncated is True


def test_reasoning_is_not_answer_text_but_is_attributed():
    """Verify reasoning is not answer text but is attributed."""
    stream = TurnStream("acp-1")
    stream.feed(lifecycle("start"))
    assert stream.feed(frame(chunk={"type": "reasoning-delta", "text": "thinking"})) == []
    deltas = stream.feed(text_delta("answer"))
    assert [event.text for event in deltas] == ["answer"]
    assert stream.settle(text="answer", stop_reason="end_turn").attempts[0].number == 1


def test_tool_progress_carries_name_and_state_but_no_arguments():
    """Verify tool progress carries name and state but no arguments."""
    stream = TurnStream("acp-1")
    events = stream.feed_notification({
        "method": "session/update",
        "params": {"sessionId": "acp-1", "update": {
            "sessionUpdate": "tool_call",
            "toolCallId": "call-1",
            "title": "pwsh",
            "status": "pending",
            "rawInput": {"command": "rm -rf /"},   # must not be carried
        }},
    })
    assert len(events) == 1
    payload = events[0].public()
    assert payload["name"] == "pwsh" and payload["state"] == "pending"
    assert payload["call_id"] == "call-1"
    assert "rm -rf" not in json.dumps(payload)


def test_the_settlement_public_form_never_contains_text():
    """Verify the settlement public form never contains text."""
    stream = TurnStream("acp-1")
    stream.feed(lifecycle("start"))
    stream.feed(text_delta("secret-content"))
    public = stream.settle(text="secret-content", stop_reason="end_turn").public()
    assert "secret-content" not in json.dumps(public)
    assert public["text_chars"] == len("secret-content")
    assert public["deltas"] == 1


# -- the transport -----------------------------------------------------------

class FakePlugin:
    """A stand-in for the Node plugin: the same greeting and frame lines."""

    def __init__(self, *, token="t0ken", protocol=1):
        """Initialize the owned state and synchronization used by this component."""
        self.token, self.protocol = token, protocol
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(1)
        self.port = self.sock.getsockname()[1]
        self.received: list[str] = []
        self._conn = None
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self):
        conn, _ = self.sock.accept()
        self._conn = conn
        with conn:
            line = b""
            while b"\n" not in line:
                chunk = conn.recv(4096)
                if not chunk:
                    return
                line += chunk
            try:
                hello = json.loads(line.split(b"\n", 1)[0])
            except ValueError:
                return
            if hello.get("token") != self.token:
                return
            conn.sendall((json.dumps({"type": "hello", "pid": 1,
                                      "protocol": self.protocol}) + "\n").encode())
            while True:
                data = conn.recv(4096)
                if not data:
                    return

    def write(self, payload):
        if isinstance(payload, dict):
            payload = json.dumps(payload)
        deadline = time.monotonic() + 5
        while self._conn is None and time.monotonic() < deadline:
            time.sleep(0.01)
        assert self._conn is not None, "the client never connected"
        self._conn.sendall((payload + "\n").encode())

    def close(self):
        """Release this component and its owned resources; repeated closure is safe."""
        try:
            self.sock.close()
        except OSError:
            pass


def test_the_bridge_authenticates_and_streams_frames():
    """Verify the bridge authenticates and streams frames."""
    plugin = FakePlugin(token="shared-token")
    bridge = LiveBridge(plugin.port, "shared-token", connect_timeout=5.0)
    try:
        assert bridge.hello["protocol"] == 1
        plugin.write(text_delta("hi"))
        message = None
        deadline = time.monotonic() + 5
        while message is None and time.monotonic() < deadline:
            message = bridge.next_frame(0.05)
        assert message["chunk"]["text"] == "hi"
    finally:
        bridge.close()
        plugin.close()


def test_the_bridge_refuses_a_wrong_token():
    """Verify the bridge refuses a wrong token."""
    plugin = FakePlugin(token="shared-token")
    with pytest.raises(BridgeError):
        LiveBridge(plugin.port, "wrong-token", connect_timeout=5.0)
    plugin.close()


def test_the_bridge_refuses_an_unknown_protocol():
    """Verify the bridge refuses an unknown protocol."""
    plugin = FakePlugin(protocol=2)
    with pytest.raises(BridgeError) as caught:
        LiveBridge(plugin.port, "t0ken", connect_timeout=5.0)
    assert "protocol" in str(caught.value)
    plugin.close()


def test_the_bridge_absent_is_a_named_error_not_a_hang():
    """Verify the bridge absent is a named error not a hang."""
    port = free_port()
    started = time.monotonic()
    with pytest.raises(BridgeError):
        LiveBridge(port, "t0ken", connect_timeout=0.5)
    assert time.monotonic() - started < 3.0


def test_oversized_and_malformed_lines_are_reported_without_losing_the_stream():
    """Verify oversized and malformed lines are reported without losing the stream."""
    plugin = FakePlugin()
    bridge = LiveBridge(plugin.port, "t0ken", connect_timeout=5.0)
    try:
        plugin.write("x" * (70 * 1024))
        plugin.write("not json")
        plugin.write({"type": "frame", "sessionId": "acp-1",
                      "chunk": {"type": "text-delta", "text": "after"}})
        seen = []
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            message = bridge.next_frame(0.05)
            if message is not None:
                seen.append(message)
            if any(entry.get("chunk", {}).get("text") == "after" for entry in seen):
                break
        assert bridge.dropped >= 1                  # the oversized line is a loss
        assert seen[0] == {"type": "malformed"}     # ...the bad line is a marker
        assert "x" * 100 not in json.dumps(seen)    # no unparsed content is forwarded
        assert seen[-1]["chunk"]["text"] == "after"
    finally:
        bridge.close()
        plugin.close()


def test_the_environment_gap_that_breaks_the_bridge_socket_is_named(tmp_path, fake):
    """A partial ``source_env`` fails ``socket()`` on Windows, which reads as "no plugin".

    The engine reports the gap instead: this is the difference between "the plugin is not
    installed" and "the child was launched without the environment Winsock needs".
    """
    config = make_config(tmp_path, fake)
    assert config.missing_for_network(dict(os.environ)) == ()
    gap = config.missing_for_network({"PATH": "C:/bin"})
    if sys.platform == "win32":
        assert gap == ("SYSTEMROOT",)
    else:
        assert gap == ()


def test_a_blocked_consumer_loses_deltas_but_keeps_the_connection_open():
    """Verify a blocked consumer loses deltas but keeps the connection open."""
    plugin = FakePlugin()
    bridge = LiveBridge(plugin.port, "t0ken", connect_timeout=5.0, queue_size=4)
    try:
        assert bridge._queue.maxsize == 4  # bounded by construction, not by hope
        for index in range(40):
            plugin.write(text_delta(f"chunk{index}"))
        deadline = time.monotonic() + 5
        while bridge.dropped == 0 and time.monotonic() < deadline:
            time.sleep(0.02)
        assert bridge.dropped > 0                     # loss is counted, not silent
        # Drain until the transport is quiet, so the frame below cannot be the one that
        # gets dropped for space; the point here is a slow consumer, not a closed one.
        quiet = 0
        while quiet < 2:
            if bridge.next_frame(0.1) is None:
                quiet += 1
            else:
                quiet = 0
        plugin.write(text_delta("still alive"))
        texts = []
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and "still alive" not in texts:
            message = bridge.next_frame(0.05)
            if message is not None:
                texts.append(message["chunk"]["text"])
        assert "still alive" in texts
    finally:
        bridge.close()
        plugin.close()


# -- the runtime, end to end -------------------------------------------------

def with_fake_env(config, **values):
    """Forward practice-only variables to the child (its environment is an allowlist).

    ``source_env`` replaces the process environment wholesale when it is set, so it is
    seeded from it here: the child still needs ``SYSTEMROOT`` to initialise Winsock (the
    plugin binds a socket) and ``TEMP`` for Node's own scratch files. A partial mapping
    would look like a plugin bug rather than the missing environment it actually is.
    """
    from dataclasses import replace

    passthrough = tuple(dict.fromkeys((*config.passthrough, *values)))
    return replace(config, passthrough=passthrough,
                   source_env={**os.environ, **dict(config.source_env), **values})


def bridged_runtime(tmp_path, fake, monkeypatch, *, bridge="normal", delay_ms=400):
    monkeypatch.setenv("FAKE_BRIDGE", bridge)
    monkeypatch.setenv("FAKE_DELAY_MS", str(delay_ms))
    config = make_config(tmp_path, fake)
    config = with_fake_env(config, FAKE_BRIDGE=bridge, FAKE_DELAY_MS=str(delay_ms))
    return HarnessRuntime(FakeSession(), config=config)


def deltas(session):
    return [event for event in session.events() if event["type"] == "delta"]


def test_deltas_reach_the_session_before_the_answer_is_committed(tmp_path, fake, monkeypatch):
    """Verify deltas reach the session before the answer is committed."""
    runtime = bridged_runtime(tmp_path, fake, monkeypatch)
    session = runtime._session
    started_wall = time.time()
    started = time.monotonic()
    result = run(runtime.run_turn("hello"))
    elapsed = time.monotonic() - started
    try:
        assert runtime.capabilities.live_text_deltas is True
        events = deltas(session)
        assert [event["data"]["text"] for event in events] == ["Hello", " there"]
        assert "".join(event["data"]["text"] for event in events) == result.final_text
        # The fake holds its reply for 400 ms, so a delta that lands more than 200 ms
        # before the turn ends provably preceded the committed answer.
        assert events[0]["time"] - started_wall < elapsed - 0.2
        settlement = runtime.stream_settlement
        assert settlement.deltas == 2
        assert settlement.first_delta_ms is not None
        assert settlement.first_delta_ms < settlement.settled_ms
        assert settlement.partial_attempts == 0
    finally:
        run(runtime.close())


def test_another_sessions_frames_never_reach_this_session(tmp_path, fake, monkeypatch):
    """Verify another sessions frames never reach this session."""
    runtime = bridged_runtime(tmp_path, fake, monkeypatch, bridge="foreign")
    session = runtime._session
    try:
        result = run(runtime.run_turn("hello"))
        assert deltas(session) == []
        assert result.final_text == "Hello there"
        assert runtime.stream_settlement.foreign == 3
    finally:
        run(runtime.close())


def test_malformed_frames_do_not_break_the_turn(tmp_path, fake, monkeypatch):
    """Verify malformed frames do not break the turn."""
    runtime = bridged_runtime(tmp_path, fake, monkeypatch, bridge="malformed")
    session = runtime._session
    try:
        result = run(runtime.run_turn("hello"))
        assert result.final_text == "Hello there"
        assert [event["data"]["text"] for event in deltas(session)] == ["ok"]
        assert runtime.stream_settlement.malformed == 3
    finally:
        run(runtime.close())


def test_a_straggler_from_the_settled_turn_is_not_rendered_into_the_next_one(
        tmp_path, fake, monkeypatch):
    """The reported gap: turn A's delayed chunk arriving while turn B is active.

    Draining the queue at turn start cannot see a frame that has not arrived yet, so the
    runtime tells each turn which bridge turn numbers are already finished and the
    translator rejects those frames as ``late`` instead of rendering them as new text.
    """
    runtime = bridged_runtime(tmp_path, fake, monkeypatch, bridge="stale", delay_ms=200)
    session = runtime._session
    try:
        assert run(runtime.run_turn("hello")).final_text == "Hello there"
        assert run(runtime.run_turn("hello again")).final_text == "Hello there"
        texts = [event["data"]["text"] for event in deltas(session)]
        assert texts == ["Hello", " there", "Hello", " there"]
        assert "straggler" not in texts
        assert runtime.stream_settlement.deltas == 2      # only this turn's frames
        assert runtime.stream_settlement.late >= 1         # the straggler is counted
    finally:
        run(runtime.close())


def test_a_late_chunk_after_settlement_changes_nothing(tmp_path, fake, monkeypatch):
    """A frame that arrives after the turn settled is discarded, and counted.

    It cannot be counted against the turn it belongs to: that settlement is already
    frozen. The next turn's start drains what is stale and attributes it as ``late``, so
    the loss stays visible instead of sitting unread in the queue.
    """
    runtime = bridged_runtime(tmp_path, fake, monkeypatch, bridge="late", delay_ms=200)
    session = runtime._session
    try:
        result = run(runtime.run_turn("hello"))
        assert result.final_text == "Hello there"
        time.sleep(1.2)  # the fake sends its chunk 0.9 s after the prompt
        assert [event["data"]["text"] for event in deltas(session)] == []
        assert runtime.stream_settlement.deltas == 0
        run(runtime.run_turn("hello again"))
        assert runtime.stream_settlement.late >= 1
    finally:
        run(runtime.close())


def test_a_retry_keeps_the_partial_attempt_visible(tmp_path, fake, monkeypatch):
    """Verify a retry keeps the partial attempt visible."""
    runtime = bridged_runtime(tmp_path, fake, monkeypatch, bridge="retry")
    session = runtime._session
    try:
        run(runtime.run_turn("hello"))
        assert [event["data"]["text"] for event in deltas(session)] == ["par", "tial"]
        settlement = runtime.stream_settlement
        assert [(a.number, a.partial) for a in settlement.attempts] == [(1, True), (2, False)]
    finally:
        run(runtime.close())


def test_tool_activity_is_rendered_then_the_turn_still_fails(tmp_path, fake, monkeypatch):
    """Verify tool activity is rendered then the turn still fails."""
    config = with_fake_env(make_config(tmp_path, fake), FAKE_MODE="tool")
    runtime = HarnessRuntime(FakeSession(), config=config)
    session = runtime._session
    try:
        with pytest.raises(RuntimeCapabilityUnavailable):
            run(runtime.run_turn("use a shell"))
        progress = [event for event in session.events() if event["type"] == "tool_progress"]
        assert progress and progress[0]["data"]["name"] == "pwsh"
        assert runtime.stream_settlement.stop_reason == "end_turn"
    finally:
        run(runtime.close())


def test_without_a_bridge_the_engine_stays_settlement_only(tmp_path, fake):
    """Verify without a bridge the engine stays settlement only."""
    config = with_fake_env(make_config(tmp_path, fake), ODESSY_HARNESS_BRIDGE="off")
    runtime = HarnessRuntime(FakeSession(), config=config)
    try:
        result = run(runtime.run_turn("hello"))
        assert result.final_text == "Hello there"
        assert runtime.capabilities.live_text_deltas is False
        assert deltas(runtime._session) == []
        assert runtime.stream_settlement.first_delta_ms is None
    finally:
        run(runtime.close())


def test_bridge_overflow_accounts_for_every_missing_frame():
    """A full queue loses one frame, not an uncounted extra queued frame."""
    import queue
    plugin = FakePlugin()
    bridge = LiveBridge(plugin.port, plugin.token)
    bridge._queue = queue.Queue(maxsize=1)
    try:
        for index in range(5):
            plugin.write({'type': 'frame', 'n': index})
        deadline = time.monotonic() + 3
        while bridge.dropped < 4 and time.monotonic() < deadline:
            time.sleep(.01)
        assert bridge._queue.qsize() + bridge.dropped == 5
    finally:
        bridge.close()
        plugin.close()
