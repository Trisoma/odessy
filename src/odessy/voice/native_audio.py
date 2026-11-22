"""Optional PortAudio devices behind the existing PCM stream/player interfaces.

Importing this module never opens hardware. Queue bounds detect overflow; device
names include the host API and ambiguous or unplugged selections fail explicitly.
"""
import importlib
from contextlib import contextmanager
import queue
import shutil
import subprocess
import sys
import threading
import wave

from .capture import CommandAudioStream, CommandPlayer

#: A device listing is observation only. Both the wall-clock bound and the entry cap
#: keep `arecord -L` / `aplay -L` from becoming an open-ended enumeration.
ALSA_PROBE_TIMEOUT = 2.0
MAX_LISTED_DEVICES = 256

# PortAudio caches device enumeration in each process. Reinitialization is only
# safe between native streams, including starts or stops still draining.
_backend_lock = threading.RLock()
_backend_users = set()


class AudioBackendBusy(RuntimeError):
    pass


class AudioDeviceUnavailable(ValueError):
    """Safe, actionable device error that may be shown in the desktop UI."""


def prepare_device(value, kind, backend='auto'):
    """Resolve an explicit selection, refreshing stale idle enumeration once.

    Never switch to another microphone/speaker, or reset an active native stream.
    Reconnection is retried only on an explicit capture/wake/playback request.
    """
    if not value or backend_name(backend) != 'native':
        return
    module = library()
    try:
        resolve_device(value, kind, module)
    except AudioDeviceUnavailable:
        try:
            refresh_devices(backend)
        except AudioBackendBusy:
            raise AudioDeviceUnavailable(
                'Audio device changed while audio is active. Stop recording, wake listening and playback, then retry.') from None
        resolve_device(value, kind, module)


def _reserve_backend():
    token = object()
    with _backend_lock:
        _backend_users.add(token)
    return token


def _release_backend(token):
    with _backend_lock:
        _backend_users.discard(token)


@contextmanager
def _using_backend():
    token = _reserve_backend()
    try:
        yield
    finally:
        _release_backend(token)


def refresh_devices(backend='auto'):
    """Refresh an already loaded native backend without opening any device.

    sounddevice 0.5.6 has no public hot-plug refresh API. Its paired lifecycle
    functions are confined here, behind the same lock used to reserve all Odessy
    native streams. Never reset while playback/capture or cancellation drains.
    A backend not loaded yet has no cached enumeration to invalidate.
    """
    if backend_name(backend) != 'native':
        return
    with _backend_lock:
        if _backend_users:
            raise AudioBackendBusy('Native audio is active or still draining; refresh deferred.')
        module = sys.modules.get('sounddevice')
        if module is None:
            return
        if getattr(module, '_initialized', 1) > 0:
            module._terminate()
        module._initialize()

def prepare_native_dependencies():
    """Load Windows audio DLLs before request workers can start concurrently.

    A first NumPy/OpenBLAS import in an executor can hold the Windows loader
    lock while the event-loop thread waits for a new executor thread to start.
    Import libraries before serving HTTP; do not open devices or model files.
    Optional dependencies remain optional, so missing voice extras never stop
    the text service. Their normal audio checks still report unavailability.
    Linux keeps its existing lazy initialization (including Jetson providers).
    """
    if sys.platform != 'win32':
        return ()
    if threading.current_thread() is not threading.main_thread():
        raise RuntimeError('Prepare native audio on the main thread before starting workers')
    unavailable = []
    for name in ('numpy', 'onnxruntime', 'sounddevice', 'openwakeword.model',
                 'openwakeword.vad', 'kokoro_onnx'):
        try:
            importlib.import_module(name)
        except (ImportError, OSError, RuntimeError):
            unavailable.append(name)
    return tuple(unavailable)

def backend_name(value):
    return ('command' if sys.platform.startswith('linux') else 'native') if value=='auto' else value

def library():
    try:return importlib.import_module('sounddevice')
    except (ImportError,OSError):raise RuntimeError('Native audio requires the voice extra and PortAudio on this computer.') from None

def alsa_listing(text, limit=MAX_LISTED_DEVICES):
    """Parse `arecord -L` / `aplay -L` output: name lines, then indented descriptions."""
    entries=[]
    for line in text.splitlines()[:4096]:
        if not line.strip():
            continue
        if line[:1] not in (' ', '\t'):
            if len(entries)>=limit:break
            entries.append([line.strip(), ''])
        elif entries and not entries[-1][1]:
            entries[-1][1]=line.strip()
    return [(name, description) for name, description in entries]

def command_devices():
    """ALSA device names usable verbatim as `-D`, plus any problems encountered."""
    rows={};problems=[]
    for tool,kind in (('arecord','input'),('aplay','output')):
        if not shutil.which(tool):
            problems.append(f'{tool} is not installed')
            continue
        try:
            completed=subprocess.run([tool,'-L'],capture_output=True,text=True,
                                     timeout=ALSA_PROBE_TIMEOUT,check=False)
        except (OSError,subprocess.SubprocessError):
            problems.append(f'{tool} -L did not complete')
            continue
        if completed.returncode!=0:
            problems.append(f'{tool} -L exited with {completed.returncode}')
            continue
        for name,description in alsa_listing(completed.stdout):
            row=rows.setdefault(name,{'id':name,'name':name,'input':False,'output':False})
            row[kind]=True
            if description and row['name']==name:row['name']=description
    devices=list(rows.values())[:MAX_LISTED_DEVICES]
    for device in devices:
        if device['id']=='default':device['is_default']=True
    return devices,problems

