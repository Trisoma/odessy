# Short-code pairing implementation ledger — 2026-10-28

## Binding decisions

- Implement on the current develop checkout at the owner's request; no branch,
  worktree, commit, merge or push. Preserve other work.
- M0 is not approved. Do not add a production PAKE/encryption composition or
  open a short-code listener. A mock success is never exposed as real pairing.
- Rust owns enrollment and trust; Python owns outbound execution consent;
  Flutter/native Windows controls present state and trusted operator actions.
- No automatic mapping from inbound grants to outbound consent. Missing consent
  means chat-only. Existing identities, incoming grants and revocations stay intact.
- Existing source/installed configuration and writer/approval machinery is reused;
  outbound consent is a separate Python-owned per-user configuration record so
  legacy role/model/voice exports do not acquire inferred authority.

## Milestones

- [ ] M0: profile decision recorded in
  [`docs/security/m0-pairing-profile.mdx`](../security/m0-pairing-profile.mdx).
  The candidate construction and threat model exist; **independent cryptographic
  approval and interoperability vectors do not**, so production enrollment stays
  closed.
- [x] M1: durable Rust-owned enrollment sessions — begin/join/status/confirm/cancel,
  six-digit codes with leading zeros, 120-second window, atomic three-attempt budget,
  two concurrent unauthenticated connections, bounded frames and aggregate bytes,
  pending-versus-active trust, staged commit, recovery at every persistence boundary.
- [x] M2: outbound consent plus revocation/revision/malformed/legacy tests, and the
  rule that incoming authority is never migrated into outgoing authority.
- [x] M3: typed Flutter pairing surface with real session states, approval polling and
  consent prompt; native Windows executor companion with workspace browsing,
  single-instance focus, owned processes and an explicit background choice.
- [x] M4 (software): package script that assembles Rust, Node, tool server, notices and
  `checksums.txt`, and a self-test that verifies them.
- [ ] M4 (acceptance): the real no-command Windows–Jetson journey on the reviewed
  profile. Blocked by M0; the journey is exercised today through the labelled test
  adapter on one machine pair.

## Security review evidence

RustCrypto spake2 0.4.0 supports the repository's MSRV and Apache/MIT licensing,
provides PAKE primitives, and explicitly disclaims an independent audit. It does
not supply an authenticated enrollment channel. RFC 9382 requires mutual key
confirmation. Cipher-suite/vector compatibility, channel binding, framing and
possession proofs have not been independently approved. No custom composition
will be promoted by a build flag or environment variable.

Sources: https://docs.rs/spake2/0.4.0/spake2/ and
https://www.rfc-editor.org/rfc/rfc9382.html .

## Increment 2 (2026-10-28, later): sessions, companion, package

Durable enrollment sessions are now implemented in Rust and exercised end to end
through two processes. Production enrollment remains closed because M0 is unapproved.

- `native/odessy-link/src/pairing/` owns the session store, the profile gate, the
  bounded exchange and the CLI: `pairing capabilities|status|begin|listen|join|confirm|cancel`.
  The code travels on stdin (`--code-stdin`), never on a command line.
- Limits are properties of the store, not of a comment: the window expires in
  `seconds_remaining`; the three-attempt budget is reserved by a conditional UPDATE
  before the code is compared; the two connection slots are released by an RAII guard.
- `awaiting_approval` writes no trust row. The commit is three durable stages with a
  durable operation id, and re-running it is the same approved operation — verified by
  asserting the grant revision does not move on the second call.
- Recovery marks an interrupted commit `incomplete` with reason `commit_interrupted`
  and does not enrol anything on the operator's behalf.
- `odessy-executor` is the Windows companion: one window (workspace, Browse…, Allow
  pairing, Approve request, Cancel), single-instance focus, children owned by the
  window, an explicit keep-in-background choice, package checksum verification before
  it starts, and `--selftest` for packaging.
- Python `pair` operation `short-code` now drives those commands, validates the reply
  shape, requires a session identity for any non-closed state and scrubs the code from
  every reply except an opening window.
- Flutter shows the real states (expired, rejected, incomplete, awaiting approval),
  keeps the digits after a failed attempt, polls while the other computer decides and
  reports the enrollment once so the page can offer **Use for tools**.

Verification on Windows, October 28 (this increment):

- `cargo test --features test-profile`: **111 unit + 3 integration passed**; three
  integration tests are two-process CLI journeys (six-digit code, wrong code three
  times, stdin code transport). The negative suite covers an oversized frame, a
  truncated frame, a message out of order, silence after the challenge (five-second
  deadline), a proof replayed into another session or another role, and a proof made
  with a different identity than the presented certificate — each asserting that no
  attempt was spent and nothing became pending.
- `cargo clippy --all-targets --features test-profile -- -D warnings`: clean.
- `pytest tests/test_setup_native_bridge.py`: **10 passed**.
- `pytest tests/test_pairing_session_e2e.py`: **1 passed** — the Python helper drives the
  real `odessy-link` binary across two state directories, including approval, workspace
  grant, durability across a helper restart, and a truthful "nothing to cancel".
