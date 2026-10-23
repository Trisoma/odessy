import asyncio
import io
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from odessy import cli
from odessy.config.schema import validate_config


class Tty(io.StringIO):
    def isatty(self):
        return True


def test_hybrid_validation_degrades_only_voice_resources(tmp_path):
    env = {'ODESSY_WHISPER_MODEL': str(tmp_path / 'missing'),
           'ODESSY_KOKORO_MODEL': str(tmp_path), 'ODESSY_MODEL_NUM_CTX': 'bad'}
    report = validate_config(env, contexts={'voice', 'wake', 'hybrid'})
    assert {issue.setting for issue in report.errors} == {'ODESSY_MODEL_NUM_CTX'}
    assert {issue.setting for issue in report.warnings} >= {'ODESSY_WHISPER_MODEL', 'ODESSY_KOKORO_MODEL'}
    assert validate_config({'ODESSY_WHISPER_MODEL': str(tmp_path / 'missing')}, contexts={'voice'}).errors


def test_cli_selects_hybrid_only_for_interactive_voice(monkeypatch):
    monkeypatch.setattr(cli, 'load_config_env', lambda: None)
    monkeypatch.setattr(cli, '_check_configuration', lambda argv: None)
    monkeypatch.setattr(cli.sys, 'stdin', Tty())
    seen = []
    async def hybrid(*args, **kwargs):
        seen.append(('hybrid', kwargs))
    async def legacy(*args, **kwargs):
        seen.append(('legacy', kwargs))
    monkeypatch.setattr(cli, '_hybrid_voice_async', hybrid)
    monkeypatch.setattr(cli, '_listen_async', legacy)
    monkeypatch.setattr(cli, '_voice_async', legacy)
    cli.main(['voice', '--listen', '--fake-model'])
    cli.main(['voice', '--fake-model'])
    cli.main(['voice', '--listen', '--no-text-fallback'])
    monkeypatch.setattr(cli.sys, 'stdin', io.StringIO('not an approval'))
    cli.main(['voice', '--listen'])
    assert [item[0] for item in seen] == ['hybrid', 'hybrid', 'legacy', 'legacy']
    assert seen[0][1]['listen'] is True and seen[1][1]['listen'] is False


def test_configuration_requires_assets_when_fallback_is_disabled(monkeypatch):
    contexts = []
    monkeypatch.setattr(cli.sys, 'stdin', Tty())
    monkeypatch.setattr('odessy.config.schema.enforce_config', lambda **kw: contexts.append(kw['contexts']))
    cli._check_configuration(['voice', '--listen'])
    cli._check_configuration(['voice', '--listen', '--no-text-fallback'])
    assert 'hybrid' in contexts[0] and 'hybrid' not in contexts[1]


def test_controller_is_created_before_audio_and_keeps_voice_budget(monkeypatch):
    from odessy.application import hybrid
    calls = []
    controller = SimpleNamespace(memory=object(), interactions=object())
    @asynccontextmanager
    async def runtime(**kwargs):
        calls.append(('controller', kwargs['mode']))
        yield controller
    class Console:
        def __init__(self, current, **kwargs):
            assert current is controller
            calls.append(('console', kwargs['persistent']))
        async def submit_voice(self, *args):
            pass
        async def stop_turn(self):
            pass
        def report_audio_error(self, *args):
            pass
        def display(self, *args):
            pass
        async def run(self):
            calls.append(('run', None))
    class Audio:
        def __init__(self, **kwargs):
            assert calls[0] == ('controller', 'voice')
            assert kwargs['memory'] is controller.memory
            calls.append(('audio', None))
    monkeypatch.setattr(cli, 'controller_runtime', runtime)
    monkeypatch.setattr(cli, '_build_model', lambda *a, **k: object())
    monkeypatch.setattr(cli.VoiceConfig, 'from_env', lambda: SimpleNamespace(ollama_model='fake', interaction='wake'))
    monkeypatch.setattr(hybrid, 'HybridConsole', Console)
    asyncio.run(cli._hybrid_voice_async(True, listen=True, audio_factory=Audio))
    assert calls == [('controller', 'voice'), ('console', True), ('audio', None), ('run', None)]
