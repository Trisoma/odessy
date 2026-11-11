# Odessy installation engine

This package is the **shared Python installation engine** used by Odessy's shipping
Windows installer. It is not a user-facing installer or window on its own.

The shipping installer is the Flutter desktop setup application, packaged as one native
setup executable:

```text
tool/build_setup_bundle.py      # offline payload: private Python, Flutter release,
                                #   application wheelhouse, Node/tools, Harness runtime
tool/audit_setup_artifact.py    # read-only inventory, digest and license audit
tool/package_native_setup.py    # native wrapper (delegates to the NSIS builder)
tool/windows_nsis.py            # one-layer NSIS bootstrap
        -> desktop/odessy_desktop.exe --setup
```

The setup window (dark teal/green theme, six steps) and its resume/stop behaviour live in
`apps/desktop/lib/features/setup/`. It calls this engine through one bounded JSON request
per process:

- `odessy.setup.bridge` operation `installer` → `installer/flutter_bridge.py`, which runs the
  role-aware component workflow (`installer/role_aware.py`, `installer/workflow.py`).
- `odessy.setup.bridge` operations `manage`, `package-*`, `installed-spec` →
  `src/odessy/setup/`, which owns the versioned installation layout
  (`<root>/versions/<id>/…`, `installation.json`, `operation.json`).
- Maintenance, repair, rollback and uninstall of **existing installations** route through
  `odessy.setup.lifecycle`, which reuses `installer/operations/` for the legacy component
  format (`.installer/state.json`).

There is no Tkinter installer window, launcher panel, maintenance window or uninstall
window in this package, and no PyInstaller build path. The former
`installer/wizard.py`, `installer/launcher.py`, `installer/maintenance_ui.py`,
`installer/uninstall_ui.py`, `installer/ui/`, `installer/start.py`,
`installer/exe_entry.py`, `installer/entrypoints.py`, `installer/build/build_exe.py`,
`installer/build/bootstrap.py` and `installer/windows.manifest` were removed once the
Flutter path replaced them. `tool/audit_setup_artifact.py` refuses a payload that still
carries them, so release packaging cannot silently select the removed wizard.

## What this package installs

The legacy component format (assembled by `installer/role_aware.py`) lays out:

| Path | Contents |
| --- | --- |
| `runtime/` | Private Python and Node.js |
| `runtime/harness-runtime/` | Pinned Harness runtime executable, sidecars and licences |
| `dependencies/` | Virtual environment created at its final path, plus pinned Python dependencies |
| `application/` | Application files and the prebuilt tool service |
| `ollama-runtime/` | Private Ollama CLI and libraries |
| `models/` | Pinned local models |
| `config/odessy.env` | Connection configuration for this installation |
| `launcher/` | Standalone launcher and shortcut records (legacy installations only) |
| `ollama-state/` | Private service state and diagnostic logs |
| `.installer/` | Install checkpoints, maintenance records and rollback copies |

Do not move an installed Python or virtual environment; Windows virtual environments are
not relocatable.

The versioned format (`src/odessy/setup/`) instead copies each payload component into
`<root>/versions/<id>/` (`python` virtualenv, `desktop`, `node`, `tools`, `harness`) and
records every owned file digest in `installation.json`.

## Download, verification and recovery

- Resource URLs, versions, sizes and SHA-256 digests are pinned in the `releases/`
  manifests. A matching digest shows the resource matches the manifest; it is not the
  publisher's digital signature.
- The v2 record for Python dependency resources binds only `requires-python` and the base
  `dependencies`. Changing test, cloud, UI or voice optional dependencies, comments or
  formatting does not invalidate the local dependency lock. A change to base dependencies
  or the Python requirement does require regenerating the resources; download manifests
  and wheel digests are still verified strictly.
- Downloads land in the cache first and are used only after verification; an interrupted or
  failed download can be re-checked and retried.
- Before installing, the engine adds up downloads, extraction, temporary files and rollback
  space against the actual disk, avoiding double-counting on the same volume.
- Install checkpoints are written; resuming re-verifies every completed component rather
  than trusting a completion marker.
- The complete install record in its original location is what launch, maintenance and
  uninstall rely on. The engine never takes over an arbitrary third-party directory.

## Repair and upgrade (existing installations)

