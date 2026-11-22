# Voice components in setup

For changes to configured voice providers/devices, see
[applying voice settings](../ui/live-voice-settings.md). Updated coordinators apply
saved voice changes when audio is idle; active recording/listening is preserved.

Setup offers speech components as an explicit opt-in. Leaving the opt-in off
keeps the previous offline flow unchanged: nothing is downloaded and nothing
contacts the network.

## What is offered

Every artifact is pinned by SHA-256 and size in `src/odessy/setup/voice-artifacts.json`.
The release helper `installer/build/pin_voice_manifest.py`
generates the manifest from cached files and compares independent digests where
available: wheels against PyPI metadata and release assets against published
digests. Whisper/Piper file-only pins hash the local cache; obtain and verify
those files separately. Their revision-pinned URLs identify an upstream snapshot,
not an independent publisher checksum. Wake assets use the runtime asset table.

| Component | Kind | License |
| --- | --- | --- |
| whisper.cpp `whisper-cli.exe`, `whisper.dll`, `ggml*.dll` (x64, v1.8.2) | `archive` | MIT |
| `ggml-base.en.bin` | `file` | MIT |
| Piper 1.3.0 engine (selected by default) | `wheel` | GPL-3.0-or-later |
| Piper `en_US-ljspeech-medium.onnx`, matching JSON and model card | `file` | MIT repository metadata; model card retained |
| `kokoro-v1.0.int8.onnx` (model-files-v1.1) | `file` | Apache-2.0 |
| `voices-v1.0.bin` (model-files-v1.1) | `file` | Apache-2.0 |
| numpy, onnxruntime, soundfile, sounddevice, kokoro-onnx and their transitive packages | `wheel` | per wheel; `phonemizer` and `espeakng-loader` are GPL-3.0-or-later |

Wheels are resolved for **CPython 3.11 / win_amd64**, because that is the
interpreter the packaged runtime ships. Pinning them for any other version would
produce a download that cannot be imported.

Automatic downloads target Windows (x86_64) only. Other platforms keep their
existing voice setup and report that limitation instead of offering a file.

## The wake word is a separate opt-in

The wake-word model is offered on the same step as the speech components but as
its own switch, because it is a different thing in every way that matters:

| | Speech components | Wake word |
| --- | --- | --- |
| What it is | Whisper input + selected Piper or Kokoro output | One pretrained detector model |
| Licence | Selected engine/model terms, shown before download | **CC BY-NC-SA 4.0** (non-commercial) |
| Size | Fresh Piper selection: about 260 MB; review shows remaining bytes | About 77 MiB including support models and Python packages |
| Where it lands | `%LOCALAPPDATA%\Odessy\Voice` | `~/.local/share/odessy/wake` (`assets.asset_dir()`) |

Both are offline-safe to skip. Setup states the licence before the download, not
after it, because the licence is the reason the switch exists: a user who does not
want a non-commercial model can leave it off and still have a working Odessy.

The model is pinned like everything else (1,271,370 bytes, sha256
`94a13cfe60075b132f6a472e7e462e8123ee70861bc3fb58434a73712ee0d2cb`, the
`openWakeWord` v0.5.1 release asset). Its Python support (`openwakeword` and the
wheels it needs) is installed into the installed runtime in the second phase,
only for the features the user chose. `voice-install` records the verified digest
in the wake directory's `assets.json`, which is the same manifest
`odessy.voice.assets.load_manifest()` reads, so the detector and the installer
cannot disagree about which file is trusted.

### Features are part of the request

`voice-plan`, `voice-install`, `voice-status` and `voice-python-install` all take
an optional `features` list (`['voice']`, `['wake']`, or both). Omitting it means
"everything offered", which keeps the pre-existing behaviour. The packaged bridge
validates the list and passes it through, so a wake-only choice cannot silently
plan or download the speech components.

The setup UI sends `tts_provider: piper` or `kokoro` with each speech request.
Piper selection downloads the pinned engine wheel, English ONNX model, adjacent
`.onnx.json`, model card and repository license metadata, plus Whisper and shared
audio dependencies. It excludes Kokoro packages and weights. Kokoro selection
excludes Piper. No existing Piper installation is required for automatic setup;
manual executable/model selection remains available.

`voice-status` answers per feature in `readiness` (`{'voice': …, 'wake': …}`) and
adds one `wake` finding. Wake-word readiness requires **both** the verified model
and support models, then loads the detector and voice-activity model in the
installed runtime. Microphone access is checked separately.

## Download, install and activate

1. **Download and verify** (after the user confirms Install, before the Odessy
   runtime is installed). Files land in
   `%LOCALAPPDATA%\Odessy\Voice` (override with `ODESSY_VOICE_ROOT`); wheels land
   in `...\Voice\wheels`. Nothing here needs an installation.
