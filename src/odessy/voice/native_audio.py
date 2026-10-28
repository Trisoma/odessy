"""Optional PortAudio devices behind the existing PCM stream/player interfaces.

Importing this module never opens hardware. Queue bounds detect overflow; device
names include the host API and ambiguous or unplugged selections fail explicitly.
"""
import importlib
import queue
import sys
import threading
import wave

from .capture import CommandAudioStream, CommandPlayer

def backend_name(value):
    return ('command' if sys.platform.startswith('linux') else 'native') if value=='auto' else value

def library():
    try:return importlib.import_module('sounddevice')
    except (ImportError,OSError):raise RuntimeError('Native audio requires the voice extra and PortAudio on this computer.') from None

def inventory(backend='auto'):
    if backend_name(backend)=='command':
        return {'status':'configured_unverified','devices':[],
                'message':'ALSA command backend: use default, pulse, or an explicit ALSA device. No microphone was opened.'}
    try:
        module=library();apis=module.query_hostapis()
        devices=[{'id':f"{apis[d['hostapi']]['name']}::{d['name']}",
                  'name':d['name'],'input':d['max_input_channels']>0,'output':d['max_output_channels']>0}
                 for d in module.query_devices()][:256]
        return {'status':'available_unverified','devices':devices,'message':'Device inventory only; no recording or playback performed.'}
    except Exception:
        return {'status':'unavailable','devices':[],'message':'Native audio unavailable. Install the voice extra/PortAudio and check OS microphone permissions.'}

def resolve_device(value,kind,module):
    if not value:return None
    apis=module.query_hostapis()
    matches=[i for i,d in enumerate(module.query_devices())
             if f"{apis[d['hostapi']]['name']}::{d['name']}"==value and d[f'max_{kind}_channels']>0]
    if len(matches)!=1:raise ValueError('Selected audio device is missing or ambiguous; refresh device selection.')
    return matches[0]

class NativeInput:
    def __init__(self,device=None):
        self.device=device;self.stream=None;self.pending=bytearray()
        self.frames=queue.Queue(maxsize=64);self.closed=threading.Event();self.overflow=False
    def start(self):
        module=library()
        def callback(data,frames,timing,status):
            if self.closed.is_set():return
            if status:self.overflow=True
            try:self.frames.put_nowait(bytes(data))
            except queue.Full:self.overflow=True
        stream=module.RawInputStream(device=resolve_device(self.device,'input',module),
                     samplerate=16000,channels=1,dtype='int16',blocksize=1280,callback=callback)
        self.stream=stream
        if self.closed.is_set():stream.close();raise RuntimeError('Capture cancelled before startup')
        try:stream.start()
        except BaseException:self.close();raise
        if self.closed.is_set():
            # close() may already have detached self.stream while start() blocked.
            # The local handle must be stopped again after startup has drained.
            try:stream.abort()
            finally:stream.close()
            raise RuntimeError('Capture cancelled during startup')
        return self
    def read(self,size):
        while not self.closed.is_set():
            if self.overflow:raise RuntimeError('Microphone overflow; captured audio discarded')
            if len(self.pending)>=size:
                data=bytes(self.pending[:size]);del self.pending[:size];return data
            try:self.pending.extend(self.frames.get(timeout=.1))
            except queue.Empty:continue
        return b''
    def close(self):
        self.closed.set()
        stream,self.stream=self.stream,None
        if stream is not None:
            try:stream.abort()
            finally:stream.close()

class NativePlayer:
    def __init__(self,device=None):
        self.device=device;self.lock=threading.Lock();self._generation=0;self.stream=None
    @property
    def generation(self):
        with self.lock:return self._generation
    def stop(self):
        with self.lock:
            self._generation+=1;stream=self.stream;self.stream=None
        if stream is not None:
            try:stream.abort()
            finally:stream.close()
    def play(self,path,*,generation=None):
        token=self.generation if generation is None else generation
        module=library()
        with wave.open(str(path),'rb') as wav:
            if wav.getsampwidth()!=2 or wav.getcomptype()!='NONE':raise ValueError('Playback requires 16-bit PCM WAV')
            stream=module.RawOutputStream(device=resolve_device(self.device,'output',module),
                       samplerate=wav.getframerate(),channels=wav.getnchannels(),dtype='int16')
            with self.lock:
                stale=token!=self._generation
                if not stale:self.stream=stream
            if stale:stream.close();return
            try:
                stream.start()
                while token==self.generation:
                    data=wav.readframes(2048)
                    if not data:break
                    stream.write(data)
                if token==self.generation:stream.stop()
            finally:
                with self.lock:
                    owned=self.stream is stream
                    if owned:self.stream=None
                # stop() can detach/close this handle before its blocking start()
                # returns. Always clean the local handle after startup drains.
                try:
                    if token!=self.generation:stream.abort()
                finally:stream.close()

def recorder(config,*,command_factory=CommandAudioStream):
    from .device_lease import LeasedStream
    return LeasedStream(NativeInput(config.audio_input_device) if backend_name(getattr(config,'audio_backend','auto'))=='native'
            else command_factory(recorder=config.audio_recorder,input_device=config.audio_input_device))

def player(config):
    return (NativePlayer(config.audio_output_device) if backend_name(getattr(config,'audio_backend','auto'))=='native'
            else CommandPlayer(player=config.audio_player,output_device=config.audio_output_device))
