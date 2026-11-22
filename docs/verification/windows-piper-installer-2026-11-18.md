# Windows Piper installer verification — November 18–19, 2026

**Developer Preview · v0.5.0-alpha · Windows x64 · unpublished candidate**

**Superseded for publication on 2026-11-19:** the recorded EXE predates the
OpenTelemetry 1.45.2, `@hono/node-server` 2.1.6 and audio error-disclosure fixes.
Its hashes and test results remain historical evidence. A new matching installer
build and qualification are required before distributing these fixes.

## Current rebuilt draft candidate

- Application source: `6f96e71c0d1517030bf30729546d4bb90578bdc3`; clean working tree at build time.
- File: `Odessy-v0.5.0-alpha-windows-x64-setup.exe`.
- SHA-256: `2d634100c87251f3f685fdf0a43b734b534b08b6d8643d7e1458c25992abf47a`.
- Size: 322,126,953 bytes.
- Local output: `build/releases/v0.5.0-alpha/6f96e71/windows-x64/`.
- Python wheel, setup/bootstrap and production Flutter app rebuilt from that source.
  Unchanged bundled Python/Node/Rust/Go dependency inputs were reused only after
  verification against the previous closed file inventory. No new dependency download.
- Generated bootstrap bytecode was excluded. An initial staging-only attempt
  failed its inventory check before installation; that rejected artifact is not
  this candidate. Public checksums must identify only the corrected file above.
- The public alpha label is `v0.5.0-alpha`; embedded Python/Flutter development
  version metadata retains its recorded value. This is not a stable release.
- Unsigned. **Final maintainer verification is pending.** Acceptance of the older
  installer below does not transfer to this rebuilt file.

| Current check | Evidence |
| --- | --- |
| Affected Python setup/install suites | 131 passed |
| Affected setup/management widgets | 68 passed |
| Flutter analysis / settings contracts | Clean / 107 contracts |
| Production Windows build | Succeeded from `6f96e71` |
| Hosted CI at `6f96e71` | Python, desktop, Node, Rust/link, native offline setup and CodeQL checks passed; Windows Python 3.12: 3,969 passed, 51 skipped, 122 subtests |
| Prior fresh isolated installed Piper (`0da75ae`) | Cached hash-pinned components installed into the packaged Python; model loaded and generated a mono 22,050 Hz WAV (3.622 seconds) |
| Final-source upgrade/repair of managed Piper | Passed; new runtime verified before activation, interpreter rebound and mono 22,050 Hz WAV generated (5.364 seconds); previous runtime retained |
| Preservation | Saved audio devices, model/workspace and unrelated user-file/history/reference sentinels preserved; sentinels are not real vault credentials |
| Cache/error handling | Zero remaining download bytes; missing model sidecar refused; final installed `pip check` passed |
| Installed source identity | Final installed application sources/resources and desktop executable match build hashes; registration completed |
| Windows OpenSSH launch environment | Real local configuration resolution passed with the qualification launch environment; no network login implied |

These are packaged bridge and native checks, not microphone/speaker acceptance or
an external user's full golden path. The final candidate was verified through a real upgrade of the isolated
`0da75ae` fresh installation. It has not been installed a second time from an empty
root. The qualification reused verified voice downloads; it is not a new
empty-cache download test. The earlier empty-cache
and human reports below retain their original artifact scope. Existing user
installations were not replaced for this candidate qualification.

Release remains contingent on review/merge gates and final human verification.
Greptile review is unavailable while organization credits are exhausted; that
is not a successful review. Keep Windows checkpoint restoration disabled.

## Native extraction scope

The native bootstrap was exercised with the intermediate `0da75ae` candidate
(`1ceaf100ef23850acb6f9723bc0516856c194ad5f78aa9e184959b35e5172cea`).
Its 8,350 extracted files and actual packaged helper context verified. The
longest real extracted path was 256 characters; a separate NSIS boundary test
covers longer paths. The final candidate retains that bootstrap source and
passes its package audit and installed helper path, but a repeat GUI launch of
the final EXE is pending maintainer verification. Computer Use was stopped with
Escape; no final visual acceptance or full GUI installation is claimed.

