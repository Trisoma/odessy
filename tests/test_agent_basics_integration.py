import asyncio

import httpx
import pytest

from odessy.application.api import create_app
from odessy.application.session import SessionController
from odessy.runtime.types import AgentTurnResult


def test_normal_service_persists_and_explicitly_restores_context(tmp_path):
    seen = []

    class Agent:
        def __init__(self, model, **kwargs):
            pass

        async def run_turn(self, text, **kwargs):
            seen.append(kwargs['context'])
            return AgentTurnResult('answer:' + text, [])

    def factory():
        return SessionController(model_factory=lambda _: object(), agent_factory=Agent,
                                 profiles={'local': {}})

    async def run():
        path = tmp_path / 'profile.env'
        app = create_app(factory, token='fixture', config_path=path,
                         conversations_enabled=True)
        headers = {'Authorization': 'Bearer fixture'}
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url='http://test', headers=headers) as client:
            live = (await client.post('/v1/sessions')).json()
            cid = live['conversation_id']
            await client.post(f"/v1/sessions/{live['id']}/turns", json={'text': 'remember boat'})
            await asyncio.gather(*app.state.turn_tasks.values())
            row = (await client.get('/v1/conversations/' + cid)).json()
            assert (await client.delete('/v1/sessions/' + live['id'])).status_code == 204
            assert (await client.get('/v1/conversations/' + cid)).status_code == 200
        app.state.conversations.store.close()
        other = create_app(factory, token='fixture', config_path=path,
                           conversations_enabled=True)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=other),
                                     base_url='http://test', headers=headers) as client:
            assert (await client.get('/v1/conversations')).json()['conversations'][0]['id'] == cid
            assert other.state.sessions == {}
            restored = await client.post('/v1/conversations/' + cid + '/resume',
                                         json={'expected_revision': row['revision']})
            assert restored.status_code == 200, restored.text
            sid = restored.json()['session_id']
            assert restored.json()['service_instance_id'] == other.state.service_instance_id
            assert sid != live['id']
            mode = (await client.get(f'/v1/sessions/{sid}/access-mode')).json()
            assert mode['mode'] == 'ask'
            await client.post(f'/v1/sessions/{sid}/turns', json={'text': 'what boat?'})
            await asyncio.gather(*other.state.turn_tasks.values())
            assert any(turn.user == 'remember boat' for turn in seen[-1].turns)
            await client.delete('/v1/sessions/' + sid)
        other.state.conversations.store.close()

    asyncio.run(run())


def test_public_deltas_survive_restart_without_exporting_duplicate_text(tmp_path):
    from odessy.application.conversations import build_service, settle_after_restart
    from odessy.application.conversations.lifecycle import ConversationRecorder
    service = build_service(path=tmp_path / 'conversations.sqlite3')
    store = service.store
    project = store.create_project('fixture')
    cid = 'e' * 32
    store.create(conversation_id=cid, project_id=project['id'], engine='legacy',
                 profile='local', title='fixture')
    recorder = ConversationRecorder(service, cid)
    recorder.begin_turn('hello')
    recorder.public_event('delta', {'text': 'partial boat'})
    recorder.public_event('tool', {'name': 'filesystem.read', 'phase': 'started',
                                 'arguments': {'secret': 'must not persist'}})
    assert settle_after_restart(store) == 1
    assert settle_after_restart(store) == 0
    events = store.raw_events(cid)
    assert events[-1]['payload']['text'] == 'partial boat'
    assert 'must not persist' not in str(events)
    assert all(event['kind'] != 'assistant_delta' for event in store.export(cid)['events'])
    store.close()


def test_context_resolver_refuses_revoked_workspace_and_cross_conversation(tmp_path):
    from types import SimpleNamespace
    from odessy.application.agent_basics import PreparedContext
    from odessy.authority.policy import OdessyPolicy
    from odessy.application.conversations.store import Conflict
    from odessy.application.workspace_inspector import InspectorError

    async def run():
        allowed = True
        resource = asyncio.create_task(asyncio.Event().wait())
        def factory():
            controller = SessionController(model_factory=lambda _: object(),
                agent_factory=lambda *a, **k: None, profiles={'local': {}})
            controller.resources = SimpleNamespace(task=resource, closing=asyncio.Event())
            controller._workspace_root = tmp_path
            controller._workspace_policy_factory = lambda: OdessyPolicy(tmp_path)
            controller._workspace_authorized = lambda: allowed
            return controller
        app = create_app(factory, token='fixture', config_path=tmp_path / 'profile.env',
                         conversations_enabled=True)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
             base_url='http://test', headers={'Authorization': 'Bearer fixture'}) as client:
            live = (await client.post('/v1/sessions')).json()
            controller = app.state.sessions[live['id']]
            composition = app.state.basics_composition
            class Provider:
                def prepare(self, cid, **kwargs):
                    nonlocal allowed
                    allowed = False
                    return PreparedContext(cid, 0, citations=({'text': 'not authorized'},))
            composition.context_provider = Provider()
            with pytest.raises(Conflict):
                composition.resolve_context_scope(controller.id, 'f' * 32)
            with pytest.raises(InspectorError):
                composition.prepare_context(controller, 'hello')
            await client.delete('/v1/sessions/' + controller.id)
        resource.cancel()
        await asyncio.gather(resource, return_exceptions=True)
        app.state.conversations.store.close()
    asyncio.run(run())


