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
a sanitized application context; no checkout is required. The setup window is the
Flutter desktop application, not the retired Tkinter wizard. Local credentials
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

## Packaged build inputs (Windows)

The shipping setup is the Flutter desktop application: the bootstrap payload ships
`desktop/` and the NSIS bootstrap runs `desktop/odessy_desktop.exe --setup`, which calls this
bridge. There is no Tkinter window and no PyInstaller build; `tool/audit_setup_artifact.py`
refuses a payload that still carries the removed wizard.

The payload's portable runtime and the wheelhouse must share one CPython ABI. The pinned
Windows runtime is CPython 3.12, so the wheelhouse must hold cp312 (or `abi3`/`py3`) wheels.
The repository `.venv` and the system `Python311` install are 3.11 and cannot install the
cp312 wheels; produce cp312 wheels, or the installed `pip install` fails.

```text
python -m pip wheel . --no-build-isolation --no-deps -w <wheelhouse>
python -m installer.components.harness --prepare          # once per machine, downloads and pins
python tool/build_setup_bundle.py --wheelhouse <wheelhouse> --licenses <licenses.json> ^
    --python-runtime <python-3.12-prefix> --desktop-bundle <flutter-release-dir> ^
    --node-runtime <node-dir> --toolserver <tool-server-dir> ^
    --harness-runtime <staged-harness-dir> --output <payload.zip>
python tool/audit_setup_artifact.py <payload.zip>
python tool/package_native_setup.py --payload <payload.zip> --platform windows --output <OdessySetup.exe>
```

`--harness-runtime` must be the directory `installer.components.harness --prepare` produced:
the builder re-verifies every declared file and ships the executable, its sidecars, the
retained licences and the completion marker. The payload declares `harness` as an optional
component, so an installation without it keeps working and a Harness session refuses with
its own actionable error. The `tools` component always carries
`src/odessy/execution/contract.json`: the reviewed Node tool server resolves that file by
walking up from its module, and a versioned installation keeps the application source in its
private virtualenv, outside that search path.

The license map may be empty; the wheel's own METADATA is the authority. A map entry that
contradicts the wheel (for example a non-MIT value for an MIT wheel) refuses the build, and
an `UNREVIEWED` placeholder is replaced by the metadata-derived license. The reviewed set for
a release is recorded in `installer/releases/windows-x64-py312/wheel-licenses.json`, which
`tool/audit_setup_artifact.py` reports wheel by wheel together with the metadata field each
license came from. Roles that also want the Node tool server or odessy-link report those as
absent components rather than failing.
