"""Stop through the application layer (prompt 06).

The runtime here is a stub registered through the supported backend seam, so each phase can be
held open deterministically: before generation, during a generation, while a human approval is
pending, immediately after settlement, and a second Stop.

What the engine does with ``session/cancel`` is covered by ``test_harness_stop.py``; this file
covers what a *session* promises: no cancelled text becomes the answer, pending waits are
released, the ``stopped`` event says what was established, and a turn admitted before the Stop
never reaches the engine.
"""

from __future__ import annotations

import asyncio

import pytest

from odessy.application.session import SessionController
from odessy.runtime.types import AgentTurnResult
from odessy.runtime_backends import HARNESS_CAPABILITIES, TurnCancelled
from odessy.runtime_backends import selection as selection_module
from odessy.runtime_backends.contracts import StopOutcome


def run(coroutine):
    return asyncio.run(coroutine)


class _Model:
    pass


class StubRuntime:
    """A ``RuntimeSession`` whose turn does exactly what a test holds open."""

    engine = "harness"

    def __init__(self, *, hold: bool = True,
                 outcome: StopOutcome | None = None, waits_for_approval: bool = False):
        """Initialize the owned state and synchronization used by this component."""
        self.session_id = "stub-session"
        self.hold = hold
        self.waits_for_approval = waits_for_approval
        self.outcome = outcome or StopOutcome(True, "stopped", "complete", "cancelled")
        self.session = None          # set by the fixture, for the approval case
        self.turns: list[str] = []
        self.stops = 0
        self.closed = False
        self._running = False
        self._cancelled = False
        self._gate = asyncio.Event()

    @property
    def capabilities(self):
        return HARNESS_CAPABILITIES

    def events(self, after_id: int = 0):
        """Return normalized events after the requested cursor."""
        return []

    def snapshot(self):
        """Return the application projection without creating a second runtime history."""
        return {}

    async def run_turn(self, text, *args, **kwargs):
        """Delegate one admitted input to the bound runtime and return its settled result."""
        self.turns.append(text)
        self._running = True
        self._cancelled = False
        try:
            if self.waits_for_approval:
                approved = await self.session.confirm_action(
                    {"reason": "Write a file", "risk": "high"})
                if not approved:
                    self._cancelled = True
            elif self.hold:
                await self._gate.wait()
        finally:
            self._running = False
        if self._cancelled:
            raise TurnCancelled(self.outcome.public())
        return AgentTurnResult(f"answer:{text}", [])

    async def stop(self, *, timeout=None):
        self.stops += 1
        if not self._running:
            return StopOutcome(False, "idle", "complete", None, "no turn is running")
        if self.outcome.execution == "stopped":
            # The engine acted on the cancellation; the waiting turn now ends.
            self._cancelled = True
            self._gate.set()
        return self.outcome

    async def close(self):
        """Release this component and its owned resources; repeated closure is safe."""
        self.closed = True
        self._gate.set()


@pytest.fixture()
def harness_controller(monkeypatch):
    """A session controller bound to the harness engine, with a stub runtime behind it."""

    def build(**stub):
        runtime = StubRuntime(**stub)
        monkeypatch.setitem(selection_module._BACKENDS, "harness",
                            lambda session: runtime)
        session = SessionController(model_factory=lambda name: _Model(),
                                    agent_factory=lambda *a, **k: None,
                                    profiles={"local": {}},
                                    runtime_selection="harness")
        runtime.session = session
        return session, runtime

    return build


def test_stop_during_a_generation_emits_stopped_and_keeps_the_text_out(harness_controller):
    """Verify stop during a generation emits stopped and keeps the text out."""
    session, runtime = harness_controller()

    async def flow():
        """Exercise the stop during a generation emits stopped and keeps the text out scenario through the async application boundary."""
        try:
            turn = asyncio.ensure_future(session.run_turn("hello"))
            while not runtime.turns:
                await asyncio.sleep(0)
            outcome = await session.stop()
            with pytest.raises(TurnCancelled):
                await turn
            return outcome, [t.assistant for t in session.memory.turns], len(session.events)
        finally:
            await session.close()

    outcome, memory, _ = run(flow())
    assert outcome["status"] == "stopped"
    assert outcome["requested"] is True
    assert outcome["confirmed"] is True
    assert runtime.turns == ["hello"]
    assert memory == []                       # the cancelled turn is not an answer
    stopped = [event for event in session.events if event["type"] == "stopped"]
    assert len(stopped) == 1
    assert stopped[0]["data"]["reason"] == "cancelled"
    assert stopped[0]["data"]["execution"] == "stopped"
    assert stopped[0]["data"]["stop_reason"] == "cancelled"
    assert "result" not in [event["type"] for event in session.events]


