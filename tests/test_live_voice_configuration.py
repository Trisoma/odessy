import asyncio
from dataclasses import replace

import httpx
import pytest

from odessy.application.api import create_app
from odessy.application.desktop_voice import DesktopVoice
from odessy.application.voice_configuration import LiveVoiceConfiguration
from odessy.setup.contracts import SetupSelection
from odessy.application.capabilities import configuration_descriptor


def test_saved_voice_applies_without_changing_model_or_workspace_and_defers_busy():
    async def run():
        original = SetupSelection.from_dict({'role': 'combined', 'model': {'provider': 'ollama', 'name': 'old'}})
        saved = replace(original, voice=replace(original.voice, enabled=True, audio_input_device='new-device'))
        state = {'selection': saved}
        voice = DesktopVoice(lambda: None, host='fixture')
        live = LiveVoiceConfiguration(voice, original, '/original', 'owner', lambda: state['selection'])
        descriptor = configuration_descriptor(original, '/original', owner='owner')
        app = create_app(lambda: None, token='test', desktop_voice=voice,
                         runtime_configuration=descriptor, voice_configuration=live)
        path = '/v1/voice/configuration'
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            assert (await client.post(path, json={})).status_code == 401
            client.headers['Authorization'] = 'Bearer test'
            fingerprint = configuration_descriptor(saved, '/original', owner='owner')['fingerprint']
            assert (await client.post(path, json={'fingerprint': 'stale'})).status_code == 409
            voice.owner = 'recording-session'
            result = await client.post(path, json={'fingerprint': fingerprint})
            assert result.json()['status'] == 'deferred'
            assert live.selection.voice == original.voice
            voice.owner = None
            result = await client.post(path, json={'fingerprint': fingerprint})
            assert result.json()['status'] == 'applied'
            assert live.selection.voice.audio_input_device == 'new-device'
            assert app.state.runtime_configuration['fingerprint'] == fingerprint
            assert voice.enabled
            state['selection'] = replace(saved, model=replace(saved.model, name='new-model'))
            await live.refresh(app)
            assert live.selection.model.name == 'old'
            assert app.state.runtime_configuration['model']['name'] == 'old'
            state['selection'] = replace(saved, voice=replace(saved.voice, enabled=False))
            await live.refresh(app)
            assert not voice.enabled
            assert voice.snapshot(type('Controller', (), {'id': 's'})())['supported'] is False
    asyncio.run(run())


def test_invalid_saved_configuration_keeps_active_audio():
    async def run():
        selection = SetupSelection(role='combined')
        voice = DesktopVoice(lambda: None, host='fixture')
        def invalid():
            raise ValueError('damaged settings')
        live = LiveVoiceConfiguration(voice, selection, '/workspace', 'owner', invalid)
        app = type('App', (), {'state': type('State', (), {})()})()
        try:
            await live.refresh(app)
        except ValueError:
            pass
        else:
            raise AssertionError('invalid configuration must not be acknowledged')
        assert live.selection == selection
    asyncio.run(run())


def test_voice_selection_clears_old_device_and_uses_default_wake_asset(monkeypatch):
    from odessy.voice.config import VoiceConfig
    monkeypatch.setenv('ODESSY_AUDIO_INPUT_DEVICE', 'stale-device')
    config = VoiceConfig.from_selection(SetupSelection(role='combined').voice)
    assert config.audio_input_device == ''
    assert str(config.wake_model).endswith('hey_jarvis_v0.1.onnx')


def test_installed_loader_reads_new_saved_selection(tmp_path, monkeypatch):
    import json
    from test_setup_installed import make_installation
    from odessy.setup import installed
    root = tmp_path / 'app'
    make_installation(root, model=True)
    captured = {}
    monkeypatch.setenv('ODESSY_API_TOKEN', 'fixture')
    monkeypatch.setattr('odessy.setup.service_entry.run', lambda *a, **kw: captured.update(kw))
    installed.serve(['--root', str(root), '--port', '18765'])
    selection = SetupSelection.from_dict({'role': 'combined', 'model': {'provider': 'ollama', 'name': 'same'},
                                         'voice': {'enabled': True, 'audio_input_device': 'fresh'}})
    from odessy.setup.contracts import public
    (root / 'configuration.json').write_text(json.dumps({'schema_version': 1, 'selection': public(selection), 'workspace': str(tmp_path / 'work')}))
    assert captured['voice_selection_loader']().voice.audio_input_device == 'fresh'


