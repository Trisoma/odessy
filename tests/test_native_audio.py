import sys
from types import SimpleNamespace
import pytest

def test_inventory_does_not_open_devices_and_duplicate_name_does_not_silently_pick(monkeypatch):
    from odessy.voice import native_audio
    devices=[dict(name='Headset',hostapi=0,max_input_channels=1,max_output_channels=2)]*2
    module=SimpleNamespace(query_devices=lambda:devices,query_hostapis=lambda:[{'name':'test'}])
    monkeypatch.setitem(sys.modules,'sounddevice',module)
    data=native_audio.inventory('native')
    assert len(data['devices'])==2
    with pytest.raises(ValueError,match='ambiguous'):
        native_audio.resolve_device('test::Headset','input',module)


@pytest.mark.parametrize('kind', ['input','output'])
def test_reconnect_never_refreshes_active_audio_or_selects_a_different_device(monkeypatch, kind):
    from odessy.voice import native_audio
    calls=[]
    module=SimpleNamespace(query_devices=lambda:[],query_hostapis=lambda:[{'name':'test'}],
        _terminate=lambda:calls.append('terminate'),_initialize=lambda:calls.append('initialize'))
    monkeypatch.setitem(sys.modules,'sounddevice',module)
    reservation=native_audio._reserve_backend()
    try:
        with pytest.raises(native_audio.AudioDeviceUnavailable,match='Stop recording'):
            native_audio.prepare_device('test::Disconnected headset',kind,'native')
        assert calls==[]
    finally:native_audio._release_backend(reservation)
    with pytest.raises(native_audio.AudioDeviceUnavailable,match='missing or ambiguous'):
        native_audio.prepare_device('test::Disconnected headset',kind,'native')
    assert calls==['terminate','initialize']

def test_cancel_unblocks_native_capture_and_overflow_fails_closed(monkeypatch):
    from odessy.voice import native_audio
    streams=[]
    class Stream:
        def __init__(self,**kwargs):self.callback=kwargs['callback'];streams.append(self)
        def start(self):pass
        def abort(self):pass
        def close(self):pass
    module=SimpleNamespace(RawInputStream=Stream)
    monkeypatch.setitem(sys.modules,'sounddevice',module)
    stream=native_audio.NativeInput();stream.start()
    streams[0].callback(bytes(320),160,None,True)
    with pytest.raises(RuntimeError,match='overflow'):stream.read(320)
    stream.close()
    assert stream.read(320)==b''

def test_microphone_lease_refuses_second_reader_and_releases_on_close(tmp_path,monkeypatch):
    from odessy.voice import device_lease
    monkeypatch.setattr(device_lease,'lease_path',lambda:tmp_path/'mic.lock')
    one,two=device_lease.MicrophoneLease(),device_lease.MicrophoneLease()
    one.acquire()
    try:
        with pytest.raises(RuntimeError,match='microphone'):two.acquire()
    finally:one.close()
    two.acquire();two.close()

def test_stop_during_stream_startup_retains_lease_until_start_drains(tmp_path,monkeypatch):
    import threading
    from odessy.voice import device_lease
    monkeypatch.setattr(device_lease,'lease_path',lambda:tmp_path/'mic.lock')
    started,release=threading.Event(),threading.Event();errors=[]
    class Stream:
        def start(self):started.set();release.wait(3)
        def close(self):pass
    stream=device_lease.LeasedStream(Stream())
    def open_():
        try:stream.start()
        except RuntimeError:errors.append('cancelled')
    worker=threading.Thread(target=open_);worker.start();assert started.wait(2)
    try:
        stream.close()
        with pytest.raises(RuntimeError):device_lease.MicrophoneLease().acquire()
    finally:release.set();worker.join(3)
    assert errors==['cancelled']
    later=device_lease.MicrophoneLease();later.acquire();later.close()

@pytest.mark.parametrize('kind',['input','output'])
def test_native_stop_during_blocking_start_never_leaves_restarted_device(kind,tmp_path,monkeypatch):
    import threading,wave
    from odessy.voice import native_audio
    entered,release=threading.Event(),threading.Event();streams=[];errors=[]
    class Stream:
        def __init__(self,**options):self.active=False;self.writes=0;streams.append(self)
        def start(self):entered.set();release.wait(2);self.active=True
        def abort(self):self.active=False
        def close(self):self.active=False
        def stop(self):self.active=False
        def write(self,data):self.writes+=1
    monkeypatch.setitem(sys.modules,'sounddevice',SimpleNamespace(RawInputStream=Stream,RawOutputStream=Stream))
    path=tmp_path/'sample.wav'
    with wave.open(str(path),'wb') as wav:wav.setparams((1,2,16000,0,'NONE',''));wav.writeframes(bytes(3200))
    device=native_audio.NativeInput() if kind=='input' else native_audio.NativePlayer()
    def run():
        try:device.start() if kind=='input' else device.play(path)
        except RuntimeError:errors.append('cancelled')
    worker=threading.Thread(target=run);worker.start();assert entered.wait(1)
    try:device.close() if kind=='input' else device.stop()
    finally:release.set();worker.join(3)
    assert not worker.is_alive()
    assert streams[0].active is False
    assert streams[0].writes==0


