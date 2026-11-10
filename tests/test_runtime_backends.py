"""Focused tests for the engine seam introduced in prompt 02.

They assert the seam's own rules (selection, availability, locking, event envelope) and
that wrapping the existing engine changes nothing observable. Harness *behaviour* is
tested in ``tests/test_harness_runtime.py``; here ``harness`` only appears as a
registered engine whose configuration decides whether it is available.
"""

import asyncio
import json

import pytest

from odessy.application.session import SessionController
from odessy.runtime.types import AgentTurnResult
from odessy.runtime_backends import (
    CAPABILITIES,
    LEGACY_CAPABILITIES,
    LegacyRuntime,
    RuntimeEngineLocked,
    RuntimeEvent,
    RuntimeSelection,
    RuntimeSelectionError,
    RuntimeUnavailable,
    available_engines,
    normalize_engine,
    register_backend,
    require_available,
    resolve_selection,
)
from odessy.runtime_backends import selection as selection_module


class Model:
    pass


def controller(**kwargs):
    return SessionController(
        model_factory=lambda name: Model(),
        agent_factory=lambda *args, **kwargs: None,
        profiles={"local": {}},
        **kwargs,
    )


# -- selection rules ---------------------------------------------------------

def test_absent_selection_defaults_to_legacy():
    """Verify absent selection defaults to legacy."""
    assert resolve_selection(None, environ={}) == RuntimeSelection("legacy", "default")
    assert resolve_selection("", environ={}) == RuntimeSelection("legacy", "default")
    assert resolve_selection("   ", environ={}) == RuntimeSelection("legacy", "default")


def test_environment_selects_and_explicit_value_wins():
    """Verify environment selects and explicit value wins."""
    assert resolve_selection(None, environ={"ODESSY_RUNTIME_ENGINE": "legacy"}) == \
        RuntimeSelection("legacy", "explicit")
    assert resolve_selection("legacy", environ={"ODESSY_RUNTIME_ENGINE": "harness"}) == \
        RuntimeSelection("legacy", "explicit")


def test_unknown_engine_is_a_malformed_selection_not_an_unavailable_one():
    """Verify unknown engine is a malformed selection not an unavailable one."""
    with pytest.raises(RuntimeSelectionError) as caught:
        resolve_selection("turo", environ={})
    assert caught.value.code == "runtime_engine_invalid"
    assert "expected one of" in str(caught.value)


def test_harness_is_registered_and_availability_is_configuration(monkeypatch):
    # A registered engine and an available engine are different questions. Harness has
    # a backend in this build; without its configuration, building it still fails.
    """Verify harness is registered and availability is configuration."""
    monkeypatch.delenv("ODESSY_HARNESS_EXE", raising=False)
    selection = resolve_selection("harness", environ={})
    assert selection.engine == "harness"
    assert available_engines() == ("harness", "legacy")
    require_available(selection)  # registered, so this passes
    with pytest.raises(RuntimeUnavailable) as caught:
        from odessy.runtime_backends import build_runtime
        build_runtime(object(), selection)
    assert caught.value.code == "runtime_unavailable"
    assert caught.value.engine == "harness"
    assert caught.value.public()["reason"].startswith("not configured")


def test_preconstructed_selection_is_validated_not_treated_as_unavailable():
    # An unknown engine in a constructed object is malformed configuration, not a
    # known-but-unavailable engine. It must never reach require_available().
    """Verify preconstructed selection is validated not treated as unavailable."""
    with pytest.raises(RuntimeSelectionError) as caught:
        RuntimeSelection("turo", "explicit")
    assert caught.value.code == "runtime_engine_invalid"
    with pytest.raises(RuntimeSelectionError):
        RuntimeSelection("legacy", "somewhere")
    # An absent engine normalizes to the default, exactly like resolve_selection(None).
    assert RuntimeSelection(None, "default") == RuntimeSelection("legacy", "default")

    normalized = RuntimeSelection(" Legacy ", "explicit")
    assert normalized.engine == "legacy"
    assert resolve_selection(normalized) == normalized
    assert resolve_selection(normalized).source == "explicit"


def test_a_registered_backend_becomes_available_and_cannot_be_overwritten(monkeypatch):
    """Verify a registered backend becomes available and cannot be overwritten."""
    calls = []

    def builder(session):
        calls.append(session)
        return "runtime-for-" + session

    # Start from a registry with no harness backend, so registration is observable.
    monkeypatch.setattr(selection_module, "_BACKENDS",
                        {"legacy": selection_module._legacy_builder})
    assert available_engines() == ("legacy",)
    with pytest.raises(RuntimeUnavailable):
        require_available(RuntimeSelection("harness", "explicit"))
    register_backend("harness", builder)
    assert "harness" in available_engines()
    require_available(RuntimeSelection("harness", "explicit"))
    with pytest.raises(RuntimeSelectionError):
        register_backend("harness", builder)
    with pytest.raises(RuntimeSelectionError):
        register_backend("not-an-engine", builder)
    assert calls == []