def test_deferred_reload_is_applied_automatically_after_owner_releases():
    async def run():
        selection = SetupSelection(role='combined')
        saved = replace(selection, voice=replace(selection.voice, enabled=True))
        voice = DesktopVoice(lambda: None, host='fixture')
        live = LiveVoiceConfiguration(voice, selection, '/work', 'owner', lambda: saved)
        app = create_app(lambda: None, token='fixture', desktop_voice=voice,
                         runtime_configuration=configuration_descriptor(selection, '/work', owner='owner'),
                         voice_configuration=live)
        voice.owner = 'busy'
        async with app.router.lifespan_context(app):
            await asyncio.sleep(0.6)
            assert live.selection == selection
            voice.owner = None
            await asyncio.sleep(0.6)
            assert live.selection.voice.enabled
        assert voice.owner is None
    asyncio.run(run())


def test_explicit_save_refreshes_unchanged_idle_devices_without_restarting(monkeypatch):
    from odessy.voice import native_audio
    calls = []
    monkeypatch.setattr(native_audio, 'refresh_devices', lambda backend: calls.append(backend), raising=False)
    async def run():
        selection = SetupSelection(role='combined')
        selection = replace(selection, voice=replace(selection.voice, enabled=True))
        voice = DesktopVoice(lambda: None, host='fixture')
        live = LiveVoiceConfiguration(voice, selection, '/work', 'owner', lambda: selection)
        descriptor = configuration_descriptor(selection, '/work', owner='owner')
        app = create_app(lambda: None, token='test', desktop_voice=voice,
                         runtime_configuration=descriptor, voice_configuration=live)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test',
                                     headers={'Authorization': 'Bearer test'}) as client:
            response = await client.post('/v1/voice/configuration', json={'fingerprint': descriptor['fingerprint']})
            assert response.status_code == 200
            assert response.json()['status'] == 'applied'
            assert calls == ['auto']
            await live.refresh(app)
            assert calls == ['auto'], 'ordinary polling must not reset audio'
            assert live.selection == selection
    asyncio.run(run())


def test_unchanged_save_defers_during_active_audio_and_refreshes_after_release(monkeypatch):
    from odessy.voice import native_audio
    calls = []
    monkeypatch.setattr(native_audio, 'refresh_devices', lambda backend: calls.append(backend), raising=False)
    async def run():
        selection = SetupSelection(role='combined')
        selection = replace(selection, voice=replace(selection.voice, enabled=True))
        voice = DesktopVoice(lambda: None, host='fixture')
        live = LiveVoiceConfiguration(voice, selection, '/work', 'owner', lambda: selection)
        descriptor = configuration_descriptor(selection, '/work', owner='owner')
        app = create_app(lambda: None, token='test', desktop_voice=voice,
                         runtime_configuration=descriptor, voice_configuration=live)
        for state in ['recording', 'listening', 'speaking', 'cancelling']:
            voice.owner = 'active-session'; voice.state = state
            result = await live.refresh(app, descriptor['fingerprint'])
            assert result['status'] == 'deferred'
            assert voice.owner == 'active-session' and voice.state == state
            assert not calls
        voice.owner = None; voice.state = 'idle'
        assert (await live.refresh(app))['status'] == 'applied'
        assert calls == ['auto']
        await live.refresh(app)
        assert calls == ['auto']
    asyncio.run(run())