def test_explicit_refresh_reenumerates_without_opening_a_device(monkeypatch):
    from odessy.voice import native_audio
    calls = []
    device_rows = []
    def initialize():
        calls.append('initialize')
        device_rows.append(dict(name='New headset',hostapi=0,max_input_channels=1,max_output_channels=0))
    module = SimpleNamespace(_terminate=lambda: calls.append('terminate'), _initialize=initialize,
        query_devices=lambda: device_rows, query_hostapis=lambda: [{'name':'test'}])
    monkeypatch.setitem(sys.modules, 'sounddevice', module)
    with pytest.raises(ValueError): native_audio.resolve_device('test::New headset','input',module)
    native_audio.refresh_devices('native')
    assert native_audio.resolve_device('test::New headset','input',module) == 0
    assert calls == ['terminate','initialize']
    native_audio.refresh_devices('command')
    assert len(calls) == 2


@pytest.mark.parametrize('kind', ['input', 'output'])
def test_refresh_refuses_live_native_stream_and_cancelled_start_still_draining(kind, tmp_path, monkeypatch):
    import threading, wave
    from odessy.voice import native_audio
    entered, release = threading.Event(), threading.Event()
    refreshes = []
    class Stream:
        def __init__(self, **kwargs): pass
        def start(self): entered.set(); release.wait(4)
        def abort(self): pass
        def close(self): pass
        def stop(self): pass
        def write(self, data): pass
    module = SimpleNamespace(RawInputStream=Stream, RawOutputStream=Stream,
        _terminate=lambda: refreshes.append('terminate'), _initialize=lambda: refreshes.append('initialize'))
    monkeypatch.setitem(sys.modules, 'sounddevice', module)
    path = tmp_path/'test.wav'
    with wave.open(str(path), 'wb') as wav:
        wav.setparams((1,2,16000,0,'NONE','')); wav.writeframes(bytes(320))
    device = native_audio.NativeInput() if kind == 'input' else native_audio.NativePlayer()
    def run():
        try: device.start() if kind == 'input' else device.play(path)
        except RuntimeError: pass
    worker = threading.Thread(target=run); worker.start(); assert entered.wait(2)
    try:
        with pytest.raises(RuntimeError, match='active|draining'):
            native_audio.refresh_devices('native')
        device.close() if kind == 'input' else device.stop()
        with pytest.raises(RuntimeError, match='active|draining'):
            native_audio.refresh_devices('native')
        assert not refreshes
    finally:
        release.set(); worker.join(4)
    assert not worker.is_alive()
    native_audio.refresh_devices('native')
    assert refreshes == ['terminate','initialize']


def test_refresh_retries_failed_initialization_without_unbalanced_termination(monkeypatch):
    from odessy.voice import native_audio
    calls = []
    module = SimpleNamespace(_initialized=1)
    def terminate():
        calls.append('terminate'); module._initialized -= 1
        assert module._initialized >= 0
    def initialize():
        calls.append('initialize')
        if calls.count('initialize') == 1: raise RuntimeError('device changed during refresh')
        module._initialized += 1
    module._terminate=terminate; module._initialize=initialize
    monkeypatch.setitem(sys.modules,'sounddevice',module)
    with pytest.raises(RuntimeError,match='device changed'):
        native_audio.refresh_devices('native')
    native_audio.refresh_devices('native')
    assert module._initialized == 1
    assert calls == ['terminate','initialize','initialize']


@pytest.mark.parametrize('kind', ['input','output'])
def test_backend_refresh_waits_for_blocking_native_shutdown(kind, tmp_path, monkeypatch):
    import threading,wave
    from odessy.voice import native_audio
    closing,release,playing = threading.Event(),threading.Event(),threading.Event()
    class Stream:
        def __init__(self,**kwargs): pass
        def start(self): pass
        def abort(self): closing.set(); release.wait(4)
        def close(self): pass
        def stop(self): pass
        def write(self,data): playing.set(); release.wait(4)
    module=SimpleNamespace(RawInputStream=Stream,RawOutputStream=Stream,
        _terminate=lambda:None,_initialize=lambda:None)
    monkeypatch.setitem(sys.modules,'sounddevice',module)
    if kind=='input':
        device=native_audio.NativeInput();device.start()
        player=None
    else:
        path=tmp_path/'shutdown.wav'
        with wave.open(str(path),'wb') as wav:
            wav.setparams((1,2,16000,0,'NONE',''));wav.writeframes(bytes(320))
        device=native_audio.NativePlayer()
        player=threading.Thread(target=lambda:device.play(path));player.start()
        assert playing.wait(2)
    shutdown=threading.Thread(target=device.close if kind=='input' else device.stop)
    shutdown.start(); assert closing.wait(2)
    try:
        with pytest.raises(RuntimeError,match='active|draining'):
            native_audio.refresh_devices('native')
    finally:
        release.set();shutdown.join(4)
        if player is not None:player.join(4)
    native_audio.refresh_devices('native')