def test_engine_names_are_normalized_but_not_guessed():
    """Verify engine names are normalized but not guessed."""
    assert normalize_engine(None) == "legacy"
    assert normalize_engine(" Legacy ") == "legacy"
    with pytest.raises(RuntimeSelectionError):
        normalize_engine("legacy2")
    with pytest.raises(RuntimeSelectionError):
        normalize_engine(3)


# -- session binding ---------------------------------------------------------

def test_session_binds_legacy_at_creation():
    """Verify session binds legacy at creation."""
    session = controller()
    try:
        assert session.engine == "legacy"
        assert isinstance(session.runtime, LegacyRuntime)
        assert session.runtime.engine == "legacy"
        assert session.runtime.session_id == session.id
        assert session.runtime.capabilities == LEGACY_CAPABILITIES
        assert session.runtime_selection == RuntimeSelection("legacy", "default")
    finally:
        asyncio.run(session.close())


def test_session_with_unconfigured_engine_is_not_created_at_all(monkeypatch):
    """Verify session with unconfigured engine is not created at all."""
    monkeypatch.delenv("ODESSY_HARNESS_EXE", raising=False)
    with pytest.raises(RuntimeUnavailable):
        controller(runtime_selection="harness")


def test_session_refuses_to_move_engine():
    """Verify session refuses to move engine."""
    session = controller()
    try:
        assert session.select_engine("legacy") == "legacy"
        with pytest.raises(RuntimeEngineLocked) as caught:
            session.select_engine("harness")
        assert caught.value.current == "legacy"
        assert caught.value.requested == "harness"
        assert session.engine == "legacy"
        with pytest.raises(RuntimeSelectionError):
            session.select_engine("nope")
    finally:
        asyncio.run(session.close())


def test_closed_session_rejects_engine_selection_and_turns():
    """Verify closed session rejects engine selection and turns."""
    session = controller()
    asyncio.run(session.close())

    async def run():
        """Exercise the closed session rejects engine selection and turns scenario through the async application boundary."""
        with pytest.raises(RuntimeError, match="closed"):
            session.select_engine("legacy")
        with pytest.raises(RuntimeError, match="closed"):
            await session.runtime.run_turn("hello")

    asyncio.run(run())


# -- normalization -----------------------------------------------------------

def test_runtime_event_round_trips_the_public_envelope():
    """Verify runtime event round trips the public envelope."""
    public = {"id": 7, "type": "progress", "time": 12.5, "data": {"message": "Working"}}
    event = RuntimeEvent.from_public(public)
    assert event.id == 7 and event.type == "progress" and event.time == 12.5
    assert event.public() == public
    assert json.loads(json.dumps(event.public())) == public


@pytest.mark.parametrize("payload", [
    [{"text": "a"}, {"text": "b"}],     # list, as a voice/consultation event can be
    "plain text",
    3,
    1.5,
    True,
    None,
    {"nested": {"items": [1, {"deep": None}]}},
    ("a", 1),
])
def test_runtime_event_preserves_every_valid_payload_shape(payload):
    """Verify runtime event preserves every valid payload shape."""
    public = {"id": 3, "type": "x", "time": 1.0, "data": payload}
    event = RuntimeEvent.from_public(public)
    assert event.data == payload
    assert event.public() == public


def test_runtime_event_rejects_unsupported_payloads_instead_of_discarding_them():
    """Verify runtime event rejects unsupported payloads instead of discarding them."""
    with pytest.raises(TypeError, match="JSON-compatible"):
        RuntimeEvent.from_public({"id": 1, "type": "t", "time": 1.0, "data": {"s": {1, 2}}})
    with pytest.raises(TypeError, match="JSON-compatible"):
        RuntimeEvent.from_public({"id": 1, "type": "t", "time": 1.0, "data": {1: "x"}})
    with pytest.raises(TypeError, match="JSON-compatible"):
        RuntimeEvent.from_public({"id": 1, "type": "t", "time": 1.0, "data": object()})


def test_runtime_event_omitted_data_is_empty_not_none():
    """Verify runtime event omitted data is empty not none."""
    event = RuntimeEvent.from_public({"id": 1, "type": "t", "time": 1.0})
    assert event.data == {}
    assert event.public()["data"] == {}


def test_malformed_runtime_event_is_rejected():
    """Verify malformed runtime event is rejected."""
    with pytest.raises(ValueError):
        RuntimeEvent.from_public({"type": "progress"})
    with pytest.raises(ValueError):
        RuntimeEvent.from_public({"id": 1, "type": ""})
    with pytest.raises(TypeError):
        RuntimeEvent.from_public("progress")


