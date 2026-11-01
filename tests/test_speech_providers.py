import sys, types
from dataclasses import replace
from pathlib import Path
import pytest
from odessy.voice.config import VoiceConfig
from odessy.voice.stt import STTResult, WhisperCppSTT
from odessy.voice.tts import TTSResult, KokoroTTS
from odessy.voice import providers

def config(**kw):
    return VoiceConfig('whisper-cli', 'tiny.bin', 'kokoro.onnx', 'voices.bin', **kw)

def test_defaults_keep_existing_adapters_and_onnx_settings():
    c = config(onnx_provider='cpu')
    assert isinstance(providers.create_stt(c), WhisperCppSTT)
    assert isinstance(providers.create_tts(c), KokoroTTS)
    assert providers.create_tts(c).provider == 'cpu'

def test_configured_module_factories_and_cleanup(monkeypatch, tmp_path):
    calls = []
    module = types.ModuleType('odessy_test_speech')

    class Speech:

        def transcribe(self, path):
            return STTResult('Hi', 'ok')

        def synthesize(self, text, path):
            path.write_bytes(b'audio')
            return TTSResult(path, 'ok')

        def close(self):
            calls.append('closed')
    module.make = lambda c: Speech()
    monkeypatch.setitem(sys.modules, module.__name__, module)
    c = config(stt_provider='odessy_test_speech:make', tts_provider='odessy_test_speech:make', stt_model='custom.bin')
    stt = providers.create_stt(c)
    tts = providers.create_tts(c)
    assert stt.transcribe(tmp_path / 'input.wav').text == 'Hi'
    assert tts.synthesize('hello', tmp_path / 'out.wav').status == 'ok'
    providers.close_providers(stt, tts)
    providers.close_providers(stt, tts)
    assert calls == ['closed', 'closed']

def test_unknown_factory_error_and_wrong_contract(monkeypatch, tmp_path):
    with pytest.raises(providers.ProviderError):
        providers.create_stt(config(stt_provider='missing_provider_xyz'))
    module = types.ModuleType('bad_speech')
    module.make = lambda c: object()
    monkeypatch.setitem(sys.modules, 'bad_speech', module)
    with pytest.raises(providers.ProviderError):
        providers.create_tts(config(tts_provider='bad_speech:make'))

def test_custom_errors_are_normalized_and_cancel_is_explicit(monkeypatch, tmp_path):
    module = types.ModuleType('failing_speech')
    cancelled = []

    class Speech:

        def transcribe(self, path):
            raise RuntimeError('speech service down')

        def cancel(self):
            cancelled.append(True)
    module.make = lambda c: Speech()
    monkeypatch.setitem(sys.modules, 'failing_speech', module)
    stt = providers.create_stt(config(stt_provider='failing_speech:make'))
    assert stt.transcribe(tmp_path / 'audio.wav').status == 'failed'
    providers.cancel_provider(stt)
    assert cancelled == [True]

def test_setup_exports_custom_providers_without_default_asset_requirements():
    from odessy.setup.contracts import SetupSelection, VoiceSelection
    from odessy.setup.planning import build_plan
    from odessy.setup.verification import verify
    voice = VoiceSelection(enabled=True, stt_provider='my_speech:stt', tts_provider='my_speech:tts', stt_model='/external/model')
    selection = SetupSelection(role='combined', voice=voice)
    plan = build_plan(selection)
    assert plan.environment['ODESSY_STT_PROVIDER'] == 'my_speech:stt'
    assert plan.environment['ODESSY_STT_MODEL'] == '/external/model'
    assert 'kokoro' not in plan.components and 'whisper.cpp' not in plan.components
    assert 'whisper_bin' not in verify(selection)['voice'].get('missing', [])

def test_config_validation_does_not_require_unused_default_assets(tmp_path):
    from odessy.config.schema import validate_config
    report = validate_config({'ODESSY_STT_PROVIDER': 'somewhere:stt', 'ODESSY_TTS_PROVIDER': 'somewhere:tts'}, contexts={'voice'})
    assert not any((i.level == 'error' and i.setting.startswith(('ODESSY_WHISPER', 'ODESSY_KOKORO')) for i in report.issues))

