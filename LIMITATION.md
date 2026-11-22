# Known limitations

**English** | [简体中文](docs/zh-CN/LIMITATION.md) | [Español](docs/es/LIMITATION.md)

**Developer Preview · v0.5.0-alpha**

The [current support matrix](docs/current-status.md) is the maintained capability
and release baseline. This page summarizes constraints that affect adoption.

- **Installer:** the [public alpha](https://github.com/Trisoma/odessy/releases/tag/v0.5.0-alpha) was built before the security fixes in `08efca9`. Use [source](docs/getting-started.md) for those fixes. See [release status](docs/current-status.md) for the build and its checks.
- **Checkpoint safety:** restoration is disabled on Windows at the service and
  write boundary until directory-handle race protection is implemented. Creation,
  listing and preview remain available. Direct restore requests return unsupported;
  an older running service must be updated to gain this restriction. Text-only
  snapshots are not a full backup and cannot undo commands or external effects.
- **Engine differences:** Custom supports bounded restart continuation. Harness
  history remains readable but cannot resume after restart through this adapter.
  Custom access modes and context injection are not silently emulated on Harness.
- **Authority:** full control never grants new workspace/remote rights or defeats
  hard denials. SSH login starts read-only monitoring. Custom can run individually approved commands after explicit SSH execution selection. Pairing, monitoring and execution
  authorization remain independent.
- **Interactive shell:** an explicitly approved local shell has user-level OS
  authority; it is not intercepted or sandboxed command by command. Native helper
  availability is platform/package dependent.
- **Isolation:** user-space path checks are not complete kernel confinement.
  Windows, Linux and Jetson isolation differ. Do not expose legacy unauthenticated
  plaintext LAN execution to untrusted networks.
- **Privacy:** cloud configuration sends request data and permitted observations
  to the selected provider. Redaction is best-effort. SQLite history and memory
  are local profile data, not an encrypted credential vault.
- **Hardware and voice:** no general minimum hardware specification or universal
  audio/provider compatibility is established. Models/assets are optional and
  subject to their own licenses. macOS and general MCU control are unqualified.
- **Coordination:** existing Jobs/Handoffs remain reachable, but supported behavior
  depends on the engine; deferred Harness delegation parity is not implemented
  merely because consultation or a page works.

See [detailed security boundaries](docs/reference/current-limitations.mdx),
[terminal/checkpoint semantics](docs/ui/terminal-checkpoints.md),
[history](docs/ui/conversation-history.md) and [historical evidence](docs/README.md).