def test_failed_device_refresh_is_not_acknowledged_and_can_retry(monkeypatch):
    from odessy.voice import native_audio
    attempts = []
    def refresh(backend):
        attempts.append(backend)
        if len(attempts) == 1: raise RuntimeError('device refresh failed')
    monkeypatch.setattr(native_audio, 'refresh_devices', refresh, raising=False)
    async def run():
        selection = SetupSelection(role='combined')
        selection = replace(selection, voice=replace(selection.voice, enabled=True))
        voice = DesktopVoice(lambda: None, host='fixture')
        class IdleAudio:
            closed = False
            async def close(self):
                self.closed = True
        previous = IdleAudio()
        voice.audio = previous
        live = LiveVoiceConfiguration(voice, selection, '/work', 'owner', lambda: selection)
        descriptor = configuration_descriptor(selection, '/work', owner='owner')
        app = create_app(lambda: None, token='test', desktop_voice=voice,
                         runtime_configuration=descriptor, voice_configuration=live)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test',
                                     headers={'Authorization': 'Bearer test'}) as client:
            response = await client.post('/v1/voice/configuration', json={'fingerprint': descriptor['fingerprint']})
            assert response.status_code == 503
            assert not voice.reconfiguring
            assert live.selection == selection
            assert voice.audio is previous and not previous.closed
            response = await client.post('/v1/voice/configuration', json={'fingerprint': descriptor['fingerprint']})
            assert response.status_code == 200 and response.json()['status'] == 'applied'
            assert attempts == ['auto', 'auto']
            assert previous.closed and voice.audio is None
    asyncio.run(run())


def test_save_protects_unowned_preparing_decoding_speaking_and_draining(monkeypatch):
    from odessy.voice import native_audio
    calls=[]
    monkeypatch.setattr(native_audio,'refresh_devices',lambda backend:calls.append(backend))
    async def run():
        selection=SetupSelection(role='combined')
        selection=replace(selection,voice=replace(selection.voice,enabled=True))
        voice=DesktopVoice(lambda:None,host='fixture')
        live=LiveVoiceConfiguration(voice,selection,'/work','owner',lambda:selection)
        descriptor=configuration_descriptor(selection,'/work',owner='owner')
        app=create_app(lambda:None,token='test',desktop_voice=voice,
                       runtime_configuration=descriptor,voice_configuration=live)
        for attribute in ['_preparing','_decoding','_speaking','_draining']:
            setattr(voice,attribute,True)
            assert voice.busy
            assert (await live.refresh(app,descriptor['fingerprint']))['status']=='deferred'
            assert not calls
            setattr(voice,attribute,None if attribute=='_draining' else False)
        await live.refresh(app)
        assert calls==['auto']
    asyncio.run(run())


def test_changed_saved_device_refreshes_before_new_capture_and_keeps_configuration_owner(monkeypatch):
    from odessy.voice import native_audio
    from odessy.voice.desktop_audio import DesktopAudio
    calls=[]
    monkeypatch.setattr(native_audio,'refresh_devices',lambda backend:calls.append('refresh'))
    async def start(audio):
        assert audio.config.audio_input_device=='new-headset'
        calls.append('capture')
    monkeypatch.setattr(DesktopAudio,'start',start)
    async def run():
        from odessy.application.session import SessionController
        original=SetupSelection.from_dict({'role':'combined','model':{'provider':'ollama','name':'kept'},'voice':{'enabled':True,'audio_input_device':'old-headset'}})
        saved=replace(original,voice=replace(original.voice,audio_input_device='new-headset'))
        voice=DesktopVoice(lambda:None,host='fixture')
        live=LiveVoiceConfiguration(voice,original,'/kept-workspace','owner',lambda:saved)
        def session():return SessionController(model_factory=lambda _:object(),agent_factory=lambda *a,**k:object(),profiles={'local':{}})
        app=create_app(session,token='test',desktop_voice=voice,runtime_configuration=configuration_descriptor(original,'/kept-workspace',owner='owner'),voice_configuration=live)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test',headers={'Authorization':'Bearer test'}) as client:
            sid=(await client.post('/v1/sessions')).json()['id']
            fingerprint=configuration_descriptor(saved,'/kept-workspace',owner='owner')['fingerprint']
            assert (await client.post('/v1/voice/configuration',json={'fingerprint':fingerprint})).json()['status']=='applied'
            capture=await client.post(f'/v1/sessions/{sid}/voice',json={'action':'test_input'})
            assert capture.status_code==200 and capture.json()['state']=='recording'
            assert calls==['refresh','capture']
            assert app.state.runtime_configuration['workspace'].endswith('kept-workspace')
            assert app.state.runtime_configuration['model']['name']=='kept'
            assert app.state.runtime_configuration['owner']=='owner'
            await client.post(f'/v1/sessions/{sid}/voice',json={'action':'cancel_capture','capture_id':capture.json()['capture_id']})
            await client.delete(f'/v1/sessions/{sid}')
        await voice.close()
    asyncio.run(run())


