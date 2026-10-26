# Odessy Linux x64 development preview

Experimental build for Ubuntu 24.04 x86_64. This is NOT an ARM64/Jetson build.
GTK 3 and a working graphical Linux session are required. No installer, compiler,
system Python or npm is needed. Do not run as root.

This development artifact is not approved for public/production release:
production Node dependency audit findings (including one critical) and complete
transitive license review remain open. Use a disposable workspace for evaluation.

## Start

```bash
sha256sum -c odessy-0.5.0.dev0-linux-x86_64.tar.gz.sha256
tar -xzf odessy-0.5.0.dev0-linux-x86_64.tar.gz
cd odessy-0.5.0.dev0-linux-x86_64
./Odessy --verify
./Odessy --diagnose
./Odessy
```

The first screen permits configuration of an existing local model or a supported
remote endpoint. Configure later opens an unconfigured interface, not a fake
assistant. No model is downloaded and no inference occurs until explicitly used.

Settings are stored outside this folder under
`${XDG_STATE_HOME:-$HOME/.local/state}/odessy/portable/`.
Workspaces, model files, credentials and pairing trust remain separate. Move the
whole extracted folder to relocate it; close Odessy before removing its binaries.

The package contains the real Node runtime and precompiled tool server without
npm/npx. Existing Python policy and host-specific sandbox limitations still apply.
Monitoring is opt-in and requires its documented configuration and trust grants.
Voice dependencies/models are not included by default.

## Verification boundary

Built using Flutter 3.47.8 on Ubuntu 24.04 x64 WSL2. Checksum, manifest,
relocated runtime imports and a real Node read using FakeModel passed. The desktop
process remained alive in a short startup smoke; WSL graphics emitted warnings.
This does not establish visual acceptance, real-model quality, voice, ARM64 or
compatibility with other Linux distributions. A clean-machine test remains needed.

Inspect `notices/inventory.json`, `LICENSE` and `THIRD-PARTY-NOTICES.md` for the
included development license inventory and unresolved review items.
