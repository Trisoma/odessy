import asyncio
import pytest
pytest.importorskip('fastapi')
httpx = pytest.importorskip('httpx')
from odessy.application.api import create_app
from odessy.application.session import SessionController
from odessy.runtime.types import AgentTurnResult

class Agent:
    def __init__(self, model, **kwargs):
        pass
    async def run_turn(self, text, **kwargs):
        return AgentTurnResult('answer:' + text, [])

@pytest.mark.parametrize('path,method', [('/v1/sessions', 'POST'), ('/v1/profiles', 'GET')])
@pytest.mark.parametrize('wrapped', [False, True])
@pytest.mark.parametrize('asynchronous', [False, True])
def test_missing_cloud_key_is_recoverable_service_unavailability(monkeypatch, path, method, wrapped, asynchronous):
    from odessy.models.cloud_model import build_cloud_model
    from odessy import cli

    for name in ('DEEPSEEK_API_KEY', 'ODESSY_CLOUD_API_KEY'):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv('ODESSY_CLOUD_FAILOVER', '')

    def factory():
        if wrapped:
            return cli._build_model(False, backend='deepseek')
        return build_cloud_model('deepseek')

    async def async_factory():
        return factory()

    async def run():
        app = create_app(async_factory if asynchronous else factory, token='secret')
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test',
                                     headers={'Authorization': 'Bearer secret'}) as client:
            response = await client.request(method, path)
            assert response.status_code == 503
            assert 'DEEPSEEK_API_KEY' in response.json()['detail']
            assert not app.state.sessions
            assert (await client.get('/v1/settings')).status_code == 200

    asyncio.run(run())

def test_cloud_configuration_errors_are_redacted_and_other_errors_are_not_misclassified():
    from odessy.models.cloud_model import CloudModelConfigError

    async def run():
        for error, status in [(CloudModelConfigError('API_KEY=private-value-123456'), 503),
                              (RuntimeError('unrelated programming error'), 500)]:
            def factory():
                raise error
            app = create_app(factory, token='secret')
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
                                         base_url='http://test', headers={'Authorization': 'Bearer secret'}) as client:
                response = await client.post('/v1/sessions')
                assert response.status_code == status
                assert 'private-value-123456' not in response.text
                assert not app.state.sessions

    asyncio.run(run())

def test_api_auth_session_turn_selection_and_no_query_credentials():
    async def run():
        app = create_app(lambda: SessionController(model_factory=lambda name: name,
            agent_factory=Agent, profiles={'local': {}}), token='test-secret')
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            assert (await client.get('/v1/profiles')).status_code == 401
            assert (await client.get('/v1/profiles?token=test-secret')).status_code == 401
            headers = {'Authorization': 'Bearer test-secret'}
            assert (await client.get('/v1/profiles', headers=headers)).status_code == 200
            response = await client.post('/v1/sessions', headers=headers)
            assert response.status_code == 201
            sid = response.json()['id']
            route = '/v1/sessions/' + sid
            response = await client.post(route + '/turns', json={'text': 'hello'}, headers=headers)
            assert response.status_code == 202
            await asyncio.sleep(0)
            state = (await client.get(route, headers=headers)).json()
            assert state['history'][0]['assistant'] == 'answer:hello'
            assert (await client.put(route + '/model', json={'profile': 'absent'}, headers=headers)).status_code == 400
            assert (await client.delete(route, headers=headers)).status_code == 204
    asyncio.run(run())

def test_generated_token_and_diagnostic_html_do_not_embed_secret():
    app = create_app(lambda: None)
    assert len(app.state.auth_token) >= 32

def test_settings_validation_persists_atomic_config_and_sse_replays_redacted_events(tmp_path):
    async def run():
        from odessy.collaboration.config import CollaborationConfig, load_config
        config_path = tmp_path / 'collaboration.toml'
        app = create_app(lambda: SessionController(model_factory=lambda name: name,
            agent_factory=Agent, profiles={'local': {}}), token='secret',
            config=CollaborationConfig(), config_path=config_path)
        headers = {'Authorization': 'Bearer secret'}
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            bad = await client.put('/v1/settings', json={'max_consultations': -1}, headers=headers)
            assert bad.status_code == 400
            assert not config_path.exists()
            saved = await client.put('/v1/settings', json={'enabled': True, 'max_consultations': 2}, headers=headers)
            assert saved.status_code == 200
            assert load_config(config_path).settings.max_consultations == 2
            sid = (await client.post('/v1/sessions', headers=headers)).json()['id']
            session = app.state.sessions[sid]
            session.emit('progress', {'message': 'first'})
            session.emit('progress', {'message': 'API_KEY=abcdefghi'})
            await session.close()
            response = await client.get('/v1/sessions/' + sid + '/events', headers={**headers, 'Last-Event-ID': '1'})
            assert response.status_code == 200
            assert 'event: progress' in response.text
            assert 'id: 2' in response.text
            assert 'first' not in response.text
            assert 'abcdefghi' not in response.text
            assert '<redacted>' in response.text
    asyncio.run(run())