def test_resume_rejects_changed_workspace_and_reuses_monitor_lifecycle(tmp_path):
    roots = [tmp_path / 'a', tmp_path / 'b']
    for root in roots:
        root.mkdir()
    root = roots[0]
    made = []
    attached = []

    class Monitor:
        async def attach(self, controller):
            attached.append(controller.id)

    def factory():
        from odessy.authority.policy import OdessyPolicy
        controller = SessionController(model_factory=lambda _: object(),
            agent_factory=lambda *a, **k: None, profiles={'local': {}})
        controller._workspace_root = root
        controller._workspace_policy_factory = lambda: OdessyPolicy(root)
        made.append(controller)
        return controller

    async def run():
        nonlocal root
        app = create_app(factory, token='fixture', config_path=tmp_path / 'profile.env',
                         conversations_enabled=True, monitoring=Monitor())
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
             base_url='http://test', headers={'Authorization': 'Bearer fixture'}) as client:
            live = (await client.post('/v1/sessions')).json()
            cid = live['conversation_id']
            row = (await client.get('/v1/conversations/' + cid)).json()
            await client.delete('/v1/sessions/' + live['id'])
            root = roots[1]
            response = await client.post('/v1/conversations/' + cid + '/resume',
                                        json={'expected_revision': row['revision']})
            assert response.status_code == 409, response.text
            assert made[-1]._closed
            assert made[-1].id in attached
            assert app.state.sessions == {}
            root = roots[0]
            response = await client.post('/v1/conversations/' + cid + '/resume',
                                        json={'expected_revision': row['revision']})
            assert response.status_code == 200, response.text
            assert response.json()['session_id'] in attached
            await client.delete('/v1/sessions/' + response.json()['session_id'])
        app.state.conversations.store.close()
    asyncio.run(run())


@pytest.mark.parametrize('mode', ['ask', 'auto', 'full'])
def test_mode_uses_real_prepare_and_dispatch_barrier(tmp_path, mode):
    from types import SimpleNamespace
    from odessy.authority.policy import OdessyPolicy
    from odessy.runtime.agent_loop import OdessyAgent, ExecutionTarget
    from odessy.runtime.types import ToolRequest, AgentTurnMetrics
    from odessy.application.workspace_inspector import bind_write_review
    import mcp.types as types

    async def run():
        path = tmp_path / 'note.txt'
        path.write_text('before', encoding='utf-8')
        policy = OdessyPolicy(tmp_path)
        async def pending_resource():
            await asyncio.Event().wait()
        resource = asyncio.create_task(pending_resource())
        def factory():
            controller = SessionController(model_factory=lambda _: object(),
                agent_factory=lambda *a, **k: None, profiles={'local': {}})
            controller.resources = SimpleNamespace(task=resource, closing=asyncio.Event())
            controller._workspace_policy_factory = lambda: policy
            controller._workspace_root = tmp_path
            controller._workspace_authorized = lambda: True
            return controller
        app = create_app(factory, token='fixture', config_path=tmp_path / 'profile.env',
                         conversations_enabled=True)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
            base_url='http://test', headers={'Authorization': 'Bearer fixture'}) as client:
            sid = (await client.post('/v1/sessions')).json()['id']
            controller = app.state.sessions[sid]
            modes = app.state.access_modes
            state = modes.snapshot(sid)
            updated = await client.put(f'/v1/sessions/{sid}/access-mode', json={
                'mode':mode, 'expected_revision':state['revision'],
                'service_instance_id':app.state.service_instance_id})
            assert updated.status_code == 200, updated.text
            asked = []
            async def confirm(plan):
                asked.append(plan)
                return True
            class Server:
                async def call_tool(self, name, arguments, *, before_dispatch):
                    before_dispatch()
                    path.write_text(arguments['content'], encoding='utf-8')
                    return types.CallToolResult(content=[], structuredContent={'status':'ok'})
            server = Server()
            agent = OdessyAgent(model=object(), policy=policy,
                mcp_server=(ExecutionTarget(server),), confirm_action=confirm)
            agent._basics_session = controller
            async def bind(plan, request):
                return await bind_write_review(plan, request, app.state.workspace_inspector, sid)
            agent._bind_action_review = bind
            registry={'filesystem.write': (SimpleNamespace(name='filesystem.write'), server)}
            controller._conversation_recorder.begin_turn('write note')
            request=ToolRequest('filesystem.write', {'path':'note.txt','content':'after'})
            args=await agent._prepare_call(request,registry,[],AgentTurnMetrics())
            await agent._call_tool(request.name,args,registry)
            assert path.read_text(encoding='utf-8') == 'after'
            assert bool(asked) == (mode == 'ask')
            # Another prepared call cannot dispatch after a mode revision changes.
            next_request=ToolRequest('filesystem.write', {'path':'note.txt','content':'revoked'})
            next_args=await agent._prepare_call(next_request,registry,[],AgentTurnMetrics())
            current=modes.snapshot(sid)
            modes.set_mode(sid, mode='ask', expected_revision=current['revision'],
                           service_instance_id=app.state.service_instance_id)
            with pytest.raises(PermissionError):
                await agent._call_tool(next_request.name,next_args,registry)
            assert path.read_text(encoding='utf-8') == 'after'
            events=app.state.conversations.store.raw_events(controller._conversation_id)
            assert any(e['kind']=='tool_call' for e in events)
            assert any(e['kind']=='tool_result' for e in events)
            await client.delete('/v1/sessions/' + sid)
        resource.cancel()
        await asyncio.gather(resource,return_exceptions=True)
        app.state.conversations.store.close()
    asyncio.run(run())


