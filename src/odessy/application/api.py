"""Optional bearer-authenticated FastAPI transport for shared sessions.

Importing this module does not import FastAPI or start a listener.
"""
import asyncio
import contextlib
import hmac
import inspect
import json
import secrets
from contextlib import asynccontextmanager

from .session import public
from odessy.interaction.broker import InteractionError

_DIAGNOSTIC = r'''<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Odessy diagnostics</title>
<style>body{font:16px system-ui;max-width:960px;margin:2rem auto;padding:0 1rem;background:#10151d;color:#e2e8f0}input,select,textarea,button{font:inherit;margin:.3rem;padding:.5rem;background:#1f2937;color:inherit;border:1px solid #64748b;border-radius:4px}textarea{display:block;width:90%;min-height:5rem}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#18212e;padding:1rem;max-height:30rem;overflow:auto}button{cursor:pointer}label{display:inline-block}#approvals>div,#questions>div{padding:1rem;border:1px solid #eab308;margin:1rem 0}</style>
<h1>Odessy diagnostics</h1>
<p>Enter the bearer token printed by the local server. It stays in this page's memory.</p>
<label>Bearer token <input id="token" type="password" autocomplete="off"></label>
<button id="connect">Connect / new session</button><p id="status" role="status">Disconnected</p>
<section id="controls" hidden><label>Coordinator <select id="model"></select></label>
<button id="select">Select for next turn</button><button id="stop">Stop</button>
<label>Consult first <select id="participant"><option value="">No explicit consultation</option></select></label>
<textarea id="text" placeholder="Message or consultation question" aria-label="Message"></textarea><button id="send">Send</button>
<div id="approvals" aria-live="polite"></div><div id="questions" aria-live="polite"></div><h2>Conversation</h2><pre id="conversation"></pre>
<h2>Events, usage, and jobs</h2><pre id="out"></pre></section>
<script>
const $=id=>document.getElementById(id);let sid='',headers={},streamAbort=null,lastEvent=0,pendingSnapshotId=0,lines=[];
function status(message){$('status').textContent=message;}
async function api(path,method='GET',data){const response=await fetch(path,{method,headers:{...headers,...(data===undefined?{}:{'Content-Type':'application/json'})},body:data===undefined?undefined:JSON.stringify(data)});if(!response.ok){let detail;try{detail=(await response.json()).detail;}catch{detail=response.statusText;}throw Error(response.status+': '+detail);}return response.status===204?null:response.json();}
function route(suffix=''){return '/v1/sessions/'+encodeURIComponent(sid)+suffix;}
function log(event){lines.push(JSON.stringify(event));if(lines.length>200)lines.shift();$('out').textContent=lines.join('\n');$('out').scrollTop=$('out').scrollHeight;}
function approval(entry){if(document.getElementById('approval-'+entry.id))return;const box=document.createElement('div');box.id='approval-'+entry.id;const text=document.createElement('pre');text.textContent=JSON.stringify(entry,null,2);box.append(text);for(const [label,approved]of [['Approve',true],['Decline',false]]){const button=document.createElement('button');button.textContent=label;button.onclick=async()=>{try{await api(route('/approvals/'+encodeURIComponent(entry.id)),'POST',{approved});box.remove();}catch(error){status(error.message);}};box.append(button);}$('approvals').append(box);}
function question(entry){if(document.getElementById('question-'+entry.id))return;const box=document.createElement('div');box.id='question-'+entry.id;const prompt=document.createElement('p');prompt.textContent=entry.question+' (answer channels: '+entry.allowed_channels.join(', ')+')';box.append(prompt);const send=async answer=>{try{await api(route('/questions/'+encodeURIComponent(entry.id)),'POST',{answer});box.remove();status('Answer sent; running');}catch(error){status(error.message);}};for(const choice of entry.choices||[]){const button=document.createElement('button');button.textContent=choice;button.onclick=()=>send(choice);box.append(button);}if(entry.allow_free_text){const input=document.createElement('input');input.setAttribute('aria-label','Your answer');const button=document.createElement('button');button.textContent='Answer';button.onclick=()=>send(input.value);box.append(input,button);}$('questions').append(box);status('Waiting for your answer');}
function restorePending(snapshot){pendingSnapshotId=Math.max(lastEvent,...(snapshot.events||[]).map(event=>event.id));$('approvals').replaceChildren();$('questions').replaceChildren();for(const entry of snapshot.pending_approvals||[])approval(entry);for(const entry of snapshot.pending_questions||[])question(entry);}
function eventReceived(event){lastEvent=Math.max(lastEvent,event.id);log(event);if(event.id<=pendingSnapshotId&&['approval_required','approval_answered','approval_expired','question_required','question_answered','question_expired'].includes(event.type))return;if(event.type==='transcript'){$('conversation').textContent+=(event.data.role+': '+event.data.text+'\n\n');}if(event.type==='approval_required')approval(event.data);if(event.type==='question_required')question(event.data);if(['question_answered','question_expired'].includes(event.type))document.getElementById('question-'+event.data.id)?.remove();if(event.type==='approval_expired')document.getElementById('approval-'+event.data.id)?.remove();if(event.type==='approval_answered')document.getElementById('approval-'+event.data.id)?.remove();if(['result','error','stopped'].includes(event.type))status(event.type);}
async function stream(session,signal){while(!signal.aborted&&sid===session){try{const snapshot=await api('/v1/sessions/'+encodeURIComponent(session));if(signal.aborted||sid!==session)return;restorePending(snapshot);const response=await fetch('/v1/sessions/'+encodeURIComponent(session)+'/events',{headers:{...headers,'Last-Event-ID':String(lastEvent)},signal});if(!response.ok)throw Error('Event stream: '+response.status);const reader=response.body.getReader(),decoder=new TextDecoder();let buffer='';while(true){const {done,value}=await reader.read();if(done)break;buffer+=decoder.decode(value,{stream:true}).replace(/\r\n/g,'\n');let boundary;while((boundary=buffer.indexOf('\n\n'))>=0){const block=buffer.slice(0,boundary);buffer=buffer.slice(boundary+2);const data=block.split('\n').filter(line=>line.startsWith('data:')).map(line=>line.slice(5).trimStart()).join('\n');if(data)eventReceived(JSON.parse(data));}}}catch(error){if(signal.aborted)return;status(error.message);}if(!signal.aborted)await new Promise(resolve=>setTimeout(resolve,1000));}}
$('connect').onclick=async()=>{try{streamAbort?.abort();if(sid){await api(route(),'DELETE').catch(()=>{});sid='';}headers={Authorization:'Bearer '+$('token').value};const profiles=(await api('/v1/profiles')).profiles;const session=await api('/v1/sessions','POST');sid=session.id;lastEvent=0;pendingSnapshotId=0;lines=[];$('conversation').textContent='';$('approvals').replaceChildren();$('questions').replaceChildren();$('model').replaceChildren();$('participant').innerHTML='<option value="">No explicit consultation</option>';for(const [name,profile]of Object.entries(profiles)){const option=document.createElement('option');option.value=option.textContent=name;$('participant').append(option.cloneNode(true));if(!['codex','claude_code','custom_agent'].includes(profile.adapter))$('model').append(option);}if(!Array.from($('model').options).some(option=>option.value===session.selected_profile)){$('model').add(new Option(session.selected_profile,session.selected_profile));}$('model').value=session.selected_profile;$('controls').hidden=false;status('Connected: '+sid);streamAbort=new AbortController();void stream(sid,streamAbort.signal);}catch(error){status(error.message);}};
$('select').onclick=async()=>{try{await api(route('/model'),'PUT',{profile:$('model').value});status('Coordinator selected for next turn');}catch(error){status(error.message);}};
$('send').onclick=async()=>{const text=$('text').value.trim();if(!text)return;try{const data={text};if($('participant').value)data.participant=$('participant').value;await api(route('/turns'),'POST',data);$('text').value='';status('Running');}catch(error){status(error.message);}};
$('stop').onclick=async()=>{try{await api(route('/stop'),'POST');$('approvals').replaceChildren();$('questions').replaceChildren();status('Stopped');}catch(error){status(error.message);}};
window.addEventListener('pagehide',()=>streamAbort?.abort());
</script></html>'''


