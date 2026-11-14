"""Per-session Stop against the fake runtime (prompt 06).

The engine under test is the production ``HarnessRuntime``; only the child process is a
fixture, which speaks ACP and accepts ``session/cancel`` while a prompt is in flight.
Covered here: idle/stopped/unconfirmed outcomes, repeated Stop, Stop between streamed chunks,
a crash during Stop, two simultaneous sessions, teardown after a Stop, and the rule that a
turn is never released while its prompt is still running.

The pinned binary is exercised by
``experiments/restructure/harness_compatibility/smoke_p06.py``.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import threading
import time

import pytest

from odessy.runtime_backends import RuntimeUnavailable, StopOutcome, TurnCancelled
from odessy.runtime_backends.harness import HarnessRuntime
from odessy.runtime_backends.harness.runtime import CANCEL_STOP_REASON

from test_harness_runtime import FAKE_RUNTIME, FakeSession, make_config

PASSTHROUGH = ("FAKE_MODE", "FAKE_HOLD", "FAKE_HOLD_TURNS", "FAKE_PROMPT_FILE",
               "FAKE_PIDFILE", "FAKE_CANCEL_FILE")


def run(coroutine):
    return asyncio.run(coroutine)


@pytest.fixture()
def fake(tmp_path):
    script = tmp_path / "fake_runtime.py"
    script.write_text(FAKE_RUNTIME, encoding="utf-8")
    return (sys.executable, str(script))


def configured(tmp_path, fake, monkeypatch, *, mode, hold=5.0, hold_turns=1,
               stop_bound=5.0, prompt_file=None, pidfile=None, cancel_file=None):
    """A runtime whose fake child behaves like ``mode``, with an explicit stop bound."""
    from dataclasses import replace

    tmp_path.mkdir(parents=True, exist_ok=True)
    values = {"FAKE_MODE": mode, "FAKE_HOLD": str(hold), "FAKE_HOLD_TURNS": str(hold_turns)}
    if prompt_file is not None:
        values["FAKE_PROMPT_FILE"] = str(prompt_file)
    if pidfile is not None:
        values["FAKE_PIDFILE"] = str(pidfile)
    if cancel_file is not None:
        values["FAKE_CANCEL_FILE"] = str(cancel_file)
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    config = make_config(tmp_path, fake, passthrough=PASSTHROUGH, shutdown_timeout=stop_bound)
    config = replace(config, source_env={**os.environ, **values})
    return HarnessRuntime(FakeSession(), config=config)


def wait_for_prompt(path, timeout=15.0):
    """The fake writes this once the prompt reached it: generation has started."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists() and path.read_text(encoding="utf-8").strip():
            return
        time.sleep(0.02)
    raise AssertionError(f"the prompt never reached the child ({path})")


def _alive(pid):
    """Use the shared platform-aware process liveness check."""
    from test_harness_runtime import _pid_alive
    return _pid_alive(pid)


