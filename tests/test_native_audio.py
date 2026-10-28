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
