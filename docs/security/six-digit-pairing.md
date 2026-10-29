# Experimental six-digit pairing

October 29, 2026. The maintainer explicitly replaced the earlier M0 pre-implementation
gate with a small experimental pairing flow. Historical M0 documents are retained
as review history, not as the current runtime contract.

The host shows six digits; the joining computer enters them; the host explicitly
approves the authenticated device/workspace. Python outbound execution consent stays
separate. Existing Rust mTLS identity validation and revocation are unchanged.

The insecure public password-hash exchange and duplicate fake protocol/profile
were removed. One default implementation uses pinned `spake2 0.4.0` and
`snow 0.9.6` (`Noise_NNpsk0_25519_ChaChaPoly_BLAKE2s`). SPAKE2 roles are fixed;
length-prefixed protocol/window/ordered PAKE messages are bound in the Noise
prologue. Both directions verify encrypted confirmation before exchanging identity
data. Noise handles directional encryption keys, counters and authentication.
The code is never transmitted or hashed into a public password verifier.

Certificate possession uses the actual parsed P-256 leaf SPKI (`x509-parser 0.16.0`),
replacing the DER byte-pattern scan. No trust/workspace grant is written on the
host until local approval. Entering the code consents to saving the authenticated
host identity on the joiner, without incoming or outbound execution grants.

Expiry, three global attempts, two connection slots, bounded records, absolute
five-second frame deadlines and connection timeout are retained. Host authentication
cannot complete after its pairing window expires. Initial identity creation reuses
existing protected identity storage and never overwrites an existing identity.
The executor endpoint uses the authenticated peer address and fixed port 7443.

The former `test-profile` Cargo feature / legacy CLI alias remain no-op compatibility
inputs only; they do not select another exchange or bypass checks. Existing staged
commit/recovery, authorization and executor code are retained, not rewritten.

Verification: normal-build Rust suite passed **111 link tests, 5 executor tests,
3 two-process CLI tests**. Correct code, wrong code, three-attempt exhaustion,
explicit workspace approval, idempotency and identity substitution checks passed.
An additional absolute-frame-deadline correction was followed by another suite run.
A separate technical reviewer found no authentication blocker in the inspected
PAKE/Noise integration; its availability finding was corrected. This is not a formal
third-party cryptographic audit or interoperability certification. No claim of
RFC9382 or Magic Wormhole wire compatibility is made.

**Deployment:** these are source changes. Earlier executor ZIPs still contain the
disabled implementation and must not be used to judge this change. A rebuilt Rust
binary is required on both computers. No fresh Windows/ARM64 release archive or
physical Windows–Jetson acceptance was performed in this task.

Sources: https://docs.rs/spake2/0.4.0/spake2/ and
https://noiseprotocol.org/noise.html .
