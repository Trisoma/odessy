"""Device inventory and asset discovery: observation only, additive contracts."""
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


AREcord = 'default\n    Default Audio Device\n    and more\nsysdefault:CARD=PCH\n    HDA Intel PCH\nhw:CARD=PCH,DEV=0\n    Direct hardware device\n'
APlay = 'default\n    Default Audio Device\nhw:CARD=HDMI,DEV=3\n    HDMI output\n'


def fake_listing(calls, outputs=None, codes=None):
    outputs = outputs if outputs is not None else {'arecord': AREcord, 'aplay': APlay}
    codes = codes or {}

    def run(argv, **kwargs):
        calls.append(list(argv))
        tool = argv[0]
        return subprocess.CompletedProcess(argv, codes.get(tool, 0), outputs.get(tool, ''), '')

    return run


def command_backend(monkeypatch, **kwargs):
    from odessy.voice import native_audio
    monkeypatch.setattr(native_audio.shutil, 'which', lambda name: f'/usr/bin/{name}')
    monkeypatch.setattr(native_audio.subprocess, 'run', fake_listing(**kwargs))
    return native_audio


def test_command_backend_lists_alsa_names_and_never_opens_a_device(monkeypatch):
    calls = []
    native_audio = command_backend(monkeypatch, calls=calls)

    def refuse(*args, **kwargs):
        raise AssertionError('inventory must not start any process beyond a listing')

    monkeypatch.setattr(native_audio.subprocess, 'Popen', refuse)
    data = native_audio.inventory('command')

    assert [call[1] for call in calls] == ['-L', '-L']
    assert {call[0] for call in calls} == {'arecord', 'aplay'}
    assert data['backend'] == 'command'
    assert data['status'] == 'available_unverified'

    by_id = {device['id']: device for device in data['devices']}
    # Identifiers are literal ALSA names: exactly what `-D` consumes downstream.
    assert set(by_id) == {'default', 'sysdefault:CARD=PCH', 'hw:CARD=PCH,DEV=0', 'hw:CARD=HDMI,DEV=3'}
    # A name listed by both tools becomes one device that serves both directions.
    assert by_id['default']['input'] and by_id['default']['output']
    assert by_id['default']['is_default'] is True
    assert by_id['hw:CARD=PCH,DEV=0']['input'] and not by_id['hw:CARD=PCH,DEV=0']['output']
    assert by_id['hw:CARD=HDMI,DEV=3']['output'] and not by_id['hw:CARD=HDMI,DEV=3']['input']
    # Readable descriptions replace the raw identifier; the identifier is untouched.
    assert by_id['sysdefault:CARD=PCH']['name'] == 'HDA Intel PCH'
    assert data['directions']['input'] == {
        'available': True, 'status': 'available_unverified', 'count': 3,
        'message': '3 input device(s) listed; nothing was opened.'}
    assert data['directions']['output']['count'] == 2


def test_command_backend_without_tools_keeps_old_status_and_explains_why(monkeypatch):
    from odessy.voice import native_audio
    monkeypatch.setattr(native_audio.shutil, 'which', lambda name: None)
    data = native_audio.inventory('command')
    assert data['devices'] == []
    # Unchanged from before this feature, so existing consumers keep working.
    assert data['status'] == 'configured_unverified'
    assert [key for key in ('backend', 'devices', 'message', 'status', 'directions')
            if key not in data] == []
    assert data['directions']['input']['available'] is False
    assert 'arecord is not installed' in data['directions']['input']['message']
    assert 'arecord is not installed' in data['message']


def test_failed_enumeration_reports_partial_results_rather_than_raising(monkeypatch):
    calls = []
    native_audio = command_backend(
        monkeypatch, calls=calls,
        outputs={'arecord': AREcord, 'aplay': ''}, codes={'aplay': 1})
    data = native_audio.inventory('command')
    assert [device['id'] for device in data['devices']] == [
        'default', 'sysdefault:CARD=PCH', 'hw:CARD=PCH,DEV=0']
    assert data['status'] == 'available_unverified'
    assert 'aplay -L exited with 1' in data['message']
    assert data['directions']['output']['available'] is False


def test_enumeration_that_cannot_start_does_not_raise(monkeypatch):
    from odessy.voice import native_audio
    monkeypatch.setattr(native_audio.shutil, 'which', lambda name: f'/usr/bin/{name}')

    def explode(argv, **kwargs):
        raise OSError('no such binary')

    monkeypatch.setattr(native_audio.subprocess, 'run', explode)
    data = native_audio.inventory('command')
    assert data['devices'] == []
    assert data['status'] == 'configured_unverified'