Open Odessy's **Runtime maintenance** page, select the exact installation root and review
the operation before confirming. The page reports the installation format (legacy
`.installer/state.json` or versioned `installation.json`) and routes to that format's
original engine; both records present is rejected as ambiguous.

Legacy repair needs a verified prebuilt tool-server bundle, not a native wheelhouse.
Original pinned-model installations additionally require a compatible standalone launcher
EXE for their launcher entry; their live inference verification and any owned-model repair
download require separate explicit opt-ins, defaulting off. Role-aware core installations
repair only their core components, without downloading a model or running inference.
External model stores, credentials, settings and unrelated files are preserved.

A custom or missing connection configuration is never overwritten with defaults;
maintenance stops and asks for a decision. Shortcuts are read and rewritten through the
Windows Unicode API, so paths outside the system ANSI code page work.

## Cleaning maintenance backups

Review the backup usage first, then confirm the cleanup separately. Cleaning permanently
gives up that maintenance run's rollback ability. Backups are not deleted by default.

Only `backup-*` copies tied to the current maintenance record and whose manifests are
unchanged are cleaned; the application, models, configuration, working folders, shared
cache and other maintenance files are preserved. A new or changed file stops the cleanup.
Older maintenance directories no longer tied to the current record are not cleaned
automatically.

## Uninstall (existing installations)

Use Odessy's **Runtime maintenance** page and confirm the uninstall plan. The engine
deletes only what it has checked, one item at a time; an unlisted file or a permission
problem stops it and leaves the remainder in place. Models, user configuration and the
shared download cache are kept by default; deleting models or configuration is a separate
choice. The user's working folders are outside the uninstall scope.

## Directory layout

| Path | Purpose |
| --- | --- |
| `installer/*.py` | Engine entry points: `role_aware`, `workflow`, `flutter_bridge` and the `_compat` forwards. |
| `installer/core/` | Shared fundamentals: downloads, manifests, paths, disk space, extraction, checkpoints, process environment and preflight. |
| `installer/components/` | Install components: Python/Node, dependencies, application, Harness runtime, models, Ollama, connection verification and the tool bundle. |
| `installer/operations/` | Post-install maintenance: repair, upgrade, backup cleanup and uninstall. |
| `installer/windows/` | Windows integration: privileges, process trees, shell links and legacy launcher-entry records. |
| `installer/build/` | Developer build helpers: wheel preparation, the packaged installer context and voice manifest pinning. |
| `installer/_compat/` | Compatibility forwarding for historical module names; no business logic. |
| `installer/assets/` | Build and packaging assets; `odessy-boat.ico` is the NSIS installer icon and `application_icon.png` is the Linux desktop icon source. |
| `installer/releases/` | Pinned download manifests, verification resources and build dependencies. |
| `installer/templates/` | Launch templates written into the target directory after installation. |
| `installer/examples/` | Example data (third-party licence excerpts). |
| `tests/test_installer*.py` | Engine regression tests (components, maintenance, uninstall, packaging). |

## Source development and build

Run these from the repository root. Use fresh, separate output directories.

```powershell
# Engine preview/status (no writes without --run)
python -m installer.workflow --install-dir D:\Apps\Odessy --tool-bundle D:\OdessyBuild\tool-bundle
python -m installer.workflow --install-dir D:\Apps\Odessy --tool-bundle D:\OdessyBuild\tool-bundle --run
python -m installer.workflow --install-dir D:\Apps\Odessy --status
python -m installer.workflow --install-dir D:\Apps\Odessy --resume
```

`installer.component_repair`, `installer.backup_cleanup` and `installer.uninstall` provide
command-line entry points for maintenance, backup cleanup and uninstall. Use `--help` for
their arguments; operations that delete files must be previewed first and then confirmed by
supplying that plan's `plan_sha256`.

Run the installer tests:

```powershell
python -m unittest discover -s tests -p 'test_installer*.py' -q
```

When committing, keep the installer, its tests, the required resource manifests and the
documentation; do not commit the EXE, dependency packages, models, temporary install
directories, caches, test output or build logs.

## Current scope

The engine covers Windows x64 install and maintenance, on-machine deployment of the
Harness runtime and a pinned local model. Speech components are an explicit opt-in
download (see `docs/setup/voice-components.md`).

Runtime checks do not guarantee that arbitrary unprobed file damage is found, nor model
performance on every machine. Acceptance on a clean Windows machine and a formal release
are still outstanding, so `installation_ready` stays `false`.
