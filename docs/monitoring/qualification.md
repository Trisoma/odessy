# Monitoring implementation ledger — October 25, 2026

Plan: `../multihost-monitoring-codex-plan.md` in the outer workspace.

Implementation in progress. No release/physical-device acceptance claimed.
Shared checkout: Codex owns Rust/Python/Dart/contracts/integration; DeepSeek owns
`services/monitoring/**` until explicit handoff. No commits or branch operations.

Ruling: preserve the current shared checkout rather than create a worktree, as
explicitly required by the plan. Existing execution authorization is unchanged.

Preflight: worker protocol is the Go/Rust boundary; internal HTTP is the
Python/Go boundary; public REST/SSE is the Dart/UI boundary. All use monitoring-v1.

## Verification in this session

- Python: **69 passed**, affected monitoring/application/pairing suites, including
  the real Go→Rust worker→host metrics→Python authenticated REST/SSE path and
  separate Rust worker→mTLS→telemetry-only peer→revocation test. No models or
  execution grants used. Tests used bundled Python3.11 and existing site packages:
  the repository `.venv` launcher points to a missing Python installation.
- Rust: **75 passed**, including existing DPAPI/certificate/pairing regressions,
  grant isolation, bounded frames, group-local warmup and vendor parser tests.
  DPAPI tests required an unsandboxed user context; sandbox-only runs failed5
  existing identity-store tests and were not treated as product regressions.
- Go: all8 packages with tests passed `go test ./... -count=1`; `go vet ./...`
  passed; Windows binary built. Race detector remains unverified on this host.
- Dart: analysis clean; **11 passed,2 existing opt-in integrations skipped**.
  New monitoring tests use real local HTTP/SSE; this does not qualify Flutter
  presentation or a deployed desktop application.
- `git diff --check` passed. No commit, merge, push, real-device test or paid
  provider request was performed. No installer/UI teammate files were rewritten.

## Independent review and corrections

A separate reviewer inspected Codex's changes, then Go event/snapshot wiring.
Confirmed and corrected: interleaved-group rate/warmup errors; upstream HTTP
errors flattened to503; unbounded unterminated SSE buffering; optional-config
failure preventing application startup; swallowed startup cancellation; Go replay
send-on-closed-channel panic/order; subscriber byte limits; snapshot/cursor capture
and expiry; Dart wrapped-host decoding. Replay panic and partial-group erasure
were reproduced with failing Go regressions before correction.

Real integration additionally caught numeric registry revisions, optional swap and
vendor-group aliases, and slow synchronous disk enumeration. Decoder aliases were
added; disk capacity now uses a bounded single background collector/cache. Missing
groups remain available in the latest host view with original observation times;
history receives only newly collected groups.

## Earlier rulings and remaining work (superseded where noted below)

- Ruling: do not edit DeepSeek's Go module until explicit handoff. User supplied
  that handoff during this task; integration corrections afterwards touch that
  module. Its initial fake-only success did not establish combined acceptance.
- Ruling: disk enumeration may block in the OS. One owned background collector
  prevents it blocking basic metrics; pending/stale output is visible. Hardware
  latency/memory qualification remains required; no universal deadline guarantee.
- **C6 is not complete.** SQLite storage, persisted aggregation tiers and restart
  queries are absent: DeepSeek used a bounded JSONL journal because its dependency
  fetch failed. Full durable-history behavior must be implemented and tested.
- Portable preview inclusion, final wheel/artifact verification, Flutter Windows
  build/visual acceptance, native Linux/macOS/ARM64 qualification and benchmarks
  are outstanding. The new CI workflow is authored, not claimed remotely green.
- Remote runtime/AI attribution, true TTFT/observed resident-model data where the
  underlying adapters lack them, disk I/O and additional hardware collectors are
  unknown/unsupported. No invented readiness, inference probe or zero metrics.
- Pinned sysinfo0.33.1 is used for basic collection. Existing Rust1.85 floor was
  preserved in metadata, but this session ran Cargo/Rust1.92; minimum-toolchain and
  complete transitive redistribution inventory still need release verification.
- Current Rust transport reconnects per remote request; persistent connection
  pooling and low-power overhead measurements remain unqualified.

The full monitoring plan remains open. These results establish the tested core,
not release readiness or completion of all four monitoring layers on every host.

## Continuation: SQLite, reuse and Windows build

Implemented SQLite persistence, 60/300-second aggregate tiers, deduplication,
retention, bounded storage/queue/query output and restart queries. Independent
review exposed queue-overflow write suppression and unaligned range handling;
regressions reproduced both and pass after corrections. JSONL is no longer the
active store; historical JSONL migration is not implemented.

Implemented bounded Rust connection reuse with peer/grant checks, and optional
hashed Go monitoring component inclusion for coordinator/combined bundles.

Verified locally: 19 affected Python packaging/context/supervisor tests; eight
Rust telemetry tests; final real local monitoring integration and mTLS/revocation
checks (two passed). Windows Flutter release build succeeded. A fresh wheel was
built and its monitoring supervisor imported outside the checkout. Component
hashes are development evidence, not a final signed portable release manifest.

Still open: clean-machine full portable artifact/visual acceptance; native
Linux/macOS/ARM64 and Rust 1.85 qualification; complete transitive license and
redistribution review; hardware collector latency/memory/power benchmarks; remote
runtime attribution and unavailable hardware metrics. No provider/inference probe
was added. The authored CI workflow has not been observed remotely green.

Final Go verification: all eight test packages passed after the last correction; `go vet ./...` passed. `git diff --check` passed.
