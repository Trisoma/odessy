# Why Odessy

> **DRAFT — the maintainer rewrites this in their own voice before publishing.**
> Nothing here is a publication promise; intended timing is mid-November, after
> outside testers have had a chance to run it.

Every agent framework I looked at answered the same question well: how does a model
turn a goal into a series of tool calls? Almost none of them answered the question I
actually had, which was: *who decides?*

Odessy is my attempt to put the decision somewhere other than the model. The model
proposes. Odessy decides. That inversion is the whole project, and everything else —
five languages, the pairing protocol, the desktop client — exists to keep that
inversion honest.

## The shape of it

Odessy is a local-first runtime, not a hosted service. It speaks five languages
because each one is good at one thing and I did not want any of them to sprawl:

**Python owns authority.** The agent loop, sessions, model adapters, and — the part
that matters — the policy layer. When a model proposes `terminal.run` with a command,
Python evaluates it against rules, decides `allow` / `confirm` / `deny`, and decides
which isolation mode the resulting process gets. A model that proposes something
dangerous gets refused by code it never touches.

**Node owns tools.** The existing MCP tool server is TypeScript: files, search, git,
terminal. It is the execution surface, and it is deliberately dumb about policy — it
executes what it is given and reports structured results. It also redacts credential-shaped
text from output *before* any of it is persisted or shown.

**Rust owns identity and transport.** `odessy-link` handles enrollment, grants, and
mutual TLS for connecting two machines. That is a separate trust boundary from the
model, and it needed code that could not be talked out of a decision by a prompt.

**Flutter owns the screen.** The desktop client is presentation and typed commands.
It is explicitly *not* execution authority; per-model-request approval is bound to the
request the user is looking at, not to text on the screen.

**Go owns monitoring, and is the honest exception.** It aggregates telemetry for a
local dashboard. It is experimental, partially integrated, and not a supported shipped
feature. I would rather say that plainly than dress it up.

## One decision, and what it cost

The decision I would point at is **approval binding**.

The obvious design is a confirmation dialog: the agent wants to run something risky,
you see a summary, you click Yes. It is familiar, and it is wrong in a way that is
hard to see. Between the summary and the execution there is a gap, and anything that
can write to that gap — a model that retries with different arguments, a stale
request, a confused user — inherits your approval.

So in Odessy an approval is bound to one displayed operation, in one session, with
one request identity. Concretely, the qualification runs on October 23, 2026
established that:

- a bare "yes" does not authorize anything; the acceptance phrase must be exact;
- a grant covers exactly one reviewed workspace, and a request for a different
  directory is refused *before* the tool server is reached;
- enrollment alone grants no workspace access — the host refuses admission;
- revoking a peer terminates an already-open session, and the host warns that an
  in-flight outcome is now unknown.

That last one is the trade-off in miniature. Binding approval tightly means the
system sometimes tells you "I cannot confirm what happened" instead of guessing. The
cost is real: a revoked session can leave an ambiguous in-flight result, and the CLI
is unforgiving about phrasing.

And the cost showed up exactly where I expected it to, which is speech. When the
maintainer said "approve this action" out loud, Whisper transcribed "Approve this
option." Odessy refused to authorize it — correctly, since it was not the exact
phrase — and the request stayed pending. Fail-closed is the right behaviour and a
terrible user experience. **Spoken authorization is therefore not qualified**, and I
would rather ship a preview that says so than one that quietly loosens the rule until
voice feels smooth.

The second trade-off is placement. Binding to an exact operation means the approval
has to belong to a specific authority layer, so the desktop client cannot approve
things on its own behalf, and the Node tool server cannot decide anything. That
concentrates a lot in Python. If Python is wrong, a lot is wrong. I think that is the
right place to concentrate risk — one reviewable policy file beats five languages each
doing 20% of the security — but it is a bet, not a proof.

## What it costs to be honest

The hard part of this project has not been writing an agent loop. It has been refusing
to let the documentation outrun the evidence.

The release notes say Windows 11 x64 from source is the only path tested end to end.
Linux is CI-tested and experimental. The Jetson Orin Nano 4GB is experimental, and
local models on it are genuinely unreliable — a Q8 trial produced about 14.23 tok/s on
a 512-token allowance and then no final answer at all, and 15-GPU-layer requests
failed to allocate 1.1 GB. The test suite on a clean snapshot was 1,970 passed and
46 skipped in 183 s; skips are not passes. macOS is untested. There is no minimum
hardware specification, because I have not measured enough machines to write one.

The isolation story is the same shape. Odessy has a sandbox layer, and that is not the
same as being sandboxed. Coverage depends on the host: bwrap on Linux, a partial ACL
boundary on Windows, largely unavailable on the Jetson. Without Node.js,
`terminal.run` is unconfined. Every run reports the enforcement it actually achieved
rather than the one you hoped for.

I would rather write that paragraph than ship a demo video where the right six seconds
worked.

## Why "unstable"

This is v0.5.0-unstable: a developer preview, source-only, with nine known production
dependency advisories disclosed in the release notes. Much of the code was written with
AI assistance and is still being reviewed. The desktop first run is not guided. The
two-machine workflow is a qualification exercise, not a setup wizard.

Those are not disclaimers bolted onto the end. They are the current state, and the
project is more useful to me if the state is legible.

## What I am looking for

Odessy is aimed at edge machines — Jetsons, small boxes, a laptop that happens to be
on the same network — where you want a model to actually *do* something, and where you
are not willing to hand it root to find out whether it can.

If that shape interests you: the architecture map is one page, the starter issues are
drafts I would happily hand over, and the most useful thing anyone can contribute right
now is a measurement on hardware I do not own. DGX Spark, RTX 50-series and Apple
Silicon are on the qualification wish list — as measurements, not as support claims.

Build it from source, break it, and tell me where it lied to you. That is the feedback
that changes this project.