def test_alsa_listing_is_bounded_and_keeps_first_description():
    from odessy.voice.native_audio import alsa_listing
    entries = alsa_listing(AREcord)
    assert entries[0] == ('default', 'Default Audio Device')
    assert entries[1] == ('sysdefault:CARD=PCH', 'HDA Intel PCH')
    assert len(alsa_listing(''.join(f'd{n}\n    x\n' for n in range(500)))) == 256


def test_native_inventory_marks_capabilities_defaults_and_duplicate_names(monkeypatch):
    from odessy.voice import native_audio
    devices = [
        dict(name='Headset', hostapi=0, max_input_channels=1, max_output_channels=2),
        dict(name='Headset', hostapi=1, max_input_channels=0, max_output_channels=2),
        dict(name='Webcam Mic', hostapi=0, max_input_channels=1, max_output_channels=0),
        dict(name='S/PDIF', hostapi=0, max_input_channels=0, max_output_channels=1),
    ]
    module = SimpleNamespace(query_devices=lambda: devices,
                             query_hostapis=lambda: [{'name': 'Core'}, {'name': 'WASAPI'}],
                             default=SimpleNamespace(device=(2, 0)))
    monkeypatch.setitem(sys.modules, 'sounddevice', module)
    data = native_audio.inventory('native')

    assert data['backend'] == 'native'
    assert len(data['devices']) == 4                      # duplicates are not collapsed
    assert data['devices'][0]['id'] == 'Core::Headset'
    assert data['devices'][1]['id'] == 'WASAPI::Headset'
    assert data['devices'][1]['input'] is False and data['devices'][1]['output'] is True
    # Defaults come from the module, not from a guess about ordering.
    assert data['devices'][2].get('is_default') is True
    assert data['devices'][0].get('is_default') is True
    assert 'is_default' not in data['devices'][1]
    assert data['directions']['input']['count'] == 2
    assert data['directions']['output']['count'] == 3


def test_native_inventory_isolates_a_module_that_has_no_default(monkeypatch):
    from odessy.voice import native_audio
    module = SimpleNamespace(
        query_devices=lambda: [dict(name='Only', hostapi=0, max_input_channels=1, max_output_channels=0)],
        query_hostapis=lambda: [{'name': 'ALSA'}])
    monkeypatch.setitem(sys.modules, 'sounddevice', module)
    data = native_audio.inventory('native')
    assert data['devices'][0]['id'] == 'ALSA::Only'
    assert data['directions']['output']['available'] is False
    assert 'PortAudio listed no device' in data['directions']['output']['message']


def test_missing_native_library_is_reported_not_raised(monkeypatch):
    from odessy.voice import native_audio
    monkeypatch.setitem(sys.modules, 'sounddevice', None)
    data = native_audio.inventory('native')
    assert data['status'] == 'unavailable'
    assert data['devices'] == []
    assert data['backend'] == 'native'


def test_linux_auto_lists_command_devices_without_changing_the_backend(monkeypatch):
    """Auto on Linux resolves to the ALSA command backend; the saved value stays 'auto'."""
    from odessy.voice import native_audio
    monkeypatch.setattr(sys, 'platform', 'linux')
    monkeypatch.setattr(native_audio.shutil, 'which', lambda name: f'/usr/bin/{name}')
    monkeypatch.setattr(native_audio.subprocess, 'run', fake_listing(calls=[]))
    data = native_audio.inventory('auto')
    assert data['backend'] == 'command'
    assert [device['id'] for device in data['devices']] == [
        'default', 'sysdefault:CARD=PCH', 'hw:CARD=PCH,DEV=0', 'hw:CARD=HDMI,DEV=3']
    assert data['directions']['input']['available'] is True


def test_backend_selection_does_not_change_saved_identifiers():
    """`auto` resolves to the same backend the runtime uses; nothing is rewritten."""
    from odessy.voice.native_audio import backend_name
    assert backend_name('auto') == ('command' if sys.platform.startswith('linux') else 'native')
    assert backend_name('command') == 'command'
    assert backend_name('native') == 'native'


# --------------------------------------------------------------------------- assets

