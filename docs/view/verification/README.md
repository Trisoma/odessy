# UI gallery capture

Production Flutter widget renderer, authenticated real local Python service and deterministic provider output. The production UI source is unchanged by this gallery update. Only its existing capture test was extended to visit all settings and navigation pages.

From `apps/desktop`, select an existing compatible Python and installed dependency site directory:

```powershell
$env:ODESSY_PREFLIGHT_PYTHON = '<existing Windows Python executable>'
$env:ODESSY_PREFLIGHT_SITE = '<existing compatible site-packages directory>'
$env:ODESSY_07_CAPTURE_DIR = '<temporary capture output directory>'
flutter test --no-pub --reporter expanded test/agent_basics_connected_preflight_test.dart
```

`ODESSY_07_CAPTURE_DIR` is the existing internal capture variable, not a product version. The public gallery is organized by screen. No dependency installation is part of this workflow. Capture output is staged first, then selected images are named by page and verified by SHA256 before replacement.

Latest connected capture: 1 test passed,42 seconds; `apps/desktop/build/developer-ui-gallery-capture.log`. Earlier full desktop:591 passed,13 skipped; analysis clean. Source, build and installed qualification evidence is preserved separately in manifest.json; a new screenshot does not constitute a new package qualification.

Current set:30 images.29 connected production renders plus one independently captured native Windows settings window. All eight settings groups are represented. The native capture is reused only because its production source hashes still match. Source/artifact identities and fixture hashes are explicit in manifest.json.
