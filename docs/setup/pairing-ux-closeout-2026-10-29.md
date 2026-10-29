# Pairing UX / executor closeout — October 29, 2026

## Scope and result

Completed the unblocked Windows executor/package and pairing lifecycle corrections.
Production six-digit enrollment remains disabled: M0 has not been approved. This
increment does not turn the test exchange into an approved production protocol.

- Fixed the native window's invalid creation-state pointer. The enhanced smoke
  failed before the fix and passed afterward; it checks the owning pointer and
  all ten controls, and actually paints the status.
- Native helper work completes through one bounded channel, without passing window
  handles/raw result pointers to worker threads. Closing during a mutation waits
  for its bounded outcome; completed grants are never described as undone.
- Status polling no longer replaces cancellation ownership or restarts an executor
  after Stop. Local listener status is distinct from remote connection health.
- Start/Stop executor reuse the existing Rust mTLS host and bundled Node server.
  Windows job ownership contains relay/Node descendants. The child waits for a
  private stdin activation signal until job assignment succeeds, before listening.
  No new trust or workspace permissions are inferred by starting the listener.
- Dart cancellation submits the exact session created by that adapter; observing
  someone else's session never authorizes cancelling it. Stale polls are rejected.
- Python helper output retention and timeout cleanup are bounded. Regression:
  a descendant holding stdout previously delayed a 0.2s timeout beyond 3 seconds;
  the corrected test returns within its 2-second bound.
- Package assembly refuses existing output paths rather than recursively clearing
  them. It stages real Node 22.23.0, complete production tool-server dependencies,
  ESM metadata, notices, icon asset, inventory and checksums. No models, credentials,
  Python runtime or development toolchain are needed by the executor package.

## Artifact

Unsigned Windows x64 development package:

`build/OdessyExecutor-windows-x64-20261029-verified.zip`

Adjacent `.sha256` is the archive checksum. Extract the complete archive and open
`odessy-executor.exe` inside its package directory. Do not copy the EXE alone.
Choose a workspace with Browse. Start executor serves existing enrolled identities
whose receiving-side workspace grant matches; Stop retains identities/configuration.
Allow pairing reports the M0 blocker. It cannot enroll an ordinary new device yet.

This supersedes earlier placeholder-package evidence. The older generated archives
are retained as build outputs; use the explicitly named **verified** archive above.
Checksums establish file integrity, not publisher signing or cryptographic approval.

## Verification performed

- Python affected authorization, bridge, chat-only, packaging and process bounds:
  **48 passed**.
- Rust default suite: **114 link tests, 4 executor tests and 1 integration test**
  passed, then the added stop/ownership regression made the executor suite **5 passed**.
  Production builds and `cargo clippy --all-targets --locked -- -D warnings` passed.
- Dart full suite: **20 passed, 2 skipped**; analysis clean.
- Flutter full suite: **180 passed, 7 skipped**; analysis clean. Targeted pairing
  and accessibility suite: **13 passed**.
- Final packaged self-test verified **3534 files** and ran bundled Node 22.23.0.
- Native window smoke: **10 controls**, correct owner binding, status painted,
  normal destruction; exit 0. This is automated wiring, not human visual acceptance.
- Actual packaged companion engine → Rust mTLS → Node: initialized, listed **11
  tools**, read the qualification workspace, refused an outside-workspace request,
  then refused a new connection after revocation. Repeated on final binaries.
  Administrative fixture identities were generated only in isolated qualification
  directories. This is not short-code or physical Windows–Jetson acceptance and
  does not establish Python action-policy approval UX from the desktop.
- Fresh independent correction review completed. Important Windows findings fixed;
  no remaining concrete blocker reported in that correction pass. Not M0 approval.

Evidence: `../.qualification/executor-real-20261029-verified/result.json`,
`host.log`, `pair-closeout-full-flutter.log`, `pair-ux-analysis.log` and
`executor-package-final.log`.

## Remaining gates

1. Technically complete M0 protocol implementation/profile, interoperability vectors
   and required independent security approval. Real enrollment stays closed.
2. Human companion keyboard/visual/firewall trials and the real packaged
   Windows–Jetson code → approval → consent → policy-controlled tool journey.
3. Signing/final redistribution review. Linux/macOS companion delivery and Unix
   descendant cleanup are outside this Windows-only increment.

No branch switch, commit, merge, push, paid API, model inference or Jetson deployment.


## Oct 29: physical-trial regressions corrected

- Executor composition failures now produce a tool-free chat session even before a paired alias exists. Model initialization failures remain errors; no fake model or alternative executor is selected.
- Linux checkout discovery no longer picks a copied Windows executable. Desktop helpers resolve the native sibling component, and explicit UI component selection is passed to the short-code adapter.
- Pairing advertisements have their own mDNS instance and purpose, separate from mTLS execution advertisements. Discovery groups address/interface results by advertised fingerprint (still untrusted), prefers pairing and IPv4, and never grants trust through discovery.
- Verified: Rust 111 link + 5 executor + 3 CLI tests; client 6 tests; Flutter 9 targeted tests; Flutter analysis passed. Jetson fake-model chat/discovery 9 tests and real helper pairing/platform tests 22 passed, 1 skipped. Docker ARM64 Flutter and Rust release builds passed. Corrected backend and desktop deployed to the closed Jetson preview; no inference or physical pairing claim.
- Removed eight superseded disabled-pairing preview paths under build. Configuration, keys, models and reusable compiler caches retained.

## Live chat failure: missing credentials (Oct 29)
Inspected the actual configured DeepSeek process without exposing key values: no API-key variables were present. Reproduced CloudModelConfigError before executor initialization. The prior executor fallback did not address this cause. Added a session-only masked key prompt at owned source-coordinator launch; keys enter only that child environment, not argv/config/logs. Explicit credential references retain their scope. Missing-key HTTP responses now use a fixed typed client message. Removed the conversation-connected prerequisite from an otherwise verified owned-service restart; owner matching and restart reservation remain mandatory. Eight targeted Flutter tests and one real HTTP Dart credential-error test passed; Flutter analysis passed. No provider call or live successful conversation is claimed.
