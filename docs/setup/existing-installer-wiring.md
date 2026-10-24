# Flutter and existing installer lifecycle

The desktop **Install Odessy** page reviews Windows x64 core component installation,
checkpoint inspection and explicit resume through `installer/flutter_bridge.py`.
The **Runtime maintenance** page now routes repair, rollback and uninstall through
the installation's original engine, including `installer/operations`.

## Select and review the installation

Enter the exact installation root, then inspect it. The page reports its format:

- **Legacy:** `.installer/state.json`; original component checkpoints and maintenance/uninstall journals.
- **Versioned:** `installation.json`; owned versions and `operation.json`.
- Both records present: rejected as ambiguous, without choosing a backend.

Review the operation before confirming. Consent binds the operation, canonical root,
arguments, selected version and relevant state/journal/file inventory. Editing inputs
invalidates consent; stale results cannot authorize a changed request. State is
rechecked at dispatch and under the original engine's writer lock. No mutation is
retried automatically.

Legacy repair needs a verified prebuilt tool-server bundle, not a native wheelhouse.
Original pinned-model installations additionally require a compatible standalone
launcher EXE. Their live inference verification and any owned-model repair download
require separate explicit opt-ins, defaulting off. Role-aware core installations
repair only their core components, without downloading a model or running inference.
External model stores, credentials, settings and unrelated files are preserved.
Services must be removed through their owned service workflow before changing a
versioned runtime; legacy service operations are explicitly unsupported here.

## Cancellation and recovery

Cancel first signals an owned marker; after the grace period the desktop terminates
and drains its owned helper process tree. The existing checkpoint/journals record
completed effects. Closing and reopening never replays a mutation. Inspect the same
root, review a fresh operation and explicitly confirm resume, recovery rollback or
remaining uninstall. Changed/incomplete files are preserved; no recursive root cleanup
is added. A cancelled operation may already have completed some steps.

Installation resumes revalidate completed stages. Legacy repair reuses the original
staging/backups/activation/restore transaction. Rollback is limited to a recoverable
owned journal or intact owned version. Partial uninstall requires a fresh review.

## Packaged operation and platform boundaries

Wheels and standalone bundles include the installer modules, release resources and
a sanitized application context; no checkout or Tk UI is required. Local credentials
are excluded from the packaged default configuration. Select an existing model or
explicit cloud configuration; nothing forces LFM. Core installation is not hardware
readiness. To use stored selection, explicitly set `ODESSY_CONFIG_FILE` to
`<installation>/config/odessy.env` when launching.

The original component engine supports **Windows x64 only**. Versioned native
workflows retain their platform adapters; this bridge does not create Linux/macOS
component manifests. Signing, successful target-native service checks, final
redistribution/license acceptance and physical-device tests remain owner gates.

## Verification — October 24

- Affected Python installer/setup suites: **451 passed, 101 subtests passed**.
- Flutter suite: **45 passed**, including actual Python child-process legacy
  inspection/uninstall, retained external fixtures and close/reopen inspection.
- Flutter and pure Dart static analysis: no issues. Pure Dart client: **9 passed,
  2 opt-in tests skipped**. Protected contract: **89 settings validated**.
- Windows desktop release build succeeded.
- Built wheel installed outside checkout: actual legacy maintenance dispatch and
  retained-model uninstall passed; packaged resource resolution verified.
- Independent read-only review found no remaining confirmed blocker. Its final
  rollback-journal hardening suggestion was implemented with a failing-first regression.

Real Windows core installation, repair, rollback and uninstall passed using pinned Python/Node downloads,
locked dependencies and the built Node bundle. External model/unrelated-file fixtures were retained. No inference,
model download or service registration was performed. The qualification run exited successfully.