def _wait_until_dead(pid, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _alive(pid):
            return True
        time.sleep(0.1)
    return not _alive(pid)


# -- the outcomes ------------------------------------------------------------

def test_stop_with_no_turn_is_idle_and_repeatable(tmp_path, fake, monkeypatch):
    """Verify stop with no turn is idle and repeatable."""
    runtime = configured(tmp_path, fake, monkeypatch, mode="hold")

    async def flow():
        """Exercise the stop with no turn is idle and repeatable scenario through the async application boundary."""
        try:
            await runtime.start()
            return await runtime.stop(), await runtime.stop()
        finally:
            await runtime.close()

    first, second = run(flow())
    for outcome in (first, second):
        assert isinstance(outcome, StopOutcome)
        assert outcome.requested is False
        assert outcome.execution == "idle"
        assert outcome.cleanup == "complete"
        assert outcome.confirmed is False


def test_stop_does_not_end_the_session_it_did_not_interrupt(tmp_path, fake, monkeypatch):
    """Verify stop does not end the session it did not interrupt."""
    runtime = configured(tmp_path, fake, monkeypatch, mode="hold", hold_turns=0)

    async def flow():
        """Exercise the stop does not end the session it did not interrupt scenario through the async application boundary."""
        try:
            await runtime.start()
            before = await runtime.stop()          # nothing running: must not break anything
            return before, await runtime.run_turn("hello")
        finally:
            await runtime.close()

    before, result = run(flow())
    assert before.execution == "idle"
    assert result.final_text == "Hello there"


def test_stop_between_streamed_chunks_is_confirmed_and_keeps_the_answer_out(
        tmp_path, fake, monkeypatch):
    """Stop while the fake is generating: the runtime reports ``cancelled``, its text is not
    an answer, and the session survives for the next turn."""
    marker = tmp_path / "prompt.txt"
    runtime = configured(tmp_path, fake, monkeypatch, mode="hold", hold=10.0, prompt_file=marker)

    async def flow():
        """Exercise the stop between streamed chunks is confirmed and keeps the answer out scenario through the async application boundary."""
        try:
            await runtime.start()
            turn = asyncio.ensure_future(runtime.run_turn("hello"))
            await asyncio.to_thread(wait_for_prompt, marker)
            outcome = await runtime.stop(timeout=5.0)
            with pytest.raises(TurnCancelled) as caught:
                await turn
            second = await runtime.run_turn("hello again")   # HOLD_TURNS=1: this one finishes
            return outcome, caught.value, second
        finally:
            await runtime.close()

    outcome, cancelled, second = run(flow())
    assert outcome.execution == "stopped"
    assert outcome.cleanup == "complete"
    assert outcome.stop_reason == CANCEL_STOP_REASON
    assert outcome.confirmed is True
    assert cancelled.outcome["execution"] == "stopped"
    assert cancelled.outcome["stop_reason"] == CANCEL_STOP_REASON
    # The cancelled text never became the answer, and the next turn is unaffected.
    assert second.final_text == "Hello there"
    assert runtime.orphaned_turn is False


def test_stop_that_the_runtime_ignores_is_unconfirmed_not_stopped(tmp_path, fake, monkeypatch):
    """The engine never acts on the cancel: the bound elapses and the report says so."""
    marker = tmp_path / "prompt.txt"
    runtime = configured(tmp_path, fake, monkeypatch, mode="ignore-cancel", hold=3.0,
                         stop_bound=0.5, prompt_file=marker)

    async def flow():
        """Exercise the stop that the runtime ignores is unconfirmed not stopped scenario through the async application boundary."""
        try:
            await runtime.start()
            turn = asyncio.ensure_future(runtime.run_turn("hello"))
            await asyncio.to_thread(wait_for_prompt, marker)
            started = time.monotonic()
            outcome = await runtime.stop(timeout=0.5)
            elapsed = time.monotonic() - started
            finished = await turn           # the wedged turn ends on its own afterwards
            return outcome, elapsed, finished
        finally:
            await runtime.close()

    outcome, elapsed, finished = run(flow())
    assert outcome.execution == "unconfirmed"
    assert outcome.cleanup == "pending"
    assert outcome.confirmed is False
    assert outcome.stop_reason is None
    assert elapsed < 3.0                       # bounded: it returned on the Stop bound
    assert finished.final_text == "Hello there"   # the runtime finished it, not the client
    assert any("did not end the turn" in line for line in runtime.diagnostics())


def test_a_crash_during_stop_is_unknown_not_stopped(tmp_path, fake, monkeypatch):
    """Verify a crash during stop is unknown not stopped."""
    marker = tmp_path / "prompt.txt"
    pidfile = tmp_path / "runtime.pid"
    runtime = configured(tmp_path, fake, monkeypatch, mode="crash-on-cancel", hold=10.0,
                         prompt_file=marker, pidfile=pidfile)

    async def flow():
        """Exercise the a crash during stop is unknown not stopped scenario through the async application boundary."""
        try:
            await runtime.start()
            turn = asyncio.ensure_future(runtime.run_turn("hello"))
            await asyncio.to_thread(wait_for_prompt, marker)
            outcome = await runtime.stop(timeout=5.0)
            with pytest.raises(RuntimeUnavailable):
                await turn
            return outcome
        finally:
            await runtime.close()

    outcome = run(flow())
    assert outcome.execution == "unconfirmed"
    assert outcome.cleanup == "pending"
    assert "without reporting a reason" in outcome.detail
    assert _wait_until_dead(int(pidfile.read_text(encoding="utf-8")))


def test_stop_keeps_the_event_loop_responsive(tmp_path, fake, monkeypatch):
    """Stop waits on a bound in a worker thread, so control messages are not stalled."""
    marker = tmp_path / "prompt.txt"
    runtime = configured(tmp_path, fake, monkeypatch, mode="ignore-cancel", hold=2.0,
                         stop_bound=0.5, prompt_file=marker)

    async def flow():
        """Exercise the stop keeps the event loop responsive scenario through the async application boundary."""
        try:
            await runtime.start()
            turn = asyncio.ensure_future(runtime.run_turn("hello"))
            await asyncio.to_thread(wait_for_prompt, marker)
            ticks = 0
            stopping = asyncio.ensure_future(runtime.stop(timeout=0.5))
            while not stopping.done():
                ticks += 1
                await asyncio.sleep(0.01)   # any blocking inside stop() would starve this
            outcome = await stopping
            await turn
            return outcome, ticks
        finally:
            await runtime.close()

    outcome, ticks = run(flow())
    assert outcome.execution == "unconfirmed"
    assert ticks >= 20, f"the loop only advanced {ticks} times during a 0.5 s stop"


def test_stop_during_the_window_before_the_prompt_starts_is_not_silently_idle(
        tmp_path, fake, monkeypatch):
    """A turn has started but its prompting thread has not published its handle yet.

    Reporting ``idle`` there would be a Stop that did nothing at all, so the runtime waits
    (bounded) for the handle and otherwise says the outcome is unknown.
    """
    runtime = configured(tmp_path, fake, monkeypatch, mode="hold")

    async def flow():
        """Exercise the stop during the window before the prompt starts is not silently idle scenario through the async application boundary."""
        try:
            await runtime.start()
            assert runtime._turn_lock.acquire(blocking=False)   # a turn is "in flight"
            outcome = await runtime.stop(timeout=0.2)
            runtime._turn_lock.release()
            return outcome
        finally:
            await runtime.close()

    outcome = run(flow())
    assert outcome.execution == "unconfirmed"
    assert outcome.cleanup == "pending"
    assert "has not started" in outcome.detail


def test_a_child_that_stops_reading_stdin_leaves_the_stop_unconfirmed(
        tmp_path, fake, monkeypatch):
    """The cancel is written but never read, and the turn finishes on its own schedule.

    The Stop must not claim the cancellation took effect: the runtime completed the turn.
    """
    marker = tmp_path / "prompt.txt"
    runtime = configured(tmp_path, fake, monkeypatch, mode="wedged", hold=1.5,
                         stop_bound=5.0, prompt_file=marker)

    async def flow():
        """Exercise the a child that stops reading stdin leaves the stop unconfirmed scenario through the async application boundary."""
        try:
            await runtime.start()
            turn = asyncio.ensure_future(runtime.run_turn("hello"))
            await asyncio.to_thread(wait_for_prompt, marker)
            outcome = await runtime.stop(timeout=5.0)
            finished = await turn
            return outcome, finished
        finally:
            await runtime.close()

    outcome, finished = run(flow())
    assert outcome.execution == "unconfirmed"
    assert outcome.confirmed is False
    assert outcome.stop_reason == "end_turn"     # the runtime finished it instead
    assert "instead of" in outcome.detail
    assert finished.final_text == "Hello there"  # it really did finish normally


def test_the_cancel_write_itself_is_bounded(tmp_path, fake, monkeypatch):
    """A child whose stdin nobody drains blocks the write; the Stop must still return."""
    marker = tmp_path / "prompt.txt"
    runtime = configured(tmp_path, fake, monkeypatch, mode="hold", hold=10.0,
                         stop_bound=0.3, prompt_file=marker)

    async def flow():
        """Exercise the the cancel write itself is bounded scenario through the async application boundary."""
        try:
            await runtime.start()
            turn = asyncio.ensure_future(runtime.run_turn("hello"))
            await asyncio.to_thread(wait_for_prompt, marker)
            process = runtime._process
            original_notify = process.notify

            def wedged_notify(method, params=None):
                # A full pipe: the write never completes.
                time.sleep(1.0)

            monkeypatch.setattr(process, "notify", wedged_notify)
            started = time.monotonic()
            outcome = await runtime.stop(timeout=0.3)
            elapsed = time.monotonic() - started
            await runtime.stop(timeout=0.3)          # and again: still bounded
            # Restore a working write so the turn can actually be cancelled, proving the
            # bound was in the waiting and not in a lost cancellation.
            process.notify = original_notify
            recovered = await runtime.stop(timeout=2.0)
            with pytest.raises(TurnCancelled):
                await turn
            return outcome, elapsed, recovered
        finally:
            await runtime.close()

    outcome, elapsed, recovered = run(flow())
    assert outcome.execution == "unconfirmed"
    assert "could not be written" in outcome.detail
    assert elapsed < 1.5, f"the Stop waited {elapsed:.2f}s for a blocked write"
    assert recovered.execution == "stopped"


def test_a_timed_out_cancel_write_blocks_new_turns_until_the_real_write_finishes(
        tmp_path, fake, monkeypatch):
    """A late cancellation must never target the next turn on the same ACP session."""
    marker = tmp_path / "prompt.txt"
    runtime = configured(tmp_path, fake, monkeypatch, mode="hold", hold=0.3,
                         hold_turns=2, stop_bound=0.05, prompt_file=marker)
    writer_entered = threading.Event()
    release_writer = threading.Event()
    writer_finished = threading.Event()

    async def flow():
        """Exercise the a timed out cancel write blocks new turns until the real write finishes scenario through the async application boundary."""
        try:
            await runtime.start()
            original_notify = runtime._process.notify

            def delayed_real_notify(method, params=None):
                writer_entered.set()
                if not release_writer.wait(5.0):
                    raise TimeoutError("test did not release the cancellation writer")
                try:
                    # Unlike a sleep-only stub, this really sends the stale cancellation.
                    original_notify(method, params)
                finally:
                    writer_finished.set()

            monkeypatch.setattr(runtime._process, "notify", delayed_real_notify)
            first = asyncio.create_task(runtime.run_turn("first"))
            await asyncio.to_thread(wait_for_prompt, marker)
            outcome = await runtime.stop(timeout=0.05)
            assert writer_entered.is_set()
            assert outcome.execution == "unconfirmed"
            # The original turn ends normally while the timed-out writer is still blocked.
            assert (await first).final_text == "Hello there"
            assert not writer_finished.is_set()
            with pytest.raises(RuntimeError, match="stopp|cancellation"):
                await runtime.run_turn("must not reach the child")
            release_writer.set()
            assert await asyncio.to_thread(writer_finished.wait, 2.0)
            # Let the cancellation worker finish its bookkeeping as well.
            deadline = time.monotonic() + 2.0
            while runtime.stopping and time.monotonic() < deadline:
                await asyncio.sleep(0.01)
            assert (await runtime.run_turn("after the write")).final_text == "Hello there"
        finally:
            release_writer.set()
            await runtime.close()

    run(flow())


def test_a_cancel_is_not_sent_before_the_prompt_has_been_written(tmp_path, fake, monkeypatch):
    """The reported race: cancelling between handle publication and the prompt write.

    The prompt is held before its write; Stop must wait for the write instead of sending a
    cancel the runtime would never see, and the cancel must arrive once the prompt exists.
    """
    marker = tmp_path / "prompt.txt"
    cancel_file = tmp_path / "cancels.txt"
    runtime = configured(tmp_path, fake, monkeypatch, mode="hold", hold=10.0,
                         stop_bound=5.0, prompt_file=marker, cancel_file=cancel_file)
    gate = threading.Event()
    process = None

    async def flow():
        """Exercise the a cancel is not sent before the prompt has been written scenario through the async application boundary."""
        nonlocal process
        try:
            await runtime.start()
            process = runtime._process
            original = process.call

            def gated(method, params=None, *, timeout, on_sent=None):
                if method == "session/prompt":
                    gate.wait(5.0)          # the prompt is not written yet
                return original(method, params, timeout=timeout, on_sent=on_sent)

            monkeypatch.setattr(process, "call", gated)
            turn = asyncio.ensure_future(runtime.run_turn("hello"))
            # The completion handle is published before the prompt is written ...
            deadline = time.monotonic() + 5
            while runtime._turn_done is None and time.monotonic() < deadline:
                await asyncio.sleep(0.01)
            assert runtime._turn_done is not None
            assert not runtime._turn_prompt_sent.is_set()
            stopping = asyncio.ensure_future(runtime.stop(timeout=5.0))
            await asyncio.sleep(0.3)
            assert not stopping.done()                       # it waits for the write
            assert not cancel_file.exists()                  # nothing was cancelled yet
            gate.set()                                        # now the prompt goes out
            outcome = await stopping
            with pytest.raises(TurnCancelled):
                await turn
            return outcome
        finally:
            await runtime.close()

    outcome = run(flow())
    assert outcome.execution == "stopped"
    assert outcome.stop_reason == CANCEL_STOP_REASON
    assert cancel_file.exists(), "the cancel never reached the child"


def test_the_api_reports_unconfirmed_within_its_bound_for_a_wedged_child(
        tmp_path, fake, monkeypatch):
    """End to end through HTTP: the child stops reading stdin, the turn never cancels.

    The response must come back inside the Stop bound, say ``unconfirmed``, and the
    ``stopped`` event must agree with it — and the session must recover once the wedged turn
    finishes on its own.
    """
    import httpx

    from odessy.application.api import create_app
    from odessy.application.session import SessionController
    from odessy.runtime_backends import selection as selection_module

    marker = tmp_path / "prompt.txt"
    config = configured(tmp_path, fake, monkeypatch, mode="wedged", hold=2.0,
                        stop_bound=0.5, prompt_file=marker)._config
    monkeypatch.setitem(selection_module._BACKENDS, "harness",
                        lambda session: HarnessRuntime(session, config=config))
    profile = {"name": "local", "adapter": "cloud", "provider": "deepseek",
               "model": "mock-model"}
    session = SessionController(model_factory=lambda name: None,
                                agent_factory=lambda *a, **k: None,
                                profiles={"local": profile},
                                runtime_selection="harness")
    app = create_app(lambda: session, token="secret")
    headers = {"Authorization": "Bearer secret"}

    async def flow():
        try:
            """Exercise the the api reports unconfirmed within its bound for a wedged child scenario through the async application boundary."""
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test",
                                         headers=headers) as client:
                sid = (await client.post("/v1/sessions")).json()["id"]
                route = "/v1/sessions/" + sid
                assert (await client.post(route + "/turns", json={"text": "hello"})).status_code == 202
                await asyncio.to_thread(wait_for_prompt, marker)
                started = time.monotonic()
                response = await client.post(route + "/stop")
                elapsed = time.monotonic() - started
                body = response.json()
                state = (await client.get(route)).json()
                deadline = time.monotonic() + 10
                while state["running"] and time.monotonic() < deadline:
                    await asyncio.sleep(0.1)
                    state = (await client.get(route)).json()
                return response.status_code, body, elapsed, state, session
        finally:
            await session.close()

    status, body, elapsed, state, controller = run(flow())
    assert status == 200
    assert body["execution"] == "unconfirmed"
    assert body["confirmed"] is False
    assert elapsed < 1.6, f"the Stop handler took {elapsed:.2f}s for a 0.5s bound"
    stopped = [event for event in controller.events if event["type"] == "stopped"]
    assert stopped and stopped[-1]["data"]["execution"] == "unconfirmed"
    # The wedged turn finished on its own; the session is usable again and nothing was
    # replayed or hidden.
    assert state["running"] is False
    assert state["history"] == []


