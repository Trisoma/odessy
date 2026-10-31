# Odessy: the story we are building

> DRAFT — status narrative for the v0.5.0 unstable preview. A description of intent,
> not a support statement or a release commitment.

## The scenario

You are standing at an edge machine on a bench. You ask it out loud:
*"What is using the disk?"*

The machine hears you, transcribes the request, and a model proposes one exact
operation. That operation does not run where it was proposed. It travels to a paired
laptop, which shows you the precise command and waits.

You approve it there. The command runs on the laptop. The result comes back, and
Odessy speaks the answer.

That is the experience Odessy exists to make ordinary: a conversation with a machine
that has no business holding root, acting through a computer you are already sitting
at, with a human deciding at the one moment where deciding matters.

## What exists today, and what does not

The paragraph above is the *intended combined* experience. It is not a claim that the
combination is qualified, and those are different statements.

The parts have each been exercised somewhere:

- **Windows 11 x64, from source** — CLI and Flutter desktop chat with an existing
  Ollama model or a cloud API, policy-controlled tools with approve/reject, Stop and
  an audit log. This is the only path tested end to end by maintainers.
- **Jetson Orin Nano 4GB** — a live fake-model wake session completed two time-answer
  turns: microphone → wake → transcription → time tool → Kokoro → headset playback,
  with the user confirming clear spoken output (October 23, 2026).
- **Windows–Jetson pairing** — over a direct LAN, enrolled-certificate mTLS
  initialized the real Node tool server, listed 11 tools and read a proof file
  (October 23, 2026).

The chain has not been demonstrated as one run. Most sharply: **spoken authorization
is not yet qualified.** When the maintainer said "approve this action", Whisper
transcribed "Approve this option." — and Odessy correctly refused to authorize it.
That is the right failure, and it is still a failure.

## The model proposes; Odessy decides

A model never grants itself permission. It emits a proposal, and Python's authority
layer decides whether that proposal is allowed, needs a human, or is refused. The
approval you give is bound to the exact operation shown to you — not to an editable
summary of it — which is why a bare "yes" does not authorize anything, why a grant
covers exactly one reviewed workspace, and why revoking a peer terminates an
already-open session rather than the next request.

Two properties follow, and both are deliberate: a model that is confused is not a
model that is trusted, and an unanswered question is never an approval.

## More than one machine

Odessy is meant to be a coordinator plus one or more execution hosts. The Jetson can
hold the conversation and the model while the laptop holds the tools, or the two can
be the same box. When they are not, the trust boundary is the paired channel: Rust
owns identity, enrollment and mutual TLS; the plaintext LAN executor is a trusted-network
prototype and nothing more.

## Adapters are boundaries

Models, speech-to-text and text-to-speech are swappable adapters: a local Ollama model
or a cloud provider; Whisper.cpp for listening; Kokoro for speaking; openWakeWord's
"Hey Jarvis" weights for waking. None of them is baked into the runtime, none is
assumed present, and a missing adapter is expected to stay *visibly unavailable*
rather than quietly substituted. Voice assets ship separately and have their own
licence terms.

## Hardware we have actually measured

Tested hardware is exactly two machines: Windows 11 x64 and a Jetson Orin Nano 4GB
(ARM64). Older GPUs, DGX Spark, RTX 50-series and Apple Silicon are **proposed
qualification targets**, never supported or tested platforms. macOS is untested.

Numbers from October 23, 2026 on the Jetson Orin Nano 4GB, single user, one turn at a
time: a clean-snapshot test run of 1,970 passed, 46 skipped in 183.17 s; Whisper
transcribed a 20 s mono 16 kHz recording in 1.89 s; a Q8 model generated at about
14.23 tok/s on a 512-token allowance on a turn that produced no final answer; a
15-GPU-layer request failed to allocate 1,101,179,904 bytes. These are observations
under changing memory state, not a benchmark.

## What's next

Qualifying spoken approval end to end (exact-phrase recognition, not just fail-closed
refusal). Making local models reliable within 4 GB. Narrowing the gap between the
fragmented monitoring experiment and something supportable. Turning the two-machine
path into a repeatable setup rather than a qualification exercise.

## Where you can help

Documentation and first-run troubleshooting; accessibility reviews of the desktop
client; offline speech-to-text and text-to-speech examples that do not need a
network; build and packaging evidence; and hardware measurement reports — including
on the platforms we have *not* measured. Starter drafts live in
[contributor-issues.md](contributor-issues.md), and the build path starts at the
[from-source quick start](../README.md#from-source-quick-start).