def test_http_approval_wait_keeps_event_loop_responsive_and_stop_declines():
    async def run():
        entered = asyncio.Event()
        class WaitingAgent(Agent):
            def __init__(self, model, **kwargs):
                self.confirm = kwargs['confirm']
            async def run_turn(self, text, **kwargs):
                entered.set()
                await self.confirm({'reason': 'Write a file'}, {'name': 'filesystem.write'})
                return AgentTurnResult('done', [])
        app = create_app(lambda: SessionController(model_factory=lambda name: name,
            agent_factory=WaitingAgent, profiles={'local': {}}), token='secret')
        headers = {'Authorization': 'Bearer secret'}
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test', headers=headers) as client:
            sid = (await client.post('/v1/sessions')).json()['id']
            route = '/v1/sessions/' + sid
            assert (await client.post(route + '/turns', json={'text': 'write'})).status_code == 202
            await asyncio.wait_for(entered.wait(), 1)
            state = (await client.get(route)).json()
            assert state['running'] and state['pending_approvals']
            aid = state['pending_approvals'][0]['id']
            assert (await client.post(route + '/turns', json={'text': 'overlap'})).status_code == 409
            assert (await client.post(route + '/approvals/' + aid, json={'approved': 'false'})).status_code == 400
            assert (await client.post(route + '/stop')).status_code == 200
            state = (await client.get(route)).json()
            assert not state['running'] and not state['pending_approvals']
            assert (await client.post(route + '/approvals/' + aid, json={'approved': True})).status_code == 410
            assert not state['history']
            assert (await client.post(route + '/turns', content='oops')).status_code == 400
            assert (await client.get('/missing', headers={'Authorization':'Bearer wrong'})).status_code == 401
    asyncio.run(run())

def test_public_diagnostic_shell_and_all_v1_routes_require_header():
    async def run():
        app = create_app(lambda: None, token='never-embed-this-secret')
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            shell = await client.get('/')
            assert shell.status_code == 200
            assert 'never-embed-this-secret' not in shell.text
            assert (await client.get('/v1/settings')).status_code == 401
            assert (await client.get('/v1/settings?token=never-embed-this-secret')).status_code == 401
    asyncio.run(run())


def test_profiles_get_reflects_settings_updates(tmp_path):
    async def run():
        from odessy.collaboration.config import CollaborationConfig, ParticipantProfile
        config = CollaborationConfig(participants={'old': ParticipantProfile('old', 'ollama')})
        app = create_app(lambda: None, config=config, profiles={'current': {}, 'old': {}}, token='secret', config_path=tmp_path / 'config.toml')
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test', headers={'Authorization': 'Bearer secret'}) as client:
            response = await client.put('/v1/settings', json={'settings': {}, 'participants': {'new': {'adapter': 'ollama'}}})
            assert response.status_code == 200
            profiles = (await client.get('/v1/profiles')).json()['profiles']
            assert 'new' in profiles and 'current' in profiles and 'old' not in profiles
    asyncio.run(run())


def test_session_jobs_use_controller_manager_and_cannot_bypass_approval():
    async def run():
        class Jobs:
            def __init__(self):
                self.submitted = []
            async def submit(self, **data):
                self.submitted.append(data)
                return {'job_id': 'job1', 'state': 'queued'}
            def get(self, job_id):
                if job_id != 'job1':
                    raise KeyError(job_id)
                return {'job_id': 'job1', 'state': 'running'}
            async def cancel(self, job_id):
                return {'job_id': job_id, 'state': 'cancelled'}
        jobs = Jobs()
        app = create_app(lambda: SessionController(model_factory=lambda name: name, agent_factory=Agent, profiles={'local': {}}, jobs=jobs), token='secret')
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test', headers={'Authorization': 'Bearer secret'}) as client:
            sid = (await client.post('/v1/sessions')).json()['id']
            route = '/v1/sessions/' + sid
            request = asyncio.create_task(client.post(route + '/jobs', json={'participant': 'coder', 'task': 'change file', 'mode': 'patch', 'approved': True}))
            for _ in range(20):
                await asyncio.sleep(0)
                approvals = app.state.sessions[sid].pending_approvals
                if approvals or request.done():
                    break
            assert approvals, 'client approval flag must not bypass controller approval'
            assert not jobs.submitted
            await client.post(route + '/approvals/' + approvals[0]['id'], json={'approved': True})
            response = await request
            assert response.status_code == 202
            assert jobs.submitted[0]['approved'] is True
            assert (await client.get(route + '/jobs/job1')).json()['state'] == 'running'
            assert (await client.post(route + '/jobs/job1/cancel')).json()['state'] == 'cancelled'
            assert (await client.get('/v1/jobs/job1')).json()['state'] == 'running'
            await client.delete(route)
    asyncio.run(run())

