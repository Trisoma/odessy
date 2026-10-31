# LinkedIn — DRAFT

> **DRAFT — the maintainer rewrites this in their own voice before posting.**
> Intended timing: mid-November, after outside testers have run it. This is not a
> release-day announcement and no publication date is promised.

I have been building Odessy, an open-source agent runtime for edge machines. Source is
public now; I am looking for feedback and contributors before I call it anything.

The scenario I am building toward: you speak to a small edge computer on a bench —
*"what is using the disk?"* — and the operation it proposes does not run there. It
travels to a paired laptop, which shows you the exact command and waits. You approve
it. It runs. The answer is spoken back.

The rule underneath that is simple: **the model proposes, Odessy decides.** Python owns
authority and policy; Node owns the MCP tools; Rust owns identity and mutual TLS between
machines; Flutter is presentation only; Go is an experimental monitoring piece. An
approval binds to the exact operation on screen, which is why a bare "yes" authorizes
nothing.

What is honest about the status: Windows 11 x64 from source is the only path tested end
to end. Linux is CI-tested and experimental. The Jetson Orin Nano 4GB is experimental
and local models on it are genuinely unreliable. macOS is untested. Spoken approval is
**not** qualified — when I said "approve this action" out loud, Whisper heard "Approve
this option" and Odessy correctly refused. There is no installer, and nine production
dependency advisories are disclosed in the release notes.

I would rather publish that paragraph than a demo where the good six seconds worked.

If you run edge hardware — especially DGX Spark, RTX 50-series or Apple Silicon, none of
which I have measured — a measurement report is the most useful thing you could send.
If you write docs, or care about desktop accessibility, there are drafted starter issues
waiting for an owner.

Build from source: <placeholder: repository URL>/blob/develop/README.md#from-source-quick-start

Looking for feedback and contributors. <placeholder: maintainer name>
