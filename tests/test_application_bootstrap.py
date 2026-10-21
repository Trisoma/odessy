import asyncio
from contextlib import asynccontextmanager

import pytest
from mcp.server import MCPServer

from odessy import cli
from odessy.application.bootstrap import create_controller
from odessy.collaboration.config import CollaborationConfig
from odessy.config.deployment import model_tool_scope
from odessy.models.fake_model import FakeModel
from odessy.runtime.agent_loop import ExecutionTarget


@pytest.mark.anyio
async def test_resource_scope_can_close_from_a_later_http_task(monkeypatch, tmp_path):
    closed = asyncio.Event()
    server = MCPServer("scope-test")

    @asynccontextmanager
    async def tools(*args, **kwargs):
        with model_tool_scope(["time.now"]):
            try:
                yield (ExecutionTarget(server),)
            finally:
                closed.set()

    monkeypatch.setattr(cli, "_tool_servers", tools)
    monkeypatch.setattr("odessy.application.bootstrap.load_config", lambda: CollaborationConfig())
    controller = await create_controller(workspace=tmp_path, model=FakeModel())
    await asyncio.create_task(controller.close())
    assert closed.is_set()


@pytest.mark.anyio
async def test_fake_model_profiles_and_peer_never_start_real_providers(monkeypatch, tmp_path):
    from odessy.collaboration.config import CollaborationSettings, ParticipantProfile

    server = MCPServer("offline")

    @asynccontextmanager
    async def tools(*args, **kwargs):
        yield (ExecutionTarget(server),)

    config = CollaborationConfig(CollaborationSettings(enabled=True), {
        "cloud": ParticipantProfile("cloud", "cloud", model="not-a-real-model", credential_env="MISSING_KEY")})
    monkeypatch.setattr(cli, "_tool_servers", tools)
    monkeypatch.setattr("odessy.application.bootstrap.load_config", lambda: config)
    controller = await create_controller(workspace=tmp_path, model=FakeModel(), use_fake_model=True)
    try:
        controller.select_model("cloud")
        assert isinstance(controller._model, FakeModel)
        response = await asyncio.to_thread(controller.collaboration.consult,
            participant="cloud", question="Anything", explicit=True)
        assert response["status"] == "ok"
        assert "Fake participant cloud" in response["answer"]
    finally:
        await controller.close()