## Earlier installer accepted on November 18

The following dated evidence describes the earlier installer only.

The maintainer confirmed that the rebuilt installer completed successfully, then
confirmed SSH connects after correcting the test launch environment. These are
human reports, separate from the automated checks below. No new installer run
was needed to resolve the SSH error.

### Earlier candidate identity

- File: `Odessy-v0.5.0-alpha-windows-x64-setup.exe`
- SHA-256: `15063674bf944db7beae83fa4b55318aca831e47801db7d3828a41f6edf831ae`
- Size: 320,916,018 bytes.
- Local build output: `build/releases/v0.5.0-alpha/1834c1a/windows-x64/piper-auto/`.
- Base revision: `1834c1a441fb444ecb1712429c550bafce7d3b19` on `fix/windows-setup-long-paths`.
- This is a **dirty source snapshot** with the hashes below; the base commit alone
  is insufficient to reproduce it. The adjacent `verification.json` is the build receipt.
- Unsigned; no release upload, signing or new public asset URL is established here.

### Earlier scope and checks

| Check | Result |
| --- | --- |
| Affected Python setup suites | 109 passed in the candidate build receipt |
| Flutter setup tests | 53 passed |
| Complete Flutter suite | 650 passed, 15 skipped |
| Flutter analysis / desktop contracts | Clean / 107 runtime settings |
| Production Windows build | Succeeded |
| Fresh isolated speech selection | Downloaded pinned Piper/Whisper from an empty voice root; no existing Piper installation required |
| Installed Piper | Active runtime loaded the model and generated a valid mono 22,050 Hz WAV, 3.309 seconds of audio |
| Cache/error handling | Cached plan reports zero remaining bytes; missing model sidecar refused; pip check passed |
| Isolated Windows registration | Completed; final EXE extracted and launched the production setup |
| Human installation | Maintainer reported rebuilt installer successful |
| Current affected setup/install/SSH suites | 135 passed, including seven added environment cases and real local OpenSSH configuration resolution |
| Actual installed SSH endpoint | Authenticated `/v1/ssh/inspect` returned HTTP 200 after corrected launch; unknown host remained untrusted |
| Remote SSH login | Maintainer confirmed “SSH connects now”; no additional remote command execution claimed |

The full Python repository suite was stopped at approximately 63% for time; there
is no full-suite success claim. Candidate receipt counts precede this documentation
update and retain their exact source scope. Current focused 135-test results were
run after adding the launch-environment regression cases.

### SSH incident and resolution

The temporary native-installer qualification launcher constructed a reduced
environment for an isolated profile but omitted `ProgramData` and `ALLUSERSPROFILE`.
The desktop and its service inherited it. Local OpenSSH `ssh -G` then exited 255
without stdout or stderr, before network authentication. Applying just the failing
`ProgramData` difference to a working environment reproduced the error.

Restoring the Windows system-folder variables made the same OpenSSH call return
0 with configuration output. The existing desktop was closed and relaunched with
the same executable, installation root and profile, preserving the selected voice
configuration and data. Its authenticated SSH route then passed, and the maintainer
confirmed login. No application binary, installed module, host-key rule, password,
execution approval or installer was changed for this repair. The separate working
test installation was untouched.

The production NSIS bootstrap inherits the Windows environment and adds only its
payload pointer. `desktop_environment()` removes four setup/Python context variables
and preserves system/profile values. Normal Start-menu launch also inherits the
user environment. Regression tests now check that preservation, and a real local
OpenSSH configuration check passes with the production launch environment.
Custom launchers must preserve these variables; this evidence is not a guarantee
against every future host configuration or networking failure.

### Voice delivery and remaining gates

Piper is recommended for CPU, and Kokoro remains selectable. Optional speech
downloads install the selected engine and model plus Whisper/audio dependencies;
wake is a separate choice. The EXE does not embed all optional model downloads.
Verified cache files alone do not mean the active runtime is ready. Model import
and Piper model-load checks run before configuration activation. Provider, engine,
model and wake licenses retain their separate terms.

