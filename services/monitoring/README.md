# odessy-monitor (Go monitoring service)

Central, single-process Go monitoring service for Odessy's multi-host monitoring
feature. It owns fleet scheduling, identity projection, liveness, bounded
history and the internal REST/SSE API. It does **not** own authentication
policy, collection, the public API, or presentation.

Contract revision: **monitoring-v1**. The canonical field/deadline/enum
definitions live in `../../multihost-monitoring-codex-plan.md` §2 of the
planning workspace; this module implements them against fake Rust/runtime
sources.

## Boundary

| Owner | Responsibility |
| --- | --- |
| This module (`services/monitoring/**`) | Go service, scheduling, state, history, events, internal API, fakes, docs |
| Rust `odessy-link` (Codex) | Identity, mTLS transport, collectors, `telemetry worker` child |
| Python (Codex) | Supervision, public authz, public API filtering, runtime metrics |
| Flutter (UI teammate) | Presentation |

Go never reads a Rust trust database or private key, never authorizes
execution, and exposes no pairing/revocation/config mutation endpoint.

## Layout

```text
cmd/odessy-monitor/      binary: private bootstrap, readiness, graceful shutdown
internal/protocol/       versioned wire types, strict validation, settings limits
internal/link/           Rust worker interface + bounded JSONL process adapter
internal/fleet/          identity projection, trust leases, liveness, scheduler
internal/history/        recent-sample ring, weighted aggregates, durable journal
internal/events/         epoch:sequence cursor, bounded replay ring, subscribers
internal/runtime/        sanitized runtime ingestion and deduplication
internal/httpapi/        loopback-only bearer-authenticated REST + SSE
internal/testsupport/    fake clock, fake link, fake worker executable
testdata/                owned monitoring-v1 fixtures
```

## Launch protocol

Python writes exactly one bounded (`<=64 KiB`) frame to private stdin:

```json
{"schema_version":1,"op":"configure","token":"<>=32 chars>","state_dir":"...",
 "link_binary":"/abs/path/odessy-link",
 "settings":{"monitored_host_ids":[],"include_local":false,"preset":"default"}}
```

The binary starts the configured `link_binary` with the fixed arguments
`telemetry worker` (never through a shell), binds `127.0.0.1:0`, and writes one
readiness frame to stdout:

```json
{"schema_version":1,"op":"ready","base_url":"http://127.0.0.1:PORT","epoch":"..."}
```

The token never appears in argv, the readiness frame or diagnostics. Closing
stdin or sending SIGINT/SIGTERM shuts down cleanly: HTTP stops, in-flight polls
drain within the deadline, and only the owned worker child is terminated.

## Internal API

All routes require `Authorization: Bearer <internal token>` with constant-time
comparison. Errors are `{"schema_version":1,"error":{code,message,retryable}}`
with codes `MONITOR_UNAVAILABLE`, `HOST_NOT_FOUND`, `HOST_ACCESS_DENIED`,
`INVALID_REQUEST`, `INVALID_RANGE`, `UNSUPPORTED_VERSION`, `RESOURCE_LIMIT`.

| Route | Purpose |
| --- | --- |
| `GET /internal/v1/monitoring` | fleet health, host count, event cursor, history status |
| `GET /internal/v1/hosts?limit=&cursor=` | host list from a pinned immutable snapshot |
| `GET /internal/v1/hosts/{id}` | one host projection |
| `GET /internal/v1/hosts/{id}/metrics` | latest sample, stale groups, services, AI |
| `GET /internal/v1/hosts/{id}/history?from=&to=&step=&series=` | bounded series |
| `GET /internal/v1/events` | SSE, `id: epoch:sequence` |
| `POST /internal/v1/runtime-observations` | sanitized runtime metrics from Python |

## Behavioural guarantees implemented here

- **Opt-in only.** Enrollment alone never enables polling; a host must be in
  `monitored_host_ids` (or be local with `include_local`).
- **Trust lease.** Inventory refreshes every 1 s; after 3 s without success the
  trust state becomes `unknown` and remote observations are refused. Failed
  refreshes never look like an empty successful inventory.
- **Revocation.** A previously known peer that disappears from a successful
  authoritative inventory becomes a revoked tombstone with a new generation.
  Delayed heartbeats/samples for the old generation are rejected, so late data
  cannot restore liveness or append history.
- **Scheduling.** Heartbeat 5 s, basic 10 s, GPU/thermal/power 15 s, disks 60 s
  (low-power 15/30/30/120, hard minimums 5/5/10/30). One in-flight request per
  host, four concurrent requests overall with one slot reserved for inventory
  so a stalled host cannot starve inventory refresh. Overdue groups coalesce
  into a single sample; missed periods are never replayed.
- **Liveness.** `online` at `<=2` heartbeat intervals, `stale` above 2 and below
  6, `offline` at `>=6`. Metrics go stale after two of their own group
  intervals. An authenticated heartbeat establishes `online` even when
  collection fails; missing values stay `null`, never fabricated zeros.
- **Bounded memory.** 1 h / 16 MiB recent-sample ring, 4,096-event / 8 MiB replay
  ring, 8 subscribers × 128 events / 1 MiB, 8 pagination snapshots for 30 s,
  250,000 history rows / 128 MiB journal budget with explicit degraded status.
- **Runtime hygiene.** Runtime bodies are `<=64 KiB`, strictly decoded against
  the section 2.4 field set, and rejected when any secret- or
  conversation-bearing key appears. Only the local host may receive runtime
  attribution; remote runtime values remain unknown. Per-source sequences must
  increase, so retries cannot duplicate totals.

## Configuration

`settings.preset` is `default` or `low_power`; optional overrides are
`cadence.{heartbeat_ms,basic_ms,disk_ms,gpu_ms,runtime_ms,aggregate_ms}` and
`retention.{memory_minutes,minute_hours,five_minute_days}`. Values below the
hard minimums, more than 32 hosts, malformed host ids and unknown presets are
rejected at bootstrap rather than clamped. Changing settings requires a
supervisor restart, which starts a new SSE epoch.

## Verification

```sh
go build ./...                 # all packages
go vet ./...
go test ./...                  # unit + HTTP/SSE + fake-worker process tests
go test -race ./...
go build -o /tmp/odessy-monitor ./cmd/odessy-monitor
```

The fake worker is built from this module and exercised through the real
process adapter, including malformed frames, interleaved request ids and
deadline cancellation.

## Known limitations (honest handoff)

1. **SQLite driver not pinned.** This environment has no module-proxy access, so
   the pinned pure-Go SQLite driver could not be fetched. History persistence is
   implemented behind `history.Store` with the contract's semantics (bounded
   single-writer queue, row cap, byte budget, explicit degraded status) over an
   append-only JSONL journal. Swapping in the SQLite driver replaces `Store`
   internals only; nothing else imports a driver.
2. **History queries read the in-memory ring.** Persisted journal rows are
   counted for capacity/degradation but are not re-derived into aggregates yet,
   so history does not survive a restart. Codex-owned integration should decide
   whether that backfill is required before the UI ships.
3. **Aggregate tiers are in-memory only.** The 1-minute/24 h and 5-minute/7-day
   bucket maps are not yet materialised into the journal.
4. **No real Rust, mTLS or GPU interaction is claimed.** Fake-worker success
   proves the adapter, not physical pairing or device support.
