"""Explicit runtime selection through the application factories (prompt 12).

The desktop may name an engine for a **new** session. These tests pin the seam: the name is
validated, an engine this build cannot inject is refused instead of replaced by the default,
a restored session keeps the engine its history was written with, and the snapshot reports
the backend identity a diagnostics surface needs.
"""
import asyncio
import json

import pytest

from odessy.application.session import SessionController
from odessy.runtime_backends import selection

PROFILE = {"name": "custom", "adapter": "cloud", "provider": "custom", "model": "mock"}


def selected_controller(runtime=None):
    return SessionController(
        model_factory=lambda name: object(),
        agent_factory=lambda *args, **kwargs: None,
        profiles={"custom": dict(PROFILE)},
        selected_profile="custom",
        runtime_selection=runtime,
    )


def factory(restore=None, runtime=None):
    return selected_controller(runtime)


def run(coroutine):
    return asyncio.run(coroutine)


def client(app):
    httpx = pytest.importorskip("httpx")
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                             base_url="http://test",
                             headers={"Authorization": "Bearer secret"})


# ---------------------------------------------------------------- the restore rule


def test_a_restore_keeps_the_engine_its_record_names():
    from odessy.application.bootstrap import restored_runtime_selection

    record = {"engine": {"name": "harness"}}
    assert restored_runtime_selection(None, record) == "harness"
    assert restored_runtime_selection("harness", record) == "harness"


def test_a_restore_naming_a_different_engine_is_refused_before_anything_is_built():
    from odessy.application.bootstrap import restored_runtime_selection
    from odessy.application.durable import RuntimeSessionUnavailable

    with pytest.raises(RuntimeSessionUnavailable) as caught:
        restored_runtime_selection("legacy", {"engine": {"name": "harness"}})
    assert caught.value.public()["verdict"] == "engine_mismatch"
    assert "harness" in caught.value.public()["reason"]


def test_a_record_without_engine_identity_keeps_the_callers_resolution():
    from odessy.application.bootstrap import restored_runtime_selection

    assert restored_runtime_selection(None, {}) is None
    assert restored_runtime_selection("harness", {"engine": {}}) == "harness"


# ---------------------------------------------------------------- the HTTP surface


def test_a_session_without_a_runtime_uses_the_installations_default():
    httpx = pytest.importorskip("httpx")
    from odessy.application.api import create_app

    async def flow():
        app = create_app(factory, token="secret")
        async with client(app) as api:
            created = await api.post("/v1/sessions")
            assert created.status_code == 201
            engine = created.json()["engine"]
            assert engine["name"] == "legacy"
            assert engine["source"] == "default"
            assert engine["capabilities"]["engine"] == "legacy"

    run(flow())


def test_an_explicit_runtime_applies_to_a_new_session():
    httpx = pytest.importorskip("httpx")
    from odessy.application.api import create_app

    async def flow():
        app = create_app(factory, token="secret")
        async with client(app) as api:
            legacy = (await api.post("/v1/sessions", json={"runtime": "legacy"})).json()
            default = (await api.post("/v1/sessions")).json()
            assert legacy["engine"]["name"] == "legacy"
            assert legacy["engine"]["source"] == "explicit"
            assert default["engine"]["source"] == "default"
            # Two sessions, two decisions: the choice is per session, not global state.
            assert legacy["id"] != default["id"]

    run(flow())


def test_an_unknown_runtime_is_a_400_not_a_legacy_session():
    httpx = pytest.importorskip("httpx")
    from odessy.application.api import create_app

    async def flow():
        app = create_app(factory, token="secret")
        async with client(app) as api:
            unknown = await api.post("/v1/sessions", json={"runtime": "telepathy"})
            assert unknown.status_code == 400
            assert "telepathy" in unknown.json()["detail"]
            non_string = await api.post("/v1/sessions", json={"runtime": 7})
            assert non_string.status_code == 400
            assert not app.state.sessions

    run(flow())


def test_a_runtime_this_build_cannot_inject_is_a_501(monkeypatch):
    httpx = pytest.importorskip("httpx")
    from odessy.application.api import create_app

    def bare_factory():
        return selected_controller()

    async def flow():
        app = create_app(bare_factory, token="secret")
        async with client(app) as api:
            refused = await api.post("/v1/sessions", json={"runtime": "legacy"})
            assert refused.status_code == 501
            assert not app.state.sessions
            # Without the field the same factory still works: the refusal is about the
            # request, not about the composition.
            assert (await api.post("/v1/sessions")).status_code == 201

    run(flow())


def test_an_unavailable_engine_is_a_503_and_no_session_is_created(monkeypatch):
    httpx = pytest.importorskip("httpx")
    from odessy.application.api import create_app

    monkeypatch.delitem(selection._BACKENDS, "harness")

    async def flow():
        app = create_app(factory, token="secret")
        async with client(app) as api:
            refused = await api.post("/v1/sessions", json={"runtime": "harness"})
            assert refused.status_code == 503
            assert "harness" in refused.json()["detail"]
            assert not app.state.sessions

    run(flow())


