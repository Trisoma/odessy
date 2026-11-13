# Wired production UI evidence

Actual Flutter RepaintBoundary PNG renders of `OdessyApp`, not the separate TasteCode demo. Desktop: 1600 x 1050; narrow: 720 x 900. Explicit Demo/offline fixtures; no microphone, command, provider or remote device was used. Device values, outputs and approvals in these images are fixtures, not live qualification. Handoffs/Jobs truthfully show unavailable where the capture API does not implement their services.

From `apps/desktop`, using the existing cached SDK:

```powershell
flutter pub get --offline
flutter test --no-pub --dart-define=CAPTURE_WIRED=true test/tastecode_wired_test.dart
flutter run -d windows --no-pub
```

The test is the deterministic capture entry point and constructs the normal `OdessyApp` with isolated project metadata/preferences. Normal launch remains `lib/main.dart`; use its existing connection dialog/local configuration. `lib/tastecode_demo.dart` remains an explicit design reference only.

Adapted hierarchy: boat/sidebar, searchable project/session groups, workspace context, inspector navigation, exact approval details, visible voice controls and two handoff step cards. Retained production settings/usage/jobs/recovery are richer than the original demo. Layout is guidance, never a feature inventory.

Working production wiring: authenticated inspector, policy-checked reads, explicit reviewed text attachments, existing text send/stop and approvals, local preference persistence, live process-session selection, retained device/voice owners. Output is read-only; Harness has no fabricated stdout. Session metadata does not persist conversations. SSH is absent on this source base.

No reference source/assets were copied in this integration; existing Odessy fonts/boat/components and their licensing are retained. No dependencies added.

Checks: 567 desktop tests passed, 12 skipped; client 50 passed, 2 skipped; focused Python boundary/runtime suites; final report lists limitations. Captures assert no rendering exception. Assistant/diff/settings/handoff/narrow-navigation images inspected visually. No native-window, installed, hardware or human-acceptance claim. Dedicated denied-device screenshot and a single live Python-client-view smoke remain pending.

## Screenshots

- [01-assistant-approval](01-assistant-approval.png)
- [02-composer-voice](02-composer-voice.png)
- [03-files](03-files.png)
- [04-output](04-output.png)
- [05-diff](05-diff.png)
- [06-activity-usage](06-activity-usage.png)
- [07-workspaces](07-workspaces.png)
- [08-devices](08-devices.png)
- [09-device-performance](09-device-performance.png)
- [10-device-details](10-device-details.png)
- [handoffs](handoffs.png)
- [jobs](jobs.png)
- [narrow-handoffs](narrow-handoffs.png)
- [narrow-navigation](narrow-navigation.png)
- [settings-1](settings-1.png)
- [settings-2](settings-2.png)
- [settings-3](settings-3.png)
- [settings-4](settings-4.png)
- [settings-5](settings-5.png)
- [settings-6](settings-6.png)
- [settings-7](settings-7.png)
- [settings-8](settings-8.png)