def test_stop_during_a_pending_approval_declines_it_and_stops_the_turn(harness_controller):
    """Verify stop during a pending approval declines it and stops the turn."""
    session, runtime = harness_controller(waits_for_approval=True)

    async def flow():
        """Exercise the stop during a pending approval declines it and stops the turn scenario through the async application boundary."""
        try:
            turn = asyncio.ensure_future(session.run_turn("write"))
            while not session.pending_approvals:
                await asyncio.sleep(0)
            approval = session.pending_approvals[0]["id"]
            outcome = await session.stop()
            with pytest.raises(TurnCancelled):
                await turn
            return outcome, approval, runtime.stops
        finally:
            await session.close()

    outcome, approval, stops = run(flow())
    assert not session.pending_approvals     # Stop declines; nothing is left hanging
    with pytest.raises(Exception) as caught:
        session.answer_approval(approval, True)   # a late answer cannot resurrect it
    assert getattr(caught.value, "status_code", None) == 410
    assert outcome["execution"] == "stopped"
    assert stops == 1


def test_stop_before_generation_sends_nothing_to_the_engine(harness_controller):
    """A turn admitted before the Stop must not reach the engine afterwards."""
    session, runtime = harness_controller()
    admitted = session.stop_epoch

    async def flow():
        """Exercise the stop before generation sends nothing to the engine scenario through the async application boundary."""
        try:
            outcome = await session.stop()    # the runtime is idle: nothing to cancel
            with pytest.raises(TurnCancelled):
                await session.run_turn("hello", admitted_epoch=admitted)
            return outcome
        finally:
            await session.close()

    outcome = run(flow())
    assert outcome["execution"] == "idle"
    assert session.stop_epoch != admitted
    assert runtime.turns == []                # the engine was never asked
    stopped = [event for event in session.events if event["type"] == "stopped"]
    assert stopped and stopped[-1]["data"]["detail"] == "stopped before generation"


def test_repeated_stop_is_idempotent(harness_controller):
    """Verify repeated stop is idempotent."""
    session, runtime = harness_controller()

    async def flow():
        """Exercise the repeated stop is idempotent scenario through the async application boundary."""
        try:
            turn = asyncio.ensure_future(session.run_turn("hello"))
            while not runtime.turns:
                await asyncio.sleep(0)
            first = await session.stop()
            with pytest.raises(TurnCancelled):
                await turn
            second = await session.stop()          # nothing running any more
            return first, second, runtime.stops
        finally:
            await session.close()

    first, second, stops = run(flow())
    assert first["execution"] == "stopped"
    assert second["execution"] == "idle"
    assert second["requested"] is False
    assert stops == 2


def test_stop_after_settlement_changes_nothing(harness_controller):
    """Verify stop after settlement changes nothing."""
    session, runtime = harness_controller(hold=False)

    async def flow():
        """Exercise the stop after settlement changes nothing scenario through the async application boundary."""
        try:
            result = await session.run_turn("hello")
            before = len(session.events)
            memory = [turn.assistant for turn in session.memory.turns]
            outcome = await session.stop()
            return result, outcome, before == len(session.events), memory
        finally:
            await session.close()

    result, outcome, unchanged, memory = run(flow())
    assert result.final_text == "answer:hello"
    assert outcome["execution"] == "idle"      # the turn had already settled
    assert unchanged is True                   # a late Stop invents no event
    assert memory == ["answer:hello"]


def test_an_unconfirmed_stop_is_reported_as_unconfirmed(harness_controller):
    """Verify an unconfirmed stop is reported as unconfirmed."""
    session, runtime = harness_controller(
        outcome=StopOutcome(True, "unconfirmed", "pending", None,
                            "the runtime did not end the turn within 0.5s"))

    async def flow():
        """Exercise the an unconfirmed stop is reported as unconfirmed scenario through the async application boundary."""
        try:
            turn = asyncio.ensure_future(session.run_turn("hello"))
            while not runtime.turns:
                await asyncio.sleep(0)
            outcome = await session.stop()
            # The engine never ended the turn, so the client stops waiting for it: the task
            # is cancelled locally and the incident is reported, not hidden.
            with pytest.raises(asyncio.CancelledError):
                await turn
            return outcome
        finally:
            await session.close()

    outcome = run(flow())
    assert outcome["status"] == "unconfirmed"
    assert outcome["confirmed"] is False
    assert outcome["cleanup"] == "pending"
    assert "did not end the turn" in outcome["detail"]
    # The event says the same thing the response does: the local await was abandoned and
    # nothing confirmed the engine, so it must not read as a plain "stopped".
    stopped = [event for event in session.events if event["type"] == "stopped"]
    assert stopped and stopped[-1]["data"]["reason"] == "local-cancel"
    assert stopped[-1]["data"]["execution"] == "unconfirmed"
    assert stopped[-1]["data"]["confirmed"] is False
    assert not session.memory.turns


