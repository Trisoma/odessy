# Test current Odessy on Jetson

Updated October 20, 2026, against merged `develop` at `b86aea9`. This is a
hardware test procedure, not a report that the Jetson tests have passed.
v0.4.0 development remains unreleased. Start with headphones and a microphone;
open-speaker echo cancellation is not implemented.

## 1. Update and install

Run inside the runnable repository containing `pyproject.toml`, not the outer
notes directory. Keep your existing Jetson virtual environment and working
Whisper/Kokoro assets. Commands below are Bash, not PowerShell.

```bash
git status --short
git switch develop
git pull --ff-only origin develop
source .venv/bin/activate
python --version
python -m pip install -e '.[voice,wake,test]'
sudo apt-get install alsa-utils
```

Stop if the worktree is dirty or the fast-forward is refused; do not start a
pull-rebase to replay already-integrated feature commits. Python 3.10 is supported.
ARM64 dependency wheels depend on your JetPack/Python environment; installation
errors are setup failures, not evidence of a working wake detector. Preserve any
existing Jetson-specific ONNX Runtime installation.

For single-machine mode, build the local Node tools with Node 22 available:

```bash
cd tools
npm ci --no-audit --no-fund
npm run build
cd ..
export ODESSY_DEPLOYMENT=single
export ODESSY_TOOL_CONCURRENCY=1
export ODESSY_AUTO_CLOUD_FALLBACK=off
```

For your existing Jetson/laptop arrangement instead, keep `ODESSY_DEPLOYMENT=split`
and the configured laptop MCP URL; start its server as described in the README.
The first tests below use Python time/calculator tools and do not need laptop commands.

## 2. Point at existing voice assets and check audio

Use real absolute paths for your installation. These are examples to replace:

```bash
export ODESSY_WHISPER_BIN=/absolute/path/to/whisper.cpp/build/bin/whisper-cli
export ODESSY_WHISPER_MODEL=/absolute/path/to/ggml-tiny.en.bin
export ODESSY_KOKORO_MODEL=/absolute/path/to/kokoro-v1.0.int8.onnx
export ODESSY_KOKORO_VOICES=/absolute/path/to/voices-v1.0.bin
export ODESSY_ONNX_PROVIDER=cpu
arecord -l
aplay -l
```

Use ALSA device names from your board; do not copy somebody else's card number.
If defaults are wrong, set `ODESSY_AUDIO_INPUT_DEVICE` and
`ODESSY_AUDIO_OUTPUT_DEVICE`, for example `plughw:1,0` only when that device exists.
Check the selected microphone independently:

```bash
arecord -D "${ODESSY_AUDIO_INPUT_DEVICE:-default}" -f S16_LE -r 16000 -c 1 -d 5 /tmp/odessy-mic-test.wav
aplay -D "${ODESSY_AUDIO_OUTPUT_DEVICE:-default}" /tmp/odessy-mic-test.wav
```

You should hear the recorded speech. These commands and the later listening
tests open the microphone; doctor does not. Environment exports override user
configuration. Put persistent machine paths in `~/.config/odessy/odessy.env`.
Whisper currently runs CPU-only through the adapter's `-ng` argument.

## 3. Install wake assets and initialize both detectors

The phrase is **Hey Jarvis**, not Hey Odessy. The pretrained keyword weights are
CC-BY-NC-SA-4.0, separate from the detector code's Apache-2.0 license.

```bash
python -m odessy voice --setup-wake
python -c 'from openwakeword.utils import download_models; download_models(model_names=["hey_jarvis"])'
```

