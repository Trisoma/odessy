"""Model routes and credentials for the Harness engine (prompt 04).

The fixtures here cover the acceptance list: local/cloud routing, a missing key, saved vs
session vs environment precedence, a custom endpoint, an invalid model, and next-turn
selection. They drive Odessy's real credential resolution and the real cloud constructor;
only the runtime process is fake, and the fake is the same one ``test_harness_runtime``
uses so there is one definition of the protocol.
"""

import asyncio
import json
import sys
import threading

import pytest

from odessy.runtime_backends import ModelRouteError, ModelRouteLocked, RuntimeUnavailable
from odessy.runtime_backends.harness import HarnessRuntime
from odessy.runtime_backends.harness.routes import (
    KEY_ENV,
    ROUTE_PATCH_PREFIX,
    ROUTE_PREFIX,
    HarnessRoute,
    compatibility,
    resolve_route,
    route_patch_name,
)

from test_harness_runtime import FAKE_RUNTIME, FakeSession, make_config


class Vault:
    def __init__(self):
        self.values = {}

    def get_password(self, service, account):
        return self.values.get((service, account))

    def set_password(self, service, account, value):
        self.values[(service, account)] = value

    def delete_password(self, service, account):
        self.values.pop((service, account), None)


@pytest.fixture
def credentials(monkeypatch):
    from odessy import credentials as module

    module.clear_sessions()
    vault = Vault()
    monkeypatch.setattr(module, "_backend", lambda: vault)
    yield module
    module.clear_sessions()


@pytest.fixture()
def fake(tmp_path):
    """The same fake runtime ``test_harness_runtime`` uses, declared per directory."""
    script = tmp_path / "fake_runtime.py"
    script.write_text(FAKE_RUNTIME, encoding="utf-8")
    return (sys.executable, str(script))


def cloud_profile(**overrides):
    profile = {"name": "p", "adapter": "cloud", "provider": "deepseek", "model": "deepseek-flash"}
    profile.update(overrides)
    return profile


def run(coroutine):
    return asyncio.run(coroutine)


# -- routing table -----------------------------------------------------------