def test_history_only_harness_does_not_start_runtime(tmp_path):
    def forbidden():
        raise AssertionError('History access cannot start any engine')
    async def run():
        app=create_app(forbidden,token='fixture',config_path=tmp_path/'profile.env',
                       conversations_enabled=True)
        store=app.state.conversations.store
        project=store.create_project('Harness project')
        row=store.create(conversation_id='a'*32,project_id=project['id'],
                         engine='harness',profile='current',title='saved')
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
             base_url='http://test',headers={'Authorization':'Bearer fixture'}) as client:
            assert (await client.get('/v1/conversations/' + row['id'])).json()['continuation']=='history_only'
            response=await client.post('/v1/conversations/'+row['id']+'/resume',
                                       json={'expected_revision':row['revision']})
            assert response.status_code==501
            assert app.state.sessions=={}
        store.close()
    asyncio.run(run())


def test_storage_failure_refuses_model_and_context_seam_is_real(tmp_path):
    from odessy.application.agent_basics import PreparedContext
    from odessy.runtime.types import ConversationTurn
    from odessy.application.conversations.store import StorageError
    seen=[]
    class Agent:
        def __init__(self,*a,**k):pass
        async def run_turn(self,text,**kwargs):
            seen.append(kwargs['context'])
            return AgentTurnResult('done',[])
    def factory():
        return SessionController(model_factory=lambda _:object(),agent_factory=Agent,
                                 profiles={'current':{}})
    async def run():
        app=create_app(factory,token='fixture',config_path=tmp_path/'profile.env',
                       conversations_enabled=True)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
           base_url='http://test',headers={'Authorization':'Bearer fixture'}) as client:
            live=(await client.post('/v1/sessions')).json()
            controller=app.state.sessions[live['id']]
            store=app.state.conversations.store
            def fail(operation):raise OSError('fixture disk full')
            store._fault=fail
            with pytest.raises(StorageError):await controller.run_turn('unsaved')
            assert seen==[]
            store._fault=None
            class Provider:
                allowed = True
                validated = False
                async def validate_sources(self, cid):
                    if not self.allowed:
                        raise PermissionError('source revoked')
                    self.validated = True
                def prepare(self,cid,*,query,max_chars=24000):
                    assert self.validated
                    return PreparedContext(cid,0,turns=(ConversationTurn('earlier','answer'),),
                        summary='old summary',facts=({'id':'m','text':'boat preference','revision':1},),
                        citations=())
            app.state.basics_composition.context_provider=Provider()
            await controller.run_turn('next')
            assert 'boat preference' in seen[-1].turns[0].user
            assert seen[-1].turns[-1].user=='earlier'
            app.state.basics_composition.context_provider.allowed = False
            count = len(seen)
            with pytest.raises(PermissionError, match='source revoked'):
                await controller.run_turn('revoked source must never reach model')
            assert len(seen) == count
            await client.delete('/v1/sessions/'+live['id'])
        store.close()
    asyncio.run(run())