def test_runtime_events_are_filtered_by_id_and_share_the_snapshot():
    """Verify runtime events are filtered by id and share the snapshot."""
    session = controller()
    try:
        session.emit("first", {"n": 1})
        session.emit("second", {"n": 2})
        events = session.runtime.events()
        assert [event.type for event in events] == ["first", "second"]
        assert [event.type for event in session.runtime.events(after_id=events[0].id)] == ["second"]
        # The envelope is lossless: the snapshot's events are exactly the raw buffer.
        assert session.public_snapshot()["events"] == session.events
        assert session.runtime.snapshot() == session.public_snapshot()
        assert set(session.public_snapshot()) == {
            "id", "selected_profile", "available_profiles", "active_profile", "running",
            "history", "pending_approvals", "pending_questions", "events",
        }
    finally:
        asyncio.run(session.close())


def test_legacy_runtime_runs_the_existing_turn_unchanged():
    """Verify legacy runtime runs the existing turn unchanged."""
    class Agent:
        async def run_turn(self, text, **kwargs):
            """Delegate one admitted input to the bound runtime and return its settled result."""
            return AgentTurnResult("answer:" + text, [])

    async def run():
        """Exercise the legacy runtime runs the existing turn unchanged scenario through the async application boundary."""
        session = SessionController(
            model_factory=lambda name: Model(),
            agent_factory=lambda *args, **kwargs: Agent(),
            profiles={"local": {}},
        )
        try:
            result = await session.runtime.run_turn("hello")
            assert result.final_text == "answer:hello"
            types = [event.type for event in session.runtime.events()]
            assert types[:2] == ["transcript", "selection"]
            assert types[-2:] == ["cost", "result"]
            assert session.runtime.snapshot()["history"][-1]["assistant"] == "answer:hello"
        finally:
            await session.close()

    asyncio.run(run())


def test_capability_reports_are_distinct_and_frozen():
    """Verify capability reports are distinct and frozen."""
    from dataclasses import FrozenInstanceError

    from odessy.runtime_backends import HARNESS_TARGET_CAPABILITIES

    assert CAPABILITIES["legacy"] is LEGACY_CAPABILITIES
    assert CAPABILITIES["harness"].questions is False
    # Not implemented yet: the record says so, and the runtime raises for each flag.
    assert CAPABILITIES["harness"].live_text_deltas is False
    # Implemented in prompt 06: ACP session/cancel on this session's own child.
    assert CAPABILITIES["harness"].per_session_cancel is True
    assert CAPABILITIES["harness"].approvals is False
    assert HARNESS_TARGET_CAPABILITIES.live_text_deltas is True
    with pytest.raises(FrozenInstanceError):
        CAPABILITIES["legacy"].approvals = False


def test_capability_flags_are_checked_by_name_and_unknown_names_raise():
    """Verify capability flags are checked by name and unknown names raise."""
    assert CAPABILITIES["legacy"].supports("live_text_deltas") is False
    assert CAPABILITIES["legacy"].supports("per_session_cancel") is True
    assert CAPABILITIES["harness"].supports("live_text_deltas") is False
    with pytest.raises(KeyError):
        CAPABILITIES["legacy"].supports("telepathy")


# -- HTTP surface ------------------------------------------------------------

def test_unavailable_engine_is_a_503_not_a_legacy_session():
    """Verify unavailable engine is a 503 not a legacy session."""
    httpx = pytest.importorskip("httpx")
    pytest.importorskip("fastapi")
    from odessy.application.api import create_app

    def factory():
        raise RuntimeUnavailable("harness", "no backend is registered in this build")

    async def run():
        """Exercise the unavailable engine is a 503 not a legacy session scenario through the async application boundary."""
        app = create_app(factory, token="secret")
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test",
                                     headers={"Authorization": "Bearer secret"}) as client:
            response = await client.post("/v1/sessions")
            assert response.status_code == 503
            assert "harness" in response.json()["detail"]
            assert not app.state.sessions

    asyncio.run(run())


def test_malformed_engine_is_a_400():
    """Verify malformed engine is a 400."""
    httpx = pytest.importorskip("httpx")
    pytest.importorskip("fastapi")
    from odessy.application.api import create_app

    def factory():
        raise RuntimeSelectionError("Unknown runtime engine 'turo'")

    async def run():
        """Exercise the malformed engine is a 400 scenario through the async application boundary."""
        app = create_app(factory, token="secret")
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test",
                                     headers={"Authorization": "Bearer secret"}) as client:
            response = await client.post("/v1/sessions")
            assert response.status_code == 400
            assert not app.state.sessions

    asyncio.run(run())
