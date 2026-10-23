# Desktop backbone handoff

Target branch: `feat/flutter-desktop-preview`. Create teammate feature branches
from its remote tip; target PRs back here, not develop, while v0.5.0 is isolated.

## Start here

```powershell
cd apps/desktop
flutter pub get
flutter run -d windows
# Development-only state gallery (never a live fallback):
flutter run -d windows -t lib/gallery.dart
```

Use `linux`/`macos` on those platforms after installing their native toolchains.
Gallery exposes empty, loading, offline, error, busy, approval and clarification
fixtures. Device names there are illustrative. Live mode only uses actual APIs.

## Stable seams

| Concern | Foundation | Teammate extension |
|---|---|---|
| Transport | `packages/odessy_client` public exports | Consume contracts; request additions |
| Session/Stop/selection | `lib/app/session_controller.dart` | Render state and invoke commands |
| Connections/credentials | `lib/app/connections`, `CredentialStore` | Compose entry points, never store tokens in preferences |
| Settings | `features/settings/controllers` | Reuse typed fields/form controller; see settings-extension.md |
| Materials | `lib/widgets/glass_surface.dart`, `app/theme.dart` | Refine tokens and composition, preserve opaque fallback |
| Interaction cards | `features/conversation/presentation/interaction_card.dart` | Style backend-bound details; preserve controller guards |
| Runtime settings | `docs/ui/settings-inventory.json` | Build only when management contract exists |

Profile metadata is additive; existing fake clients implementing the legacy names
interface remain compatible. Snapshots are immutable and connection/session-bound.
Credential storage defaults to ephemeral memory. The interface permits a future
reviewed native vault; it does not claim one is implemented.

## Required workflow

1. Pick one screen and its inventory entries. Confirm the API capability first.
2. Reuse a controller. If absent, propose its typed contract with fake + HTTP tests.
3. Implement presentation states and keyboard/focus/accessible labels.
4. Run the checks below and attach screenshots in both themes.
5. Request owner review for protected paths. Do not disable tests to change a seam.

```powershell
# Repository root:
python tool/check_desktop_contracts.py --check
python -m unittest discover -s tests -p test_desktop_settings_inventory.py -v
cd packages/odessy_client
dart pub get
dart analyze
dart test
cd ../../apps/desktop
flutter analyze
flutter test
```

## Explicit later work

This handoff does not make unavailable backend features available. Full runtime
settings read/validate/save with revision preconditions, service-instance identity,
host management, desktop voice, native persistent credential vaults, signed
packaging and platform/hardware qualification remain separate owner work. Settings
CAS cannot be simulated by a client-side timestamp. Existing configuration and
CLI workflows remain authoritative until their management adapters land.

GitHub required-review/check configuration must be enabled by the repository
owner; AGENTS.md and CI files alone do not enforce remote branch protection.
