# Show HN — DRAFT

> **DRAFT — the maintainer rewrites this in their own voice before posting.**
> Intended timing: mid-November, after outside testers have run it. Not a release-day
> announcement; no publication date is promised.

## Title (pick one, ≤80 characters — Show HN convention is "Show HN: <thing>")

1. `Show HN: Odessy – an agent runtime where the model proposes and policy decides`
2. `Show HN: Odessy – self-hosted agents for edge boxes, with approval bound to the exact command`

## First comment

Author here. Source only, unstable, and I would rather lead with what does not work.

Odessy is an agent runtime for small edge machines. The one design decision that
matters: **the model proposes, Odessy decides.** A model emits an action; a Python policy
layer decides allow / confirm / deny and picks the isolation mode for the process. The
approval you give binds to the exact operation displayed, in that session, for that
request — so a bare "yes" authorizes nothing, and a grant covers exactly one reviewed
workspace. Revoking a paired machine terminates an open session rather than the next
request, and the host will tell you an in-flight outcome is now unknown instead of
guessing.

Five languages, each doing one job: Python for the loop, policy and model adapters;
TypeScript/Node for the MCP tool server (files, search, git, terminal) which executes but
does not decide; Rust for enrollment and mutual TLS between machines; Flutter for the
desktop client, which is presentation only; Go for an experimental monitoring
aggregator that is not a supported shipped feature.

Status, honestly:

- Windows 11 x64 from source is the only path tested end to end. Linux is CI-tested and
  experimental. macOS is untested. No minimum hardware spec exists.
- The Jetson Orin Nano 4GB is experimental and local models on it are unreliable: one
  Q8 trial generated at about 14.23 tok/s on a 512-token allowance and then produced no
  final answer; 15-GPU-layer requests failed to allocate 1.1 GB.
- Pairing two machines works in a qualification run (mTLS, real tool server, proof file
  read over the LAN), but the combined voice→propose→approve→execute→speak chain has
  never been demonstrated as one run.
- Spoken approval is not qualified. "Approve this action" was transcribed as "Approve
  this option" and was correctly refused. Fail-closed, bad UX.
- Isolation is host-dependent and partial on Windows; without Node.js `terminal.run` is
  unconfined. Do not call it a sandbox.
- Nine production dependency advisories are disclosed in the release notes rather than
  quietly patched for a demo.

Why I am posting before it is finished: the gaps are mostly things other people can
close. Measurements on hardware I do not own (DGX Spark, RTX 50-series, Apple Silicon —
as measurements, not support claims), documentation fixes from people who just tried to
install it, accessibility reports on the desktop client, and offline speech examples.

From-source quick start: <placeholder: repository URL>/blob/develop/README.md#from-source-quick-start

Architecture map (one page): <placeholder: repository URL>/blob/develop/docs/ARCHITECTURE.md

Unclaimed starter issues: <placeholder: repository URL>/blob/develop/docs/contributor-issues.md

Looking for feedback and contributors — especially from anyone who has run small models
on edge hardware and can tell me where my numbers are wrong.
