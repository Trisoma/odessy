# Shared interactions and Windows–Jetson pairing qualification

October 23, 2026. Runtime source snapshot: `218b3b78903bf844a11d9634e9d3cbe0f1fb02a1`.
Testing only; no runtime fixes, release tag or sign-off.

Deployment acceptance decision, October 23: physical echo/pre-cue trials are
deferred until the complete software is deployed. Software timing/isolation
regressions pass; physical echo/pre-cue admission and open-speaker operation are
not qualified by WAV injection. This does not block ongoing development.

## Shared approval/clarification paths

Focused Windows pytest run with `ODESSY_LINK_E2E=1`: **106 passed in 58.53 s**.
Covered broker, combined ordinary/escalation interaction, user questions, spoken
answer parsing from text, action/job binding, hybrid console, paired-host policy,
and opt-in real Rust relay/Node integration tests.

This includes exact-phrase approval, rejection of bare yes, channel/risk checks,
clarification choices, cancellation/expiry and stale bindings. It does not prove
real STT phrase recognition, physical prompt echo isolation or microphone timing.

## Rust native/platform checks

- Windows Rust crate: **65 passed**, built debug binary.
- Linux ARM64 Rust crate: **62 passed**, built native ARM64 binary.
- ARM64 used an isolated Rust/Cargo toolchain under
  `/tmp/odessy-rust-qualification`, without modifying system compiler or shell PATH.
- Linux build emitted unused-import and dead-code warnings; no claim of clean
  Linux clippy is made.

## Real cross-host checks

Created temporary identities and a one-file workspace under the outer laptop
`.qualification/pairing-crosshost-20261023` and Jetson `/tmp`. Existing trust stores,
user source/configuration and normal workspaces were not changed. Public bundles
were exchanged over pre-existing trusted SSH, independently of the Rust TLS path.

Windows served on `10.1.1.1:18879`; Jetson connected directly over LAN, with no SSH
tunnel for this test. Verified:

1. Wrong enrollment fingerprint is rejected.
2. Enrollment alone grants no MCP workspace access; the host refuses admission.
3. A grant for a different directory is refused before the Node server is used.
4. With the exact workspace grant, Jetson Python → ARM64 Rust → Windows Rust →
   real Node tool listing and proof-file read succeeds.
5. `../windows-host.json` read returns an outside-workspace error.
6. Revoking the temporary Jetson peer terminates an already-open session; its next
   proof-file request fails with MCP `Connection closed`. The host explicitly warns
   that an in-flight outcome would be unknown. No termination-latency metric was
   established. Rust reports unexpected TLS EOF during this revocation cleanup.

## Confirmed discovery CLI defect

`serve --advertise` is rejected as `unsupported option`, despite appearing in the
usage text. `commands::serve` passes all arguments to a parser whose allowed list
excludes `--advertise`; `connect --discover` has the equivalent structure. Thus
the latter defect is source-confirmed, while the serve rejection was reproduced.
Explicit-address TLS pairing works; discovery cannot be signed off until bare
switch parsing is fixed and exercised by CLI regressions. Runtime code was not
edited as part of these tests.

## User-paced WAV boundary smoke test

Discovery CLI follow-up: fixed on `develop` by parsing documented bare switches
explicitly. `serve --advertise` and `connect --discover` now reach normal command
validation. Unknown/duplicate options, values attached to switches, and missing
option values remain rejected. Regression tests reproduced both original failures
before the fix; the full Windows Rust suite then passed: **68 passed, 0 failed**.
This fixes CLI admission; live LAN mDNS discovery has not been re-qualified.

Subsequent live retest passed on the direct Windows–Jetson LAN with rebuilt
Windows and ARM64 binaries. Windows `serve --advertise` advertised the temporary
host; Jetson `discover --seconds 6` resolved `10.1.1.1:18879`. The Jetson registry
was verified to contain `address: null`, so `connect --peer laptop --discover`
could not fall back to a saved address. It completed enrolled-certificate mTLS,
initialized the real Node MCP server, listed 11 tools, and read `proof.txt` with
the expected content. Relay exited 0. All three new CLI regressions also passed
on ARM64. The temporary peer was revoked and qualification listener stopped.