# -- interaction with turns --------------------------------------------------

def test_a_turn_is_refused_while_the_stopped_turn_is_still_in_flight(
        tmp_path, fake, monkeypatch):
    """A Stop does not release the turn: the next turn must not collide on the session."""
    marker = tmp_path / "prompt.txt"
    runtime = configured(tmp_path, fake, monkeypatch, mode="hold", hold=10.0, prompt_file=marker)

    async def flow():
        """Exercise the a turn is refused while the stopped turn is still in flight scenario through the async application boundary."""
        try:
            await runtime.start()
            turn = asyncio.ensure_future(runtime.run_turn("hello"))
            await asyncio.to_thread(wait_for_prompt, marker)
            stopping = asyncio.ensure_future(runtime.stop(timeout=5.0))
            await asyncio.sleep(0)
            with pytest.raises(RuntimeError, match="already running|being stopped"):
                await runtime.run_turn("overlap")
            await stopping
            with pytest.raises(TurnCancelled):
                await turn
        finally:
            await runtime.close()

    run(flow())


def test_an_abandoned_turn_keeps_the_session_busy_then_hands_it_back(
        tmp_path, fake, monkeypatch):
    """Cancelling the caller's await must not free the session while the prompt still runs."""
    marker = tmp_path / "prompt.txt"
    runtime = configured(tmp_path, fake, monkeypatch, mode="ignore-cancel", hold=2.0,
                         stop_bound=0.2, prompt_file=marker)

    async def flow():
        """Exercise the an abandoned turn keeps the session busy then hands it back scenario through the async application boundary."""
        try:
            await runtime.start()
            turn = asyncio.ensure_future(runtime.run_turn("hello"))
            await asyncio.to_thread(wait_for_prompt, marker)
            turn.cancel()                     # the client gave up without a confirmed stop
            with pytest.raises(asyncio.CancelledError):
                await turn
            assert runtime.orphaned_turn is True
            with pytest.raises(RuntimeError, match="already running"):
                await runtime.run_turn("collides")
            deadline = time.monotonic() + 10
            while runtime.orphaned_turn and time.monotonic() < deadline:
                await asyncio.sleep(0.05)     # the wedged turn finishes on its own
            return await runtime.run_turn("after")
        finally:
            await runtime.close()

    assert run(flow()).final_text == "Hello there"


