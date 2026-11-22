# Save audio settings without restarting

**Developer Preview · November 17, 2026 · source verification**

Branch: `fix/ssh-history-inspector`. Base: `3b7cea13922d773f95275c970f1d50c275cd5a04`
with existing uncommitted work preserved. This receipt covers the source fix;
the earlier installed-update receipt does not include these later changes.

## Behavior

Save now requests an idle native-backend refresh even if device identifiers are
unchanged. This clears PortAudio's cached enumeration before a new adapter uses
the saved input/output selection. Ordinary polling does not repeatedly reset
audio. Refresh does not open a microphone, play speech, or change model/workspace
configuration.

Active recording, wake listening, preparation, decoding, synthesis/playback and
draining cancellation defer application. Native reservations also protect
device enumeration, stream startup and blocking shutdown. No active stream is
reset to apply a setting. Once idle, the existing configuration watcher applies
the saved selection; the next microphone/wake operation uses it.

Refresh errors do not acknowledge application or replace the old idle adapter.
The request remains retryable. Sounddevice's paired private lifecycle functions
are isolated behind the native adapter and its reservation lock because the
supported version has no public hot-plug refresh API. A future sounddevice
upgrade must recheck this integration.

Refresh cannot repair an unplugged or ambiguous saved device. Choose an
available device in Settings and save; no replacement is selected silently.

## Changed audio files

- `src/odessy/voice/native_audio.py`: native refresh and stream reservations.
- `src/odessy/application/voice_configuration.py`: explicit refresh, deferral and acknowledgment.
- `src/odessy/application/desktop_voice.py`: include preparation/decoding/speaking/draining in busy state.
- `tests/test_native_audio.py`: enumeration refresh, startup/shutdown protection and retry.
- `tests/test_live_voice_configuration.py`: HTTP fingerprint/authentication, unchanged Save,
  refresh failure, deferred application and actual controller capture/wake/playback lifecycles.

The lifecycle tests use deterministic audio adapters, without microphone/model
access. They start each operation through `DesktopVoice`, save changed devices,
verify its state/owner/adapter survive, stop it, then verify the next operation
receives the new input/output configuration.

## Checks

The affected backend suite passed **146 tests**, with **2 Windows skips** for
POSIX execute-permission checks:

```powershell
python -B -m pytest tests/test_native_audio.py tests/test_live_voice_configuration.py tests/test_desktop_voice.py tests/test_desktop_voice_api.py tests/test_voice_runtime_events.py tests/test_voice_native_startup.py tests/test_voice_device_discovery.py tests/test_voice_path_checks.py tests/test_component_settings.py tests/test_setup_native_bridge_voice.py tests/test_voice_speech_latency.py tests/test_hybrid_audio.py tests/test_onnx_runtime_wiring.py -q
python -B tool/check_desktop_contracts.py
```

The contract validator passed for 107 settings. Targeted desktop analysis and
Dart formatting passed. The final Flutter run passed **47 tests** across
`agent_basics_layout_test`, `component_settings_test`, `voice_controller_test`,
`voice_device_selection_test` and `ui_voice_lifecycle_test`. The typed client's
`voice_api_test.dart` passed **3 tests**. `git diff --check` passed (existing Go
files emitted only line-ending warnings). These are focused checks, not a full
repository or installed-application qualification.

```powershell
# From apps/desktop, with the existing resolved cache
flutter test --no-pub test/agent_basics_layout_test.dart test/component_settings_test.dart test/voice_controller_test.dart test/voice_device_selection_test.dart test/ui_voice_lifecycle_test.dart
# From packages/odessy_client
dart test test/voice_api_test.dart --reporter expanded
```

The cached Windows Python 3.13.15 interpreter was used
with `site.addsitedir` for `.ci-venv313/Lib/site-packages`; the old virtualenv
launcher and default Cygwin `python` are broken on this machine. No dependency
installation was needed. Local logs: `.qualification/voice-layout-preview-20261117/`.

The approved layout keeps the inspector at the right, with a dismissible sheet
at very small widths, horizontal tabs and composer draft preservation. See the
[updated screenshots](../view/ssh-history-inspector/README.md). Its
`right-inspector-20261117/manifest.json` records source/image hashes from final
verification, without claiming new live-service or hardware captures. Copies are under
the Desktop's `Odessy-layout-preview-20261117` folder. Older bottom-inspector
captures are explicitly superseded.

## Preview boundary and manual check

No installer was rebuilt, no installed files were replaced, and neither
`D:/Odessy` nor `D:/Odessy-test` was modified for this fix. No commit/push/merge.
Existing unrelated SSH/history changes remain in the working tree.

In a source service using this checkout, select a currently listed microphone,
save while idle, and use **Test speech input** or enable wake listening without
restarting. Repeat Save during an active operation: it must report deferred and
leave that operation running until explicitly stopped. Spoken recognition,
Bluetooth reconnect behavior and human audio acceptance remain hardware checks;
fixture regressions do not claim those passed. Installer delivery requires a
later source-matched candidate after preview review.