The first command verifies Odessy's pinned keyword model and records its manifest
under `~/.local/share/odessy/wake` by default. The second explicitly downloads
upstream support models into the active environment's openWakeWord resources,
including the feature and VAD files; it may also download a separate keyword copy.
Those upstream-managed files are outside Odessy's checksum manifest. This extra
setup is based on the [openWakeWord v0.6 downloader](https://github.com/dscripka/openWakeWord/blob/v0.6.0/openwakeword/utils.py)
and [VAD defaults](https://github.com/dscripka/openWakeWord/blob/v0.6.0/openwakeword/vad.py).
Review upstream asset licenses separately. If another version is installed,
check its downloader API before using this command.

Check actual model loading without opening the microphone:

```bash
python - <<'PY'
from odessy.voice.config import VoiceConfig
from odessy.voice.wake import OpenWakeWordDetector
from odessy.voice.vad import SileroSpeechDetector
c = VoiceConfig.from_env()
OpenWakeWordDetector(str(c.wake_model))
SileroSpeechDetector()
print("Wake and VAD model initialization passed")
PY
python -m odessy doctor --voice
```

Doctor checks paths, imports, and ALSA listings; it does not instantiate these
models or prove they can run together. Optional voice warnings can coexist with
exit code zero. Configured third-party servers are contacted during doctor and
may take their per-server timeout (default 30 seconds) to fail.

## 4. Test voice before testing a real language model

```bash
python -m odessy --fake-model 'what time is it?'
python -m odessy voice --fake-model
python -m odessy voice --listen --fake-model
```

The one-shot command records a bounded turn. For the listening command, say
**Hey Jarvis**, wait for the ready cue, then say **what time is it?** and stop
speaking. Expect transcription, a time-tool result, and a spoken answer.
`--fake-model` replaces only the language model: microphone, wake detection,
Whisper, and Kokoro are still real. It can execute deterministic tools, so use
the harmless time/calculator examples. Ctrl+C exits.

Try these cases before enabling a real model:

| Case | Expected behavior |
|---|---|
| Silence/background speech without the phrase | No request submitted |
| Wake, cue, then silence | No model/tool call; capture closes or retries appropriately |
| Wake during synthesis/playback, cue, then `cancel` | Old reply stops or is discarded; queued replacement does not execute |
| Wake, cue, then a replacement time question | One replacement waits for the old turn to settle |
| Disconnect the microphone | Session reports failure, signals cancellation, and releases capture |
| Ctrl+C, then start again | Recorder/player are released and can be reopened |

For follow-up listening:

```bash
python -m odessy voice --listen --fake-model --interaction conversation
```

After a reply, begin your next question within the default 10-second window.
Wake-only mode requires the phrase again. Do not say phrase and command in one
breath: command capture begins after the cue. Spoken `yes` is not authorization;
tool confirmations still use the terminal. Cancellation does not undo completed
actions or guarantee that a remote operation stopped.

## 5. Enable the real local model, then automatic selection

Ensure Ollama is already serving and the named models exist (`ollama list`).
If your board uses another installed LFM tag, substitute its actual name.

```bash
export ODESSY_MODEL_ENDPOINT=http://127.0.0.1:11434
export ODESSY_MODEL_NAME=lfm2.5-thinking:1.2b-q8_0
python -m odessy --model-backend lfm 'what time is it?'
python -m odessy voice --listen --model-backend lfm
```

Only after that passes, make both auto-role models available and test:

```bash
export ODESSY_AUTO_FAST_MODEL=gemma3:1b
export ODESSY_AUTO_CAPABLE_MODEL=lfm2.5-thinking:1.2b-q8_0
export ODESSY_AUTO_CLOUD_FALLBACK=off
python -m odessy --model-backend auto 'hello'
python -m odessy --model-backend auto 'what time is it?'
python -m odessy voice --listen --model-backend auto
```

Greetings/text transforms may use the fast model; tool/uncertain requests and
requests carrying conversation history use capable. Default model names are not
an automatic weight download. Auto uses `keep_alive=0`, so reloading can increase
latency; no Jetson speedup is established. In split mode, commands.toml entries
apply only to local `terminal.run`, not `laptop.terminal.run`.

## 6. Record qualification results

Record `git rev-parse HEAD`, JetPack/Python versions, installed dependency versions,
model tags, microphone/output devices, configuration, and failures. In another
terminal, observe `tegrastats` during a 30-minute listening run. Keep transcripts
and logs private; memory redaction is best-effort.

Measure at least 50 wake attempts, false activations over an hour, and playback
interruption latency. Targets are at least 90% detection, at most one false
activation/hour, and p95 interruption within one second after the phrase.
Also record STT/model/TTS latency, memory growth, and restart/device-loss results.
These remain targets until measured on your hardware. Full-response Kokoro
synthesis can be slow; wake activation does not make TTS streaming or real-time.

Optional hardware-free regressions on the board:

```bash
python -m pytest tests/test_wake_session.py tests/test_wake_capture.py tests/test_wake_lifecycle.py tests/test_wake_doctor.py tests/test_auto_model.py -q
```

If initialization fails, first check ARM64 runtime imports and the three support
model paths. If no phrase is detected, confirm clear raw microphone audio before
changing `ODESSY_WAKE_THRESHOLD` (default 0.5); log threshold changes. If transcription
or speech output fails, test Whisper/Kokoro independently before changing routing.
