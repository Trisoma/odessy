# Installed SSH and voice update — November 18, 2026

**Developer Preview · v0.5.0-alpha**

The existing `D:\Odessy-test` installation was upgraded through the versioned
installation and Windows registration engines. No uninstall, onboarding, model
download, publication or merge into `develop` was performed. The native setup
wizard was not driven; this run called its packaged installation owners directly.

## Source and installed identity

- Branch: `fix/ssh-history-inspector`.
- Application source: `7f3b925c83a3cb8f37eae7ef0199ec4b3890c50a`, clean at build time.
- Old installed version: `bef1bec427204cea972b70217152afa6`, retained for rollback.
- New installed version: `3b24e8b4c22d475cbb67c7bcf1816440`.
- Production Flutter entry: `lib/main.dart`, release build without checkout
  preview defines or `--demo`.
- Launch: `& 'D:\Odessy-test\launch.ps1'`, or the refreshed Start-menu shortcut.
- Actual executable: `D:\Odessy-test\versions\3b24e8b4c22d475cbb67c7bcf1816440\desktop\odessy_desktop.exe`.

| Artifact | SHA-256 |
| --- | --- |
| Python wheel | `294e343e5e2b84a0d6c374b07e1182504466ccee0258b7674d29647dc4123631` |
| Audited offline payload | `472196af066a8d9c687e6275b5ff4286d032f0c47257edf68e5d6e235c9f5030` |
| Flutter `data/app.so` | `7fd90a2510837d94c5d4b4720394d230e347f15315377a4c3b0faa98effdcf31` |
| Go monitor/password helper | `0115db556fc478eb6b411bc8268172b850f12a0eb7f1cc741e458989bf070fe1` |

The wheel, desktop, helper and bootstrap were built from the same source revision.
Unchanged dependency/runtime components were hash-verified before reuse. The
installed inventory and all 235 application source/resource files matched the
candidate. The selected user-installed Piper runtime and model remain external.

## Changes and regression checks

Changes were committed separately: idle audio-device refresh and Unicode Piper
input; monitoring readiness; exact SSH command approvals; configured preview
startup; source verification evidence; and installer record safety.

The existing installation had accumulated 15.3 MB of rollback/registration
metadata. An isolated upgrade reproduced failure at the former 16 MiB read
limit. The repaired engine uses a finite 64 MiB limit for reads and writes and
rejects oversized writes before replacing the previous record. It preserves
existing rollback inventories.

| Check | Result |
| --- | --- |
| Combined affected Python suite | 205 passed. |
| Additional installer/startup/registration suites | 44 + 32 passed; includes overlap with the combined suite. |
| Complete desktop suite | 644 passed, 15 skipped. |
| Complete Dart client suite | 69 passed, 2 skipped. |
| Real source service and focused launcher/SSH tests | 8 passed with deterministic loopback provider. |
| Desktop/client analysis | Clean. |
| Go monitoring suite | Passed. |
| Desktop contract validator | 107 settings validated. |
| Isolated upgrade with preserved voice selection | Passed, including recovery beyond the former metadata limit. |
| Installed authenticated HTTP | Startup, same-service voice refresh, authentication and SSH routes passed. |
| Native installed window | One window; launch receipt `ready`, UI observed `CONNECTED`, DeepSeek selected and voice controls present. |

Piper synthesized a fixed sentence into 4.145 seconds of audio in 2.022 seconds
using the new installed interpreter. Whisper transcribed that generated speech
in 2.111 seconds. Wake and VAD models loaded and processed silence frames. These
are local component checks, not microphone recognition, audible output or a
statistical performance benchmark. No paid provider call was sent.

## Preservation, cleanup and remaining acceptance

Configuration bytes, two profile files, eleven referenced speech/runtime assets,
two desktop preference files and SSH trust hashes were preserved. Installation
and verification took 761.71 seconds; Windows registration took 201.62 seconds.
This remains a full replacement upgrade, not a delta or automatic updater.

Temporary preview launchers, their copied application directories and this run's
build/payload staging are removed after installed verification. Unique preview
chat/profile data is archived under `D:\Odessy-test\profile-backups`; it is not
silently imported into the live history. Shared dependency caches, installed
maintenance resources, rollback versions and qualification receipts are retained.

Windows did not enumerate the saved Xiaomi Air4 SE input/output devices during
verification. Their selections were preserved; no different device was selected.
Reconnect the headset or explicitly select an available device and Save before
testing microphone, wake listening and speech output. The installed service
acknowledged an idle configuration refresh without restarting.

Live Jetson command execution, spoken wake recognition and human audibility remain
user acceptance checks. SSH still requires explicit execution selection and exact
approval, with the [documented remote boundaries](../setup/ssh-computers.md).
These local checks do not establish hosted CI, signing or public-release readiness.

Detailed local hashes, plans, logs and preservation/launch/cleanup receipts are
retained under `.qualification/ssh-voice-update-final-20261118/`.
