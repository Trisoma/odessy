"""Regression coverage for desktop configuration and credential boundaries."""
import asyncio
import json

import httpx
import pytest

from odessy.setup.contracts import SetupSelection


@pytest.mark.parametrize("credential", ["", " ", "\t\n"])
def test_blank_credentials_remain_missing_without_provider_execution(monkeypatch, credential):
    from odessy.setup.diagnostics import observe
    monkeypatch.setenv("OPENAI_API_KEY", credential)
    monkeypatch.setenv("ODESSY_CLOUD_API_KEY", credential)
    selection = SetupSelection.from_dict({"role": "combined", "model": {
        "provider": "openai", "name": "fixture"}})
    assert observe(selection, "llm")["status"] == "missing"


@pytest.mark.parametrize("secret", [123456789, 1.23456789, [123456789], {"opaque": 123456789}])
def test_session_public_redacts_nontext_credentials(secret):
    from odessy.application.session import public
    original = {"password": secret, "ordinary": 42}
    encoded = json.dumps(public(original))
    assert "123456789" not in encoded and "1.23456789" not in encoded
    assert public(original)["ordinary"] == 42
    assert original["password"] == secret


def test_token_usage_measurements_and_unknown_values_remain_typed():
    from odessy.application.session import public
    assert public({"prompt_tokens": 12, "completion_tokens": None}) == {
        "prompt_tokens": 12, "completion_tokens": None}


def test_explicit_credential_reference_never_falls_back_to_another_key(monkeypatch):
    from odessy.setup.diagnostics import observe, probe
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("ODESSY_CLOUD_API_KEY", "unrelated-provider-key")
    chosen = SetupSelection.from_dict({"role": "combined", "model": {
        "provider": "openai", "name": "fixture", "credential_env": "OPENAI_API_KEY"}})
    assert observe(chosen, "llm")["status"] == "missing"
    with pytest.raises(ValueError, match="OPENAI_API_KEY"):
        probe(chosen, "llm", run=True, consent=True)


def test_reviewed_service_uses_the_explicit_credential_reference(tmp_path, monkeypatch):
    from odessy.setup.service_entry import run
    import odessy.application.serve as serve
    monkeypatch.setenv("OPENAI_API_KEY", "selected-key")
    monkeypatch.setenv("ODESSY_CLOUD_API_KEY", "unrelated-key")
    seen = []
    async def fake_server(**kwargs):
        import os
        from odessy.models.cloud_model import build_cloud_model
        seen.append(build_cloud_model(kwargs['backend'])._resolved_api_key())
        # Explicit references must win without injecting keys into global env.
        assert os.environ["ODESSY_CLOUD_API_KEY"] == 'unrelated-key'
    monkeypatch.setattr(serve, "run_server", fake_server)
    chosen = SetupSelection.from_dict({"role": "combined", "model": {
        "provider": "openai", "name": "fixture", "credential_env": "OPENAI_API_KEY"}})
    run(chosen, workspace=tmp_path)
    assert seen == ["selected-key"]


def test_saved_model_change_does_not_relabel_running_service(tmp_path, monkeypatch):
    from odessy.application.api import create_app
    from odessy.setup import configured, component_settings
    monkeypatch.setattr(configured, "desktop_state_file", lambda: tmp_path / "desktop.json")
    monkeypatch.setattr(configured.Path, "home", lambda: tmp_path)

    def save(name):
        current = component_settings.dispatch({"action": "load"})
        data = {"section": "llm", "revision": current["revision"],
                "patch": {"provider": "ollama", "name": name}}
        review = component_settings.dispatch({"action": "review", **data})
        return current, component_settings.dispatch({"action": "save", "confirm": review["plan_id"], **data})

    _, first = save("A")
    startup = first["configuration"]
    app = create_app(lambda: None, token="fixture", runtime_configuration=startup)
    stale, saved = save("B")
    reopened = component_settings.dispatch({"action": "load"})
    assert saved["saved"] is True and saved["restart_required"] is True
    assert reopened["configuration"]["model"]["name"] == "B"
    assert reopened["configuration"]["owner"] == startup["owner"]
    assert reopened["configuration"]["fingerprints"]["llm"] != startup["fingerprints"]["llm"]
    assert reopened["configuration"]["fingerprints"]["tts"] == startup["fingerprints"]["tts"]
    with pytest.raises(ValueError, match="changed"):
        component_settings.dispatch({"action": "review", "section": "llm",
            "revision": stale["revision"], "patch": {"provider": "ollama", "name": "C"}})

    async def check():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test",
                                     headers={"Authorization": "Bearer fixture"}) as client:
            observed = (await client.get("/v1/capabilities")).json()
            assert observed["configuration"] == startup
            assert observed["configuration"]["model"]["name"] == "A"
    asyncio.run(check())
