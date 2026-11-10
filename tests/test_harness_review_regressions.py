"""Regression cases for PR 62's independently verified lifecycle findings."""
import asyncio
import os
import threading
from dataclasses import replace
from types import SimpleNamespace
import pytest
from test_harness_runtime import FakeSession, fake, make_config
from odessy.runtime_backends.harness import HarnessRuntime
from odessy.runtime_backends.harness.runtime import _sibling_models
from odessy.runtime_backends.harness.process import HarnessProcess
from odessy.runtime_backends import ModelRouteError, RuntimeUnavailable
from odessy.application.session import SessionController


def configured(tmp_path, fake, monkeypatch, mode='normal', **kwargs):
    """Give each fake process its own launch settings and no optional plugin."""
    monkeypatch.setenv('FAKE_MODE', mode)
    monkeypatch.setenv('FAKE_HOLD', '2')
    config = make_config(tmp_path, fake, passthrough=('FAKE_MODE', 'FAKE_HOLD'), **kwargs)
    return replace(config, source_env={**os.environ, 'ODESSY_HARNESS_BRIDGE': 'off'})


def test_prompt_timeout_reaps_the_child_before_releasing_admission(tmp_path, fake, monkeypatch):
    """A lost ACP waiter must not permit another turn against still-running work."""
    runtime = HarnessRuntime(FakeSession(), config=configured(
        tmp_path, fake, monkeypatch, mode='hold', turn_timeout=.1))
    async def flow():
        try:
            with pytest.raises(RuntimeUnavailable, match='turn failed'):
                await runtime.run_turn('hello')
            assert not runtime._process.alive
            with pytest.raises(RuntimeUnavailable, match='closed'):
                await runtime.run_turn('another')
        finally:
            await runtime.close()
    asyncio.run(flow())


def test_cancelled_startup_does_not_permanently_abandon_the_session(tmp_path, fake, monkeypatch):
    """Stop may discard a startup attempt, then the same session can start again."""
    runtime = HarnessRuntime(FakeSession(), config=configured(
        tmp_path, fake, monkeypatch, mode='slowinit'))
    async def flow():
        try:
            start = asyncio.create_task(runtime.start())
            await asyncio.sleep(.1)
            start.cancel()
            with pytest.raises(asyncio.CancelledError):
                await start
            assert not runtime._abandoned
            await runtime.start()
            assert (await runtime.run_turn('hello')).final_text == 'Hello there'
        finally:
            await runtime.close()
    asyncio.run(flow())


def test_route_switch_rejects_an_orphaned_turn_lock(tmp_path, fake, monkeypatch):
    """An abandoned caller does not make a still-owned ACP session reconfigurable."""
    runtime = HarnessRuntime(FakeSession(), config=configured(tmp_path, fake, monkeypatch))
    runtime._turn_lock.acquire()
    try:
        with pytest.raises(ModelRouteError, match='turn is running'):
            runtime.select_route({'adapter': 'cloud', 'provider': 'deepseek', 'model': 'mock-model'})
    finally:
        runtime._turn_lock.release()
        asyncio.run(runtime.close())


def test_inferred_current_provider_declares_its_sibling_models():
    """The bootstrap current profile need not carry a redundant provider field."""
    session = SimpleNamespace(model_profiles=[{'adapter': 'cloud', 'provider': 'deepseek', 'model': 'second'}])
    assert _sibling_models(session, {'name': 'current', 'model': 'first'},
                           {'ODESSY_MODEL_BACKEND': 'deepseek'}) == ('second',)


def test_shutdown_is_bounded_while_a_writer_holds_the_pipe_lock(tmp_path, fake, monkeypatch):
    """A blocked write must not prevent the shutdown deadline from reaching tree kill."""
    process = HarnessProcess(configured(tmp_path, fake, monkeypatch, shutdown_timeout=.1))
    process.start()
    process._write_lock.acquire()
    async def flow():
        try:
            await asyncio.wait_for(asyncio.to_thread(process.close), 5)
            assert not process.alive
        finally:
            process._write_lock.release()
            process.close()
    asyncio.run(flow())


def test_model_change_keeps_the_application_event_loop_responsive():
    """A slow set_config_option waits off-loop while the controller publishes on-loop."""
    release, entered = threading.Event(), threading.Event()
    def select(*args, **kwargs):
        entered.set()
        release.wait(3)
    controller = SimpleNamespace(engine='harness', _closed=False, profiles={'p': {}},
        runtime=SimpleNamespace(select_route=select), _validate_profile_name=lambda p: None,
        emit=lambda *args: None)
    async def flow():
        task = asyncio.create_task(SessionController.select_model_async(controller, 'p'))
        try:
            assert await asyncio.to_thread(entered.wait, 1)
            await asyncio.sleep(.01)
            assert not task.done()
            release.set()
            assert await task == 'p'
        finally:
            release.set()
            await task
    asyncio.run(flow())

@pytest.mark.parametrize("endpoint", ['/v1/lifecycle/stop-all', '/v1/sessions/s/stop'])
def test_stop_all_does_not_cancel_a_newly_admitted_turn(endpoint):
    """A Stop awaiting the old task must not look up and cancel its replacement."""
    import httpx
    from odessy.application.api import create_app
    async def flow():
        app = create_app(lambda: None, token='secret')
        old = asyncio.create_task(asyncio.Event().wait())
        new = None
        async def stop():
            nonlocal new
            old.cancel()
            await asyncio.gather(old, return_exceptions=True)
            new = asyncio.create_task(asyncio.Event().wait())
            app.state.turn_tasks['s'] = new
            return {'execution': 'unconfirmed', 'cleanup': 'pending'}
        app.state.sessions['s'] = SimpleNamespace(stop=stop, engine='harness')
        app.state.turn_tasks['s'] = old
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                    base_url='http://test', headers={'Authorization': 'Bearer secret'}) as client:
                response = await client.post(endpoint, json={
                    'service_instance_id': app.state.service_instance_id,
                    'reason': 'operator stop', 'confirm': True})
                assert response.status_code == 200
                assert new is not None and not new.done()
        finally:
            old.cancel()
            if new is not None:
                new.cancel()
            await asyncio.gather(*[task for task in (old, new) if task], return_exceptions=True)
    asyncio.run(flow())


def test_refused_server_request_is_not_reparsed_as_a_reply(tmp_path, fake, monkeypatch):
    """A valid refused request is not also a malformed or unknown response."""
    process = HarnessProcess(configured(tmp_path, fake, monkeypatch))
    writes = []
    monkeypatch.setattr(process, '_write', writes.append)
    process._dispatch({'jsonrpc': '2.0', 'id': 'server-request', 'method': 'unsupported'})
    assert process.malformed_frames == 0
    assert len(process.refused_requests()) == 1
    assert len(writes) == 1 and writes[0]['error']['code'] == -32601
    assert not any('reply' in item for item in process.diagnostics())
