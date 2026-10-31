# r/selfhosted — DRAFT

> **DRAFT — the maintainer rewrites this in their own voice before posting.**
> Intended timing: mid-November, after outside testers have run it. Not a release-day
> announcement; no publication date is promised.

**Title:** Odessy — a self-hosted agent runtime where the model proposes and your policy decides (early preview, source only)

**Body:**

I have been building an agent runtime for small edge machines and I would like outside
eyes on it. It is source-only, unstable, and I am deliberately not calling it ready.

The thing I wanted and could not find: an agent where the model never grants itself
permission. It proposes an action; a policy layer written in Python decides whether that
action is allowed, needs a human, or is refused. The approval is bound to the *exact*
operation shown to you — that is why typing "yes" does nothing and the acceptance phrase
has to be the one on screen.

The setup I am building toward is two machines. A small box (I have been using a Jetson
Orin Nano 4GB) holds the conversation and the model. The tools live on a laptop. You ask
the box a question out loud, it proposes one command, the laptop shows you that command
and waits, you approve, it runs there, and the answer is spoken back.

How that actually stands today, without rounding up:

- Windows 11 x64, from source, is the only path I have tested end to end.
- Linux is covered by CI and is experimental. macOS is untested.
- The Jetson is experimental; local models on 4 GB are slow and unreliable — I measured
  about 14.23 tok/s on one trial and then no final answer at all.
- Pairing two machines over mutual TLS works in a qualification run (enrolled certs,
  11 tools listed, a proof file read over the LAN).
- Spoken approval is **not** qualified. I said "approve this action" and Whisper heard
  "Approve this option", so Odessy refused. Correct behaviour, bad experience.
- There is no installer. You build from source.
- Nine production npm advisories are disclosed in the release notes, not hidden.
- Monitoring (Go) exists but is experimental and not a supported shipped feature.

Stack, for the curious: Python for the runtime and policy, TypeScript/Node for the MCP
tool server, Rust for pairing and mTLS, Flutter for the desktop client, Go for the
monitoring experiment.

Why post it here: most of what I need is not code. I need measurements on hardware I do
not own (DGX Spark, RTX 50-series and Apple Silicon are on the wish list as *measurements*,
not support claims), documentation fixes from people who just tried to set it up, and
accessibility reports on the desktop client.

Quick start (from source): <placeholder: repository URL>/blob/develop/README.md#from-source-quick-start

Starter issues are drafted and unclaimed: <placeholder: repository URL>/blob/develop/docs/contributor-issues.md

Looking for feedback and contributors. Happy to answer hard questions about what does
not work.
