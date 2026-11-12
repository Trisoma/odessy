# SSH compatibility spike (prompt 17)

A bounded probe of one **Connect computer using SSH** journey, not a production transport.
The production slice is prompt 18; the UI is 19. The agreed interface is
`restructure/doc/ssh-integration-contract.md` and the evidence is
`restructure/doc/reports/17.md` (both in the prompt pack, outside this repository).

A live read-only run on the Jetson `10.1.1.2` (2026-11-11) proved the SSH login and one
status sample (login 707 ms, status 402 ms). Execution was not available there: its
Odessy is `0.5.0.dev0` and has no `odessy.tools.stdio_server`.

## What this proves, and what it does not

| Check | One Windows laptop | Needs the real host |
|---|---|---|
| List concrete aliases and follow `Include` | yes | — |
| Resolve an alias with `ssh -G` (no connection) | yes | — |
| Read the trusted host key from `known_hosts` (no connection) | yes | — |
| Build the restrictive `ssh` argv and POSIX remote command | yes | — |
| Fixture stdio round trip (`ServerConfig` + `mcp.Client`) | yes | — |
| SSH login, `transport` capability | no | yes |
| Read-only status sample (`monitoring`) | parser only | yes |
| Remote tool listing (`execution`) and workspace acceptance | no | yes, and only with a current remote Odessy |
| Controlled task and process-tree-confirmed Stop | no | yes |

The fixture substitutes only the `ssh` executable. It is **not** evidence of SSH,
remote execution or Stop. The capability matrix starts all-false and is filled only by
the live evidence above.

## Fixture check (no model, no remote hardware, no network)

```powershell
.\.venv\Scripts\python.exe experiments\restructure\ssh_compatibility\smoke_p17.py --self-test
.\.venv\Scripts\python.exe -m pytest -q tests\test_ssh_compatibility.py
```

The smoke runs the production `ServerConfig` + `mcp.Client` stdio path against a local
fixture server: initialize, `tools/list`, `echo`, a fixed status tool, and a cancelled
`slow` call. It reports the cancellation as unclean (`ExceptionGroup` while closing) and
the remote outcome as unknown; that is deliberate.

## Live steps (explicit, read-only until `--execution`)

Add `--config <file>` when the alias lives in a non-default `ssh_config`; otherwise the
user's normal config is used.

```powershell
# identity only; resolves the alias and reads known_hosts, no connection
python experiments/restructure/ssh_compatibility/smoke_p17.py --alias <alias> --inspect

# one bounded login (printf only)
python experiments/restructure/ssh_compatibility/smoke_p17.py --alias <alias> --test

# one fixed read-only sample: load average + root disk
python experiments/restructure/ssh_compatibility/smoke_p17.py --alias <alias> --monitor

# execution: the remote needs a current Odessy that provides odessy.tools.stdio_server
python experiments/restructure/ssh_compatibility/smoke_p17.py --alias <alias> `
  --workspace <absolute-remote-dir> --remote-python <absolute-remote-python> --execution

# one controlled task, then a Stop attempt whose outcome must be checked remotely
python experiments/restructure/ssh_compatibility/smoke_p17.py --alias <alias> `
  --workspace <absolute-remote-dir> --remote-python <absolute-remote-python> --task --stop-test
```

Then, on the remote host:

```bash
pgrep -af 'sleep 30'
```

If that returns a process, Stop did not end the remote tree; do not report it as clean.
No `sshd`, `authorized_keys` or `known_hosts` file is written by any of these commands,
and no unknown host key is accepted.

## Constraints

- Remote shell is POSIX. Windows-as-remote is out of scope (different quoting and
  process ownership).
- A password/passphrase prompt is an error (`BatchMode=yes`); this path is for existing
  keys/agent.
- The remote tool server is `python -m odessy.tools.stdio_server` from a **current**
  Odessy environment (the checked Jetson `0.5.0.dev0` lacks it); this spike installs
  nothing remotely.
- `Confirm` decisions are refused by the stdio server (it has no consent channel). That
  is a limitation of the first slice, not a reason to loosen the policy.
