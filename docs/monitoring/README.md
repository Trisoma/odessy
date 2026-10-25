# Multi-host monitoring — development integration

One central Go process owns scheduling, inventory, recent history and events.
Existing Rust `odessy-link` owns identity/mTLS and embedded collectors. Python
exposes authenticated `/v1/monitoring` REST/SSE. Monitoring is read-only and opt-in;
pairing and online status never grant execution or telemetry permissions.

## Build and enable

From the repository, build Rust with `cargo build --manifest-path
native/odessy-link/Cargo.toml`, and build the Go module in `services/monitoring`
with `go build -o <absolute-output>/odessy-monitor.exe ./cmd/odessy-monitor`.
Use the corresponding extensionless binary on Unix.

Set `ODESSY_MONITORING=on`, `ODESSY_MONITOR_BIN=<absolute Go executable>`,
`ODESSY_LINK_BIN=<absolute Rust executable>`, and optionally
`ODESSY_MONITOR_STATE_DIR=<private directory>`. Existing Rust identity must be
initialized. `ODESSY_MONITOR_LOCAL=on` explicitly enables local collection.
`ODESSY_MONITOR_HOSTS` selects comma-separated Rust-reported opaque `host_...` IDs;
default empty means no remote polling. `ODESSY_MONITOR_PRESET=low-power` reduces
sampling frequency. Restart the application after reviewed configuration changes.

For a remote host, enroll through the existing independently verified pairing
flow. Grant `peers grant <alias> --capability telemetry.read` independently on
each side, then run `serve --listen <address> --telemetry-only` on the monitored
host. This requires neither Node nor an execution/workspace grant. Existing serve
can opt in with `--telemetry`. Enrollment/re-enrollment never inherits telemetry.
Use `telemetry worker` inventory to inspect host references and generation.

Start `python -m odessy serve` normally. The existing application bearer token
protects all public monitoring routes. The Go service uses a separate random
token delivered through a private parent pipe; never configure that token in UI.

## What is implemented

- Rust telemetry-only grants, additive registry migration, negotiated mTLS channel,
  exact certificate matching, request/reply authority checks and worker protocol.
- CPU warmup, RAM/swap, disk capacity, per-interface network counters/rates.
  Unavailable disk I/O and unsupported sensors are explicitly unsupported.
- Optional fixed-path vendor helpers: set `ODESSY_TELEMETRY_GPU=on` on the
  collecting host to allow `/usr/bin/tegrastats` or the documented NVIDIA utility.
  No download, elevation or inference. Shared RAM/power domains remain labeled.
- Python supervision, nonblocking allowlisted local runtime telemetry, public
  authenticated REST/SSE and pure Dart monitoring transport/models.
- Go fleet state, SQLite aggregate history, bounded replay and internal API.

## Remaining acceptance gaps

**Not release-qualified on every target.** Durable SQLite history now persists
60-second (24-hour) and 300-second (7-day) aggregate tiers, sample deduplication,
and restart queries. Bounded asynchronous writes keep live collection independent
of storage; history degradation is visible. Existing JSONL data is not migrated.
Remote runtime/AI attribution remains unknown; currently only the local Python
runtime publishes observations. TTFT/resident-model measurements remain unknown
when the existing adapter does not expose them. Do not substitute total latency
or selected-model identity.

Remote Rust telemetry connections are reused in a bounded pool with identity/grant
rechecks. Hardware latency/overhead, native Linux/macOS/ARM64, minimum Rust version,
complete redistribution inventory and clean-machine portable/visual acceptance
remain outstanding. Optional Go component inclusion and the Windows Flutter build
are verified; they do not establish final packaged acceptance. No per-host daemon
is introduced.

See [API and UI handoff](api.md) and [qualification evidence](qualification.md).