def _directions(devices, backend, recorder='arecord', player='aplay'):
    """Per-direction availability, so a UI can explain a missing half of the list."""
    counts={'input':sum(1 for d in devices if d['input']),'output':sum(1 for d in devices if d['output'])}
    result={}
    for kind,tool in (('input',recorder),('output',player)):
        count=counts[kind]
        if count:
            result[kind]={'available':True,'status':'available_unverified','count':count,
                          'message':f'{count} {kind} device(s) listed; nothing was opened.'}
            continue
        if backend=='command':
            detail=(f'Checked {tool} -L. ' if shutil.which(tool)
                    else f'{tool} is not installed. ')
        else:
            detail='PortAudio listed no device with this capability. '
        result[kind]={'available':False,'status':'unavailable','count':0,
                      'message':f'No {kind} devices listed. '+detail+
                                'Previously saved identifiers are kept as they are.'}
    return result

def inventory(backend='auto', recorder='arecord', player='aplay'):
    """Device inventory only. Never opens a microphone, plays audio or runs inference.

    The response is additive over the original contract: `status`, `message` and
    `devices` keep their meaning, and every device keeps `id`, `name`, `input` and
    `output`. New keys are `backend`, `directions` and the optional per-device
    `is_default`. Identifiers are always exactly what the resolved backend consumes:
    `HostAPI::name` for PortAudio, a literal ALSA name for `arecord`/`aplay` `-D`.
    """
    name=backend_name(backend)
    if name=='command':
        devices,problems=command_devices()
        message=('ALSA command backend: device names are passed unchanged to arecord/aplay -D. '
                 'No microphone was opened.')
        if problems:message=' '.join(problems)+'. '+message
        return {'backend':'command',
                'status':'available_unverified' if devices else 'configured_unverified',
                'devices':devices,'message':message,
                'directions':_directions(devices,'command',recorder,player)}
    with _using_backend():
        return _native_inventory(recorder, player)


def _native_inventory(recorder, player):
    try:
        module=library();apis=module.query_hostapis()
        default=getattr(getattr(module,'default',None),'device',())
        defaults={index for index in default if isinstance(index,int) and index>=0}
        devices=[]
        for index,device in enumerate(module.query_devices()):
            if index>=MAX_LISTED_DEVICES:break
            entry={'id':f"{apis[device['hostapi']]['name']}::{device['name']}",
                   'name':device['name'],'input':device['max_input_channels']>0,
                   'output':device['max_output_channels']>0}
            if index in defaults:entry['is_default']=True
            devices.append(entry)
        return {'backend':'native','status':'available_unverified','devices':devices,
                'message':'Device inventory only; no recording or playback performed.',
                'directions':_directions(devices,'native')}
    except Exception:
        return {'backend':'native','status':'unavailable','devices':[],
                'message':'Native audio unavailable. Install the voice extra/PortAudio and check OS microphone permissions.',
                'directions':_directions([],'native')}

def resolve_device(value,kind,module):
    if not value:return None
    apis=module.query_hostapis()
    matches=[i for i,d in enumerate(module.query_devices())
             if f"{apis[d['hostapi']]['name']}::{d['name']}"==value and d[f'max_{kind}_channels']>0]
    if len(matches)!=1:
        device = 'microphone' if kind == 'input' else 'speaker'
        raise AudioDeviceUnavailable(f'Selected {device} is missing or ambiguous. Connect it and retry, or choose a device in Settings > Audio devices.')
    return matches[0]

class NativeInput:
    def __init__(self,device=None):
        self.device=device;self.stream=None;self.pending=bytearray()
        self.frames=queue.Queue(maxsize=64);self.closed=threading.Event();self.overflow=False
        self._opening=False;self._reservation=None
    def start(self):
        with _backend_lock:
            if self.closed.is_set():raise RuntimeError('Capture cancelled before startup')
            if self._reservation is not None:raise RuntimeError('Capture already started')
            self._opening=True;self._reservation=_reserve_backend()
        try:
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
            stream.start()
            if self.closed.is_set():
                # close() can detach the handle while its blocking start drains.
                try:stream.abort()
                finally:stream.close()
                raise RuntimeError('Capture cancelled during startup')
            return self
        except BaseException:
            self.close();raise
        finally:
            with _backend_lock:
                self._opening=False
                if self.closed.is_set():
                    _release_backend(self._reservation);self._reservation=None
    def read(self,size):
        while not self.closed.is_set():
            if self.overflow:raise RuntimeError('Microphone overflow; captured audio discarded')
            if len(self.pending)>=size:
                data=bytes(self.pending[:size]);del self.pending[:size];return data
            try:self.pending.extend(self.frames.get(timeout=.1))
            except queue.Empty:continue
        return b''
    def close(self):
        with _using_backend():
            self._close()
    def _close(self):
        with _backend_lock:
            self.closed.set()
            stream,self.stream=self.stream,None
            # Keep the reservation until the actual blocking owner finishes.
            reservation=None if self._opening else self._reservation
            if not self._opening:self._reservation=None
        try:
            if stream is not None:
                try:stream.abort()
                finally:stream.close()
        finally:
            if reservation is not None:_release_backend(reservation)

class NativePlayer:
    def __init__(self,device=None):
        self.device=device;self.lock=threading.Lock();self._generation=0;self.stream=None
    @property
    def generation(self):
        with self.lock:return self._generation
    def stop(self):
        with _using_backend():
            self._stop()
    def _stop(self):
        with self.lock:
            self._generation+=1;stream=self.stream;self.stream=None
        if stream is not None:
            try:stream.abort()
            finally:stream.close()
    def play(self,path,*,generation=None):
        with _using_backend():
            return self._play(path,generation=generation)
    def _play(self,path,*,generation=None):
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