@pytest.mark.anyio
async def test_custom_providers_drive_cli_without_builtin_assets(monkeypatch, tmp_path):
    from odessy import cli
    from types import SimpleNamespace
    module = types.ModuleType('cli_speech')
    events = []

    class Speech:

        def transcribe(self, path):
            events.append('transcribe')
            return STTResult('Hi', 'ok')

        def synthesize(self, text, path):
            events.append('synthesize')
            path.write_bytes(b'wav')
            return TTSResult(path, 'ok')

        def close(self):
            events.append('close')

    class Audio:

        def __init__(self, **kw):
            pass

        def record(self, path, seconds):
            path.write_bytes(b'wav')

        def play(self, path):
            events.append('play')

    class Agent:

        async def run_turn(self, text, **kw):
            events.append('model')
            return SimpleNamespace(final_text='Hello')
    module.make = lambda c: Speech()
    monkeypatch.setitem(sys.modules, 'cli_speech', module)
    monkeypatch.setattr(cli, 'CommandAudioIO', Audio)
    await cli._run_voice_turn(Agent(), config(stt_provider='cli_speech:make', tts_provider='cli_speech:make', tmp_dir=tmp_path))
    assert events == ['transcribe', 'model', 'synthesize', 'play', 'close', 'close']
    assert not list(tmp_path.glob('*.wav'))

def test_invalid_capabilities_close_initialized_provider(monkeypatch):
    closed = []
    module = types.ModuleType('invalid_caps')

    class Speech:
        capabilities = providers.SpeechCapabilities(audio_format='mp3')

        def transcribe(self, path):
            pass

        def close(self):
            closed.append(True)
    module.make = lambda c: Speech()
    monkeypatch.setitem(sys.modules, module.__name__, module)
    with pytest.raises(providers.ProviderError):
        providers.create_stt(config(stt_provider='invalid_caps:make'))
    assert closed == [True]

def test_environment_and_setup_roundtrip(monkeypatch):
    from odessy.setup.contracts import SetupSelection, public
    from odessy.setup.configuration import environment_text
    from odessy.setup.planning import build_plan
    monkeypatch.setenv('ODESSY_STT_PROVIDER', 'plugin:stt')
    monkeypatch.setenv('ODESSY_TTS_PROVIDER', 'plugin:tts')
    monkeypatch.setenv('ODESSY_STT_MODEL', 'custom.bin')
    c = VoiceConfig.from_env()
    assert (c.stt_provider, c.tts_provider, c.stt_model) == ('plugin:stt', 'plugin:tts', 'custom.bin')
    selection = SetupSelection.from_dict({'role': 'combined', 'model': {'provider': 'ollama', 'name': 'existing', 'endpoint': 'http://127.0.0.1:11434'}, 'voice': {'enabled': True, 'stt_provider': c.stt_provider, 'tts_provider': c.tts_provider}})
    assert SetupSelection.from_dict(public(selection)) == selection
    assert 'ODESSY_STT_PROVIDER' in environment_text(build_plan(selection))


def test_registered_provider_and_close_waits_for_worker(monkeypatch, tmp_path):
    import threading
    entered, release, closed = threading.Event(), threading.Event(), threading.Event()
    class Speech:
        capabilities = providers.SpeechCapabilities(cancellation=True)
        def transcribe(self, path):
            entered.set()
            assert release.wait(2)
            assert not closed.is_set()
            return STTResult('Hi', 'ok')
        def cancel(self):
            release.set()
        def close(self):
            closed.set()
    monkeypatch.setitem(providers._REGISTRY, 'stt', {})
    providers.register_stt('testing', lambda c: Speech())
    stt = providers.create_stt(config(stt_provider='testing'))
    worker = threading.Thread(target=stt.transcribe, args=(tmp_path / 'audio.wav',))
    worker.start()
    assert entered.wait(2)
    providers.cancel_provider(stt)
    providers.close_providers(stt)
    worker.join(2)
    assert not worker.is_alive() and closed.is_set()
