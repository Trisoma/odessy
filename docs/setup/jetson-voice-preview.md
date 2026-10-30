# Update and test voice on Jetson

October 30, 2026 preview: selectable audio input/output, existing-asset detection
and Browse. This is a source-backed Jetson preview, not an installer or standalone
runtime distribution. Existing models, credentials and user configuration are not
included in the archive and are not replaced by these steps.

## Copy from Windows

Use Windows PowerShell. The earlier cable address is `10.1.1.2`; replace it if
your Jetson address has changed:

```powershell
scp "C:\Users\13647\OneDrive\Desktop\MiMundo\GitHub\odessy\odessy\build\odessy-desktop-arm64.tar.gz" jaimef@10.1.1.2:~/
scp "C:\Users\13647\OneDrive\Desktop\MiMundo\GitHub\odessy\odessy\build\odessy-desktop-arm64.tar.gz.sha256" jaimef@10.1.1.2:~/
```

## Update and launch on Jetson

Close Odessy first. Open a terminal **on the Jetson desktop**, then paste:

```bash
cd ~
sha256sum -c odessy-desktop-arm64.tar.gz.sha256
```

Continue only if it reports **OK**:

```bash
mkdir -p ~/odessy-desktop-preview
tar -xzf ~/odessy-desktop-arm64.tar.gz -C ~/odessy-desktop-preview
cp -a ~/odessy-desktop-preview/src/odessy/. ~/odessy/src/odessy/
cp ~/odessy-desktop-preview/pyproject.toml ~/odessy/pyproject.toml
cd ~/odessy
source .venv/bin/activate
export PYTHONPATH="$HOME/odessy/src"
python -m pip install -e '.[ui,pairing]'
"$HOME/odessy-desktop-preview/bundle/odessy_desktop"
```

The dependency update may need Internet access but downloads no model. This uses
the existing `/home/jaimef/odessy/.venv/bin/python`. Keep your existing Whisper,
Kokoro, audio and wake dependencies; no new voice provider was introduced. Launch
from the desktop terminal rather than SSH/X11 forwarding. Do not run a second old
preview at the same time. Python/UI changes require closing and reopening Odessy.

## Human test checklist

1. **Voice devices:** Settings → Voice. Keep audio backend **Auto**. Use Refresh
   devices if needed. Choose Microphone and Speaker, or leave System default.
   Linux Auto still uses ALSA commands. If your headset is absent, check Ubuntu's
   Sound input/output selection and Bluetooth microphone profile. Detection does
   not record or play sound.
2. **STT assets:** Settings → Speech to text. Use Detect existing components;
   choose the Whisper executable and model, or Browse. The executable is normally
   `/home/jaimef/whisper.cpp/build/bin/whisper-cli`, not its parent folder.
   A typical model is `/home/jaimef/whisper.cpp/models/ggml-tiny.en.bin`.
   Save this section. TTS, Voice, LLM and host settings must remain unchanged.
3. **STT test:** choose Use microphone instead if an old WAV is selected. Click
   Test transcription, approve Run test, and speak a short sentence immediately
   for the five-second recording. Verify the displayed transcript. Alternatively
   choose a known WAV: mono, 16 kHz, 16-bit PCM, no longer than 30 seconds.
4. **TTS assets:** Settings → Text to speech. Detect/Browse the existing Kokoro
   ONNX and voices files. Save only this section. Click Test speech output and
   approve playback. Confirm the sentence is audible through your selected speaker.
5. **Save/reopen:** close Settings, reopen each section, and confirm its own saved
   values remain. No other section should have been changed. Discovery is not a
   compatibility test, and a successful TTS result does not prove human audibility.
6. **Conversation microphone:** in Voice settings enable the pipeline explicitly
   once STT and TTS are configured, then save. Close/reopen Odessy or use its
   reviewed restart flow to apply changes. In Assistant, check voice availability,
   record a short request, finish recording and verify the transcript/answer. Stop
   and cancellation must leave text chat usable. A model response may use your
   configured cloud provider and incur normal usage; the independent STT/TTS
   component tests do not call the LLM.
7. **Optional wake:** select an existing wake model using Detect/Browse. Do not
   download one implicitly. Reopen/apply saved changes, then explicitly enable wake
   listening in Assistant. Speak the phrase belonging to that model (the existing
   pretrained Jarvis model responds to “Hey Jarvis”, not “Odessy”). Wait for the cue,
   then ask a short question. Disable wake afterward and confirm listening stops.
   Wake assets retain their separate licensing restrictions.

Capture failures with the exact screen/action and message. Do not paste API keys.
If transcription fails, distinguish the selected WAV/microphone, executable/model
paths and audio-device availability rather than changing unrelated settings.

## Evidence and release boundaries

### User-confirmed Jetson wake success — October 30, 2026

The maintainer confirmed successful wake interaction on the physical Jetson with
the existing Jarvis model. Reproduce that workflow:

1. Open **Settings → Voice**, select your microphone and existing wake-model file,
   then **Save**. STT/TTS must already be configured and voice enabled.
2. Restart Odessy if requested.
3. Open **Assistant → Enable wake listening on coordinator**.
4. Say **“Hey Jarvis”**, wait for the cue, then ask your question.

Keep Odessy running. This Jarvis model recognizes **“Hey Jarvis”**, not “Odessy”.
If it fails, first verify **Settings → STT → Test transcription** works with your
microphone. This result is user-reported physical-device evidence, not an
automated hardware test or qualification of every audio device.

After the cue, wake capture waits up to **10 seconds for speech to begin**. Once
speech begins, approximately **2.5 seconds of detected silence** ends the recording;
the default recording ceiling is **20 seconds**. These durations count processed
audio frames, so processing delays can extend the wall-clock wait. Transcription
and model response time are additional. If recording repeatedly reaches the
ceiling after you stop speaking, investigate microphone noise and VAD decisions.

Windows and Linux ARM64 release compilation and automated tests do not establish
physical microphone, playback, wake, Bluetooth or clean-machine acceptance.
Record those human results before declaring voice qualified. Existing full-suite
baseline failures and release CI must also be resolved or explicitly dispositioned;
successful voice testing alone does not qualify the entire public release.
