"""Optional bearer-authenticated FastAPI transport for shared sessions.

Importing this module does not import FastAPI or start a listener.
"""
import asyncio
import contextlib
import hmac
import inspect
import json
import math
import os
import re
import secrets
from dataclasses import asdict
import time
from contextlib import asynccontextmanager

from .session import public
from odessy.interaction.broker import InteractionError
from odessy.models.cloud_model import CloudModelConfigError

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
               config_path=None, profiles=None, monitoring=None, runtime_configuration=None, desktop_voice=None):
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
        if desktop_voice is not None:await desktop_voice.close()
        if monitoring is not None and hasattr(monitoring, 'close'):
            await monitoring.close()
        for controller in tuple(sessions.values()):
            await controller.close()
        if jobs is not None:
            await jobs.close()
        zeroconf = getattr(app.state, 'zeroconf', None)
        if zeroconf is not None:
            app.state.zeroconf = None
            try:
                await asyncio.to_thread(zeroconf.close)  # Withdraws the mDNS record.
            except Exception:
                pass

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.auth_token = token if token is not None else secrets.token_urlsafe(32)
    app.state.runtime_instance_id = secrets.token_urlsafe(24)
    if not isinstance(app.state.auth_token, str) or not app.state.auth_token:
        raise ValueError('Bearer token must be a nonempty string')
    from odessy.monitoring.api import register as register_monitoring
    register_monitoring(app, monitoring)
    app.state.monitoring = monitoring
    app.state.sessions, app.state.turn_tasks = sessions, tasks
    from .lan_connections import register as register_lan_connections
    register_lan_connections(app)
    app.state.config = config
    app.state.settings = settings
    app.state.profiles = profiles
    settings_lock = asyncio.Lock()
    settings_revision = 0
    app.state.runtime_configuration = json.loads(json.dumps(runtime_configuration))
    app.state.service_instance_id = secrets.token_hex(16)
    restart_reservation = None
    pending_admissions = 0

    def admission_open():
        nonlocal restart_reservation
        if restart_reservation and restart_reservation['expires'] <= time.monotonic():
            restart_reservation = None
        if restart_reservation:
            raise HTTPException(409, 'Coordinator restart is reserved; no new work was admitted')

    def restart_idle():
        managers = [jobs] if jobs is not None else []
        managers.extend(c.jobs for c in sessions.values() if c.jobs is not None)
        return not (desktop_voice is not None and desktop_voice.busy) and not tasks and not pending_admissions and not any(
            c.running or getattr(c, 'pending_approvals', []) or getattr(c, 'pending_questions', [])
            for c in sessions.values()) and all(
            getattr(manager, 'has_active_work', True) is False for manager in managers)

    @app.get('/v1/capabilities')
    async def capabilities():
        return {'schema_version':1,'service_instance_id':app.state.service_instance_id,
                'configuration':app.state.runtime_configuration,'restart_safe':restart_idle()}

    @app.post('/v1/lifecycle/prepare-restart')
    async def prepare_restart(request: Request):
        nonlocal restart_reservation
        data = await body(request)
        admission_open()
        if data != {'service_instance_id': app.state.service_instance_id} or not restart_idle():
            raise HTTPException(409, 'Service changed or still owns active work; restart was not reserved')
        restart_reservation = {'ticket':secrets.token_hex(24),'expires':time.monotonic()+60}
        return {'ticket':restart_reservation['ticket'],'service_instance_id':app.state.service_instance_id}

    @app.post('/v1/lifecycle/resume')
    async def resume_admission(request: Request):
        nonlocal restart_reservation
        data = await body(request)
        if not restart_reservation or data != {'ticket':restart_reservation['ticket']}:
            raise HTTPException(409, 'Unknown restart reservation')
        restart_reservation = None
        return {'status':'resumed'}

    def verified(request):
        value = request.headers.get('authorization', '')
        supplied = value[7:] if value.startswith('Bearer ') else ''
        return hmac.compare_digest(supplied.encode(), app.state.auth_token.encode())

    @app.middleware('http')
    async def authenticate(request, call_next):
        if request.url.path == '/' and request.method == 'GET':
            return await call_next(request)
        if not verified(request):
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
                'available_profiles': list(controller.profiles),
                'active_profile': controller.active_profile, 'running': controller.running,
                'history': public(controller.memory.turns),
                'pending_approvals': controller.pending_approvals,
                'pending_questions': controller.pending_questions,
                'events': controller.events}

    async def make_controller():
        nonlocal pending_admissions
        admission_open()
        pending_admissions += 1
        try:
            try:
                controller = controller_factory()
                if inspect.isawaitable(controller):
                    controller = await controller
            except CloudModelConfigError as exc:
                raise HTTPException(503, public(str(exc))) from exc
            except SystemExit as exc:
                # The shared CLI model builder wraps configuration failures.
                # Translate only that known cause; unrelated exits stay errors.
                if not isinstance(exc.__cause__, CloudModelConfigError):
                    raise
                raise HTTPException(503, public(str(exc.__cause__))) from exc
            if monitoring is not None and hasattr(monitoring, 'attach'):
                await monitoring.attach(controller)
            return controller
        finally:
            pending_admissions -= 1

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

    @app.get('/v1/runtime-settings')
    async def get_runtime_settings():
        from odessy.config.schema import SETTINGS
        # Never expose values: even non-secret fields can contain credentials,
        # private paths or command-line arguments. This is process state, not a
        # claim about which file or inherited environment supplied the value.
        return {'schema_version': 1, 'instance_id': app.state.runtime_instance_id,
                'settings': [
                    {'name': item.name, 'kind': item.kind, 'summary': item.summary,
                     'configured': bool(os.environ.get(item.name)),
                     'sensitive': item.kind == 'secret', 'internal': item.internal,
                     'editable': False}
                    for item in sorted(SETTINGS, key=lambda item: item.name)]}

    async def save_coordinated(data):
        # The caller holds settings_lock. A disconnected client must not release
        # that lock while the worker can still replace the file behind our back.
        nonlocal settings_revision
        from odessy.collaboration.config import save_config
        task = asyncio.create_task(asyncio.to_thread(save_config, data, path=config_path))
        cancelled = False
        while True:
            try:
                saved = await asyncio.shield(task)
                break
            except asyncio.CancelledError:
                cancelled = True
        app.state.config = saved
        settings_revision += 1
        if cancelled:
            raise asyncio.CancelledError
        return saved

    @app.put('/v1/settings')
    async def put_settings(request: Request):
        data = await body(request)
        async with settings_lock:
            current = app.state.config
            if 'settings' not in data and 'participants' not in data:
                data = {'settings': data, 'participants': current.public_dict()['participants'] if current else {}}
            try:
                saved = await save_coordinated(data)
            except (ValueError, TypeError) as exc:
                raise HTTPException(400, str(exc))
            return saved.public_dict()

    @app.get('/v1/settings/consultation-limit')
    async def get_consultation_limit():
        async with settings_lock:
            if app.state.config is None:
                raise HTTPException(503, 'Collaboration settings unavailable')
            return {'instance_id': app.state.runtime_instance_id, 'revision': settings_revision,
                    'max_consultations': app.state.config.settings.max_consultations}

    @app.put('/v1/settings/consultation-limit')
    async def put_consultation_limit(request: Request):
        data = await body(request)
        if (set(data) != {'instance_id', 'revision', 'max_consultations'}
                or not isinstance(data['instance_id'], str)
                or type(data['revision']) is not int or data['revision'] < 0
                or type(data['max_consultations']) is not int
                or not 0 <= data['max_consultations'] <= 32):
            raise HTTPException(400, 'Invalid settings update')
        async with settings_lock:
            if app.state.config is None:
                raise HTTPException(503, 'Collaboration settings unavailable')
            if (data['instance_id'] != app.state.runtime_instance_id
                    or data['revision'] != settings_revision):
                raise HTTPException(409, 'Settings changed; reload before saving')
            document = app.state.config.public_dict()
            document['settings']['max_consultations'] = data['max_consultations']
            try:
                saved = await save_coordinated(document)
            except (ValueError, TypeError) as exc:
                raise HTTPException(400, str(exc))
            return {'instance_id': app.state.runtime_instance_id, 'revision': settings_revision,
                    'max_consultations': saved.settings.max_consultations}

    @app.get('/v1/settings/context-limit')
    async def get_context_limit():
        async with settings_lock:
            if app.state.config is None:
                raise HTTPException(503, 'Collaboration settings unavailable')
            return {'instance_id': app.state.runtime_instance_id, 'revision': settings_revision,
                    'max_context_chars': app.state.config.settings.max_context_chars}

    @app.put('/v1/settings/context-limit')
    async def put_context_limit(request: Request):
        data = await body(request)
        if (set(data) != {'instance_id', 'revision', 'max_context_chars'}
                or not isinstance(data['instance_id'], str)
                or type(data['revision']) is not int or data['revision'] < 0
                or type(data['max_context_chars']) is not int
                or not 1 <= data['max_context_chars'] <= 100_000):
            raise HTTPException(400, 'Invalid context limit')
        async with settings_lock:
            if app.state.config is None:
                raise HTTPException(503, 'Collaboration settings unavailable')
            if data['instance_id'] != app.state.runtime_instance_id or data['revision'] != settings_revision:
                raise HTTPException(409, 'Settings changed; reload before saving')
            document = app.state.config.public_dict()
            document['settings']['max_context_chars'] = data['max_context_chars']
            try:
                saved = await save_coordinated(document)
            except (ValueError, TypeError) as exc:
                raise HTTPException(400, str(exc))
            return {'instance_id': app.state.runtime_instance_id, 'revision': settings_revision,
                    'max_context_chars': saved.settings.max_context_chars}

    @app.get('/v1/settings/round-limit')
    async def get_round_limit():
        async with settings_lock:
            if app.state.config is None:
                raise HTTPException(503, 'Collaboration settings unavailable')
            return {'instance_id': app.state.runtime_instance_id, 'revision': settings_revision,
                    'max_rounds': app.state.config.settings.max_rounds}

    @app.put('/v1/settings/round-limit')
    async def put_round_limit(request: Request):
        data = await body(request)
        if (set(data) != {'instance_id', 'revision', 'max_rounds'}
                or not isinstance(data['instance_id'], str)
                or type(data['revision']) is not int or data['revision'] < 0
                or type(data['max_rounds']) is not int or not 0 <= data['max_rounds'] <= 32):
            raise HTTPException(400, 'Invalid round limit')
        async with settings_lock:
            if app.state.config is None:
                raise HTTPException(503, 'Collaboration settings unavailable')
            if data['instance_id'] != app.state.runtime_instance_id or data['revision'] != settings_revision:
                raise HTTPException(409, 'Settings changed; reload before saving')
            document = app.state.config.public_dict()
            document['settings']['max_rounds'] = data['max_rounds']
            try:
                saved = await save_coordinated(document)
            except (ValueError, TypeError) as exc:
                raise HTTPException(400, str(exc))
            return {'instance_id': app.state.runtime_instance_id, 'revision': settings_revision,
                    'max_rounds': saved.settings.max_rounds}

    @app.get('/v1/settings/collaboration-enabled')
    async def get_collaboration_enabled():
        async with settings_lock:
            if app.state.config is None:
                raise HTTPException(503, 'Collaboration settings unavailable')
            return {'instance_id': app.state.runtime_instance_id, 'revision': settings_revision,
                    'enabled': app.state.config.settings.enabled}

    @app.put('/v1/settings/collaboration-enabled')
    async def put_collaboration_enabled(request: Request):
        data = await body(request)
        if (set(data) != {'instance_id', 'revision', 'enabled'}
                or not isinstance(data['instance_id'], str)
                or type(data['revision']) is not int or data['revision'] < 0
                or type(data['enabled']) is not bool):
            raise HTTPException(400, 'Invalid collaboration setting')
        async with settings_lock:
            if app.state.config is None:
                raise HTTPException(503, 'Collaboration settings unavailable')
            if data['instance_id'] != app.state.runtime_instance_id or data['revision'] != settings_revision:
                raise HTTPException(409, 'Settings changed; reload before saving')
            document = app.state.config.public_dict()
            document['settings']['enabled'] = data['enabled']
            try:
                saved = await save_coordinated(document)
            except (ValueError, TypeError) as exc:
                raise HTTPException(400, str(exc))
            return {'instance_id': app.state.runtime_instance_id, 'revision': settings_revision,
                    'enabled': saved.settings.enabled}

    def editable_profiles():
        return [{'name': name, 'adapter': profile.adapter, 'provider': profile.provider,
                 'model': profile.model, 'endpoint': profile.endpoint}
                for name, profile in sorted(app.state.config.participants.items())
                if profile.adapter in {'cloud', 'ollama'}]

    @app.get('/v1/settings/model-profiles')
    async def get_model_profiles():
        async with settings_lock:
            if app.state.config is None:
                raise HTTPException(503, 'Model configuration unavailable')
            return {'instance_id': app.state.runtime_instance_id, 'revision': settings_revision,
                    'profiles': editable_profiles()}

    @app.get('/v1/settings/model-profile-providers')
    async def get_model_profile_providers():
        from odessy.models.cloud_model import CLOUD_PROVIDERS
        return {'providers': [{'name': name, 'credential_env': provider.api_key_env}
                              for name, provider in sorted(CLOUD_PROVIDERS.items())]}

    @app.post('/v1/settings/model-profiles', status_code=201)
    async def create_model_profile(request: Request):
        from odessy.models.cloud_model import CLOUD_PROVIDERS
        data = await body(request)
        if (set(data) != {'instance_id', 'revision', 'name', 'adapter', 'provider', 'model', 'endpoint'}
                or not isinstance(data['instance_id'], str)
                or type(data['revision']) is not int or data['revision'] < 0
                or any(not isinstance(data[key], str) for key in ('name', 'adapter', 'provider', 'model', 'endpoint'))
                or data['name'] in {'current', 'auto', 'local'}
                or data['adapter'] not in {'cloud', 'ollama'}
                or (data['adapter'] == 'cloud' and data['provider'] not in CLOUD_PROVIDERS)
                or (data['adapter'] == 'cloud' and data['provider'] == 'custom'
                    and (not data['model'].strip() or not data['endpoint'].strip()))
                or (data['adapter'] == 'ollama' and data['provider'] != '')):
            raise HTTPException(400, 'Invalid profile')
        async with settings_lock:
            if app.state.config is None:
                raise HTTPException(503, 'Model configuration unavailable')
            if data['instance_id'] != app.state.runtime_instance_id or data['revision'] != settings_revision:
                raise HTTPException(409, 'Settings changed; reload before saving')
            if data['name'] in app.state.config.participants:
                raise HTTPException(409, 'Profile name already exists')
            document = app.state.config.public_dict()
            document['participants'][data['name']] = {key: data[key] for key in ('adapter', 'provider', 'model', 'endpoint')}
            try:
                await save_coordinated(document)
            except (ValueError, TypeError) as exc:
                raise HTTPException(400, str(exc))
            return {'instance_id': app.state.runtime_instance_id, 'revision': settings_revision,
                    'profiles': editable_profiles()}

    @app.delete('/v1/settings/model-profiles/{name}')
    async def delete_model_profile(name: str, request: Request):
        data = await body(request)
        if (set(data) != {'instance_id', 'revision'} or not isinstance(data['instance_id'], str)
                or type(data['revision']) is not int or data['revision'] < 0):
            raise HTTPException(400, 'Invalid profile deletion')
        async with settings_lock:
            current = app.state.config
            if current is None:
                raise HTTPException(503, 'Model configuration unavailable')
            if data['instance_id'] != app.state.runtime_instance_id or data['revision'] != settings_revision:
                raise HTTPException(409, 'Settings changed; reload before deleting')
            profile = current.participants.get(name)
            if profile is None or profile.adapter not in {'cloud', 'ollama'}:
                raise HTTPException(404, 'Editable profile not found')
            if (current.settings.default_participant == name or
                    any(rule.get('participant') == name for rule in current.settings.rules)):
                raise HTTPException(409, 'Profile is referenced by collaboration settings')
            document = current.public_dict()
            del document['participants'][name]
            try:
                await save_coordinated(document)
            except (ValueError, TypeError) as exc:
                raise HTTPException(400, str(exc))
            return {'instance_id': app.state.runtime_instance_id, 'revision': settings_revision,
                    'profiles': editable_profiles()}

    @app.put('/v1/settings/model-profiles/{name}')
    async def put_model_profile(name: str, request: Request):
        data = await body(request)
        if (set(data) != {'instance_id', 'revision', 'model', 'endpoint'}
                or not isinstance(data['instance_id'], str)
                or type(data['revision']) is not int or data['revision'] < 0
                or not isinstance(data['model'], str) or not isinstance(data['endpoint'], str)
                or len(data['model']) > 4096 or len(data['endpoint']) > 4096):
            raise HTTPException(400, 'Invalid profile update')
        async with settings_lock:
            if app.state.config is None:
                raise HTTPException(503, 'Model configuration unavailable')
            if (data['instance_id'] != app.state.runtime_instance_id
                    or data['revision'] != settings_revision):
                raise HTTPException(409, 'Settings changed; reload before saving')
            profile = app.state.config.participants.get(name)
            if profile is None or profile.adapter not in {'cloud', 'ollama'}:
                raise HTTPException(404, 'Editable profile not found')
            document = app.state.config.public_dict()
            document['participants'][name].update(model=data['model'], endpoint=data['endpoint'])
            try:
                await save_coordinated(document)
            except (ValueError, TypeError) as exc:
                raise HTTPException(400, str(exc))
            return {'instance_id': app.state.runtime_instance_id, 'revision': settings_revision,
                    'profiles': editable_profiles()}

    @app.get('/v1/settings/model-profiles/{name}/limits')
    async def get_model_limits(name: str):
        async with settings_lock:
            if app.state.config is None:
                raise HTTPException(503, 'Model configuration unavailable')
            profile = app.state.config.participants.get(name)
            if profile is None or profile.adapter not in {'cloud', 'ollama'}:
                raise HTTPException(404, 'Editable profile not found')
            return {'instance_id': app.state.runtime_instance_id, 'revision': settings_revision,
                    'timeout': profile.timeout, 'max_tokens': profile.max_tokens}

    @app.put('/v1/settings/model-profiles/{name}/limits')
    async def put_model_limits(name: str, request: Request):
        data = await body(request)
        if (set(data) != {'instance_id', 'revision', 'timeout', 'max_tokens'}
                or not isinstance(data['instance_id'], str)
                or type(data['revision']) is not int or data['revision'] < 0
                or type(data['timeout']) is not int or not 1 <= data['timeout'] <= 3600
                or type(data['max_tokens']) is not int or not 1 <= data['max_tokens'] <= 1_000_000):
            raise HTTPException(400, 'Invalid profile limits')
        async with settings_lock:
            if app.state.config is None:
                raise HTTPException(503, 'Model configuration unavailable')
            if data['instance_id'] != app.state.runtime_instance_id or data['revision'] != settings_revision:
                raise HTTPException(409, 'Settings changed; reload before saving')
            profile = app.state.config.participants.get(name)
            if profile is None or profile.adapter not in {'cloud', 'ollama'}:
                raise HTTPException(404, 'Editable profile not found')
            document = app.state.config.public_dict()
            document['participants'][name].update(timeout=data['timeout'], max_tokens=data['max_tokens'])
            try:
                await save_coordinated(document)
            except (ValueError, TypeError) as exc:
                raise HTTPException(400, str(exc))
            return {'instance_id': app.state.runtime_instance_id, 'revision': settings_revision,
                    'profiles': editable_profiles()}

    @app.put('/v1/settings/model-profiles/{name}/provider')
    async def switch_model_provider(name: str, request: Request):
        from odessy.models.cloud_model import CLOUD_PROVIDERS
        data = await body(request)
        if (set(data) != {'instance_id', 'revision', 'provider', 'model', 'endpoint'}
                or not isinstance(data['instance_id'], str)
                or type(data['revision']) is not int or data['revision'] < 0
                or any(not isinstance(data[key], str) for key in ('provider', 'model', 'endpoint'))
                or data['provider'] not in CLOUD_PROVIDERS
                or len(data['model']) > 4096 or len(data['endpoint']) > 4096
                or (data['provider'] == 'custom' and
                    (not data['model'].strip() or not data['endpoint'].strip()))):
            raise HTTPException(400, 'Invalid provider change')
        async with settings_lock:
            current = app.state.config
            if current is None:
                raise HTTPException(503, 'Model configuration unavailable')
            if data['instance_id'] != app.state.runtime_instance_id or data['revision'] != settings_revision:
                raise HTTPException(409, 'Settings changed; reload before saving')
            profile = current.participants.get(name)
            if profile is None or profile.adapter != 'cloud':
                raise HTTPException(404, 'Cloud profile not found')
            if profile.credential_env:
                raise HTTPException(409, 'Profile has a custom credential reference')
            if (profile.provider or 'openai') == data['provider']:
                raise HTTPException(400, 'Choose a different provider')
            document = current.public_dict()
            target = document['participants'][name]
            target.update(provider=data['provider'], model=data['model'], endpoint=data['endpoint'])
            target.pop('pricing', None)
            try:
                await save_coordinated(document)
            except (ValueError, TypeError) as exc:
                raise HTTPException(400, str(exc))
            return {'instance_id': app.state.runtime_instance_id, 'revision': settings_revision,
                    'profiles': editable_profiles()}

    @app.get('/v1/settings/model-profiles/{name}/pricing')
    async def get_model_pricing(name: str):
        async with settings_lock:
            current = app.state.config
            if current is None:
                raise HTTPException(503, 'Model configuration unavailable')
            profile = current.participants.get(name)
            if profile is None or profile.adapter != 'cloud':
                raise HTTPException(404, 'Cloud profile not found')
            return {'instance_id': app.state.runtime_instance_id, 'revision': settings_revision,
                    'pricing': asdict(profile.pricing) if profile.pricing is not None else None}

    @app.put('/v1/settings/model-profiles/{name}/pricing')
    async def put_model_pricing(name: str, request: Request):
        from odessy.models.usage import pricing_from_env, CloudUsageConfigError
        data = await body(request)
        if (set(data) != {'instance_id', 'revision', 'pricing'}
                or not isinstance(data['instance_id'], str)
                or type(data['revision']) is not int or data['revision'] < 0):
            raise HTTPException(400, 'Invalid pricing update')
        pricing = data['pricing']
        if pricing is not None:
            if (not isinstance(pricing, dict) or set(pricing) != {'input', 'cached_input', 'output', 'currency'}
                    or not isinstance(pricing['currency'], str)
                    or not re.fullmatch(r'[A-Z]{3,8}', pricing['currency'])
                    or any(isinstance(pricing[k], bool) or not isinstance(pricing[k], (int, float))
                           or not 0 <= pricing[k] <= 1e12 or not math.isfinite(pricing[k])
                           for k in ('input', 'cached_input', 'output'))):
                raise HTTPException(400, 'Invalid pricing update')
        async with settings_lock:
            current = app.state.config
            if current is None:
                raise HTTPException(503, 'Model configuration unavailable')
            if data['instance_id'] != app.state.runtime_instance_id or data['revision'] != settings_revision:
                raise HTTPException(409, 'Settings changed; reload before saving')
            profile = current.participants.get(name)
            if profile is None or profile.adapter != 'cloud':
                raise HTTPException(404, 'Cloud profile not found')
            if pricing is not None:
                currencies = {p.pricing.currency for other, p in current.participants.items()
                              if other != name and p.pricing is not None}
                try:
                    global_price = pricing_from_env()
                except CloudUsageConfigError:
                    raise HTTPException(503, 'Global pricing configuration is invalid') from None
                if global_price is not None:
                    currencies.add(global_price.currency)
                if any(currency != pricing['currency'] for currency in currencies):
                    raise HTTPException(409, 'Pricing currencies must match')
            document = current.public_dict()
            target = document['participants'][name]
            if pricing is None:
                target.pop('pricing', None)
            else:
                target['pricing'] = pricing
            try:
                await save_coordinated(document)
            except (ValueError, TypeError) as exc:
                raise HTTPException(400, str(exc))
            return {'instance_id': app.state.runtime_instance_id, 'revision': settings_revision,
                    'profiles': editable_profiles()}

    @app.get('/v1/settings/model-profiles/{name}/credential-reference')
    async def get_credential_reference(name: str):
        from odessy.models.cloud_model import CLOUD_PROVIDERS
        async with settings_lock:
            current = app.state.config
            if current is None:
                raise HTTPException(503, 'Model configuration unavailable')
            profile = current.participants.get(name)
            if profile is None or profile.adapter != 'cloud':
                raise HTTPException(404, 'Cloud profile not found')
            provider = CLOUD_PROVIDERS.get(profile.provider or 'openai')
            return {'instance_id': app.state.runtime_instance_id, 'revision': settings_revision,
                    'credential_env': profile.credential_env,
                    'default_credential_env': provider.api_key_env if provider is not None else ''}

    @app.put('/v1/settings/model-profiles/{name}/credential-reference')
    async def put_credential_reference(name: str, request: Request):
        data = await body(request)
        if (set(data) != {'instance_id', 'revision', 'credential_env'}
                or not isinstance(data['instance_id'], str)
                or type(data['revision']) is not int or data['revision'] < 0
                or not isinstance(data['credential_env'], str)
                or (data['credential_env'] and not re.fullmatch(
                    r'[A-Z][A-Z0-9_]{0,110}_(?:API_KEY|ACCESS_TOKEN)', data['credential_env']))
                or (data['credential_env'].startswith('ODESSY_')
                    and data['credential_env'] != 'ODESSY_CLOUD_API_KEY')):
            raise HTTPException(400, 'Invalid credential reference')
        async with settings_lock:
            current = app.state.config
            if current is None:
                raise HTTPException(503, 'Model configuration unavailable')
            if data['instance_id'] != app.state.runtime_instance_id or data['revision'] != settings_revision:
                raise HTTPException(409, 'Settings changed; reload before saving')
            profile = current.participants.get(name)
            if profile is None or profile.adapter != 'cloud':
                raise HTTPException(404, 'Cloud profile not found')
            document = current.public_dict()
            document['participants'][name]['credential_env'] = data['credential_env']
            try:
                await save_coordinated(document)
            except (ValueError, TypeError) as exc:
                raise HTTPException(400, str(exc))
            return {'instance_id': app.state.runtime_instance_id, 'revision': settings_revision,
                    'profiles': editable_profiles()}

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
        if desktop_voice is not None and desktop_voice.owner==sid:await desktop_voice.cancel(controller)
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
        if desktop_voice is not None and desktop_voice.owner==sid:
            if desktop_voice.wake is not None:await desktop_voice.typed(controller,True)
            else:await desktop_voice.cancel(controller)
        return admit_turn(controller,data)

    def admit_turn(controller,data,spoken=False):
        sid=controller.id
        admission_open()
        if not isinstance(data.get('text'), str) or not data['text'].strip():
            raise HTTPException(400, 'Nonempty text is required')
        if data.get('participant') is not None and not isinstance(data['participant'], str):
            raise HTTPException(400, 'participant must be a string')
        if controller.running or sid in tasks:
            raise HTTPException(409, 'A turn is already running')
        turn_id = secrets.token_hex(16)
        async def run():
            try:
                result=await controller.run_turn(data['text'], participant=data.get('participant'), turn_id=turn_id)
                if spoken and desktop_voice is not None:
                    await desktop_voice.speak(controller,result.final_text)
                return result.final_text
            except (Exception, asyncio.CancelledError):
                # Controller emits the public failure; retrieve the task exception.
                pass
            finally:
                tasks.pop(sid, None)
                if desktop_voice is not None:await desktop_voice.typed(controller,False)
        tasks[sid] = asyncio.create_task(run())
        return {'turn_id': turn_id, 'session_id': sid, 'status': 'accepted'}

    @app.get('/v1/sessions/{sid}/voice')
    async def voice_snapshot(sid: str):
        controller=find(sid)
        return desktop_voice.snapshot(controller) if desktop_voice is not None else {
            'supported':False,'state':'unavailable','message':'Enable existing voice components and restart the coordinator. Recording occurs on that computer.'}

    @app.post('/v1/sessions/{sid}/voice')
    async def voice_action(sid: str,request: Request):
        controller=find(sid);data=await body(request)
        if set(data)-{'action','capture_id','expected_interaction_id'}:raise HTTPException(400,'Unknown voice fields')
        if desktop_voice is None:raise HTTPException(503,'Desktop audio is not configured; typed input remains available')
        from .desktop_voice import VoiceError
        try:
            action=data.get('action')
            if action=='enable_wake':
                admission_open()
                async def submit_wake(text,stop_event,context):
                    c=controller
                    c.emit('voice_transcript',{'text':c._public(text),'source':'wake'})
                    admit_turn(c,{'text':text})
                    task=tasks.get(c.id)
                    return (await asyncio.shield(task)) or '' if task is not None else ''
                async def stop_wake_turn():
                    await controller.stop()
                    task=tasks.get(sid)
                    if task is not None:task.cancel()
                return await desktop_voice.start_wake(controller,submit_wake,stop_wake_turn)
            if action=='disable_wake':
                if desktop_voice.wake is None:return desktop_voice.snapshot(controller)
                return await desktop_voice.cancel(controller)
            if action in ('start_capture','test_input'):
                admission_open()
                if sid in tasks and not data.get('expected_interaction_id'):raise VoiceError('Current task is busy')
                return await desktop_voice.start(controller,data.get('expected_interaction_id'),preview=action=='test_input')
            if action=='finish_capture':
                if not isinstance(data.get('capture_id'),str):raise VoiceError('capture_id is required',400)
                async def submit(c,text):admit_turn(c,{'text':text},spoken=True)
                return await desktop_voice.finish(controller,data['capture_id'],submit)
            if action in ('cancel_capture','stop_playback'):
                if action=='cancel_capture' and not isinstance(data.get('capture_id'),str):raise VoiceError('capture_id is required',400)
                if action=='stop_playback':return await desktop_voice.stop_playback(controller)
                return await desktop_voice.cancel(controller,data.get('capture_id'))
            if action=='test_output':
                admission_open()
                await desktop_voice.speak(controller,'Odessy audio output test.')
                return desktop_voice.snapshot(controller)
            raise VoiceError('Unsupported voice action',400)
        except VoiceError as exc:raise HTTPException(exc.code,str(exc)) from None
        except Exception:
            controller.emit('audio_error',{'message':'Audio unavailable; typed input remains available.'})
            raise HTTPException(503,'Audio unavailable; typed input remains available.') from None

    @app.post('/v1/sessions/{sid}/stop')
    async def stop(sid: str):
        if desktop_voice is not None and desktop_voice.owner==sid:await desktop_voice.cancel(find(sid))
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
        nonlocal pending_admissions
        admission_open()
        pending_admissions += 1
        try:
            return await submit_admitted(data, sid)
        finally:
            pending_admissions -= 1

    async def submit_admitted(data, sid=None):
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
        except PermissionError as exc:
            raise HTTPException(403, str(exc))
        except (ValueError, TypeError) as exc:
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

    @app.get('/v1/sessions/{sid}/jobs')
    async def list_session_jobs(sid: str, request: Request):
        params = request.query_params
        if any(key not in {'after', 'limit'} for key in params) or any(
                len(params.getlist(key)) != 1 for key in params):
            raise HTTPException(400, 'Invalid job list parameters')
        raw_limit = params.get('limit', '50')
        if not raw_limit.isascii() or not raw_limit.isdecimal() or len(raw_limit) > 2:
            raise HTTPException(400, 'Invalid job page size')
        selected = manager(sid)
        if not callable(getattr(selected, 'list_summaries', None)):
            raise HTTPException(503, 'Job listing is not configured for this session')
        try:
            return public(selected.list_summaries(after=params.get('after'), limit=int(raw_limit)))
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None

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