# -- two sessions ------------------------------------------------------------

def test_stopping_one_session_leaves_the_other_running(tmp_path, fake, monkeypatch):
    """Verify stopping one session leaves the other running."""
    for name in ("a", "b"):
        (tmp_path / name).mkdir(parents=True, exist_ok=True)
    marker = tmp_path / "prompt.txt"
    held = configured(tmp_path / "a", fake, monkeypatch, mode="hold", hold=10.0,
                      prompt_file=marker)
    free = configured(tmp_path / "b", fake, monkeypatch, mode="hold", hold=10.0, hold_turns=0)

    async def flow():
        """Exercise the stopping one session leaves the other running scenario through the async application boundary."""
        try:
            await held.start()
            await free.start()
            slow = asyncio.ensure_future(held.run_turn("hello"))
            other = await free.run_turn("hello")      # an unrelated session, concurrently
            await asyncio.to_thread(wait_for_prompt, marker)
            outcome = await held.stop(timeout=5.0)
            with pytest.raises(TurnCancelled):
                await slow
            return outcome, other
        finally:
            await held.close()
            await free.close()

    outcome, other = run(flow())
    assert outcome.execution == "stopped"
    assert other.final_text == "Hello there"


# -- teardown ----------------------------------------------------------------

def test_close_after_a_stop_still_reaps_the_runtime(tmp_path, fake, monkeypatch):
    """Verify close after a stop still reaps the runtime."""
    marker = tmp_path / "prompt.txt"
    pidfile = tmp_path / "runtime.pid"
    runtime = configured(tmp_path, fake, monkeypatch, mode="hold", hold=10.0,
                         prompt_file=marker, pidfile=pidfile)

    async def flow():
        """Exercise the close after a stop still reaps the runtime scenario through the async application boundary."""
        await runtime.start()
        turn = asyncio.ensure_future(runtime.run_turn("hello"))
        await asyncio.to_thread(wait_for_prompt, marker)
        await runtime.stop(timeout=5.0)
        with pytest.raises(TurnCancelled):
            await turn
        await runtime.close()

    run(flow())
    pid = int(pidfile.read_text(encoding="utf-8"))
    assert _wait_until_dead(pid), "closing after a Stop left the runtime running"

