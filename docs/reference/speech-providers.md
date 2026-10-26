# Speech providers

Whisper.cpp STT and Kokoro TTS remain the defaults. Their executable/model settings,
CPU/GPU selection and Kokoro session cache are unchanged. Audio capture, wake-word
recognition, VAD, approvals and playback remain independent of speech providers.

## Configuration

Select trusted, installed Python factories in the same interpreter as Odessy:

```sh
export ODESSY_STT_PROVIDER=my_speech:make_stt
export ODESSY_TTS_PROVIDER=my_speech:make_tts
export ODESSY_STT_EXECUTABLE=/opt/speech/bin/transcribe
export ODESSY_STT_MODEL=/opt/speech/models/recognition.bin
export ODESSY_TTS_MODEL=/opt/speech/models/synthesis.bin
python -m odessy voice --listen
```

The optional `ODESSY_TTS_EXECUTABLE` has the same purpose. Factories receive the
complete `VoiceConfig`; generic executable/model fields are passed through, not
interpreted as shell commands. A custom factory is trusted code and must implement
its provider-specific command-line/API protocol. Odessy does not install plugins,
models or their dependencies automatically. Keep credentials in the provider's
secure configuration/environment, never in the selector or model path.

Use `ODESSY_STT_PROVIDER=whisper.cpp` and `ODESSY_TTS_PROVIDER=kokoro` to restore
defaults. Existing WHISPER/KOKORO settings still configure those providers.
Custom providers do not require unused default-provider assets.

## Interface

Implement synchronous WAV-file methods returning the existing result dataclasses:

```python
from odessy.voice.stt import STTResult
from odessy.voice.tts import TTSResult
from odessy.voice.providers import SpeechCapabilities

class MySTT:
    capabilities = SpeechCapabilities()  # WAV; no native streaming/cancellation

    def __init__(self, config):
        self.model = config.stt_model
        self.executable = config.stt_executable

    def transcribe(self, audio_path):
        # Read audio_path; run your engine with a timeout and argv, never shell=True.
        text = your_engine(audio_path, self.model)
        return STTResult(text, 'ok' if text.strip() else 'empty')

    def close(self):
        pass  # release owned sessions/processes only

def make_stt(config):
    return MySTT(config)
```

TTS implements `synthesize(text, output_path)`, writes a playable WAV at exactly
`output_path`, and returns `TTSResult(output_path, 'ok')` or `'failed'` with an error.
STT statuses are `'ok'`, `'empty'`, `'failed'`. Blank-audio retries stay in the voice
pipeline; speech failures must not masquerade as empty audio. Preserve ordinary
text on synthesis failure. The exported `STT`/`TTS` protocols document these seams.

Optional `cancel()` must be thread-safe, prompt, and callable concurrently with a
worker. Advertise `SpeechCapabilities(cancellation=True)` only when implemented.
`close()` runs after owned synchronous work; custom providers are closed once.
Factories must clean partial initialization if they raise. Invalid interfaces or
capabilities fail initialization; unexpected custom operation exceptions become
failed results without echoing exception text. Set finite engine/network timeouts:
Odessy cannot forcibly terminate arbitrary Python threads. Hybrid shutdown drains
workers for its existing bounded window; unfinished cleanup can continue afterward.

Sentence-by-sentence speech streaming uses the same `synthesize` method. Native
chunked audio streaming is not negotiated or consumed; `streaming` is descriptive,
not a promise of pipeline support. Only WAV-file capabilities are accepted.

Embedded applications may instead register a callable using
`register_stt('my-stt', factory)` / `register_tts('my-tts', factory)` before starting
Odessy. Explicit module factories need no registration/bootstrap changes.

## Setup and validation

Headless setup accepts `--voice --stt-provider module:factory --tts-provider
module:factory`, plus `--stt-executable`, `--stt-model`, `--tts-executable`, and
`--tts-model`. Selection JSON uses the corresponding snake_case fields under
`voice`. Existing setup review/export/state binding applies. Default asset and
component checks are conditional on the selected provider. Doctor and setup do
not import or contact custom providers: reported asset presence is not inference,
network, audio or compatibility acceptance. The desktop's builtin asset editor
is not a generic third-party provider picker; configure custom providers through
setup JSON/headless export or the existing environment configuration.

Mock integration and existing Whisper/Kokoro regression tests run without audio
hardware. Real microphones, speakers, providers and Jetson need separate checks.
