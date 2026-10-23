# Role-aware setup block 1 — execution record

October 23, 2026. Branch: `feat/role-aware-setup-block1`, based on `develop`
at `f7d966c`. Existing checkout was clean. No new worktree or UI-plan change.

## Scope rulings

- Block 1 configures/reviews existing components; full installer runtime
  extraction, downloads, services, native packaging, pairing management and the
  Flutter wizard remain later milestones. No pretend install/Ready outcome.
- `coordinator`, `execution`, `combined` are product roles. Existing deployment
  and Rust client/server names retain their established semantics.
- Core package lives under `src/odessy/setup/` to ship in a normal wheel; it does
  not import the source-only root installer or optional UI dependencies.
- Functions are synchronous for this headless foundation. The future desktop
  bridge must offload blocking probes, rather than calling them on its UI loop.
- Configuration export is explicit and create-only. Existing-file mutation,
  merge/revision handling and credential-vault persistence are not claimed.
- Default timeout is 3 seconds per network I/O with elapsed checks between body
  reads, not a hard whole-request deadline. Response/model-count bounds apply.

## Evidence

- Initial role/planning/discovery/CLI regressions failed on missing setup package.
- Changed-plan, foreign-platform export and ignored disabled-voice tests failed
  before the corresponding guards were added.
- Strict schema tests rejected false-valued endpoint/credential fields after a
  reproduced gap. Fixed Windows test-case IDs for oversized JSON fixture data.
- Independent read-only review found foreign-platform verification probing local
  state; reproduced with a regression, then rejected it before any local checks.
  Review also identified the documented network timeout overrun limitation.
- 56 setup regressions passed before final documentation; full-suite and wheel
  verification results are recorded in the final validation section below.

## Validation and remaining work

Full Windows Python suite before the final lightweight bootstrap change:
**2,107 passed, 13 skipped, 101 subtests passed in 403.83 seconds**. After adding
the bootstrap, final setup/installer-application checks: **72 passed** (including
57 setup cases). A full-suite rerun after that final small change was not performed.

Built the wheel in pip's isolated build environment; extracted it into a fresh
temporary install layout outside the checkout, confirmed imports came from that
layout and ran `python -m odessy setup --role execution --non-interactive`
successfully. Direct wheel-ZIP imports are not supported by existing file-based
runtime resources. An earlier extracted-layout attempt via the full agent CLI
also exposed missing existing `execution/contract.json` wheel data; the new
lightweight setup entrypoint bypasses that dependency. This verifies installed
setup, not the broader normal-runtime wheel packaging, which remains later work.
The wheel retains the project's preexisting `0.3.0` metadata; version release
preparation is not changed in block 1.

No live model download, paid
provider call, enrollment, grants or background service installation was performed.
Existing installer behavior remains intact; the new setup command is an opt-in
configuration foundation. UI block 2 and actual installer/platform acceptance are
not completed by this work.