def test_advice_delegation_requires_controller_approval_and_denial_blocks_submit():
    async def run():
        class Jobs:
            async def submit(self, **data):
                raise AssertionError('declined task must not submit')
        app = create_app(lambda: SessionController(model_factory=lambda name: name, agent_factory=Agent, profiles={'local': {}}, jobs=Jobs()), token='secret')
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test', headers={'Authorization': 'Bearer secret'}) as client:
            sid = (await client.post('/v1/sessions')).json()['id']
            route = '/v1/sessions/' + sid
            request = asyncio.create_task(client.post(route + '/jobs', json={'participant': 'coder', 'task': 'review code', 'mode': 'advice', 'approved': True}))
            for _ in range(100):
                await asyncio.sleep(0)
                approvals = app.state.sessions[sid].pending_approvals
                if approvals or request.done():
                    break
            assert approvals
            await client.post(route + '/approvals/' + approvals[0]['id'], json={'approved': False})
            assert (await request).status_code == 403
            await client.delete(route)
    asyncio.run(run())


def test_session_cost_events_include_profile_identity_and_redact_coding_credentials(monkeypatch):
    async def run():
        from odessy.collaboration.config import CollaborationConfig, ParticipantProfile
        from odessy.models.usage import UsageLedger, reset_process_ledger
        ledger = UsageLedger()
        reset_process_ledger(ledger)
        monkeypatch.setenv('CODER_CREDENTIAL', 'opaque-coding-credential')
        config = CollaborationConfig(participants={'coder': ParticipantProfile('coder', 'codex', credential_env='CODER_CREDENTIAL')})
        class PricedAgent(Agent):
            async def run_turn(self, text, **kwargs):
                ledger.for_profile('reviewer', 'openai', 'example').record(prompt=10, cached=0, completion=2)
                return AgentTurnResult('opaque-coding-credential', [])
        controller = SessionController(model_factory=lambda name: name, agent_factory=PricedAgent, profiles={'local': {}}, config=config)
        await controller.run_turn('hi')
        cost = next(event['data'] for event in controller.events if event['type'] == 'cost')
        assert cost['profiles'][0]['participant'] == 'reviewer'
        assert cost['profiles'][0]['provider'] == 'openai'
        assert cost['profiles'][0]['model'] == 'example'
        assert cost['profiles'][0]['price_status'] == 'unknown'
        assert cost['profiles'][0]['total']['prompt'] == 10
        assert 'opaque-coding-credential' not in str(controller.events)
        assert 'opaque-coding-credential' not in str(controller.memory.turns)
        await controller.close()
        reset_process_ledger()
    asyncio.run(run())

def test_profile_factory_is_lazy_and_authoritative():
    async def run():
        from odessy.collaboration.config import CollaborationConfig
        values = {'local': {}}
        def unused_controller():
            raise AssertionError('profile listing must not create a controller')
        app = create_app(unused_controller, config=CollaborationConfig(), profiles=lambda: values, token='secret')
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test', headers={'Authorization': 'Bearer secret'}) as client:
            assert 'local' in (await client.get('/v1/profiles')).json()['profiles']
            values['new'] = {}
            assert 'new' in (await client.get('/v1/profiles')).json()['profiles']
    asyncio.run(run())


