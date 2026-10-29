# 2026-10-28 — short-code pairing: sessions, companion and package

Branch `develop`, working tree preserved. Nothing committed, merged, stashed or
pushed.

## What was actually done

1. **M0 decided explicitly, not implausibly.**
   [`docs/security/m0-pairing-profile.mdx`](../security/m0-pairing-profile.mdx)
   records the candidate construction (SPAKE2/RFC 9382 over P-256, `spake2 =0.4.0`,
   TLS exporter channel binding, mutual key confirmation, ChaCha20-Poly1305 framing,
   ECDSA possession proof), the exact limits, the threat model and the three external
   blockers. Production enrollment stays closed: `pairing capabilities` answers
   `supported: false, reason: protocol_review_required` and `pairing begin` creates no
   session.

2. **Durable Rust enrollment sessions** (`native/odessy-link/src/pairing/`):
   `capabilities | status | begin | listen | join | confirm | cancel`.
   - six-digit codes with leading zeros, 120-second window;
   - three attempts per window reserved by a conditional UPDATE before comparison;
   - two concurrent unauthenticated connections released by an RAII guard;
   - 64 KiB frames, 256 KiB per direction, five-second deadline per message;
   - `awaiting_approval` writes no trust row; the commit is `peer` → `grant` → `done`
     with a durable operation id and is idempotent;
   - recovery marks an interrupted commit `incomplete` (`commit_interrupted`);
   - cancel during a commit reports an unknown outcome, never a rollback;
   - the code travels on **stdin** (`--code-stdin`), never on a command line.

3. **Exchange** (`pairing/exchange.rs`): transcript binding of roles, both
   fingerprints, both nonces and the session; code comparison; mutual ECDSA P-256
   possession proofs verified against the key **inside the certificate being
   enrolled**; mutual key confirmation; every rejection recorded on both sides.

4. **Python bridge** (`src/odessy/setup/native_bridge.py`): the `short-code` action
   now drives the real commands, validates the reply shape (a non-closed state without
   a session identity is refused), scrubs the code from every reply except an opening
   window, and reports a missing component truthfully instead of as success.

5. **Flutter** (`short_code_panel.dart`, `pairing_controller.dart`,
   `pairing_page.dart`): real state copy for expired/rejected/incomplete, digits kept
   after a failed attempt, polling while the other computer decides, a single
   `onPaired` signal that surfaces the **Use for tools** review, and a paired-identity
   banner on the page.

6. **Windows companion** (`native/odessy-link/src/bin/executor.rs` + `executor/window.rs`):
   one window with the shared folder, Browse…, Allow pairing, Approve request, Cancel,
   the code and the status; single-instance focus; children owned by the window; an
   explicit keep-in-background choice; checksum verification before it starts;
   `--selftest` for packaging.

7. **Packaging** (`native/odessy-link/package-executor.ps1`): builds both binaries,
   assembles Node, tool server, notices and icon, writes `checksums.txt`, then runs the
   packaged `--selftest`.

## Commands and results

```
native/odessy-link> cargo test --features test-profile
  test result: ok. 105 passed   (unit)
  test result: ok. 3 passed     (tests/pairing_cli.rs — two-process journeys)
  test result: ok. 3 passed     (executor unit tests)

native/odessy-link> cargo clippy --all-targets --features test-profile -- -D warnings
  clean (one pre-existing let-and-return lint in telemetry/transport.rs fixed)

repo> .venv\Scripts\python.exe -m pytest tests\test_setup_native_bridge.py -q
  10 passed

repo> .venv\Scripts\python.exe -m pytest tests\test_pairing_session_e2e.py -q
  1 passed   (Python helper → real odessy-link binary, two state directories)

packages/odessy_client> dart test
  18 passed, 2 skipped
packages/odessy_client> dart analyze
  no issues

apps/desktop> flutter analyze
  no issues
apps/desktop> flutter test
  179 passed, 7 skipped, 0 failed   (complete desktop suite)
apps/desktop> flutter test test\short_code_pairing_test.dart     7 passed
apps/desktop> flutter test test\navigation_layout_test.dart      8 passed
apps/desktop> flutter test test\pairing_outbound_test.dart test\pair_discovery_test.dart test\desktop_acceptance_test.dart
  6 passed

repo> pwsh -File native\odessy-link\package-executor.ps1 -NodeDirectory ... -NodeServer ... -OutputDirectory build\executor-package
  checksums: 6 files verified
```

Tamper check: appending one byte to `tool-server.js` made `odessy-executor --selftest`
report `tool-server.js does not match its checksum; reinstall the executor package`.

## The `HTTP 503` navigation assertion

The test asserted the literal string `HTTP 503`. `ApiFailure.toString()` for 503 now
returns the documented, actionable message ("Cannot start a conversation. Check the
configured model and execution host in Settings or Devices. Configuration remains
available."), which is what the contract promises and what a user can act on. The
assertion was **replaced with a stronger one**, not deleted: the failure must still be
visible (`ComponentStateBanner`), must contain that exact message, must **not** echo a
raw status code, and navigation must still work afterwards.

## Gap-closing pass (2026-10-29)

- Added wire-level negative regressions: oversized frame, truncated frame, message out
  of order, and silence after the challenge. Each asserts the window stays
  `discoverable`, **no attempt is spent** and nothing becomes pending.
- Added substitution/replay regressions: a proof bound to one session does not verify
  in another; a host-role proof does not verify as a joiner proof; a proof made with an
  identity other than the presented certificate is refused.
- Added `odessy-executor --ui-smoke`: creates the window, asserts all eight controls
  exist, paints the status once and destroys the window (exit 0). Automated wiring
  evidence only — still not a human clicking the buttons.
- Fresh review of the change found one real defect and it was fixed: a commit that was
  already satisfied returned without moving the session to its terminal state, so the
  joining side could show `awaiting_approval` for an already-enrolled identity.
- Re-ran: Rust **111 unit + 3 integration**, clippy clean in both feature
  configurations.

## Window threading and relay wiring (2026-10-29)

- All component commands moved off the window thread (`Job` + `WM_WORK`); the status
  poll no longer blocks painting and is skipped while a job runs.
- The companion starts and stops the existing `odessy-link serve` relay using packaged
  paths and `ODESSY_LINK_NODE`, and cancels its own session by id.
- Leftover synchronous `cancel` removed; clippy clean in both feature configurations;
  `--ui-smoke` exit 0; 114 unit + 3 integration tests pass.

## What is not done, and why

- **Independent cryptographic review and interoperability vectors.** External. An AI
  review is not a cryptographic audit.
- **Windows–Jetson acceptance on the reviewed profile.** Blocked by the above; the
  journey today runs between two processes on one machine using the labelled adapter.
- **Native window interaction.** The window is created and its controls verified by
  `--ui-smoke`, but no person clicked its buttons in this increment. Unverified.
- **A release-quality package.** The assembled package used placeholder Node/tool
  server files to exercise packaging and checksums; it is not shippable.
- **Whole-change independent review** of this increment.
