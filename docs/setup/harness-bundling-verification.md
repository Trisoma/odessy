# Harness bundling and the shipping installer — verification

The shipping Windows installer is the Flutter setup application packaged by NSIS:

```text
tool/build_setup_bundle.py -> tool/audit_setup_artifact.py
    -> tool/package_native_setup.py -> tool/windows_nsis.py
    -> desktop/odessy_desktop.exe --setup --payload <staged payload>
```

The retired Tkinter wizard and its PyInstaller build were removed; the setup UI is
`apps/desktop/lib/features/setup/` and the shared Python engine is `installer/` plus
`src/odessy/setup/`.

## Source snapshot

The reviewed source is now the committed tree on `feat/harness-app-integration`, not a
patch against the base commit:

| Commit | Contents |
|---|---|
| `1497d2b` | pinned Harness runtime bundled in the Windows installer (base) |
| `c852a95` | accepted prompt-11 participant-consultation slice |
| `f28d040` | prompt-12 explicit per-session runtime selection (application, client, desktop) |
| this change | installer cleanup and guards, versioned Harness wiring, tool contract, NSIS fixes, cancellation-safe maintenance registration, licence inventory |

The earlier qualification built the payload from `1497d2b` plus
`.harness-spike/patches/03-shipping-installer.patch`
(sha256 `a516561620179c3079aad89936bddd39f3ef1fffe73b45467af365427a221e60`; 1198 files,
tree sha256 `1aa8af3ef6da5a18287c5b23f9e87d957d2445086a31f9ba791202e6bd383f9e`).
Those artifacts predate the accepted prompt-11/12 commits and the registration/licence
work above, so **a release must be rebuilt from this tree** before distribution. That
rebuild and the click-through of the green setup window were not repeated in this pass at
the owner's request; the artifact identities below are the earlier, superseded set.

## Packaged identities (earlier snapshot; superseded by the tree above)

- CPython 3.12.13, Node v22.23.0.
- Harness runtime `deepseek-harness-sdk-runtime-win-x64.exe`, 273 157 120 B,
  sha256 `f9ed01f241df37c50c702d81b6c226d576210b8f3947699735ca90285ea13365`.
- Packaged plugin `@odessy/dsh-stream-bridge` 0.0.3 — the smallest verified shipping
  plugin (live text, questions, tool roster). The 0.0.4 control-channel work is preserved
  outside the tree in `.harness-spike/wip-control-v2/` and is not part of any release
  artifact; `agents.start` remains unavailable and unclaimed.
- Flutter release `odessy_desktop.exe`, 167 936 B, sha256
  `cc715311cf134885206d4bf32b12af063f2af6b1c19098b5aa45f90f321d594c`.
- Wheel `odessy-0.5.0.dev0-py3-none-any.whl`, 1 732 691 B, sha256
  `692e33f1472325095a6a04a057c54b9da29917a5e5ea0a38e7e93f7a2daa5b14`.
- Payload `odessy-setup-windows-x64-r3.zip`, 180 033 836 B, sha256
  `95184fda0f96ff428347f15a5f7e184f951d22ebe68026989e7387b7876a86ca`, components
  `python, desktop, node, tools, harness`.
- NSIS setup `OdessySetup-r3.exe`, 125 063 496 B, sha256
  `cc66dff50699976a9797bf4e7464dd215fdf4f40e5adf9aa88c23f5ceddc1c13`.
- Its `wheelhouse/manifest.json` still carries the old `UNREVIEWED-NO-METADATA` strings for
  `backports.tarfile` and `jaraco.classes`; the corrected licence derivation and the
  build-time guard are in this tree, so the rebuilt payload is what fixes the artifact.

## Layout the engine now produces

```text
<payload>/
  runtime/                      private CPython 3.12
  desktop/odessy_desktop.exe    the Flutter setup
  node/, tools/, harness/       packaged components
  bootstrap/, wheelhouse/       setup helper and pinned wheels

<root>/versions/<id>/
  Scripts/, Lib/                private virtualenv with the Odessy wheel
  desktop/ node/ tools/ harness/ copied from the payload and digest-recorded
<root>/installation.json        owned versions, digests, configuration
```

At startup `odessy.setup.installed` exports `ODESSY_HARNESS_RUNTIME_DIR`,
`ODESSY_HARNESS_NODE` and `ODESSY_NODE_TOOLS_DIR` from the active version, so the launch
resolves the staged runtime, its Node and the reviewed tool server without any checkout
path, PATH search or package manager. The tool component carries the shared
`src/odessy/execution/contract.json` that the Node server resolves by walking up.

## Maintenance registration

`package-register` used to copy the full 1.1 GB payload into
`%LOCALAPPDATA%\OdessyMaintenance\<installation>\<manifest-key>` with no progress, no
cancellation and no resumability: an interrupted copy left a torn file that a retry then
rejected as "changed".

