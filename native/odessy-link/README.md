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
| Per-user SQLite trust registry, capabilities, revocation, monotonic grant revision | Implemented (`src/trust.rs`) |
| Enrollment grants no execution access until `peers grant` | Implemented + tested |
| Newline-delimited framing, 8 MiB frame limit, 16 MiB buffered limit, partial frames, EOF discard, duplicate ids keeping numeric/string distinct | Implemented (`src/framing.rs`) |
| Mutual-TLS `serve`/`connect`, enrolled CA roots only, expected-SAN verification, mandatory client certificates, exact leaf-fingerprint match before forwarding, resumption disabled, 10 s handshake timeout | Implemented (`src/tls.rs`, `src/commands.rs`) |
| Requests answered in either direction release the requesting slot; 32 outstanding per direction, 33rd refused | Implemented (`src/relay.rs`), proven over the real two-process transport |
| Workspace grant enforced as canonical path equality at admission, per request, and in the revocation watcher | Implemented (`workspace_matches`) |
| Content-free journal with a hard per-session id allowance; no eviction of used or unresolved ids | Implemented (`src/journal.rs`); exhaustion requires a fresh session |
| Live-session termination on revocation | Implemented (1 s watcher) |
| Owned process **tree** termination (taskkill /T on Windows, process group on Unix) with explicit failure reporting | Implemented (`src/node.rs`) |
| DPAPI-protected identity + explicit ACL on Windows; `0600` creation on Unix; plaintext migration | Implemented (`src/store.rs`) |
| mDNS discovery as an address aid (`discover --seconds`, `serve --advertise`, `connect --discover`) | Implemented (`src/discover.rs`); never enrols or grants |
| Python paired-host adapter, fail-closed startup, per-dispatch trust recheck, approval metadata | Implemented in `src/odessy/tools/paired.py`, `runtime/agent_loop.py`, `config/doctor.py` |
| Real Python → Rust → Node call and two-process transport tests | Implemented as opt-in tests (`ODESSY_LINK_E2E=1`), run by the `pairing` CI job |
| Windows–Jetson, real-network and Linux/ARM64 qualification | **Not performed**; only Windows x64 → Windows x64 has been exercised |

Nothing on the plan is unimplemented in code. What remains is qualification on real
hardware and networks, which no unit test can stand in for.

A refusal is always explicit: exit code 1 with the reason, and no trust decision.

## Usage

```text
odessy-link init
odessy-link identity export --out <bundle>
odessy-link pair add --alias <name> --certificate <bundle> --expect-sha256 <fingerprint> [--address <host:port>]
odessy-link peers list [--json]
odessy-link peers grant <peer> --capability mcp --workspace <path>
odessy-link peers revoke <peer>
odessy-link discover [--seconds <n>]
odessy-link serve --listen <address> --workspace <path> --node-server <entry> [--advertise]
odessy-link connect --peer <alias> [--address <host:port>] [--discover]
```

`ODESSY_LINK_MAX_SESSION_ITEMS` caps how many request ids one session may use
(default 4096). Reaching it ends the session; ids are never evicted to make room.

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
cargo clippy --all-targets -- -D warnings
```

57 tests on Windows. Beyond the identity, trust, storage, framing, journal and relay
cases, they include real loopback TLS handshakes: enrolled hosts complete mutual TLS
and each side reports the fingerprinted leaf it was enrolled with, an unenrolled
identity is refused with no application byte exchanged, an expired certificate and a
wrong-SAN certificate are refused, and a **sibling leaf under the same enrolled CA**
validates against the chain and is still refused by `admit_peer`.

The two-process transport itself is proven from Python rather than from Rust, because
the point is that two operating-system processes, a real socket and a real child Node
server are involved:

```powershell
ODESSY_LINK_E2E=1 python -m pytest tests/test_paired_transport.py tests/test_paired_integration.py -q
```

That suite sends 96 sequential requests through the relay and counts what the child
executed, sends 33 requests at once and shows the 33rd is refused and never executes,
replays a duplicate id, exhausts a session's id allowance, refuses a grant for another
directory, revokes a live session, and checks the child's descendant process is gone.

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