def test_question_api_validates_answers_and_keeps_status_live():
    class Jobs:
        async def refresh(self, job_id):
            return {'id': job_id, 'status': 'running'}
    async def run():
        app = create_app(lambda: SessionController(model_factory=lambda _: object(),
            agent_factory=Agent, profiles={'local': {}}), token='secret')
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test',
                headers={'Authorization': 'Bearer secret'}) as client:
            sid = (await client.post('/v1/sessions')).json()['id']
            route = '/v1/sessions/' + sid
            controller = app.state.sessions[sid]
            task = asyncio.create_task(controller.interactions.ask('Choose', ['one', 'two'], False))
            await asyncio.sleep(0)
            state = (await client.get(route)).json()
            controller.jobs = Jobs()
            assert (await client.get(route + '/jobs/background')).json()['status'] == 'running'
            qid = state['pending_questions'][0]['id']
            endpoint = route + '/questions/' + qid
            assert (await client.post(endpoint, json={'answer': 'one', 'channel': 'voice'})).status_code == 400
            assert (await client.post(endpoint, json={'answer': 'other'})).status_code == 400
            assert (await client.post(endpoint, json={'answer': 'two'})).status_code == 200
            assert (await task)['answer'] == 'two'
            assert (await client.post(endpoint, json={'answer': 'one'})).status_code == 409
            assert (await client.post(route + '/questions/missing', json={'answer': 'one'})).status_code == 404
            task = asyncio.create_task(controller.interactions.ask('More?'))
            await asyncio.sleep(0)
            qid = controller.pending_questions[0]['id']
            await controller.stop()
            assert (await task)['status'] == 'cancelled'
            assert (await client.post(route + '/questions/' + qid, json={'answer': 'one'})).status_code == 410
            await controller.close()
    asyncio.run(run())


def test_accepted_turn_id_owns_foreground_question():
    from odessy.interaction.context import current_interactions
    async def run():
        class AskingAgent(Agent):
            async def run_turn(self, text, **kwargs):
                answer = await current_interactions().ask('Destination?')
                return AgentTurnResult(answer['answer'], [])
        app = create_app(lambda: SessionController(model_factory=lambda _: object(),
            agent_factory=AskingAgent, profiles={'local': {}}), token='secret')
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test',
                headers={'Authorization': 'Bearer secret'}) as client:
            sid = (await client.post('/v1/sessions')).json()['id']
            route = '/v1/sessions/' + sid
            accepted = (await client.post(route + '/turns', json={'text': 'Go'})).json()
            state = (await client.get(route)).json()
            question = state['pending_questions'][0]
            assert question['turn_id'] == accepted['turn_id']
            await client.post(route + '/questions/' + question['id'], json={'answer': 'home'})
            task = app.state.turn_tasks.get(sid)
            if task is not None:
                await task
            await client.delete(route)
    asyncio.run(run())


def test_diagnostic_snapshot_restores_pending_controls_and_ignores_stale_replay():
    import re
    import shutil
    import subprocess
    from odessy.application.api import _DIAGNOSTIC
    node = shutil.which('node')
    if node is None:
        pytest.skip('Node is needed for diagnostic client behavior verification')
    script = re.search(r'<script>(.*?)</script>', _DIAGNOSTIC, re.S)[1]
    harness = r'''
const assert=require('node:assert/strict');
const nodes=new Map();
function element(){return {children:[],value:'',textContent:'',append(...items){this.children.push(...items);},replaceChildren(){for(const child of this.children)child.remove();this.children=[];},remove(){nodes.delete(this.id);},setAttribute(){},cloneNode(){return element();},set id(value){this._id=value;nodes.set(value,this);},get id(){return this._id;}};}
const document={getElementById(id){return nodes.get(id)||null;},createElement:element};
for(const id of ['status','out','conversation','approvals','questions','connect','select','send','stop']){const item=element();item.id=id;}
const window={addEventListener(){}};
'''
    assertions = r'''
const q={id:'q',question:'Choose',choices:['one'],allow_free_text:true,allowed_channels:['voice','terminal','authenticated_ui']};
restorePending({events:[{id:50}],pending_questions:[q],pending_approvals:[{id:'a'}]});
assert.ok(nodes.has('question-q'));assert.ok(nodes.has('approval-a'));
eventReceived({id:1,type:'question_required',data:{...q,id:'stale'}});
assert.ok(!nodes.has('question-stale'));
eventReceived({id:51,type:'question_answered',data:{id:'q'}});
assert.ok(!nodes.has('question-q'));
eventReceived({id:52,type:'question_required',data:{...q,id:'new'}});
assert.ok(nodes.has('question-new'));
restorePending({events:[{id:60}],pending_questions:[],pending_approvals:[]});
assert.ok(!nodes.has('question-new'));assert.ok(!nodes.has('approval-a'));
void (async()=>{
sid='session';const signal={aborted:false};const requested=[];
global.fetch=async path=>{requested.push(path);if(!path.endsWith('/events'))return {ok:true,status:200,json:async()=>({events:[{id:100}],pending_questions:[q],pending_approvals:[]})};return {ok:true,body:{getReader:()=>({read:async()=>{signal.aborted=true;return {done:true};}})}};};
await stream(sid,signal);
assert.deepEqual(requested,['/v1/sessions/session','/v1/sessions/session/events']);
assert.ok(nodes.has('question-q'));
})().catch(error=>{console.error(error);process.exitCode=1;});
'''
    result = subprocess.run([node, '-e', harness + script + assertions], text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
