# Preview verification · October 22, 2026

Branch: `feat/flutter-desktop-preview`, base `fd7e140`. No merge, push or commit.
Main `develop` checkout and its pre-existing release-document edit preserved.

Environment: Windows, Flutter **3.47.7**, Dart **3.13.5**, Visual Studio 2022.
The official Flutter SDK was installed in the outer workspace's `.tooling/flutter`;
it is not vendored into this repository.

| Check actually run | Result |
|---|---|
| `dart analyze` in client package | No issues |
| `dart test`, broker fixture + live contract enabled | **7 passed** |
| `dart test test/live_api_test.dart` against actual `odessy serve --fake-model` | **1 passed** |
| `flutter analyze --no-pub` | No issues |
| `flutter test --no-pub --reporter expanded` | **9 passed** |
| Existing `pytest tests/test_application_api.py -q` | **13 passed** |
| `flutter build windows --debug` | Built native executable |
| `flutter build windows --release --no-pub` | Built optimized native executable |
| `git diff --check` | Clean |

Rendered and visually inspected dark conversation and light settings screenshots;
light conversation and device screenshots are also included in `screenshots/`.
Widget tests cover send/Stop, deliberate denial, model pinning, foreground admission,
stale response refusal, connection replacement, failed Stop, settings connection
races, reconnect without resubmission, and presentation architecture boundaries.

One independent read-only review found a cross-connection settings race and false
Stop success. Both were fixed and covered by regressions. The Stop regression
failed before its correction. A separate real integration run exposed async SSE
cancellation hanging; its focused regression failed before the stream-socket
ownership fix, and the full client suite now passes. An approval widget test
initially tapped outside the visible card; it now scrolls the actual button into
view at the intended desktop size and passes without hit-test warnings.

Ruling: this is the runnable preview milestone, not the full future UI. Existing
API capabilities are live; missing management/voice contracts are unavailable.
No agentic_flutter runtime or provider SDK was imported. No reference source was
copied; bundled Roboto fonts retain their license.

Not qualified here: macOS/Linux native builds, real microphone/Jetson, signed
packaging, OS force-termination cleanup, comprehensive runtime settings, persistent
credential storage, or the later management APIs. macOS client-network entitlement
is included, but its native runner still requires platform testing. Settings CAS
and service-instance identity require future backend contracts.

The preview executable is
`apps/desktop/build/windows/x64/runner/Release/odessy_desktop.exe`.
Keep its adjacent DLL and data directory together. Starts in explicit demo mode.