def test_capabilities_names_the_engines_this_build_can_inject():
    httpx = pytest.importorskip("httpx")
    from odessy.application.api import create_app

    async def flow():
        app = create_app(factory, token="secret")
        async with client(app) as api:
            data = (await api.get("/v1/capabilities")).json()
            assert data["runtime"]["default"] == "legacy"
            assert "legacy" in data["runtime"]["engines"]
            assert "harness" in data["runtime"]["engines"]
            assert data["runtime"]["known"] == ["legacy", "harness"]

    run(flow())


def test_the_snapshot_never_carries_a_credential_through_the_engine_block(monkeypatch):
    httpx = pytest.importorskip("httpx")
    from odessy.application.api import create_app

    secret = "runtime-selection-credential-value"
    monkeypatch.setenv("RUNTIME_SELECTION_TOKEN", secret)

    def secret_controller(**kwargs):
        return SessionController(
            model_factory=lambda name: object(),
            agent_factory=lambda *args, **kwargs: None,
            profiles={"custom": {**PROFILE, "credential_env": "RUNTIME_SELECTION_TOKEN"}},
            selected_profile="custom",
            runtime_selection=kwargs.get("runtime"),
        )

    async def flow():
        app = create_app(secret_controller, token="secret")
        async with client(app) as api:
            body = (await api.post("/v1/sessions", json={"runtime": "legacy"})).json()
            assert secret not in json.dumps(body)

    run(flow())


# ------------------------------------------------- the factory keyword contract


def test_a_factory_declaring_runtime_selection_receives_that_keyword():
    """The API adapts to the factory's name instead of assuming ``runtime``."""
    httpx = pytest.importorskip("httpx")
    from odessy.application.api import create_app

    calls = []

    def keyword_factory(*, restore=None, runtime_selection=None):
        calls.append((restore, runtime_selection))
        return selected_controller(runtime_selection)

    async def flow():
        app = create_app(keyword_factory, token="secret")
        async with client(app) as api:
            created = await api.post("/v1/sessions", json={"runtime": "legacy"})
            assert created.status_code == 201, created.text
            assert created.json()["engine"]["source"] == "explicit"
            assert calls == [(None, "legacy")]

    run(flow())


def test_restore_and_runtime_selection_reach_the_factory_together():
    httpx = pytest.importorskip("httpx")
    from odessy.application.api import create_app

    calls = []

    class Stub:
        id = "a" * 32

        async def prepare(self):
            return None

        def public_snapshot(self):
            return {"id": self.id}

    def factory(*, restore=None, runtime_selection=None):
        calls.append((restore, runtime_selection))
        return Stub()

    async def flow():
        app = create_app(factory, token="secret")
        async with client(app) as api:
            restored = await api.post(
                "/v1/sessions", json={"restore": "a" * 32, "runtime": "legacy"})
            assert restored.status_code == 201, restored.text
            assert calls == [("a" * 32, "legacy")]

    run(flow())


# ------------------------------------------------- the effective default


def test_capabilities_reports_an_environment_selected_default(monkeypatch):
    httpx = pytest.importorskip("httpx")
    from odessy.application.api import create_app

    monkeypatch.setenv("ODESSY_RUNTIME_ENGINE", "harness")

    async def flow():
        app = create_app(factory, token="secret")
        async with client(app) as api:
            data = (await api.get("/v1/capabilities")).json()
            assert data["runtime"]["default"] == "harness"
            assert "error" not in data["runtime"]

    run(flow())


def test_an_invalid_environment_default_is_reported_not_flattened(monkeypatch):
    httpx = pytest.importorskip("httpx")
    from odessy.application.api import create_app

    monkeypatch.setenv("ODESSY_RUNTIME_ENGINE", "telepathy")

    async def flow():
        app = create_app(factory, token="secret")
        async with client(app) as api:
            data = (await api.get("/v1/capabilities")).json()
            assert data["runtime"]["default"] == "legacy"
            assert "telepathy" in data["runtime"]["error"]

    run(flow())


def test_an_omitted_create_uses_the_environment_default(monkeypatch):
    """No per-request choice, a harness environment default, and no backend to inject:
    the request must fail as harness rather than silently produce a legacy session."""
    httpx = pytest.importorskip("httpx")
    from odessy.application.api import create_app

    monkeypatch.setenv("ODESSY_RUNTIME_ENGINE", "harness")
    monkeypatch.delitem(selection._BACKENDS, "harness")

    async def flow():
        app = create_app(factory, token="secret")
        async with client(app) as api:
            refused = await api.post("/v1/sessions")
            assert refused.status_code == 503
            assert "harness" in refused.json()["detail"]
            assert not app.state.sessions

    run(flow())


def test_a_conflicting_environment_default_does_not_block_a_restore(monkeypatch):
    from odessy.application.bootstrap import restored_runtime_selection

    monkeypatch.setenv("ODESSY_RUNTIME_ENGINE", "legacy")
    assert restored_runtime_selection(None, {"engine": {"name": "harness"}}) == "harness"


def test_an_invalid_environment_default_does_not_block_a_restore(monkeypatch):
    from odessy.application.bootstrap import restored_runtime_selection

    monkeypatch.setenv("ODESSY_RUNTIME_ENGINE", "telepathy")
    assert restored_runtime_selection(None, {"engine": {"name": "harness"}}) == "harness"
