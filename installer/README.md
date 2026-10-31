# Odessy online installer

> **Decision (2026-10-22): work on the current installation wizard is paused and
> deferred.** This remains a development prototype: the feature scope is incomplete and
> the UI and icons are not final. It is **not** a finished install experience and not a
> release entry point, so `installation_ready` stays `false`. The implementation is kept
> for future iteration. Scope: [roadmap](../docs/releases/v0.5.0.md).

A graphical online installer for Windows x64. End users run a self-contained EXE and do
not need Python, Node.js or Git installed beforehand. The installer downloads and
verifies pinned resources, then prepares the runtime, application, local model and
connection configuration in a user-writable directory.

## Requirement: detect existing models, let the user choose, never force an LFM download

This installer is still an unfinished prototype. The workflow documented below describes
the **current** implementation, not a finished experience.

The planned version must first detect the user's configured Ollama/model services and
installed models, and let the user pick an existing model, enter a supported remote
endpoint by hand, or use cloud models only. **It must not download the whole LFM by
default, and must not install every model.** A model download must be an explicit choice
that first shows source, licence, size and space required.

Existing models, services and user configuration must never be taken over, deleted or
silently overwritten. An unreachable local service must not be treated as "not installed".

Auto's fast model, capable model and collaboration participants are configured
separately; detecting a model does not mean it is suitable for tool calls. Voice models
and dependencies are optional components. Tests must cover that "reuse an existing model"
and "cloud-only configuration" never download LFM, plus cancellation, offline and error
recovery. This is a requirement, not a change already made to installer behaviour. See the
[roadmap](../docs/releases/v0.5.0.md).

## Using the current prototype

The wizard is a dark-themed six-step flow: Welcome → Location → Review → Install →
Verify → Ready. Verify advances on real model and tool-verification events, and Ready
appears only once the workflow confirms completion. The bottom buttons respond to Tab,
Enter and Space; an install can be stopped while it runs, and error text stays on screen.

1. Double-click `OdessySetup.exe` and choose a new, separate install directory.
2. Click to check the installation plan; review paths, downloads and disk space, then confirm.
3. The installer prepares the runtime, dependencies, application, Ollama and model, then
   verifies with a real call.
4. It creates a launcher entry and desktop or Start-menu shortcuts.
5. Open the launch panel, choose the working folder you allow, and type requests into the
   dedicated terminal. `/exit` quits. A model service started by this launch stops with the
   session; services that already existed are left alone.

The default install root is `%LOCALAPPDATA%\Programs\Odessy\installation` and the shared
download cache is `%LOCALAPPDATA%\Odessy\downloads`. Another user-writable directory can
be chosen; no administrator rights are required. Choosing a working folder does not skip
the existing permission checks or operation confirmations.

On Windows, a model service the installer started and its child processes are managed by a
dedicated Job object, and shutdown waits for the whole process tree to end. When an existing
service is reused, no Job is created for it and none is terminated.

## Install directory

| Path | Contents |
| --- | --- |
| `runtime/` | Private Python and Node.js |
| `dependencies/` | Virtual environment created at its final path, plus pinned Python dependencies |
| `application/` | Application files and the prebuilt tool service |
| `ollama-runtime/` | Private Ollama CLI and libraries |
| `models/` | Pinned local models |
| `config/odessy.env` | Connection configuration for this installation |
| `launcher/` | Standalone launcher and shortcut records |
| `ollama-state/` | Private service state and diagnostic logs |
| `.installer/` | Install checkpoints, maintenance records and rollback copies |

Do not move an installed Python or virtual environment; Windows virtual environments are
not relocatable.

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
- Before installing, the installer adds up downloads, extraction, temporary files and
  rollback space against the actual disk, avoiding double-counting on the same volume.
- Install checkpoints are written; resuming re-verifies every completed component rather
  than trusting a completion marker.
- Each install-time connection verification uses its own
  `ollama-state/verification-sessions/<session-id>/` directory. Locks and logs left by a
  forced exit or reboot are kept and do not block a retry of the same installation; complete
  verified components are reused rather than reinstalled. Locks belonging to other sessions
  are never deleted and reused services are never stopped.
- After Stop is pressed, the current model call waits to return or time out before the
  installer cleans up safely, and no further model verification starts. A cancelled
  verification records no success and writes no new connection configuration. The diagnostic
  log reports where this run's service log is.
- A change to source, resources or key options can prevent an older install task from
  resuming; it then needs re-checking.
- The complete install record in its original location is what launcher, maintenance and
  uninstall rely on. The installer never takes over an arbitrary third-party directory.

## Repair and upgrade

Run the new EXE from outside the install directory, pick the existing complete installation
on the settings page, and open "Repair/upgrade this directory…". Close that installation's
launch panel, terminals and model service first, then check and confirm maintenance.

Maintenance inspects the private runtime, pinned dependencies, Ollama, pinned models, the
application and the launcher entry. It prepares only the base components that need repair;
healthy models are reused. Rebuilding Python/Node also rebuilds dependencies, and the
virtual environment is created at its final path. Dependency checks read the wheel RECORD
directly and check file existence, size and SHA-256 — enough to catch accidental damage, not
to resist a deliberately rewritten verification record.

The new application is assembled and verified first, and swapped in only after a real model
reply and a directory tool call succeed. The previous components are kept as rollback copies
and restored on failure. If maintenance is interrupted, use the new external maintenance
window's "Restore previous version" before checking again. If a backup changed, is missing,
or permissions are insufficient, maintenance stops and reports.

