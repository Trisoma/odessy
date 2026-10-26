"""Small, trusted-code speech extension seam; no discovery or package installation."""
from __future__ import annotations
import importlib
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from .stt import STTResult
from .tts import TTSResult

class STT(Protocol):

    def transcribe(self, audio_path: Path) -> STTResult:
        ...

class TTS(Protocol):

    def synthesize(self, text: str, output_path: Path) -> TTSResult:
        ...

@dataclass(frozen=True)
class SpeechCapabilities:
    audio_format: str = 'wav'
    streaming: bool = False
    cancellation: bool = False

class ProviderError(RuntimeError):
    pass

def validate_selector(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch('[A-Za-z_][A-Za-z0-9_.-]*(?::[A-Za-z_]\\w*)?', value):
        raise ValueError('speech provider must be a registered name or package.module:factory')
    return value
_REGISTRY: dict[str, dict] = {'stt': {}, 'tts': {}}

def register_stt(name, factory):
    _register('stt', name, factory)

def register_tts(name, factory):
    _register('tts', name, factory)

def _register(kind, name, factory):
    validate_selector(name)
    if ':' in name or name in ('whisper.cpp', 'kokoro') or name in _REGISTRY[kind] or (not callable(factory)):
        raise ValueError('provider name is reserved/registered, or factory is not callable')
    _REGISTRY[kind][name] = factory

def _custom(kind, selector, config):
    provider = None
    try:
        factory = _REGISTRY[kind].get(selector)
        if factory is None:
            module, separator, name = selector.partition(':')
            if not separator:
                raise ProviderError('unknown ' + kind + ' provider: ' + selector)
            factory = getattr(importlib.import_module(module), name)
        provider = factory(config)
        if not callable(getattr(provider, 'transcribe' if kind == 'stt' else 'synthesize', None)):
            raise ProviderError(kind + ' provider does not implement its speech interface')
        caps = getattr(provider, 'capabilities', SpeechCapabilities())
        if not isinstance(caps, SpeechCapabilities) or caps.audio_format != 'wav':
            raise ProviderError('speech providers must support the WAV file interface')
        if caps.cancellation and (not callable(getattr(provider, 'cancel', None))):
            raise ProviderError('provider advertises cancellation without cancel()')
        return _Managed(provider, kind, caps)
    except Exception as error:
        if provider is not None:
            close_providers(provider)
        if isinstance(error, ProviderError):
            raise
        raise ProviderError(kind + ' provider initialization failed: ' + selector) from None

class _Managed:

    def __init__(self, provider, kind, capabilities):
        self.provider, self.kind, self.capabilities = (provider, kind, capabilities)
        self._lock = threading.RLock()
        self._closed = False

    def transcribe(self, path):
        try:
            with self._lock:
                if self._closed:
                    raise ProviderError('provider closed')
                result = self.provider.transcribe(path)
                if not isinstance(result, STTResult) or result.status not in ('ok', 'empty', 'failed') or (not isinstance(result.text, str)):
                    raise ProviderError('invalid transcription result')
                return result
        except Exception:
            return STTResult('', 'failed', 'Configured STT provider failed')

    def synthesize(self, text, path):
        try:
            with self._lock:
                if self._closed:
                    raise ProviderError('provider closed')
                result = self.provider.synthesize(text, path)
                if not isinstance(result, TTSResult) or result.status not in ('ok', 'failed') or Path(result.output_path) != Path(path):
                    raise ProviderError('invalid synthesis result')
                return result
        except Exception:
            return TTSResult(Path(path), 'failed', 'Configured TTS provider failed')

    def cancel(self):
        """Signal cancellation concurrently; never wait for the operation lock."""
        hook = getattr(self.provider, 'cancel', None)
        if callable(hook):
            hook()

    def close(self):
        """Close once, after synchronous operations release their engine lock."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            hook = getattr(self.provider, 'close', None)
            if callable(hook):
                hook()

def create_stt(config, *, builtin_factory=None):
    selector = validate_selector(getattr(config, 'stt_provider', 'whisper.cpp'))
    if selector == 'whisper.cpp':
        if builtin_factory is None:
            from .stt import WhisperCppSTT
            builtin_factory = WhisperCppSTT
        return builtin_factory(whisper_bin=config.whisper_bin, model_path=config.whisper_model)
    return _custom('stt', selector, config)

def create_tts(config, *, builtin_factory=None):
    selector = validate_selector(getattr(config, 'tts_provider', 'kokoro'))
    if selector == 'kokoro':
        if builtin_factory is None:
            from .tts import KokoroTTS
            builtin_factory = KokoroTTS
        return builtin_factory(model_path=config.kokoro_model, voices_path=config.kokoro_voices, provider=config.onnx_provider, cache_dir=config.onnx_cache_dir)
    return _custom('tts', selector, config)

def cancel_provider(provider):
    hook = getattr(provider, 'cancel', None)
    if callable(hook):
        try:
            hook()
        except Exception:
            pass

def close_providers(*providers):
    for provider in providers:
        hook = getattr(provider, 'close', None)
        if callable(hook):
            try:
                hook()
            except Exception:
                pass