@pytest.mark.parametrize("provider, api, base", [
    ("deepseek", "openai-completions", "https://api.deepseek.com/v1"),
    ("openai", "openai-completions", "https://api.openai.com/v1"),
    ("qwen", "openai-completions", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
    ("kimi", "openai-completions", "https://api.moonshot.cn/v1"),
    ("claude", "anthropic-messages", "https://api.anthropic.com"),
])
def test_every_cloud_provider_maps_to_a_declared_route(credentials, provider, api, base):
    from odessy.model_providers import CLOUD_PROVIDERS

    environ = {CLOUD_PROVIDERS[provider].api_key_env: "k-literal"}
    route = resolve_route(cloud_profile(provider=provider, model=""), environ=environ)
    assert route.adapter == "cloud"
    assert route.provider == provider
    assert route.api == api
    # The endpoint is the provider preset, unchanged: pi-ai appends the wire path itself.
    assert route.base_url == base
    assert route.model == CLOUD_PROVIDERS[provider].default_model
    assert route.route == ROUTE_PREFIX + provider
    assert json.loads(route.option_value()) == [route.route, route.model]


def test_a_custom_openai_compatible_route_keeps_its_endpoint_and_model(credentials):
    profile = cloud_profile(provider="custom", model="my-local-model",
                            endpoint="http://192.168.1.9:8000/v1")
    route = resolve_route(profile, environ={"ODESSY_CLOUD_API_KEY": "k-literal"})
    assert (route.api, route.base_url, route.model) == (
        "openai-completions", "http://192.168.1.9:8000/v1", "my-local-model")


@pytest.mark.parametrize("adapter", ["ollama", "lfm", "auto"])
def test_local_and_auto_routes_are_refused_visibly_not_substituted(credentials, adapter):
    with pytest.raises(ModelRouteError) as caught:
        resolve_route({"name": "local", "adapter": adapter, "model": "gemma3:1b"},
                      environ={"DEEPSEEK_API_KEY": "k-literal"})
    assert adapter in str(caught.value)
    assert "cannot run on the Harness engine" in str(caught.value)


@pytest.mark.parametrize("backend", ["deepseek", "openai", "claude", "qwen", "kimi"])
def test_the_current_profile_follows_the_configured_cloud_backend(credentials, backend):
    """``current`` carries only a model name; the backend is in the environment.

    ``ODESSY_MODEL_BACKEND=deepseek`` names a *provider*, not an adapter: the adapter is
    ``cloud``. Reading the backend into ``adapter`` refused every cloud ``current`` session
    as an unknown adapter.
    """
    from odessy.model_providers import CLOUD_PROVIDERS

    environ = {"ODESSY_MODEL_BACKEND": backend,
               CLOUD_PROVIDERS[backend].api_key_env: "k-literal"}
    route = resolve_route({"name": "current", "model": "chosen-model"}, environ=environ)
    assert (route.adapter, route.provider) == ("cloud", backend)
    assert route.model == "chosen-model"
    assert route.api in {"openai-completions", "anthropic-messages"}


@pytest.mark.parametrize("backend, named", [("ollama", "ollama"),
                                            ("lfm", "lfm"),
                                            ("llama_cpp", "lfm"),
                                            ("auto", "auto")])
def test_the_current_profile_refuses_a_local_or_auto_backend(credentials, backend, named):
    with pytest.raises(ModelRouteError) as caught:
        resolve_route({"name": "current", "model": "x"},
                      environ={"ODESSY_MODEL_BACKEND": backend})
    assert f"adapter {named!r}" in str(caught.value)


def test_the_compatibility_table_names_every_adapter(credentials):
    table = {row["adapter"]: row for row in compatibility()}
    assert table["cloud"]["status"] == "supported"
    assert table["ollama"]["status"] == "unverified-local"
    assert table["auto"]["status"] == "unsupported"


# -- credentials -------------------------------------------------------------

def test_a_missing_key_is_refused_before_anything_is_launched(credentials):
    with pytest.raises(RuntimeUnavailable) as caught:
        resolve_route(cloud_profile(), environ={})
    assert caught.value.public()["reason"] == "model route 'deepseek' is not usable"
    assert "DEEPSEEK_API_KEY" in caught.value.public()["detail"]


def test_environment_is_used_when_nothing_is_saved(credentials):
    route = resolve_route(cloud_profile(), environ={"DEEPSEEK_API_KEY": "from-env"})
    assert route.api_key == "from-env"
    assert route.public()["credential"] == KEY_ENV
    assert "from-env" not in json.dumps(route.public())


def test_a_saved_or_session_key_beats_the_environment(credentials):
    from odessy.credentials import Scope

    scope = Scope("deepseek", "https://api.deepseek.com/v1")
    credentials.set_value(scope, "saved-key", storage="keyring", environ={})
    environ = {"DEEPSEEK_API_KEY": "from-env"}
    assert resolve_route(cloud_profile(), environ=environ).api_key == "saved-key"

    credentials.set_value(scope, "session-key", storage="session", environ=environ)
    assert resolve_route(cloud_profile(), environ=environ).api_key == "session-key"


def test_the_child_receives_only_the_resolved_value_under_one_name(credentials, tmp_path, fake):
    from odessy.runtime_backends.harness.runtime import attach_route

    credentials.set_value(credentials.Scope("deepseek", "https://api.deepseek.com/v1"),
                          "resolved-secret", storage="session", environ={})
    config = make_config(tmp_path, fake)
    route = resolve_route(cloud_profile(), environ={"DEEPSEEK_API_KEY": "ignored-env"})
    attached = attach_route(config, route)
    env = attached.child_env({"PATH": "C:/bin", "DEEPSEEK_API_KEY": "ignored-env"})
    assert env[KEY_ENV] == "resolved-secret"
    # The ambient provider variable is not forwarded, so it cannot override the choice.
    assert "DEEPSEEK_API_KEY" not in env
    assert dict(attached.extra_env) == {KEY_ENV: "resolved-secret"}
    assert "resolved-secret" not in json.dumps(attached.describe())
    assert attached.route_patch is not None
    assert attached.route_patch.name == route_patch_name(route)
    assert attached.route_patch.name.startswith(ROUTE_PATCH_PREFIX)
    assert attached.route_patch.read_text(encoding="utf-8") == route.overlay_text()
    assert attached.ordered_patches()[-1].name == "odessy_narrow.patch.yml"


def test_an_explicit_credential_reference_is_honoured(credentials):
    route = resolve_route(cloud_profile(credential_env="CUSTOM_MODEL_CREDENTIAL"),
                          environ={"DEEPSEEK_API_KEY": "ignored", "CUSTOM_MODEL_CREDENTIAL": "mine"})
    assert route.api_key == "mine"


def test_an_invalid_endpoint_is_refused_with_the_existing_validation(credentials):
    for endpoint in ("ftp://api.example/v1", "https://user:pass@api.example/v1",
                     "https://api.example/v1?x=1"):
        with pytest.raises(RuntimeUnavailable) as caught:
            resolve_route(cloud_profile(provider="custom", model="m", endpoint=endpoint),
                          environ={"ODESSY_CLOUD_API_KEY": "k-literal"})
        assert "not usable" in caught.value.public()["reason"]


def test_an_unknown_reasoning_level_is_refused(credentials):
    with pytest.raises(ModelRouteError) as caught:
        resolve_route(cloud_profile(), environ={"DEEPSEEK_API_KEY": "k",
                                                "ODESSY_CLOUD_EFFORT": "turbo"})
    assert "not a level this runtime accepts" in str(caught.value)


def test_an_accepted_reasoning_level_travels_into_the_route(credentials):
    route = resolve_route(cloud_profile(), environ={"DEEPSEEK_API_KEY": "k",
                                                    "ODESSY_CLOUD_EFFORT": "low"})
    assert route.reasoning == "low"
    assert '"reasoning": "low"' in route.overlay_text()


# -- the overlay the runtime is launched with --------------------------------

def test_the_overlay_declares_one_route_and_references_the_key_by_name(credentials, tmp_path):
    route = resolve_route(cloud_profile(), environ={"DEEPSEEK_API_KEY": "secret-value"})
    text = route.overlay_text()
    assert "- id: llm-pi-ai" in text
    assert f'"apiKeyEnv": "{KEY_ENV}"' in text
    assert "secret-value" not in text          # the file is safe to read
    config = json.loads(text.split("config: ", 1)[1])
    declared = config["providers"][route.route]
    assert declared["api"] == "openai-completions"
    assert declared["baseURL"] == route.base_url
    assert [model["id"] for model in declared["models"]] == [route.model]
    assert list(config["providers"]) == [route.route]


def test_sibling_profiles_of_the_same_provider_are_declared_up_front(credentials, tmp_path, fake):
    session = FakeSession(cloud_profile(model="mock-model"))
    session.model_profiles = [cloud_profile(model="mock-model"),
                              cloud_profile(name="second", model="other-model"),
                              cloud_profile(name="other-provider", provider="openai", model="gpt-5")]
    runtime = HarnessRuntime(session, config=make_config(tmp_path, fake))
    assert runtime.route.extra_models == ("other-model",)
    declared = json.loads(runtime.route.overlay_text().split("config: ", 1)[1])
    models = [model["id"] for model in declared["providers"][runtime.route.route]["models"]]
    assert models == ["mock-model", "other-model"]  # a model change needs no relaunch
    assert "gpt-5" not in models                    # another provider is not this route


# -- the runtime applies the route -------------------------------------------

def harness_runtime(tmp_path, fake, profile=None):
    return HarnessRuntime(FakeSession(profile), config=make_config(tmp_path, fake))


# -- one route file per session, not a shared mutable one --------------------

def test_two_sessions_with_different_endpoints_get_their_own_overlay(credentials, tmp_path, fake):
    home = tmp_path / "shared-home"
    profile_a = cloud_profile(model="mock-model", endpoint="http://10.0.0.1:8000/v1", name="a")
    profile_b = cloud_profile(model="mock-model", endpoint="http://10.0.0.2:8000/v1", name="b")
    for name in ("a", "b", "c"):
        (tmp_path / name).mkdir()
    config_a = make_config(tmp_path / "a", fake, home=home)
    config_b = make_config(tmp_path / "b", fake, home=home)
    first = HarnessRuntime(FakeSession(profile_a), config=config_a)
    second = HarnessRuntime(FakeSession(profile_b), config=config_b)

    assert first.config.route_patch != second.config.route_patch
    assert first.config.route_patch.read_text(encoding="utf-8") == first.route.overlay_text()
    assert second.config.route_patch.read_text(encoding="utf-8") == second.route.overlay_text()
    assert "10.0.0.1" in first.config.route_patch.read_text(encoding="utf-8")
    assert "10.0.0.2" in second.config.route_patch.read_text(encoding="utf-8")
    # Content-addressed: the same route in the same home reuses one file.
    again = HarnessRuntime(FakeSession(profile_a), config=make_config(tmp_path / "c", fake, home=home))
    assert again.config.route_patch == first.config.route_patch


def test_concurrent_sessions_with_different_endpoints_do_not_interleave(credentials, tmp_path, fake):
    home = tmp_path / "race-home"
    profiles = [cloud_profile(model="mock-model",
                              endpoint=f"http://10.1.1.{index}:8000/v1", name=f"p{index}")
                for index in range(1, 5)]
    configs, runtimes, errors = [], [None] * len(profiles), []
    for index in range(len(profiles)):
        (tmp_path / f"w{index}").mkdir()
        configs.append(make_config(tmp_path / f"w{index}", fake, home=home))

    barrier = threading.Barrier(len(profiles))

    def build(index):
        try:
            barrier.wait(timeout=20)
            runtimes[index] = HarnessRuntime(FakeSession(profiles[index]), config=configs[index])
        except BaseException as exc:  # noqa: BLE001 - a raise here is the failure
            errors.append(exc)

    threads = [threading.Thread(target=build, args=(index,)) for index in range(len(profiles))]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert errors == []
    assert all(runtime is not None for runtime in runtimes)
    assert len({runtime.config.route_patch for runtime in runtimes}) == len(runtimes)
    for runtime in runtimes:
        text = runtime.config.route_patch.read_text(encoding="utf-8")
        assert runtime.route.base_url in text
        assert sum(1 for other in runtimes if other is not runtime
                   and other.route.base_url in text) == 0


# -- a model change may not smuggle in other settings ------------------------

def test_a_change_to_launch_fixed_settings_is_refused_not_recorded(
        credentials, tmp_path, fake, monkeypatch):
    runtime = harness_runtime(tmp_path, fake)
    run(runtime.start())
    launched = runtime.route
    try:
        # Same provider and credential, but another endpoint: the overlay is already
        # generated, so accepting it would report a base URL the provider never sees.
        with pytest.raises(ModelRouteError) as caught:
            runtime.select_route({**cloud_profile(model="mock-model"),
                                  "endpoint": "http://10.9.9.9:8000/v1"}, profile_name="p")
        assert "base_url" in str(caught.value)
        assert runtime.route == launched  # nothing recorded, nothing applied

        # The launch-fixed settings also include the output ceiling: changing the value the
        # candidate resolves to (here via the environment it resolves in) is refused too.
        monkeypatch.setenv("ODESSY_CLOUD_OPENAI_MAX_TOKENS", "77")
        with pytest.raises(ModelRouteError) as caught:
            runtime.select_route(cloud_profile(model="mock-model-2"), profile_name="p")
        assert "max_tokens" in str(caught.value)
        assert runtime.route == launched
    finally:
        run(runtime.close())


def test_a_legitimate_model_change_keeps_every_other_setting(credentials, tmp_path, fake):
    runtime = harness_runtime(tmp_path, fake)
    run(runtime.start())
    launched = runtime.route
    try:
        applied = runtime.select_route(cloud_profile(model="mock-model-2"), profile_name="p")
        assert applied["model"] == "mock-model-2"
        assert runtime.route.model == "mock-model-2"
        assert (runtime.route.base_url, runtime.route.max_tokens, runtime.route.api,
                runtime.route.reasoning) == (launched.base_url, launched.max_tokens,
                                             launched.api, launched.reasoning)
        # What the fake runtime was actually told is the new pair, and only that.
        assert json.loads(runtime._model_option) == [launched.route, "mock-model-2"]
    finally:
        run(runtime.close())


def test_readiness_selects_the_declared_pair(credentials, tmp_path, fake):
    runtime = harness_runtime(tmp_path, fake)

    async def flow():
        try:
            await runtime.start()
            return runtime._model_option
        finally:
            await runtime.close()

    assert run(flow()) == json.dumps([runtime.route.route, "mock-model"], separators=(",", ":"))


def test_an_invalid_model_is_refused_at_readiness(credentials, tmp_path, fake):
    profile = cloud_profile(model="not-offered")
    runtime = HarnessRuntime(FakeSession(profile), config=make_config(tmp_path, fake))
    with pytest.raises(RuntimeUnavailable) as caught:
        run(runtime.start())
    assert "does not offer odessy-deepseek/not-offered" in caught.value.public()["detail"]
    assert runtime._process is None


def test_a_model_change_applies_to_the_next_turn_and_not_while_running(credentials, tmp_path, fake):
    runtime = harness_runtime(tmp_path, fake)

    async def flow():
        try:
            await runtime.start()
            first = await runtime.run_turn("hello")
            # Same provider, other declared model: applied through the interface.
            applied = runtime.select_route(cloud_profile(model="mock-model-2"), profile_name="p")
            second = await runtime.run_turn("hello again")
            return first, applied, second
        finally:
            await runtime.close()

    first, applied, second = run(flow())
    assert first.final_text == "Hello there"
    assert applied["model"] == "mock-model-2"
    assert second.final_text == "Hello there"
    assert any("model -> mock-model-2" in line for line in runtime.diagnostics())


def test_a_provider_change_is_refused_instead_of_half_applied(credentials, tmp_path, fake):
    runtime = harness_runtime(tmp_path, fake)
    run(runtime.start())
    try:
        with pytest.raises(ModelRouteLocked) as caught:
            runtime.select_route(cloud_profile(provider="openai", model="gpt-5"), profile_name="p")
        assert caught.value.current == "odessy-deepseek"
        assert caught.value.requested == "odessy-openai"
        assert runtime.route.provider == "deepseek"  # unchanged
    finally:
        run(runtime.close())


def test_a_model_the_session_did_not_declare_is_refused(credentials, tmp_path, fake):
    runtime = harness_runtime(tmp_path, fake)
    run(runtime.start())
    try:
        with pytest.raises(ModelRouteError) as caught:
            runtime.select_route(cloud_profile(model="another-model"), profile_name="p")
        assert "not declared for this session" in str(caught.value)
    finally:
        run(runtime.close())


def test_selection_is_refused_while_a_turn_is_running(credentials, tmp_path, fake):
    runtime = harness_runtime(tmp_path, fake)
    run(runtime.start())
    try:
        runtime._busy = True  # the state a running turn holds
        with pytest.raises(ModelRouteError) as caught:
            runtime.select_route(cloud_profile(model="mock-model-2"), profile_name="p")
        assert "while a turn is running" in str(caught.value)
    finally:
        runtime._busy = False
        run(runtime.close())


def test_one_session_switching_models_does_not_touch_another(credentials, tmp_path, fake):
    for name in ("a", "b"):
        (tmp_path / name).mkdir()
    first = HarnessRuntime(FakeSession(cloud_profile(model="mock-model")),
                           config=make_config(tmp_path / "a", fake))
    second = HarnessRuntime(FakeSession(cloud_profile(model="mock-model")),
                            config=make_config(tmp_path / "b", fake))

    async def flow():
        await first.start()
        await second.start()
        return second._process

    second_process = run(flow())
    try:
        first.select_route(cloud_profile(model="mock-model-2"), profile_name="p")
        assert first.route.model == "mock-model-2"
        assert second.route.model == "mock-model"
        # The other session's process is the same object: no relaunch was triggered.
        assert second._process is second_process
        assert second._process.alive
    finally:
        run(first.close())
        run(second.close())


def test_context_usage_is_recorded_and_no_tokens_are_invented(credentials, tmp_path, fake):
    runtime = harness_runtime(tmp_path, fake)

    async def flow():
        try:
            result = await runtime.run_turn("hello")
            return result
        finally:
            await runtime.close()

    result = run(flow())
    assert runtime.context_usage == {"used": 1234, "size": 32768}
    # The ACP channel reports context utilisation, not prompt/completion tokens. Odessy's
    # ledger therefore stays empty for this turn: cost remains "unknown", never invented.
    assert result.metrics.prompt_tokens is None
    assert result.metrics.completion_tokens is None


# -- HTTP mapping ------------------------------------------------------------

def test_http_maps_route_errors_to_the_right_status(monkeypatch, tmp_path, fake):
    httpx = pytest.importorskip("httpx")
    pytest.importorskip("fastapi")
    from odessy.application.api import create_app
    from odessy.application.session import SessionController
    from odessy.runtime_backends import selection as selection_module

    config = make_config(tmp_path, fake)
    monkeypatch.setitem(selection_module._BACKENDS, "harness",
                        lambda session: HarnessRuntime(session, config=config))

    class Model:
        pass

    def factory():
        return SessionController(
            model_factory=lambda name: Model(), agent_factory=lambda *a, **k: None,
            profiles={"local": cloud_profile(model="mock-model")},
            runtime_selection="harness",
        )

    async def flow():
        app = create_app(factory, token="secret")
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="http://test",
                                     headers={"Authorization": "Bearer secret"}) as client:
            created = await client.post("/v1/sessions")
            assert created.status_code == 201
            sid = created.json()["id"]
            locked = await client.put(f"/v1/sessions/{sid}/model",
                                      json={"profile": "local"})  # same route: allowed
            unknown = await client.put(f"/v1/sessions/{sid}/model",
                                       json={"profile": "missing"})
            await client.delete(f"/v1/sessions/{sid}")
            return locked.status_code, unknown.status_code

    assert run(flow()) == (200, 400)