2. **Install the Python packages** (after installation). They go into
   `<install root>\versions\<active>\Scripts\python.exe` — the venv the
   application actually imports from — with `pip install --no-index --no-deps
   --require-hashes --find-links <wheels>`. Offline and hash-locked, so an index
   cannot substitute a package. Readiness requires imports of the shared audio
   packages and the selected engine (`piper` or `kokoro_onnx`). Piper additionally
   loads the verified model and matching JSON in an isolated subprocess.
3. **Activate configuration**. Automatic Piper setup saves the installed Python
   executable and model paths through the reviewed configuration operation.
   A failure leaves an explicit retry/error state; cached files alone never mean
   that speech is ready. The desktop uses the existing external Piper adapter.

An all-cached plan displays zero remaining download bytes, then still installs
and verifies the packages in this installation. Model files stay outside the
versioned app; the engine is installed again in the active runtime as needed.
Microphone, speaker and spoken wake acceptance remain separate hardware checks.

The voice root is deliberately outside the installation folder: the packaged
installer refuses a non-empty installation folder, and speech models are
user-owned data that must survive an upgrade, rollback or uninstall.

## Safety rules

- An archive is verified as a whole, then only its allow-listed members are
  extracted as real files. A ZIP is never saved as an `.exe`, and a member name
  that is a path is rejected by the manifest loader.
- An existing file is **preserved** when its digest matches, and never replaced
  when it differs. Setup reports the conflict and stops.
- Downloads stream to `<name>.partial`, are verified, then moved into place
  atomically. A cancelled or failed download leaves no partial file behind.
- Insufficient free space is refused before the first byte is written. Below 25%
  headroom the plan states how much would remain free *before* the download.
- `voice-install` requires the `plan_id` of the plan the user was shown; an
  edited selection or a re-planned download is a stale plan and is refused.
- Each produced file's digest is recorded in `voice-install.json`, so a later
  status check can prove the file is the one that was installed.

## What a result means

`voice-install` and `voice-status` never reduce the answer to one boolean:

| Field | Meaning |
| --- | --- |
| `attempted` | Work was started (a false "nothing happened" hides a cancelled run) |
| `persisted` | Files are on disk now |
| `verified` | Every required component matched its pin afterwards |
| `severity` | `info` / `warning` / `error`, promoted from the per-component findings |

`voice-status` returns one finding per component plus one for the Python side,
each with a stable `check_id` (`setup/voice/<id>`), a `severity`, a `message`, a
`path` and a `fix_hint`. A missing optional component is a **warning**; a file
that exists but does not match its recorded digest is an **error**, because it
will not be used. Microphone and audio-device readiness remain a separate check
in Settings.

On Windows, a voice-enabled coordinator imports the optional native audio
libraries on its main thread before serving requests or starting executor
workers. First loading NumPy's native DLLs in a worker while the main thread
starts another worker can hang the local service. This initialization opens no
microphone and loads no model weights; missing optional libraries still allow
the text service to start. Linux retains its existing lazy initialization.
After a wake cue finishes, the session reports `Recording speech` so the desktop
can show that the command window is open. Model-file verification alone does not
verify microphone selection, wake sensitivity or transcription accuracy.

## Upgrading after removing the speech cache

Managed Piper is installed and verified in the replacement runtime before an
update is activated. If its hash-pinned wheel cache was deleted or verification
fails, the update stops and the previous version remains active. No download or
silent speech disable occurs.

To proceed without speech, close the running Odessy app. In **Manage Odessy**,
choose **Pause speech for update**, review the change and **Confirm once**. Then
review **Repair or update files** again. Text use remains available. Speech
paths, audio devices, wake model, model configuration and workspace are retained.
Speech stays paused until an explicit **Enable saved speech** confirmation;
that uses the retained provider path, so keep its previous runtime/files. It
does not claim to restore missing or damaged files or download them. If those
files are unavailable, select valid components in Settings or rerun speech setup
with its explicit download consent. Microphone/wake/playback checks remain
separate from component verification.

Voice-only configuration changes merge supplied keys over the stored voice
selection. Omitted device/backend/wake fields are preserved; an explicit empty
string clears a selected device. Stale configuration reviews are rejected.

## Where the ideas came from

Reference implementations were read, and only the mechanics were adapted:

- **Hermes** (`scripts/install.ps1::Install-DesktopVoiceDeps`, `hermes_cli/setup.py`):
  state the expected cost before starting, keep voice best-effort so it never
  blocks the installation, and put the manual remedy in the failure message.
- **OpenClaw** (`src/flows/health-checks.ts`, `src/commands/doctor-disk-space.ts`,
  `src/wizard/setup.inference-verification.ts`, `src/wizard/setup.completion.ts`):
  structured findings with a stable `check_id`, a closed severity set and a
  `fix_hint`; two-tier disk-space reporting; the `attempted`/`persisted`/
  `verified` result; a fix-or-continue choice on failure; and doing nothing when
  a status check shows there is nothing left to do.

Their interfaces and unrelated agent features were not copied.

## Related

- Review step: `docs/setup/wizard.md`
- Existing-file discovery: `src/odessy/setup/voice_assets.py`
- Reproducing the pins: `installer/build/pin_voice_manifest.py`
