# odessy-link

Authenticated host pairing and MCP transport for Odessy. This crate is the Rust
half of `pairing-plan.md`; Python keeps action policy and Node keeps its argument
and confinement checks.

## Status

| Plan item | State |
| --- | --- |
| ECDSA P-256 installation CA + connection certificate, DNS SAN, 5y/1y validity | Implemented (`src/identity.rs`) |
| Enrollment bundle export with no private keys, full SHA-256 fingerprint | Implemented (`identity export`) |
| Operator-confirmed fingerprint enrollment (`pair add --expect-sha256`) | Implemented (`src/trust.rs`) |
| Per-user SQLite trust registry, capabilities, revocation | Implemented (`src/trust.rs`) |
| Enrollment grants no execution access until `peers grant` | Implemented + tested |
| Newline-delimited framing, 8 MiB frame limit, 16 MiB buffered limit, partial frames, EOF discard, duplicate-id table keeping numeric/string ids distinct | Implemented (`src/framing.rs`) |
| Mutual-TLS `serve`/`connect`, enrolled CA roots only, expected-SAN client verification, mandatory client certificates, exact leaf-fingerprint match before forwarding, resumption disabled, 10 s handshake timeout | Implemented (`src/tls.rs`, `src/commands.rs`); verified by a loopback handshake test and a two-process relay run |
| 32 outstanding requests per direction, duplicate detection, cancel notifications pass through | Implemented (`src/relay.rs`) |
| One owned Node subprocess per authenticated connection, fixed executable/env/workspace, killed on session end | Implemented (`src/node.rs`) |
| 16 MiB per-direction buffer enforced while reading, stalled consumer stops the read | Implemented via `FrameCodec` in `src/relay.rs` |
| Content-free journal (session, direction, request id, state, timestamps; bounded retention) | **Implemented** (`src/journal.rs`), sqlite-backed |
| Revocation before admission; per-request recheck during a live session | **Implemented**: `serve` refuses a peer with no grant and re-checks the registry before each request |
| Terminating an idle session the instant a grant is revoked | **Not implemented** — the session ends at its next request or when the peer disconnects |
| mDNS discovery | **Not implemented** |
| Python paired-host adapter (`ODESSY_PAIRED_HOST`), `doctor` reporting, approval-binding integration | **Implemented** in `src/odessy/tools/paired.py`, `cli.py`, `config/doctor.py`; see `docs/reference/host-pairing.mdx` |
| Windows DPAPI + restrictive ACL for identity keys | **Not implemented**; Unix uses `0600` |

Anything not implemented either fails loudly or is absent; none of it pretends to work.

Anything not implemented returns exit code 1 with an explicit message and makes no
trust decision.

## Usage

```text
odessy-link init
odessy-link identity export --out <bundle>
odessy-link pair add --alias <name> --certificate <bundle> --expect-sha256 <fingerprint> [--address <host:port>]
odessy-link peers list
odessy-link peers grant <peer> --capability mcp --workspace <path>
odessy-link peers revoke <peer>
odessy-link serve --listen <address> --workspace <path> --node-server <entry>
odessy-link connect --peer <alias> [--address <host:port>]
```

`serve` listens with mutual TLS and admits only certificates whose exact leaf
fingerprint is enrolled **and** whose alias holds the `mcp` capability, then starts
one Node server in the host-configured workspace. `connect` speaks MCP on
stdin/stdout, verifies the peer's leaf fingerprint against the enrolled value, and
refuses to forward anything when it differs. `ODESSY_LINK_NODE` selects the node
executable; the rest of the child environment is fixed.

State lives in `$ODESSY_LINK_STATE_DIR`, or `%APPDATA%\odessy-link` on Windows and
`$XDG_DATA_HOME/odessy-link` (else `~/.local/share/odessy-link`) elsewhere:
`identity/identity.json` for keys and `trust.sqlite3` for enrollments.

Re-enrolling an existing alias replaces its certificate and **drops its grants**,
so a swapped certificate never inherits execution access.

## Tests

```powershell
cargo test
```

32 tests. Beyond the identity, trust and framing cases, they include two real
loopback TLS handshakes: enrolled hosts complete mutual TLS and each side reports
the fingerprinted leaf it was enrolled with, and an unenrolled identity is refused
by client-certificate verification with no application byte exchanged. The relay is
covered against in-memory duplex streams for split reads, duplicate ids, the 33rd
outstanding request, and an incomplete frame at EOF.

Manual two-process check performed on Windows:

1. two state directories each `init` + `identity export`;
2. each `pair add` the other's bundle with the matching fingerprint;
3. `peers grant <peer> --capability mcp --workspace <dir>` on the host;
4. `serve --listen 127.0.0.1:<port> ... --node-server <stub.js>`;
5. `connect --peer <alias>` with `{"jsonrpc":"2.0","id":1,"method":"tools/list"}` on stdin.

The response arrived over the authenticated connection
(`{"jsonrpc":"2.0","id":1,"result":{"echo":"tools/list",...}}`), a plain-TCP probe
to the same port was refused at handshake, and a wrong fingerprint was refused at
enrollment. This is a stub Node server, not Odessy's real tool server.
## Comparison with the plan

`pairing-plan.md` requires both operators to compare the full fingerprint through
an independently trusted channel. Nothing in this crate can verify that a human
actually did the comparison; it only makes the comparison mandatory in the CLI and
rejects a bundle whose certificate does not hash to the value supplied.
