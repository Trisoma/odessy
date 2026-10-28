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

- [ ] M0: evidence/profile review. External independent cryptographic approval
  remains required; no reviewed complete profile is currently available.
- [ ] M1: fail-closed native session boundary, lifecycle/state/persistence tests.
  Real cryptographic enrollment is blocked by M0.
- [ ] M2: reviewed outbound consent, revocation/revision checks, migration tests.
- [ ] M3: typed Flutter pairing surface and native Windows executor companion,
  truthful unsupported state and command-free existing-executor management.
- [ ] M4: package/checksums, local runtime checks, whole-change independent review.
  Real short-code Windows–Jetson acceptance is blocked until M0/M1 are complete.

## Security review evidence

RustCrypto spake2 0.4.0 supports the repository's MSRV and Apache/MIT licensing,
provides PAKE primitives, and explicitly disclaims an independent audit. It does
not supply an authenticated enrollment channel. RFC 9382 requires mutual key
confirmation. Cipher-suite/vector compatibility, channel binding, framing and
possession proofs have not been independently approved. No custom composition
will be promoted by a build flag or environment variable.

Sources: https://docs.rs/spake2/0.4.0/spake2/ and
https://www.rfc-editor.org/rfc/rfc9382.html .

## Implemented increment and verification

This is a partial implementation, not completed M1–M4 acceptance.

- Rust exposes a bounded JSON-lines, disabled pairing capability boundary. No
  listener, PAKE, secret exchange or enrollment runs. Constants describing planned
  network limits do not mean those network limits have been implemented.
- Existing TLS hosting reloads current active roots for each new connection.
  Revoked peers are excluded from roots and effective execution grants.
- Python stores reviewed outbound consent separately from incoming grants, binds
  alias/certificate/trust scope and revisions, and rechecks it before dispatch.
  Every save changes the generation to prevent replay of an earlier review.
- Missing consent defaults disabled. Existing users must explicitly review
  **Use for tools** and reconnect; unavailable execution retains chat-only recovery.
- Flutter auto-scans nearby candidates, exposes the explicit M0 blocker, and keeps
  manual administrator operations under Advanced. The six-digit flow is exercised
  only through labelled test adapters. Incoming and outgoing authority are distinct.
- Cancellation failure reports an unknown outcome rather than false success.
  The current adapter cancels its helper only: durable owned-session cancellation,
  interactive event transport and recovery remain absent, not implicitly provided.

Verification on Windows, October 28:

- Python affected authorization/bridge/chat-only/capability tests: **47 passed**.
  The initial restricted run failed DPAPI initialization; permitted rerun passed.
- Rust full suite: **81 passed**. Old incremental-cache cleanup reported access
  warnings; no test failed.
- Dart client full suite: **17 passed, 2 skipped**; analysis clean.
- Flutter focused pairing/accessibility tests: **9 passed**; analysis clean.
- Full Flutter run before the final large-text correction: **173 passed,
  7 skipped, 2 failed**. The large-text case subsequently passed with all nine
  focused tests. The separate navigation test expecting literal `HTTP 503` remains
  unresolved; do not describe the complete desktop suite as green.
- Pairing widget screenshot inspected; this is not native window/device acceptance.
- Independent whole-change review found no confirmed M0 bypass or expanding
  migration. Its cancellation-status finding was fixed with a regression; its
  missing session-lifecycle finding remains explicitly open. This is not M0 approval.

## Blocking and undelivered work

M0 still needs an approved complete protocol/dependency profile, interoperability
vectors, threat model and independent cryptographic composition review. No user
toggle or environment variable bypasses this gate. Real enrollment cannot proceed
without it. Durable pending enrollment and authenticated recovery (M1/M2), the
native Windows executor companion, complete role-specific packaging (M3), and the
real no-command Windows–Jetson journey (M4) are not delivered by this increment.
No new release artifact or deployment is claimed. Nothing was committed or pushed.
