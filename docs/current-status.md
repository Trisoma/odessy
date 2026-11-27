# Current support and release status

**English** | [简体中文](zh-CN/current-status.md) | [Español](es/current-status.md)

**Developer Preview · v0.5.0-alpha**

## Source and release baseline

[v0.5.0-alpha](https://github.com/Trisoma/odessy/releases/tag/v0.5.0-alpha) is a public prerelease, published November 19, 2026. It includes a Windows x64 installer.

The installer was built from application source `6f96e71` and predates the security fixes in `08efca9`. The [verification record](verification/windows-piper-installer-2026-11-18.md) identifies the EXE by checksum and records the isolated upgrade and Piper synthesis checks for that file. [Run from source](getting-started.md) to use the later fixes.

## First contributor run

Follow [Getting started](getting-started.md), then check one approved local command in the [manual](GOLDEN_PATH.md). See [Contributing](../CONTRIBUTING.md) for component checks.

## Capability matrix

| Capability | Implemented scope | Important boundary |
| --- | --- | --- |
| Desktop | Production Flutter shell, projects/recent chats, inspector, Activity & usage, Jobs, Handoffs and eight settings groups | A visible page does not guarantee that its backend capability is available. |
| Durable history | Profile-local SQLite projects/conversations/events, search and bounded export | History browsing has no side effects; stored calls are not replayed. |
| Custom continuation | Restores completed public exchanges into a fresh authorized controller | Workspace binding must match; grants, approvals, jobs and process handles are not restored. |
| Harness | Selectable runtime and saved readable history | Restart continuation and Custom access-mode/context parity are unavailable; no silent fallback. |
| Access modes | Custom ask/auto/full with service acknowledgments and revisions | Existing policy, hard denials, sensitive paths and independent grants remain effective. |
| Context/memory/retrieval | Explicit project facts, opt-in bounded lexical indexing and Custom context/citations | Source validation and current live workspace authority remain required. No automatic cloud indexing. |
| Interactive terminal | Local Windows PTY with verified packaged Node/native helper and explicit grant | Runs with user OS permissions; no remote PTY, restored handles or replayed input. Missing closure means unavailable. |
| Checkpoints | Git-backed bounded text snapshots and preview; restoration uses exact approval and a safety snapshot, with handle-anchored replacement on Windows | Windows restoration is enabled when the NT handle bindings are available; a host without them returns unsupported. Binary/sensitive/unsupported files are excluded. |
| Monitoring | Optional Go aggregation with local/paired telemetry and read-only SSH status | Connectivity, permission and freshness remain separate. Monitoring DTOs grant no execution or workspace access. |
| SSH execution | Explicit temporary Custom target selection and exactly approved `ssh.run` commands | Separate from monitoring; no remote sandbox, PTY, file inspector or Harness SSH execution. Reconnect after service restart. |
| Voice | Piper recommended for CPU; Kokoro selectable; optional Whisper input and separate wake downloads | Installed Piper model load/synthesis verified; microphone, playback, echo and wake require device-specific acceptance. |
| Setup | Windows installer with a bundled runtime and optional Piper/Kokoro | Download the published alpha. See above for the installer’s source and checks. |

## Platform matrix

| Platform | Evidence and support level |
| --- | --- |
| Windows x64 | Primary developer-preview target; source/CI and recorded native/installed checks exist for specific snapshots. Final candidate qualification remains separate. |
| Linux x86_64 | Source/CI coverage; portable packaging and native GUI acceptance are not established for the final snapshot. |
| Jetson/Linux ARM64 | Direction-specific historical monitoring, pairing and voice evidence only. Not a blanket desktop or execution claim. |
| macOS | Unqualified. |

No general minimum RAM/GPU specification, complete sandbox, autonomous coordination
parity or MCU support is claimed. SSH command execution has the bounded Custom scope
above; it is not a general remote IDE or confinement guarantee.

## Gates before a beta claim

1. Verify the packaged candidate bundles the handle-anchored Windows restore and
   passes its junction/refuse and readable-after-replace checks. Resolve or disable
   any other safety/data-loss findings.
2. Build and audit one coherent candidate from the final source snapshot with
   pinned runtime/helper closure, licenses and artifact hashes.
3. Verify isolated install/update/uninstall and profile/history/settings preservation
   against that candidate; distinguish fixtures from real vault/provider evidence.
4. Match onboarding, supported workflows, screenshots and limitations to the candidate.
5. Record maintainer visual acceptance and remaining platform/hardware/provider gates.

Alpha does not waive safety requirements. Do not distribute an unsafe restore
path as production-safe. See [release verification](release-verification.md).