Microphone capture, speaker playback, spoken wake, all hardware, clean-machine
behavior and the complete external-user golden path are not universally qualified.
Windows checkpoint restoration remains disabled. Existing screenshots and older
upgrade receipts keep their own revision and hardware scope. The current draft uses the rebuilt candidate above; this older human-tested
installer is retained as historical evidence only. Public distribution, signing and hosted CI are separate actions.

### Packaged dirty source hashes

These are build-time hashes, before the subsequent documentation corrections.

| Path | SHA-256 |
| --- | --- |
| `THIRD-PARTY-NOTICES.md` | `df6234d2b810c36d44f1245e6dd02e735c194b53d8321619e8fb69d2234434e5` |
| `apps/desktop/lib/app/setup/setup_application.dart` | `be1d8b84d2afa19a31b30a0270f935dee0297f1c747ee93d7fcbdfd87666a950` |
| `apps/desktop/lib/features/setup/presentation/get_started_page.dart` | `1ac736bfd12dd45ce5552c6c705f3a1113855826b74d211bb7ae29b7beb5dfb7` |
| `apps/desktop/lib/features/setup/presentation/setup_wizard.dart` | `d952c4db98508f07b0f6fec7a34fb2781e6adf2c24d32eaa2c2cbe5c3ad487df` |
| `apps/desktop/test/setup_flow_fakes.dart` | `b3b9b53e7fea740d7ba6e0187fd9fa89cc6a516589f8a7d6306aa612dd1f6348` |
| `apps/desktop/test/setup_streamlined_test.dart` | `0df33e91b2ef1170a2f261e91d880d8599620d1284ce4f8d2248b83e5690231f` |
| `docs/setup/voice-components.md` | `0c10b975c4d4dec1bae111d0c4aabb85685af73ebfafd61fed511a83c6818f9b` |
| `docs/ui/piper-connection.md` | `558f8cead60816800be78c3fb4ec4a2b642b182facd4e2b0d6b563471a2be94e` |
| `docs/ui/voice-performance.md` | `20f6aa9bb02850327a0020c077185860d717344239fbd64b4a22e669247c3e76` |
| `installer/README.md` | `556d52754308d0d54eb768a52193d88273e6302a0315d9a00d1af5d78973723d` |
| `installer/build/pin_voice_manifest.py` | `aceae9b569f05d9d8c8847ce20d03b31f123c053997184d65179092f76c7f12d` |
| `src/odessy/setup/configuration.py` | `307bf46261770bf77a0272185b956375e6f1661dc76ec2a84c9c855a4744c096` |
| `src/odessy/setup/native_bridge.py` | `f2e0436405427f77bcd29a9ee222f6225d749809c55ec24799dd07d8837c6990` |
| `src/odessy/setup/voice-artifacts.json` | `e5e8fec77d16479177e06e4fb7c97f55a0e2949735b63db6d5a5c5efa4e34f30` |
| `src/odessy/setup/voice_install.py` | `e7654c08ca7ecc3574d4f6cdfb4d2ea03e4f7fce1f7208caa1308700aa21af30` |
| `tests/test_setup_configuration.py` | `433e5e4aa21a4569e7b176424944a9f2114b543aa6ece9c0ae4f887141ebc409` |
| `tests/test_setup_native_bridge_voice.py` | `bc90e325dc88fb82eef23296e45f237925e0e2368307760ce18b5b43050833ff` |
| `tests/test_setup_nsis.py` | `32830dcb2ca69884fb2b73aafee1a3bf2c7850ce962621919d96c606294ca49a` |
| `tests/test_setup_voice_install.py` | `d9efbab18a2cfe5871ff337b7242ee971970775542406d943ce965f71f0f7a2b` |
| `tool/windows_nsis.py` | `7878408549bfd2796751d692414e5a48611a9c14049394f4f9076b68d988764b` |