def test_stop_does_not_claim_complete_while_tool_cleanup_is_pending(tmp_path, fake, monkeypatch):
    runtime = configured(tmp_path, fake, monkeypatch, mode='hold', hold=3)
    class Control:
        running = {12345}
        clean = threading.Event()
        def cancel(self):
            pass
        def begin(self):
            pass
        def close(self):
            pass
    async def flow():
        try:
            await runtime.start()
            runtime._control = Control()
            turn = asyncio.create_task(runtime.run_turn('hello'))
            for _ in range(200):
                if runtime._turn_prompt_sent is not None and runtime._turn_prompt_sent.is_set():
                    break
                await asyncio.sleep(.01)
            result = await runtime.stop(timeout=.1)
            await asyncio.gather(turn, return_exceptions=True)
            assert result.cleanup == 'pending'
            assert result.execution == 'unconfirmed'
        finally:
            await runtime.close()
    run(flow())


def test_cleanup_admission_failure_releases_the_turn_lock(tmp_path, fake, monkeypatch):
    runtime = configured(tmp_path, fake, monkeypatch, mode='normal')
    class Control:
        def begin(self):
            raise RuntimeError('tool cleanup is pending')
        def cancel(self):
            pass
        def close(self):
            pass
    async def flow():
        try:
            await runtime.start()
            runtime._control = Control()
            with pytest.raises(RuntimeError, match='cleanup'):
                await runtime.run_turn('hello')
            assert not runtime._turn_lock.locked()
        finally:
            await runtime.close()
    run(flow())
