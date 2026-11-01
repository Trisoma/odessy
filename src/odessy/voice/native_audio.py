"""Optional PortAudio devices behind the existing PCM stream/player interfaces.

Importing this module never opens hardware. Queue bounds detect overflow; device
names include the host API and ambiguous or unplugged selections fail explicitly.
"""
import importlib
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