A custom or missing connection configuration is never overwritten with defaults; maintenance
stops and asks for a decision. Shortcuts can be re-created with "Create/complete shortcuts";
foreign or already-modified links with the same name are left as they are. Shortcuts are
created and read through the Windows Unicode API, so paths outside the system ANSI code page
work. When a transient file lock is hit, the install record retries an atomic replace a
bounded number of times; a persistent permission problem still fails and keeps the original
record.

## Cleaning maintenance backups

In the repair window click "Show backup usage", review it, then confirm "Clean backups"
separately. Cleaning permanently gives up that maintenance run's rollback ability. Backups
are not deleted by default.

Only `backup-*` copies tied to the current maintenance record and whose manifests are
unchanged are cleaned; the application, models, configuration, working folders, shared cache
and other maintenance files are preserved. Files listed in the manifest are deleted one at a
time, then empty directories are removed; a new or changed file stops the cleanup. After a
failure, re-check and confirm again — retrying must not delete new files. Older maintenance
directories no longer tied to the current record are not cleaned automatically. The sum of
file sizes does not guarantee the physical space freed. Cleaning does not raise account
privileges.

## Uninstall

Run the installer's uninstall UI from outside the install directory:

```powershell
.\OdessySetup.exe --mode uninstall --install-dir D:\Apps\Odessy
```

Check the deletion scope before confirming. Models, user configuration and the shared
download cache are kept by default; deleting models or configuration is a separate choice.
The user's working folders are outside the uninstall scope. Uninstall deletes only what it
has checked, one item at a time; an unlisted file or a permission problem stops it and leaves
the remainder in place. Files created by an earlier administrator that the current user may
not delete are not force-taken over.

## Directory layout

| Path | Purpose |
| --- | --- |
| `installer/*.py` | Stable entry points: wizard, launcher, terminal sessions, maintenance/uninstall windows, workflow and EXE distribution. |
| `installer/core/` | Shared fundamentals: downloads, manifests, paths, disk space, extraction, checkpoints, process environment and preflight. |
| `installer/components/` | Install components: Python/Node, dependencies, application, models, Ollama, connection verification and the tool bundle. |
| `installer/operations/` | Post-install maintenance: repair, upgrade, backup cleanup and uninstall. |
| `installer/windows/` | Windows integration: privileges, process trees, shell links and shortcuts. |
| `installer/build/` | Release build: EXE packaging, wheel preparation and embedded-resource bootstrap. |
| `installer/_compat/` | Compatibility forwarding for historical module names; no business logic. Existing commands and imports keep working. |
| `installer/ui/` | Presentation: `ui_theme.py` holds the design tokens and window assembly, `ui_controls.py` provides the custom-drawn controls, `brand.py` generates the pixel sailboat mark, and `wizard_controller.py` adapts between the UI and the workflow. |
| `installer/assets/` | Build and packaging assets; `odessy-boat.ico` (16/32/48/64) is the title-bar/taskbar icon for all four windows and the `--icon` for `OdessySetup.exe`. |
| `installer/releases/` | Pinned download manifests, verification resources and build dependencies. |
| `installer/templates/` | Launch templates written into the target directory after installation. |
| `installer/examples/` | Example data (third-party licence excerpts). |
| `tests/test_installer*.py` | Installer regression tests covering UI, privileges, shortcuts, uninstall and end-to-end flows. |

The icon is applied once, in `installer/ui/ui_theme.py`'s `configure()`, so the wizard,
maintenance, uninstall and launcher windows all get the same icon automatically. New
windows should reuse that method rather than calling `iconphoto` themselves.

New code uses the grouped module paths, such as `installer.components.runtime`,
`installer.core.checkpoint` and `installer.operations.maintenance`. Historical imports
resolve to the same module objects, preserving type and test-patch consistency, and the
existing commands such as `python -m installer.runtime` and `python -m installer.build_exe`
keep working. Resource manifests, templates and the EXE entry point are unchanged. This
reorganisation does not change the decision to leave the installer paused.

## Source development and build

Run these from the repository root. The development machine needs Windows x64 and a Python
with Tcl/Tk, using the project's existing dependencies and interfaces. Use fresh, separate
output directories; the example paths can be replaced.

Prepare the private build runtime and the prebuilt tool bundle:

```powershell
python -m installer.runtime --runtime-dir D:\OdessyBuild\runtime --prepare
python -m installer.toolbundle --runtime-dir D:\OdessyBuild\runtime --output-dir D:\OdessyBuild\tool-bundle --build
```

Start the source wizard, or build the standalone EXE:

```powershell
python -m installer.wizard --tool-bundle D:\OdessyBuild\tool-bundle
python -m installer.build_exe --tool-bundle D:\OdessyBuild\tool-bundle --output-dir D:\OdessyBuild\setup --build
```

The builder uses the build dependencies pinned in
`releases/windows-x64-bootstrap/build-requirements.txt` inside an isolated environment, and
outputs `OdessySetup.exe` plus `build-report.json`. End-user installation runs no npm or
TypeScript build.

The command-line workflow only previews by default; add `--run` to actually install:

```powershell
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

The implementation covers install and maintenance for Windows x64, on-machine deployment and
a pinned local model. Automatic voice-dependency preparation, model migration, cloud account
configuration, distributed deployment and a minimal UI for the core workflow are not
included. The launch panel still takes requests and completes confirmations through the
terminal.

Runtime checks do not guarantee that arbitrary unprobed file damage is found, nor model
performance on every machine. Acceptance on a clean Windows machine and a formal release are
still outstanding, so `installation_ready` stays `false`.