- `dart test` (client): **18 passed, 2 skipped**; `dart analyze`: clean.
- `flutter test` (desktop, complete suite): **179 passed, 7 skipped, 0 failed**;
  `flutter analyze`: no issues. The navigation test no longer asserts a raw `HTTP 503`;
  it asserts the documented actionable message and that the code is not echoed.
- Packaging: `build/executor-package/` contains `odessy-executor.exe`,
  `odessy-link.exe`, `tool-server.js`, `node/`, `LICENSE`, `THIRD-PARTY-NOTICES.md` and
  `checksums.txt` (6 files). `--selftest` reported `checksums: 6 files verified`; after
  one byte was appended to `tool-server.js` it reported `does not match its checksum`.
- `odessy-executor --ui-smoke` creates the real window, asserts that all eight controls
  exist, lets one timer turn paint the status, and destroys it: `window: 8 controls
  created, status painted, window destroyed`, exit 0. This is automated wiring
  evidence, not a person clicking through the flow.
- Review finding fixed: an already-satisfied commit returned the session without
  moving it to its terminal state, so the joining side could display
  `awaiting_approval` for an identity that was already enrolled. It now records the
  same terminal state the first attempt would have produced, and the idempotency test
  still shows the grant revision does not move.

## Increment 3 (2026-10-29): window threading and the real relay

- **M0 corrected.** `spake2 0.4.0` implements `Ed25519Group`; the earlier P-256 claim
  could only be honoured with a custom group, so the profile now uses the Ed25519 PAKE
  and keeps the existing ECDSA P-256 identity for possession. See
  [the M0 record](../security/m0-pairing-profile.mdx).
- **No subprocess runs on the window thread.** `Allow`, `Approve`, `Cancel` and the
  one-second status poll all run on a worker thread and return through `WM_WORK`; the
  poll is skipped while another job is in flight. `--ui-smoke` still creates the window,
  asserts all eight controls and destroys it (exit 0).
- **The companion now runs the existing mTLS relay.** After a `ready` commit it starts
  `odessy-link serve --workspace … --node-server … --advertise` with
  `ODESSY_LINK_NODE` pointing at the packaged `node/node.exe`, and stops it with the
  window. Cancellation names the owned session id and stops the listener first.
- **Bounded subprocess output**: stdout is drained on a reader thread, capped at
  256 KiB and otherwise discarded, so a chatty child cannot deadlock the pipe.
- **Packaging refuses unsafe work**: an output directory outside `build/` that is not
  an existing executor package is refused before any deletion; a `node.exe` under
  1 MiB or without an `MZ` header is refused; a tool server under 1 KiB is refused;
  missing notices are an error. Both refusals were demonstrated.
- `ready` no longer claims connectivity, and status inspection can no longer mark a
  live commit `incomplete` (`Store::open_readonly`, plus a 60-second liveness window).
- Codes are drawn by rejection sampling, so all 10^6 values are equally likely and
  leading zeros are reachable.

Evidence: `cargo test --features test-profile` **114 unit + 3 integration**; clippy
clean with and without the feature; `--ui-smoke` exit 0; `pytest` bridge + E2E **11
passed**.

Real packaging inputs found on this machine, not yet assembled into an artifact:
`C:\Program Files\nodejs\node.exe` (runtime), `tools/dist/src/server.js` (built tool
server entry) and `tools/node_modules` (41 MB of dependencies). No package or checksum
is claimed for this increment.

## Blocking and undelivered work

- **M0 approval** (independent review of the profile in
  `docs/security/m0-pairing-profile.mdx`, plus interoperability vectors). Until it
  exists, `pairing begin` returns the closed snapshot and no listener opens.
- **Real two-device acceptance** on the reviewed profile, on the intended network.
  The Windows–Jetson journey with the packaged companion has not been run.
- **Native window interaction** was not exercised: the companion compiles, its
  logic has unit tests and its `--selftest` runs, but nobody clicked its buttons in
  this increment. That is unverified, not passing.
- **A real Node runtime and tool server** are not part of this repository checkout;
  the package above was assembled with placeholder files to exercise the packaging
  and checksum path, and is not a releasable bundle.
- **Independent whole-change review** of this increment has not been performed. The
  previous review is not M0 approval and does not cover these files.

Nothing was committed, merged or pushed.

## October 29 closeout update

See [pairing UX closeout](pairing-ux-closeout-2026-10-29.md). It supersedes the
placeholder-package, synchronous-window and missing whole-change-review items
above: a real Windows executor package and local packaged mTLS/Node verification
now exist. Exact-session cancellation, native state ownership, polling/Stop,
bounded helper output/cleanup and owned executor containment were corrected.
M0 and real Windows–Jetson short-code acceptance remain blocked/unperformed.
