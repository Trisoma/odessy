# Test current Odessy on Jetson

Updated October 20, 2026, against merged `develop` at `b86aea9`, with the
user-reported Jetson smoke-test results below. This procedure does not establish
full hardware qualification or an automatic-selection performance improvement.
v0.4.0 development remains unreleased. Start with headphones and a microphone;
open-speaker echo cancellation is not implemented.

Observed on October 20: the fake-model listening test reached transcription,
model execution, and speech output. The Q8 profile was created successfully with
`num_gpu=15`, `num_batch=64`, and `num_ctx=1024`. Automatic-selection routing,
long-running stability, and cancellation qualification remain test steps below,
not completed hardware results.

Later text auto smoke tests returned a greeting, an unchanged rewrite sentence,
and a time answer. The auto voice run also reached transcription, thinking, and
speaking. The user then reported saying `Hi` while Q8 LFM appeared in
`ollama ps`. That observation remains unresolved: the actual Whisper transcript,
history state, and fast-model escalation reason were not captured. It does not
establish correct routing or a router defect. See the
[routing boundary and observations](reference/model-selection.mdx#routing-boundary-and-october-20-jetson-observations).

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

Turn on and unmute the microphone before testing. In the October 20 session,
wake stopped responding after an accidental suspend and SSH reconnection. Raw
recording showed a 0% meter and the recorded wake score was about 0.000096 against
the unchanged 0.5 threshold. The user subsequently confirmed that the microphone
was off; this incident was not evidence of a suspend-related wake-detector fault.
Exports from an earlier SSH shell must be restored if they are not saved in the
user configuration.

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

### Confirmed VAD frame-size correction

The checked `b86aea9` source calls `self._vad.predict(frame)` even though Odessy
supplies 512-sample frames. The installed openWakeWord VAD defaults to 480 samples;
the user's direct test reproduced `Conv_73` / `If_25` invalid input shape `{224}`
with `frame_size=480`, while `frame_size=512` succeeded. The repository adapter
now explicitly passes `frame_size=512`, with a hardware-free regression test.
After syncing this fix to Jetson, no manual source substitution is needed.
Check the installed adapter without opening the microphone:

```bash
python -c 'from odessy.voice.vad import SileroSpeechDetector; print("Silence detected as speech:", SileroSpeechDetector().is_speech(bytes(1024)))'
```

The reported result was `Silence detected as speech: False`, without the
convolution error. This command only checks the installed adapter. If the earlier
manual patch is still present in the Jetson checkout, review `git diff` before
syncing; it may already be identical to this repository correction. The new test
checks the adapter contract with dependency stubs, not a real ONNX execution.

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
The October 20 baseline uses **LFM Q8**, specifically
`lfm2.5-thinking:1.2b-q8_0`, with 15 GPU layers, a 64-token batch, and a
1,024-token context. Both 12- and 15-layer short requests succeeded. Fifteen
layers is a working starting point, not a measured optimum: a later 15-layer
allocation failed, and a subsequent 12-layer request followed by 15 layers
succeeded. Full voice-pipeline memory headroom remains to be measured.

Odessy at this snapshot sends context/output limits but does not send `num_gpu`
or `num_batch`. A successful `curl` request does not persist those options for
later Odessy calls. Save them in a separate profile based on the existing Q8
model; the original tag and quantization are preserved. See the
[Ollama Modelfile reference](https://docs.ollama.com/modelfile).

```bash
cat > /tmp/odessy-q8.Modelfile <<'EOF'
FROM lfm2.5-thinking:1.2b-q8_0
PARAMETER num_gpu 15
PARAMETER num_batch 64
PARAMETER num_ctx 1024
EOF

ollama create odessy-lfm-q8:jetson -f /tmp/odessy-q8.Modelfile
ollama show odessy-lfm-q8:jetson --parameters
ollama stop lfm2.5-thinking:1.2b-q8_0
```

Configure Odessy in the same shell. The text backend reads `ODESSY_MODEL_NAME`;
the explicit voice backend receives `ODESSY_OLLAMA_MODEL` from voice configuration.
Keep the existing Whisper/Kokoro asset paths and audio-device settings.

```bash
export ODESSY_MODEL_ENDPOINT=http://127.0.0.1:11434
export ODESSY_MODEL_NAME=odessy-lfm-q8:jetson
export ODESSY_OLLAMA_MODEL=odessy-lfm-q8:jetson
export ODESSY_MODEL_NUM_CTX=1024
export ODESSY_MODEL_NUM_PREDICT=512
export ODESSY_ONNX_PROVIDER=cpu

export ODESSY_AUTO_FAST_MODEL=gemma3:1b
export ODESSY_AUTO_CAPABLE_BACKEND=lfm
export ODESSY_AUTO_CAPABLE_MODEL=odessy-lfm-q8:jetson
export ODESSY_AUTO_CLOUD_FALLBACK=off
```

First repeat the fake-model wake test from section 4. Say **Hey Jarvis**, wait
for the cue, then **what time is it?** Check transcription, spoken output,
another activation, and Ctrl+C cleanup. Then enable Q8:

```bash
python -m odessy --model-backend lfm 'what time is it?'
python -m odessy voice --listen --model-backend lfm
```

Only after that passes, test automatic selection. Run each text command as a
separate process, then start listening:

```bash
python -m odessy --model-backend auto 'hello'
python -m odessy --model-backend auto 'rewrite: The meeting starts tomorrow.'
python -m odessy --model-backend auto 'what time is it?'
python -m odessy voice --listen --model-backend auto
```

Expected initial routes for the three text requests are Gemma, Gemma, and Q8 LFM;
a fast-model failure may escalate to capable. For a voice greeting, use the first
request in a fresh session: requests carrying conversation history use capable.
The model status lines now show the actual model, backend, role, and reason before
generation, including fallback (`reason=fast_failed`). Keep those lines when
reporting a routing issue, along with what Whisper actually submitted and whether
history existed. `ollama ps` alone cannot distinguish initial selection from
fallback. A model shown as `Stopping...` is consistent with auto's unload policy.
Default model names are not an automatic weight download. Auto uses
`keep_alive=0`, so reloading can increase latency; no Jetson speedup is established.
In split mode, commands.toml entries apply only to local `terminal.run`, not
`laptop.terminal.run`.

Keep `tegrastats` running in another terminal throughout the tests. The
1,024-token context was demonstrated with a short greeting; tool prompts may
need more. If context is the failure, try `ODESSY_MODEL_NUM_CTX=2048` separately
and recheck memory. If CUDA allocation fails with voice running, change the
profile's `num_gpu` to 12 and recreate it, keeping the other settings fixed.
The 32-token diagnostic requests consumed their output budget on thinking;
their empty answer text was not a model-loading failure.

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