@pytest.mark.parametrize('action', ['capture', 'wake', 'playback'])
def test_save_keeps_actual_audio_operation_until_stop_then_uses_saved_device(monkeypatch, action):
    from odessy.application.session import SessionController
    from odessy.voice import native_audio, desktop_audio

    async def run():
        refreshed = []
        speaking, release = asyncio.Event(), asyncio.Event()

        class Wake:
            closed = False
            async def start(self):
                return True
            async def aclose(self, **options):
                self.closed = True

        class Audio:
            def __init__(self, config):
                self.config, self.closed = config, False
            async def start(self):
                pass
            async def cancel(self):
                pass
            async def close(self):
                self.closed = True
            async def speak(self, text):
                speaking.set()
                await release.wait()
            def make_wake(self, **options):
                self.wake = Wake()
                return self.wake

        monkeypatch.setattr(native_audio, 'refresh_devices', lambda backend: refreshed.append(backend))
        monkeypatch.setattr(desktop_audio, 'DesktopAudio', Audio)
        from odessy.voice.config import VoiceConfig
        original = SetupSelection.from_dict({'role': 'combined', 'voice': {
            'enabled': True, 'audio_input_device': 'old-input', 'audio_output_device': 'old-output'}})
        saved = replace(original, voice=replace(original.voice,
            audio_input_device='new-input', audio_output_device='new-output'))
        old = Audio(VoiceConfig.from_selection(original.voice))
        voice = DesktopVoice(lambda: old, host='fixture')
        live = LiveVoiceConfiguration(voice, original, '/work', 'owner', lambda: saved)
        app = create_app(lambda: None, token='test', desktop_voice=voice,
            runtime_configuration=configuration_descriptor(original, '/work', owner='owner'),
            voice_configuration=live)
        controller = SessionController(model_factory=lambda _: object(),
            agent_factory=lambda *a, **k: object(), profiles={'local': {}})
        playback = None
        async def start():
            nonlocal playback
            if action == 'capture':
                await voice.start(controller, preview=True)
            elif action == 'wake':
                await voice.start_wake(controller, lambda *a: None, lambda: None)
            else:
                speaking.clear(); release.clear()
                playback = asyncio.create_task(voice.speak(controller, 'fixture'))
                await speaking.wait()
        async def stop():
            if playback is not None:
                release.set()
                await playback
            else:
                await voice.cancel(controller)
        try:
            await start()
            before = voice.snapshot(controller)
            fingerprint = configuration_descriptor(saved, '/work', owner='owner')['fingerprint']
            assert (await live.refresh(app, fingerprint))['status'] == 'deferred'
            assert voice.snapshot(controller) == before
            assert voice.audio is old and not old.closed and not refreshed
            if action == 'wake':
                assert not old.wake.closed
            await stop()
            assert (await live.refresh(app))['status'] == 'applied'
            assert refreshed == ['auto'] and old.closed
            await start()
            assert voice.audio is not old
            assert voice.audio.config.audio_input_device == 'new-input'
            assert voice.audio.config.audio_output_device == 'new-output'
            await stop()
        finally:
            release.set()
            if playback is not None:
                await playback
            await voice.close()
            await controller.close()
    asyncio.run(run())
