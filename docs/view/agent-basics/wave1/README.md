# Wave 1 layout renders (prompt 03)

Production `OdessyApp` rendered by `test/tastecode_wired_test.dart` with
offline fixtures (`WiredCaptureApi`, `CaptureProjects`, `SavedComponents`)
explicitly injected. The standalone `tastecode_demo` entry point is not
evidence here. No backend, network, model, provider or installer ran.
PNG bytes are written by that test into this directory; original files under
`docs/view/` are unchanged.

| File | Size | State |
| --- | --- | --- |
| `01-assistant-approval.png` | 1600x1050 | conversation, assistant reply with a pending approval |
| `01b-activity-expanded.png` | 1600x1050 | conversation, expanded activity disclosure |
| `02-composer-voice.png` | 1600x1050 | conversation, voice menu anchored to the composer model row |
| `03-files.png` | 1600x1050 | inspector open on Files |
| `03b-attachment.png` | 1600x1050 | inspector open on Files with attachment metadata |
| `04-output.png` | 1600x1050 | inspector open on Terminal tab showing recorded output only |
| `05-diff.png` | 1600x1050 | inspector open on Changes with a preview diff |
| `06-activity-usage.png` | 1600x1050 | Activity & usage page, offline usage fixtures |
| `07-workspaces.png` | 1600x1050 | Workspaces page |
| `08-devices.png` | 1600x1050 | Devices hub |
| `09-device-performance.png` | 1600x1050 | device performance inside the Devices hub |
| `10-device-details.png` | 1600x1050 | device details reached through the Devices hub |
| `conversation-1280.png` | 1280x800 | conversation at 1280x800 |
| `handoffs.png` | 1600x1050 | Handoffs page |
| `jobs.png` | 1600x1050 | Jobs page |
| `narrow-assistant.png` | 720x900 | narrow 720x900 conversation with drawer closed |
| `narrow-diff.png` | 720x900 | narrow 720x900 inspector overlay on Changes |
| `narrow-handoffs.png` | 720x900 | narrow 720x900 Handoffs page |
| `narrow-navigation.png` | 720x900 | narrow 720x900 navigation drawer with icon rail destinations |
| `narrow-settings.png` | 720x900 | narrow 720x900 settings |
| `settings-1.png` | 1600x1050 | settings section 1 of 8, dark theme |
| `settings-2.png` | 1600x1050 | settings section 2 of 8, dark theme |
| `settings-3.png` | 1600x1050 | settings section 3 of 8, dark theme |
| `settings-4.png` | 1600x1050 | settings section 4 of 8, dark theme |
| `settings-5.png` | 1600x1050 | settings section 5 of 8, dark theme |
| `settings-6.png` | 1600x1050 | settings section 6 of 8, dark theme |
| `settings-7.png` | 1600x1050 | settings section 7 of 8, dark theme |
| `settings-8.png` | 1600x1050 | settings section 8 of 8, dark theme |
| `settings-light.png` | 1600x1050 | settings, light theme |

Desktop captures are 1600x1050, `conversation-1280` is 1280x800 and
`narrow-*` are 720x900. `settings-1`..`settings-8` map to the eight existing
settings groups; `settings-light` is the light theme.

Hashes, exact dimensions and the source hashes of the owning files are in
`artifact-manifest.json`. Regenerate with:

```powershell
cd apps/desktop
$dart = "C:/Users/13647/develope/flutter/bin/cache/dart-sdk/bin/dart.exe"
$tools = "C:/Users/13647/develope/flutter/bin/cache/flutter_tools.snapshot"
& $dart $tools test --no-pub test/tastecode_wired_test.dart
```

PNG bytes vary slightly between runs of identical source (scrollbar/caret/hover
noise; two runs differed in 10/29 files by at most 0.55%, with identical
dimensions and states). Treat the hashes as identifying this delivered run, not
as a reproducibility check; compare renders visually.

These are fixture renders: user visual acceptance is pending, and they do not
prove connected-service, native-runtime, installer or hardware behavior.
