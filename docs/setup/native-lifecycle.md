# Offline runtime and user-service lifecycle

These commands extend the configuration wizard with headless native lifecycle
operations. They do not silently download models, take over Ollama, or enroll/grant
remote peers. A Python 3.10+ installation with venv support is required. This is an
offline bootstrap archive, **not** a signed standalone EXE/DMG/DEB installer.

## Build an artifact on its target OS/architecture

Build an Odessy wheel, then collect its dependency wheels with `pip download
--only-binary=:all:` on the target platform. Optional UI/cloud/voice extras must be
included deliberately. Create a JSON map from every wheel filename to its license
expression or license-file reference; retain the complete wheel license contents.

```text
python tool/build_setup_bundle.py --wheelhouse wheelhouse --licenses licenses.json --output odessy-setup.zip
```

The native CI workflow builds and exercises Windows, Ubuntu and macOS archives.
A successful future CI run is required before advertising those artifacts as
qualified. Hashes detect bundle changes; authenticate the original manifest/archive
through a trusted release channel. Checksums alone are not publisher authentication.
No Python runtime, Node, Rust binaries, Ollama, model weights, finalized branding
or code signature is bundled by this builder. Execution components are reused.

## Install or upgrade

Extract the archive and run `python install.py` from that directory. In an existing
Odessy installation the equivalent prefix is `python -m odessy setup manage`.

```powershell
$plan = python install.py plan --root C:/OdessyRuntime --bundle ./wheelhouse --role execution | ConvertFrom-Json
python install.py install --root C:/OdessyRuntime --bundle ./wheelhouse --role execution --confirm $plan.plan_id
python install.py inspect --root C:/OdessyRuntime
python install.py verify --root C:/OdessyRuntime
```

Use a new dedicated directory. `--config selection.json` replaces `--role` for
existing-model/cloud/optional-voice selection. No configured model is required to
install the text core; coordinator service creation requires an explicit model.
The wheel inventory is checksum-checked, installed without an index or dependency
resolution, and checked with `pip check`, installed setup and runtime help probes.
Missing dependency/extra wheels cause failure rather than an online fallback.

A kernel lock excludes concurrent writers. Each attempt gets a new virtualenv that
never moves. The active pointer changes only after successful checks. Ctrl+C stops
future steps and terminates the owned installer process group. Completed effects
are not undone: failed/interrupted version directories remain for inspection.
`operation.json` records the last attempt; `inspect` shows the active version.
Do not delete partial directories blindly or assume a cancelled install never wrote.

## Services

`service-plan` shows the exact native definition and a `service_plan_id`:

```text
python install.py service-plan --root <root> --workspace <existing-workspace>
python install.py service-install --root <root> --workspace <same-workspace> --confirm <service_plan_id>
python install.py start --root <root> --confirm <installation_id>
python install.py status --root <root>
python install.py stop --root <root> --confirm <installation_id>
python install.py service-remove --root <root> --confirm <installation_id>
```

For execution nodes add explicit existing `--node-link`, `--node-server` and
`--listen` arguments to service-plan/install. Rust enrollment and node-local
workspace/capability grants remain independently required. Registration grants no
remote authority. For coordinator/combined roles the service starts the loopback
API with the configured model; UI extra dependencies must already be included.
Credentials remain separately supplied to the service user's environment.

Linux uses `systemctl --user`; macOS uses a user LaunchAgent; Windows uses a
least-privilege interactive logon task. Windows/Linux registration does not start
the process; macOS bootstrap starts it. These run in a logged-in user session and
do not promise pre-login boot operation. There is no sudo/admin/password escalation.
Native permission denial is an explicit error with ownership records retained for
inspection. An unavailable user manager is not reported as a running service.
Service definitions changed externally cannot be controlled or replaced by setup.

## Repair, rollback and uninstall

First remove the recorded service before changing runtime versions. This prevents
an existing service from silently continuing on a different version than the UI
reports. Review a fresh `plan` with the same selection and bundle; pass its ID to
`repair --root <root> --bundle <bundle> --confirm <plan_id>`. Repair builds a fresh
version and preserves modified old files. Inspect returns installation/version IDs.

```text
python install.py rollback-plan --root <root> --version <owned-version>
python install.py rollback --root <root> --version <owned-version> --confirm <plan_id>
python install.py uninstall-plan --root <root>
python install.py uninstall --root <root> --confirm <plan_id>
```

Rollback revalidates the target version's hashes and restores its selected role.
Uninstall removes only recorded files whose hashes still match; modified/unowned
files, model caches and settings remain. Ownership/journal records and empty or
partial directories remain inspectable. No recursive cleanup or external service
removal is performed. This is conservative maintenance, not automatic garbage
collection or in-place repair of user changes.

## Acceptance

`python tool/check_setup_installation.py --wheelhouse <verified-wheelhouse>` performs
an actual isolated install, repair, rollback, integrity check and uninstall. It
preserves an external fixture and unrelated settings. The script creates temporary
state and does not register a service or contact a model/provider. Native service
registration requires a separate opt-in check. Physical voice and cross-host
pairing/revocation remain real-device checks, not consequences of wheel installation.

## Unified maintenance review tokens

For current bootstrap/CLI and desktop clients, rollback and uninstall require a
fresh operation-specific preview, rather than reusing the installation ID:

```text
python install.py rollback-plan --root <root> --version <owned-version>
python install.py rollback --root <root> --version <same-version> --confirm <plan_id>
python install.py uninstall-plan --root <root>
python install.py uninstall --root <root> --confirm <plan_id>
```

The same commands are available under `python -m odessy setup manage`. Legacy
roots select their original engine; legacy rollback restores its recoverable
maintenance journal and does not accept a version selector. Inspect first and
review again after interruption or input/state changes. See
[format-aware maintenance](existing-installer-wiring.md).