def test_the_stop_route_reports_the_outcome_and_the_session_keeps_working(harness_controller):
    """The HTTP surface: no 501 for a Harness session, and Stop is followed by a usable
    session (the child was never killed)."""
    import httpx

    from odessy.application.api import create_app

    session, runtime = harness_controller()
    app = create_app(lambda: session, token="secret")
    headers = {"Authorization": "Bearer secret"}

    async def flow():
        """Exercise the the stop route reports the outcome and the session keeps working scenario through the async application boundary."""
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test",
                                     headers=headers) as client:
            sid = (await client.post("/v1/sessions")).json()["id"]
            route = "/v1/sessions/" + sid
            assert (await client.post(route + "/turns", json={"text": "hello"})).status_code == 202
            while not runtime.turns:
                await asyncio.sleep(0)
            response = await client.post(route + "/stop")
            assert response.status_code == 200
            body = response.json()
            assert body["execution"] == "stopped"
            assert body["requested"] is True
            assert body["confirmed"] is True
            assert body["cleanup"] == "complete"
            state = (await client.get(route)).json()
            assert state["running"] is False
            assert state["history"] == []
            # The session still works: nothing was killed to stop one turn.
            assert (await client.post(route + "/turns", json={"text": "hello"})).status_code == 202
            for _ in range(50):
                await asyncio.sleep(0)
                if (await client.get(route)).json()["history"]:
                    break
            assert (await client.get(route)).json()["history"][-1]["assistant"] == "answer:hello"

    run(flow())


def test_the_explicit_process_wide_stop_is_guarded_and_reports_every_session(harness_controller):
    """There is a process-wide stop, but it is its own explicit operation: it needs a reason
    and ``confirm``, and it names every session with the outcome it established."""
    import httpx

    from odessy.application.api import create_app

    first, first_runtime = harness_controller()
    second, second_runtime = harness_controller()
    sessions = [first, second]
    app = create_app(lambda: sessions.pop(0), token="secret")
    headers = {"Authorization": "Bearer secret"}

    async def flow():
        """Exercise the the explicit process wide stop is guarded and reports every session scenario through the async application boundary."""
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test",
                                     headers=headers) as client:
            ids = [(await client.post("/v1/sessions")).json()["id"] for _ in range(2)]
            for sid in ids:
                assert (await client.post("/v1/sessions/" + sid + "/turns",
                                          json={"text": "hello"})).status_code == 202
            for _ in range(50):
                await asyncio.sleep(0)
                if len(first_runtime.turns) and len(second_runtime.turns):
                    break
            instance = (await client.get("/v1/capabilities")).json()["service_instance_id"]
            wrong_instance = await client.post("/v1/lifecycle/stop-all",
                                               json={"service_instance_id": "nope",
                                                     "reason": "quit", "confirm": True})
            assert wrong_instance.status_code == 409
            missing_confirm = await client.post("/v1/lifecycle/stop-all",
                                                json={"service_instance_id": instance,
                                                      "reason": "quit"})
            assert missing_confirm.status_code == 400
            no_reason = await client.post("/v1/lifecycle/stop-all",
                                          json={"service_instance_id": instance, "confirm": True})
            assert no_reason.status_code == 400
            body = (await client.post("/v1/lifecycle/stop-all",
                                      json={"service_instance_id": instance,
                                            "reason": "operator quit", "confirm": True})).json()
            return ids, body

    ids, body = run(flow())
    assert [entry["session_id"] for entry in body["affected"]] == ids
    assert all(entry["engine"] == "harness" for entry in body["affected"])
    assert all(entry["execution"] == "stopped" for entry in body["affected"])
    assert all(entry["reason"] == "operator quit" for entry in body["affected"])
    assert body["unconfirmed"] == []
    # Both sessions were stopped through their own engines, not by killing anything.
    assert first_runtime.stops >= 1 and second_runtime.stops >= 1
