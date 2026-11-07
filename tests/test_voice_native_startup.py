"""Windows native imports must finish before the HTTP service starts workers."""
import asyncio
import threading

from odessy.voice import native_audio


def test_windows_audio_imports_are_initialized_on_the_main_thread(monkeypatch):
    monkeypatch.setattr(native_audio.sys, 'platform', 'win32')
    imported = []

    def load(name):
        assert threading.current_thread() is threading.main_thread()
        imported.append(name)

    monkeypatch.setattr(native_audio.importlib, 'import_module', load)
    assert native_audio.prepare_native_dependencies() == ()
    assert imported == ['numpy', 'onnxruntime', 'sounddevice',
                        'openwakeword.model', 'openwakeword.vad', 'kokoro_onnx']


def test_missing_optional_audio_does_not_prevent_text_service_startup(monkeypatch):
    monkeypatch.setattr(native_audio.sys, 'platform', 'win32')

    def missing(name):
        raise ImportError('optional library unavailable')

    monkeypatch.setattr(native_audio.importlib, 'import_module', missing)
    assert 'numpy' in native_audio.prepare_native_dependencies()


def test_non_windows_keeps_existing_lazy_audio_imports(monkeypatch):
    monkeypatch.setattr(native_audio.sys, 'platform', 'linux')
    monkeypatch.setattr(native_audio.importlib, 'import_module',
                        lambda name: (_ for _ in ()).throw(AssertionError(name)))
    assert native_audio.prepare_native_dependencies() == ()


def test_voice_dependencies_are_prepared_before_serving_requests(monkeypatch, tmp_path):
    import uvicorn
    from odessy.application import api, serve
    from odessy.collaboration import config

    order = []
    monkeypatch.setattr(native_audio, 'prepare_native_dependencies',
                        lambda: order.append('native imports') or ())
    monkeypatch.setattr(config, 'load_config', lambda: None)
    monkeypatch.setattr(api, 'create_app', lambda *args, **kwargs: object())
    monkeypatch.setattr(uvicorn, 'Config', lambda *args, **kwargs: object())

    class Server:
        def __init__(self, configuration):
            order.append('server')

        async def serve(self):
            order.append('requests')

    monkeypatch.setattr(uvicorn, 'Server', Server)
    asyncio.run(serve.run_server(token='fixture', workspace=tmp_path,
                                 desktop_voice=object()))
    assert order == ['native imports', 'server', 'requests']