It now uses the existing journal/atomic-write mechanisms:

- `registration.json` beside `operation.json` records bounded counters
  (`status`, `stage`, `files_done`/`files_total`, `bytes_done`/`bytes_total`,
  `reused`, `replaced`), throttled by the shared `Progress` writer; `package-status`
  reports them as `registration_progress` so the setup UI can show a determinate bar.
- Each member is written to a temporary sibling and renamed only after its digest matches,
  so an interrupted run cannot leave a torn cache entry.
- Every member is verified before it is trusted: a completed cache is reused without
  rewriting (`reused`), and a damaged or missing entry is replaced (`replaced`), so a retry
  resumes instead of failing.
- The desktop setup already passes a cancel marker for `manage` requests; `package-register`
  now honours it, marks the journal `cancelled`, and leaves the previous registration and
  the ownership record untouched. A registry failure is recorded as `interrupted`.
- Assets are not pruned to make the copy cheaper: the cache must be able to repair and
  uninstall later, so the whole reviewed payload is retained.

The Flutter management window polls the counters through a second read-only helper and
offers Cancel while the copy runs.

## Qualification from the earlier snapshot

Automated on this host (Windows 11 x64), from the patched snapshot only:

- A fresh export plus the patch reproduced the reviewed tree byte-for-byte (1198/1198 files).
- Source tests: **226 passed, 16 subtests passed** (setup bundle/NSIS/packaged components/
  installed/versioned maintenance, installer registration/repair/maintenance/backup/
  uninstall/shortcuts/Flutter bridge/role-aware/Harness, Harness installed/resource/runtime),
  run from the patched export with no checkout `PYTHONPATH`.
- Payload audit: clean inventory and checksums; components `python, desktop, node, tools,
  harness`; setup entry `desktop/odessy_desktop.exe`; no removed installer path.
- Install through `odessy.setup.bridge` (the desktop setup's own request protocol):
  `package-review` → `package-install` into a path with spaces and non-ASCII → `package-verify`
  `valid: true`. The pinned Harness executable digest, `harness/**` ownership, the tool
  server, its execution contract and the packaged plugin are present in the installed
  version.
- Startup: `python -I -m odessy.setup.installed` answered `GET /v1/capabilities` with 200 and
  exited 0 on stdin close.
- Installed interactions: **26/26** — Harness engine selection, streaming deltas and settled
  result, approved/denied external tool, approved/denied bundled Node tool, clarification,
  Stop, refresh, restore with growing history, two distinct sessions, no orphaned runtime.
- Repair re-installed into a new owned version (previous version retained) and verified
  `valid: true`. Uninstall ran from the external payload helper, removed the owned versions
  and copied runtimes, kept the configuration and workspace, and left no active version.
- `OdessySetup-r3.exe` executed: staged 7770 files and launched
  `desktop/odessy_desktop.exe --setup --payload "<staging>"`, exit 0.

## Checks run in this pass

Focused, on the committed tree (no installation was rerun):

| Check | Result |
|---|---|
| `tests/test_windows_installation.py` — bounded progress, cancel, resume, torn-cache repair, ownership not recorded on cancel/interruption | 23 passed |
| `tests/test_setup_bundle.py` — Flutter/NSIS guards, Harness payload, licence derivation, contradiction refusal, inventory coverage | 15 passed |
| `apps/desktop` `flutter analyze --no-pub` | no issues |
| `apps/desktop` `flutter test test/manage_registration_progress_test.dart` | 2 passed |
| `tool/audit_setup_artifact.py` on the r3 payload (read-only) | `unknown_license_metadata: []` |

## Licences

Every wheel in the shipping set now resolves through
`installer/releases/windows-x64-py312/wheel-licenses.json` (name, version, SHA-256,
license, and the metadata field it came from). `backports.tarfile` 1.2.0 and
`jaraco.classes` 3.4.0 publish only the MIT OSI classifier; their wheels ship the MIT
permission text (`*.dist-info/LICENSE`, 1 023 B, sha256
`86da0f01aeae46348a3c3d465195dc1ceccde79f79e87769a64b8da04b2a4741`) and their PyPI
digests match the packaged files. No unknown metadata remains. Each wheel carries its own
notices inside the wheel, and the payload carries the CPython, Harness and repository
notices. Accepting the resulting licence set for distribution is the publisher's decision.

## Not completed / remaining

- A rebuild of wheel, payload and NSIS from this tree, and the click-through of the green
  setup window from that EXE (owner asked not to rerun the installation in this pass).
- A real `package-register` run against a fresh isolated installation (Start-menu entry,
  Installed-apps record, launch, repair, unregister): the code path is covered by the unit
  tests above, not yet by an installed run.
- Publisher signing/notarization, clean-machine acceptance, real provider credentials,
  microphone/voice and Linux/Jetson qualification.
