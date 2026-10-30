# Odessy desktop

The desktop application is a Flutter client for the authenticated Odessy service.
Its presentation layer uses typed controllers; execution, authorization and device
identity remain in the existing Python, Node and Rust components. User-facing UI is
in English. See [handoff](handoff.md), [architecture](architecture.md),
[design](design.md) and [API contract](api-contract.md) for contribution boundaries.

## Windows local development

Run `apps/desktop/build/windows/x64/runner/Release/odessy_desktop.exe` with its
adjacent DLLs and `data` directory. The local desktop build owns an authenticated
loopback service and a read-only monitor, and stops them on exit. It is specific
to this checkout and depends on its local Python environment and native binaries;
it is not a standalone installer.

No model is downloaded or selected automatically. In **Settings → Models and
connections**, add a model profile, start a new session, and select the profile in
**Conversation**. Real inference requires an available model service and any
necessary credentials. Device-specific remote management, native microphone
capture and audio device selection are not yet available.

Application configuration and trust are stored under `build/local-desktop/state`;
its workspace is `build/local-desktop/workspace`. These are persistent user data.
After source changes, run `powershell -NoProfile -File tool/build_local_desktop.ps1`
from the repository root. The script requires the project-local Flutter SDK, Python
environment, and monitoring and link binaries.

## Validation

From `apps/desktop`, run `flutter analyze --no-pub` and `flutter test --no-pub`.
From the repository root, run `python tool/check_desktop_contracts.py`. Tests that
use fixtures identify them explicitly; live failures must not fall back to mock data.
Changes to shared foundation and native contracts require project-owner review as
described in [AGENTS.md](../../apps/desktop/AGENTS.md).
