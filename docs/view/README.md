# Final integrated Odessy UI

2026-11-14. Branch `feat/ui-wiring-integration`. These are actual Flutter-rendered production widgets, captured through `OdessyApp`, not generated mockups or the standalone reference demo. Wide images are 1600×1050; narrow images are 720×900. The approved sibling `view/ui/tastecode-demo` references are unchanged.

The production shell now uses the approved navy/mint hierarchy, bundled typography, boat, grouped sidebar, compact context composer and adjacent inspector. Original Activity & usage, Jobs, Handoffs, richer settings, pairing and maintenance routes remain reachable. Eight settings sections retain their original controllers and supported fields.

## Launch

From `apps/desktop`, with Flutter and existing packages available:

```powershell
flutter run -d windows --no-pub
```

This starts the normal application disconnected until a service is configured. To inspect an explicit offline production-shell preview:

```powershell
flutter build windows --debug --no-pub
& '.\build\windows\x64\runner\Debug\odessy_desktop.exe' --demo
```

The latter uses `DemoApi` and its visible demo notice. It does not promise the identical rich capture scenario below. Do not use `lib/tastecode_demo.dart` to evaluate the integrated production shell.

## Reproduce captures

From `apps/desktop`:

```powershell
flutter test --no-pub --dart-define=CAPTURE_WIRED=true test/tastecode_wired_test.dart test/monitoring_app_integration_test.dart --reporter expanded
```

Capture-only fixtures inject temporary/in-memory project, settings, session, voice and tool state. They never replace failed live responses in ordinary application startup. Commands, device names/readings, agent names, costs, approval previews and voice readiness in these images are offline fixture data. No shell command, microphone, paid provider or real host was used. The denied-device image uses a real Dart loopback HTTP fixture returning 403; its CONNECTED badge describes the session service, not telemetry permission or real hardware. Expected unavailable controls remain disabled.

## Screenshot index

| State | Screenshot |
| --- | --- |
| Conversation, sidebar and exact approval | [Assistant](01-assistant-approval.png) |
| Expanded actual activity widget and output link | [Activity](01b-activity-expanded.png) |
| Composer draft and expanded voice controls | [Composer](02-composer-voice.png) |
| Adjacent file tree/viewer | [Files](03-files.png) |
| Explicitly reviewed text attachment | [Attachment](03b-attachment.png) |
| Read-only recorded command output | [Output](04-output.png) |
| Exact pending file diff | [Diff](05-diff.png) |
| Retained usage dashboard | [Activity & usage](06-activity-usage.png) |
| Real project/session presentation | [Workspaces](07-workspaces.png) |
| Device overview | [Devices](08-devices.png) |
| Device performance | [Performance](09-device-performance.png) |
| Device details | [Details](10-device-details.png) |
| Monitoring denied, stale metrics cleared | [Denied](11-devices-permission-denied.png) |
| Retained jobs | [Jobs](jobs.png) |
| Two-step handoff and review | [Handoffs](handoffs.png) |
| General and durable appearance | [General](settings-1.png) |
| Models and connections | [Models](settings-2.png) |
| Speech to text | [STT](settings-3.png) |
| Text to speech | [TTS](settings-4.png) |
| Voice | [Voice](settings-5.png) |
| Devices and tool access | [Access](settings-6.png) |
| Advanced | [Advanced](settings-7.png) |
| Installation and runtime maintenance | [Installation](settings-8.png) |
| Light appearance | [Light](settings-light.png) |
| Narrow assistant and retained draft | [Assistant](narrow-assistant.png) |
| Narrow populated exact diff | [Diff](narrow-diff.png) |
| Narrow handoff | [Handoff](narrow-handoffs.png) |
| Narrow navigation, including usage/jobs/handoffs | [Navigation](narrow-navigation.png) |
| Narrow preferences selector | [Settings](narrow-settings.png) |

## Real wiring and limits

A separate test `production_service_integration_test.dart` starts the actual Python `create_app` and `SessionController` on authenticated loopback, then uses the real Dart `LocalApi`, production controllers and views. A deterministic provider replaces model/terminal execution. It verifies session create/list, visible turn results, bounded file reads, sensitive-file denial, recorded output projection, exact content-bound approval and a temporary-file change, Stop, workspace permission revocation and honest unavailable voice. It never runs a terminal command. Existing backend tests separately exercise Custom and Harness ownership/stop/approval boundaries; this full service-to-view fixture uses Custom only.

To run that test, install the repository's existing `[test,ui]` development extras using the supported environment, then set `ODESSY_UI_TEST_PYTHON` to that Python's absolute executable path and run:

```powershell
flutter test --no-pub test/production_service_integration_test.dart --reporter expanded
```

`ODESSY_UI_TEST_SITE` is an optional existing-site-packages path for this machine's cached interpreter workaround; ordinary environments should omit it. Without the Python selection only this explicit service fixture skips. The existing Desktop preview CI now selects Python and installs the same existing extras so it runs there; hosted CI has not been executed in this task.

Final verification: 569 desktop tests passed, 12 existing skips; the final two modified capture/service tests passed again. Client: 50 passed, 2 skipped. Backend focused suite: 148 passed. Desktop/client analysis clean; formatting and diff checks clean. 107 runtime settings validated; existing package snapshot has 225 owned files and excludes capture/test/reference trees. Windows debug build succeeded and its owned preview process launched.

Images were visually inspected, including all eight settings sections, denial, light mode and narrow layouts. Native computer-use inspection was blocked by `Computer Use native pipe is unavailable ... (os error 2)`. These PNGs therefore establish actual Flutter widget rendering, not native OS window/IME/accessibility qualification. Human design acceptance remains with the owner. No installer, signing, clean-machine, microphone, paid-provider or hardware qualification is claimed. The present branch still lacks an SSH implementation; fixture Jetson labels do not change that. Read-only monitoring never implies execution/workspace authorization. Output remains read-only, sessions process-scoped, and Harness diffs stay unavailable where its runtime supplies no proposal.

## Attribution and artifacts

No TasteCode source, icons, photographs or assets were copied in this pass. The visual reference inspired hierarchy and layout. Existing repository-owned boat, Flutter Material icons, bundled OdessySans/Roboto and OdessyMono/Roboto Mono remain in use with their existing notices; no font or runtime dependency was added. [Artifact manifest](artifact-manifest.json) records each PNG's dimensions, bytes/hash and the modified source hashes. [Integration report](integration-report.md) records closure and preservation evidence.