def test_scan_is_unchanged_for_required_assets_and_now_lists_wake_models(tmp_path, monkeypatch):
    from odessy.setup.voice_assets import discover_voice_assets
    monkeypatch.setattr(Path, 'home', classmethod(lambda cls: tmp_path))
    required = {
        'whisper_bin': 'whisper.cpp/build/bin/whisper-cli',
        'whisper_model': 'whisper.cpp/models/ggml-tiny.en.bin',
        'kokoro_model': 'odessy-voice-test/kokoro-v1.0.int8.onnx',
        'kokoro_voices': 'odessy-voice-test/voices-v1.0.bin',
    }
    for relative in required.values():
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'fixture')
    wake = tmp_path / '.local/share/odessy/wake'
    wake.mkdir(parents=True)
    (wake / 'hey_jarvis_v0.1.onnx').write_bytes(b'fixture')
    (wake / 'custom_wake.onnx').write_bytes(b'fixture')
    (tmp_path / 'models').mkdir()
    (tmp_path / 'models/unrelated.onnx').write_bytes(b'fixture')

    result = discover_voice_assets({})
    for name, relative in required.items():
        assert str(tmp_path / relative) in result['candidates'][name]
    assert result['missing'] == []            # wake stays optional
    assert result['partial'] is False
    assert sorted(Path(p).name for p in result['candidates']['wake_model']) == [
        'custom_wake.onnx', 'hey_jarvis_v0.1.onnx']
    # A Kokoro model is never offered as a wake model, and unrelated ONNX files are
    # not guessed at.
    assert not any('kokoro' in path for path in result['candidates']['wake_model'])
    assert not any('unrelated' in path for path in result['candidates']['wake_model'])
    for name in ('whisper_bin', 'whisper_model', 'kokoro_model', 'kokoro_voices'):
        assert name in result['candidates']


def test_configured_and_environment_wake_models_are_offered(tmp_path, monkeypatch):
    from odessy.setup import voice_assets
    monkeypatch.setattr(Path, 'home', classmethod(lambda cls: tmp_path))
    configured = tmp_path / 'elsewhere/configured.onnx'
    configured.parent.mkdir(parents=True)
    configured.write_bytes(b'fixture')
    from_environment = tmp_path / 'elsewhere/from_env.onnx'
    from_environment.write_bytes(b'fixture')
    monkeypatch.setenv('ODESSY_WAKE_MODEL', str(from_environment))
    result = voice_assets.discover_voice_assets({'voice': {'wake_model': str(configured)}})
    assert str(configured) in result['candidates']['wake_model']
    assert str(from_environment) in result['candidates']['wake_model']


def test_missing_required_assets_are_still_reported(tmp_path, monkeypatch):
    from odessy.setup.voice_assets import discover_voice_assets
    monkeypatch.setattr(Path, 'home', classmethod(lambda cls: tmp_path))
    monkeypatch.delenv('ODESSY_WHISPER_BIN', raising=False)
    result = discover_voice_assets({})
    assert 'wake_model' not in result['missing']
    assert set(result['missing']) == {'whisper_bin', 'whisper_model', 'kokoro_model', 'kokoro_voices'}


def test_wake_directory_matches_the_installer_default():
    from odessy.setup import voice_assets
    from odessy.voice import assets
    # The installer constant is bound at import time, so compare its shape rather than
    # a patched HOME: this still catches the two drifting apart.
    assert voice_assets.wake_directory().parts[-4:] == assets.DEFAULT_WAKE_ASSET_DIR.parts[-4:]
    assert voice_assets.wake_directory().parts[-4:] == ('.local', 'share', 'odessy', 'wake')


def test_deadline_stops_the_scan_and_reports_partial(tmp_path, monkeypatch):
    """A scan that runs out of budget keeps what it found and says so."""
    from odessy.setup import voice_assets
    monkeypatch.setattr(Path, 'home', classmethod(lambda cls: tmp_path))
    models = tmp_path / 'models'
    models.mkdir()
    (models / 'ggml-base.en.bin').write_bytes(b'fixture')
    ticks = iter([0.0, 0.0, 99.0] + [99.0] * 50)
    monkeypatch.setattr(voice_assets.time, 'monotonic', lambda: next(ticks))
    result = voice_assets.discover_voice_assets({})
    assert result['partial'] is True
    assert isinstance(result['candidates'], dict)
    assert set(result) == {'candidates', 'missing', 'partial', 'reason'}