Host cleanup reported `taskkill could not confirm termination` because the Node
PID was no longer found. This is a separate cleanup-reporting limitation, not a
discovery or tool-call failure; no clean-cleanup claim is made from relay exit 0.

Record these in separate quiet 5-second mono 16 kHz WAVs on Jetson:

| Filename | Say exactly | Expected follow-up test |
|---|---|---|
| approve.wav | approve this action | Medium accepted; high/escalation refused |
| yes.wav | yes | Must not authorize |
| deny.wav | deny | Must decline |
| stop.wav | stop | Owning turn cancelled |
| question.wav | Odessy | Answer to an Odessy/Other choice, not authorization |

Keep files in `/home/jaimef/odessy-qualification-audio/`. Real Whisper → trusted
voice parser → real broker can be exercised against harmless pending fixtures;
no destructive tool is needed. Recorded-file tests complement but cannot prove
physical playback-echo exclusion and pre-cue capture timing. Those remain separate
small live boundary checks if full physical acceptance is claimed.

### Recorded-file results after user re-recording

Ran the five recordings through the installed `ggml-tiny.en.bin` Whisper adapter,
then the actual wake session interaction submission and real interaction broker.
Seven fixture checks completed without executing tools or invoking a model turn.

| Recording | Actual Whisper transcript | Actual handling |
|---|---|---|
| approve.wav | Approve this option. | Remains pending; not the exact acceptance phrase |
| yes.wav | Yes. | Remains pending; does not authorize |
| deny.wav | I deny. | Remains pending; current parser accepts deny/no/reject |
| stop.wav | Stop this! | Remains pending; current cancellation grammar does not include this variant |
| question.wav | Odyssey. | Returned as human information in a free-text clarification |

High-risk and sandbox-escalation fixtures also remained pending for the recorded
approval transcript. Because that transcript was already invalid for medium risk,
these two WAV checks alone do not isolate stronger-channel enforcement; the earlier
exact-text regressions cover that distinction.

Safety remained fail-closed, but successful spoken authorization is **not yet
qualified**: Whisper changed “approve this action” to “Approve this option.” Natural
denial/cancellation variants are a confirmed usability limitation. The clarification
test used free text; it does not establish exact-choice recognition of “Odessy.”
No runtime code changed. Physical echo and pre-cue behavior remain untested here.

### Latest approval recording: successful recognition

Retested all five files after the user recorded approval again. Whisper now returns
`Approve this action.` The actual voice session and broker accept it for medium-risk
approval and leave high-risk and sandbox-escalation approvals pending. Bare `Yes.`
still cannot authorize. Thus the recorded approval now exercises the stronger-channel
boundary with a valid acceptance phrase, superseding the earlier approval-recognition
limitation above. All seven fixture checks completed successfully.

The other transcripts remain `I deny.`, `Stop this!`, and `Odyssey.`: denial and
cancellation variants still leave the request pending; the free-text clarification
returns the transcript as human information. Exact-choice recognition and physical
echo/pre-cue behavior are not established. No runtime code changed.

### Natural refusal/cancellation implementation and WAV retest

Implemented shared whole-response denial/cancellation phrases on `develop` for
wake sessions, one-shot voice, and typed pending interaction replies. Positive
approval still requires `approve this action`; no model interprets permission.
Negated cancellation such as `don't stop` does not stop the turn.

Windows affected regressions: **119 passed**, plus **36 passed** for voice
entrypoint/integration. New denial/cancellation regressions failed before the fix.
Replayed the five real WAV files using the updated isolated Jetson snapshot:
medium approval accepted; bare yes remained pending; `I deny.` declined;
`Stop this!` cancelled the owning turn; free-text clarification returned human
information; high-risk and sandbox-escalation approval remained pending. All seven
fixture checks completed. The deployed `~/odessy` checkout was not changed.
This supersedes the natural-phrase limitation above; echo/pre-cue qualification
and exact-choice recognition remain outside this recorded-file test.
