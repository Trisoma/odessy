"""Optional external Piper CLI: plain text in, validated WAV out; no library import.

The user selects a trusted Piper executable (or its Python interpreter) and an
existing ONNX/JSON pair. This adapter never downloads or installs an engine.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import threading
import wave

from .tts import TTSResult


class PiperTTS:
    def __init__(self, executable: str, model_path: str, *, timeout: float = 45):
        self.executable = executable
        self.model_path = model_path
        self.timeout = timeout
        self._lock = threading.Lock()
        self._operation = threading.Lock()
        self._process = None
        self._generation = 0
        self._closed = False

    def _command(self, model, output):
        executable = str(Path(self.executable).expanduser())
        command = [executable]
        if Path(executable).stem.lower().startswith('python'):
            # stdin is UTF-8 bytes. Windows redirected Python stdin otherwise
            # uses the active locale, corrupting normal punctuation/accented text.
            # -I ignores PYTHONUTF8, so set the interpreter option explicitly.
            command += ['-I', '-B', '-X', 'utf8', '-m', 'piper']
        return command + ['--model', str(model), '--output_file', str(output)]

    def synthesize(self, text: str, output_path: Path) -> TTSResult:
        output_path = Path(output_path)
        process = None
        try:
            with self._lock:
                generation = self._generation
                if self._closed:
                    raise RuntimeError('Piper provider is closed')
            if not text.strip() or len(text) > 16000:
                raise ValueError('Piper requires nonempty text of at most 16000 characters')
            model = Path(self.model_path).expanduser()
            sidecar = Path(str(model) + '.json')
            if not self.executable or not model.is_file() or not sidecar.is_file():
                raise ValueError('Select an existing Piper executable and ONNX model with its .onnx.json file')
            if sidecar.stat().st_size > 1024 * 1024:
                raise ValueError('Piper model configuration is too large')
            config = json.loads(sidecar.read_text(encoding='utf-8'))
            rate = config.get('sample_rate', config.get('audio', {}).get('sample_rate'))
            if type(rate) is not int or not 8000 <= rate <= 96000:
                raise ValueError('Invalid Piper model configuration sample rate')
            output_path.parent.mkdir(parents=True, exist_ok=True)
            with self._operation, tempfile.TemporaryDirectory(prefix='odessy-piper-', dir=output_path.parent) as folder:
                temporary = Path(folder) / 'speech.wav'
                with self._lock:
                    if self._closed or generation != self._generation:
                        raise RuntimeError('Piper synthesis was stopped')
                    process = subprocess.Popen(
                        self._command(model, temporary), stdin=subprocess.PIPE,
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                        **({'creationflags': subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP}
                           if os.name == 'nt' else {'start_new_session': True}),
                    )
                    self._process = process
                try:
                    process.communicate((' '.join(text.splitlines()) + '\n').encode('utf-8'), timeout=self.timeout)
                    if process.returncode != 0:
                        raise RuntimeError('Piper failed or was stopped; check the executable, model and dependencies')
                    if not temporary.is_file() or temporary.stat().st_size > 32 * 1024 * 1024:
                        raise ValueError('Piper output is missing or exceeds 32 MiB')
                    with wave.open(str(temporary), 'rb') as wav:
                        if (wav.getnchannels() != 1 or wav.getsampwidth() != 2
                                or wav.getframerate() != rate or not wav.getnframes()):
                            raise ValueError('Piper did not produce the expected mono PCM WAV')
                    with self._lock:
                        if self._closed or generation != self._generation:
                            raise RuntimeError('Piper synthesis was stopped')
                        os.replace(temporary, output_path)
                finally:
                    if process.poll() is None:
                        self._terminate(process)
                    process.wait(timeout=5)
                    with self._lock:
                        if self._process is process:
                            self._process = None
            return TTSResult(output_path, 'ok')
        except Exception as error:
            return TTSResult(output_path, 'failed', str(error))

    def cancel(self):
        with self._lock:
            self._generation += 1
            if self._process is not None and self._process.poll() is None:
                self._terminate(self._process)

    @staticmethod
    def _terminate(process):
        try:
            if os.name == 'nt':
                subprocess.run(['taskkill.exe', '/PID', str(process.pid), '/T', '/F'],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               timeout=5, creationflags=subprocess.CREATE_NO_WINDOW)
            else:
                os.killpg(process.pid, signal.SIGKILL)
        except (OSError, subprocess.TimeoutExpired):
            pass
        if process.poll() is None:
            try:
                process.kill()
            except OSError:
                pass

    def close(self):
        with self._lock:
            self._closed = True
        self.cancel()
        with self._operation:
            pass