def create_app(controller_factory, *, config=None, settings=None, jobs=None, token=None,
               config_path=None, profiles=None, monitoring=None):
    """Build an app; the caller owns loopback-only binding and displaying its token."""
    try:
        from fastapi import FastAPI, Request, HTTPException
        from fastapi.responses import HTMLResponse, StreamingResponse, Response
    except ImportError as exc:
        raise RuntimeError('Install odessy[ui] to run the HTTP server') from exc

    sessions, tasks = {}, {}
    configured_names = set(config.participants) if config is not None else set()
    static_profiles = {} if callable(profiles) else {
        name: value for name, value in (profiles or {}).items() if name not in configured_names}

    if monitoring is None:
        from odessy.monitoring.settings import MonitoringSettings
        from odessy.monitoring.supervisor import MonitoringSupervisor
        try:
            monitor_settings = MonitoringSettings.from_env()
        except ValueError:
            monitor_settings = MonitoringSettings()
        if monitor_settings.enabled:
            monitoring = MonitoringSupervisor(monitor_settings)

    @asynccontextmanager
    async def lifespan(app):
        if monitoring is not None and hasattr(monitoring, 'start'):
            try:
                await monitoring.start()
            except Exception:
                pass  # Optional monitoring failure must not disable agent sessions.
        yield
        if monitoring is not None and hasattr(monitoring, 'close'):
            await monitoring.close()
        for controller in tuple(sessions.values()):
            await controller.close()
        if jobs is not None:
            await jobs.close()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.auth_token = token if token is not None else secrets.token_urlsafe(32)
    if not isinstance(app.state.auth_token, str) or not app.state.auth_token:
        raise ValueError('Bearer token must be a nonempty string')
    from odessy.monitoring.api import register as register_monitoring
    register_monitoring(app, monitoring)
    app.state.monitoring = monitoring
    app.state.sessions, app.state.turn_tasks = sessions, tasks
    app.state.config = config
    app.state.settings = settings
    app.state.profiles = profiles

    @app.middleware('http')
    async def authenticate(request, call_next):
        if request.url.path == '/' and request.method == 'GET':
            return await call_next(request)
        value = request.headers.get('authorization', '')
        supplied = value[7:] if value.startswith('Bearer ') else ''
        if not hmac.compare_digest(supplied.encode(), app.state.auth_token.encode()):
            return Response(status_code=401, headers={'WWW-Authenticate': 'Bearer'})
        return await call_next(request)

    async def body(request):
        try:
            data = await request.json()
        except (ValueError, UnicodeDecodeError):
            raise HTTPException(400, 'Expected a JSON object')
        if not isinstance(data, dict):
            raise HTTPException(400, 'Expected a JSON object')
        return data

    def find(sid):
        if sid not in sessions:
            raise HTTPException(404, 'Session not found')
        return sessions[sid]

    def snapshot(controller):
        return {'id': controller.id, 'selected_profile': controller.selected_profile,
                'active_profile': controller.active_profile, 'running': controller.running,
                'history': public(controller.memory.turns),
                'pending_approvals': controller.pending_approvals,
                'pending_questions': controller.pending_questions,
                'events': controller.events}

    async def make_controller():
        controller = controller_factory()
        if inspect.isawaitable(controller):
            controller = await controller
        if monitoring is not None and hasattr(monitoring, 'attach'):
            await monitoring.attach(controller)
        return controller

    @app.get('/', response_class=HTMLResponse)
    async def diagnostic():
        return _DIAGNOSTIC

    @app.get('/v1/profiles')
    async def get_profiles():
        values = app.state.profiles
        if callable(values):
            values = values()
        elif app.state.config is not None:
            values = {**static_profiles, **app.state.config.participants}
        if values is None:
            controller = await make_controller()
            try:
                values = controller.profiles
            finally:
                await controller.close()
        return {'profiles': {name: value.public_dict() if hasattr(value, 'public_dict') else public(value)
                             for name, value in values.items()}}

    @app.get('/v1/settings')
    async def get_settings():
        current = app.state.config
        return current.public_dict() if current is not None else public(app.state.settings or {})

    @app.put('/v1/settings')
    async def put_settings(request: Request):
        from odessy.collaboration.config import save_config
        data = await body(request)
        current = app.state.config
        if 'settings' not in data and 'participants' not in data:
            data = {'settings': data, 'participants': current.public_dict()['participants'] if current else {}}
        try:
            saved = await asyncio.to_thread(save_config, data, path=config_path)
        except (ValueError, TypeError) as exc:
            raise HTTPException(400, str(exc))
        app.state.config = saved
        return saved.public_dict()

    @app.post('/v1/sessions', status_code=201)
    async def new_session():
        controller = await make_controller()
        sessions[controller.id] = controller
        return snapshot(controller)

    @app.get('/v1/sessions/{sid}')
    async def get_session(sid: str):
        return snapshot(find(sid))

    @app.delete('/v1/sessions/{sid}', status_code=204)
    async def delete_session(sid: str):
        controller = find(sid)
        await controller.close()
        sessions.pop(sid)
        return Response(status_code=204)

    @app.put('/v1/sessions/{sid}/model')
    async def select_model(sid: str, request: Request):
        data = await body(request)
        if not isinstance(data.get('profile'), str):
            raise HTTPException(400, 'profile is required')
        try:
            find(sid).select_model(data['profile'])
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(400, str(exc))
        return snapshot(find(sid))

    @app.post('/v1/sessions/{sid}/turns', status_code=202)
    async def start_turn(sid: str, request: Request):
        controller = find(sid)
        data = await body(request)
        if not isinstance(data.get('text'), str) or not data['text'].strip():
            raise HTTPException(400, 'Nonempty text is required')
        if data.get('participant') is not None and not isinstance(data['participant'], str):
            raise HTTPException(400, 'participant must be a string')
        if controller.running or sid in tasks:
            raise HTTPException(409, 'A turn is already running')
        turn_id = secrets.token_hex(16)
        async def run():
            try:
                await controller.run_turn(data['text'], participant=data.get('participant'), turn_id=turn_id)
            except (Exception, asyncio.CancelledError):
                # Controller emits the public failure; retrieve the task exception.
                pass
            finally:
                tasks.pop(sid, None)
        tasks[sid] = asyncio.create_task(run())
        return {'turn_id': turn_id, 'session_id': sid, 'status': 'accepted'}

    @app.post('/v1/sessions/{sid}/stop')
    async def stop(sid: str):
        await find(sid).stop()
        task = tasks.get(sid)
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        return {'status': 'stopped'}

    @app.post('/v1/sessions/{sid}/approvals/{approval_id}')
    async def answer(sid: str, approval_id: str, request: Request):
        data = await body(request)
        if set(data) - {'approved'}:
            raise HTTPException(400, 'Unknown approval fields')
        if not isinstance(data.get('approved'), bool):
            raise HTTPException(400, 'approved must be a boolean')
        try:
            find(sid).answer_approval(approval_id, data['approved'], channel='authenticated_ui')
        except InteractionError as exc:
            raise HTTPException(exc.status_code, str(exc))
        except KeyError:
            raise HTTPException(404, 'Approval not found or already answered')
        return {'id': approval_id, 'approved': data['approved']}

    @app.post('/v1/sessions/{sid}/questions/{question_id}')
    async def answer_question(sid: str, question_id: str, request: Request):
        data = await body(request)
        if set(data) != {'answer'} or not isinstance(data.get('answer'), str):
            raise HTTPException(400, 'Only a string answer is accepted')
        try:
            find(sid).answer_question(question_id, data['answer'], channel='authenticated_ui')
        except InteractionError as exc:
            raise HTTPException(exc.status_code, str(exc))
        return {'id': question_id, 'status': 'answered'}

    @app.get('/v1/sessions/{sid}/events')
    async def events(sid: str, request: Request):
        controller = find(sid)
        try:
            after = int(request.headers.get('last-event-id', request.query_params.get('after_id', '0')))
        except ValueError:
            raise HTTPException(400, 'Invalid event ID')
        async def stream():
            iterator = controller.subscribe(after).__aiter__()
            pending = asyncio.create_task(iterator.__anext__())
            try:
                while not await request.is_disconnected():
                    done, _ = await asyncio.wait({pending}, timeout=15)
                    if not done:
                        yield ': keepalive\n\n'
                        continue
                    try:
                        event = pending.result()
                    except StopAsyncIteration:
                        return
                    yield f"id: {event['id']}\nevent: {event['type']}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
                    pending = asyncio.create_task(iterator.__anext__())
            finally:
                pending.cancel()
                with contextlib.suppress(asyncio.CancelledError, StopAsyncIteration):
                    await pending
                await iterator.aclose()
        return StreamingResponse(stream(), media_type='text/event-stream',
                                 headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})

    def manager(sid=None, job_id=None):
        if sid is not None:
            selected = find(sid).jobs
            if selected is None:
                raise HTTPException(503, 'Jobs are not configured for this session')
            return selected
        candidates = [jobs] if jobs is not None else []
        candidates.extend(controller.jobs for controller in sessions.values() if controller.jobs is not None)
        if job_id is not None:
            for candidate in candidates:
                try:
                    if candidate.get(job_id) is not None:
                        return candidate
                except KeyError:
                    continue
            raise HTTPException(404, 'Job not found')
        if jobs is not None:
            return jobs
        raise HTTPException(400, 'session_id is required for job submission')

    async def submit(data, sid=None):
        from odessy.runtime.types import ToolRequest
        allowed = {'participant', 'task', 'mode', 'workspace', 'approved', 'session_id'}
        if set(data) - allowed:
            raise HTTPException(400, 'Unknown job fields')
        if not isinstance(data.get('participant'), str) or not isinstance(data.get('task'), str) or not data['task'].strip():
            raise HTTPException(400, 'participant and nonempty task are required')
        if 'approved' in data and type(data['approved']) is not bool:
            raise HTTPException(400, 'approved must be a boolean')
        if 'mode' in data and not isinstance(data['mode'], str):
            raise HTTPException(400, 'mode must be a string')
        if data.get('workspace') is not None and not isinstance(data['workspace'], str):
            raise HTTPException(400, 'workspace must be a string')
        if sid is None:
            sid = data.get('session_id')
        if sid is not None and not isinstance(sid, str):
            raise HTTPException(400, 'session_id must be a string')
        selected = manager(sid)
        values = {key: value for key, value in data.items() if key not in {'approved', 'session_id'}}
        mode = values.get('mode', 'advice')
        if sid is None:
            raise HTTPException(400, 'A session is required for controller approval')
        controller = find(sid)
        try:
            approved = await controller.confirm(
                {'reason': 'Delegate a coding task in ' + mode + ' mode', 'risk': 'high'},
                ToolRequest('agent.delegate', values))
        except (ValueError, TypeError, PermissionError) as exc:
            raise HTTPException(400, str(exc))
        if not approved:
            raise HTTPException(403, 'Job submission declined')
        try:
            result = selected.submit(**values, approved=approved)
            if inspect.isawaitable(result):
                result = await result
            return public(result)
        except (ValueError, TypeError, PermissionError) as exc:
            raise HTTPException(400, str(exc))

    @app.post('/v1/jobs', status_code=202)
    async def submit_job(request: Request):
        return await submit(await body(request))

    @app.post('/v1/sessions/{sid}/jobs', status_code=202)
    async def submit_session_job(sid: str, request: Request):
        return await submit(await body(request), sid)

    async def read_job(job_id, sid=None):
        try:
            selected = manager(sid, job_id)
            result = await selected.refresh(job_id) if hasattr(selected, 'refresh') else selected.get(job_id)
            if result is None:
                raise KeyError(job_id)
            return public(result)
        except KeyError:
            raise HTTPException(404, 'Job not found')

    @app.get('/v1/jobs/{job_id}')
    async def get_job(job_id: str):
        return await read_job(job_id)

    @app.get('/v1/sessions/{sid}/jobs/{job_id}')
    async def get_session_job(sid: str, job_id: str):
        return await read_job(job_id, sid)

    async def cancel(job_id, sid=None):
        try:
            return public(await manager(sid, job_id).cancel(job_id))
        except KeyError:
            raise HTTPException(404, 'Job not found')

    @app.post('/v1/jobs/{job_id}/cancel')
    async def cancel_job(job_id: str):
        return await cancel(job_id)

    @app.post('/v1/sessions/{sid}/jobs/{job_id}/cancel')
    async def cancel_session_job(sid: str, job_id: str):
        return await cancel(job_id, sid)

    return app
