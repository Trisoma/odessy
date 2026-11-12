# `odessy.ssh` — SSH identity/config only

The Go monitoring service owns the SSH computer's polling, freshness, history and
events. Python only resolves the selected OpenSSH alias and trusted host key before
starting that service, and projects the resulting host record for the desktop.

| Piece | Owner |
|---|---|
| `identity.py` | `ssh -G` alias resolution, `known_hosts` fingerprint, restrictive SSH argv. No connection while resolving. |
| Go `internal/link/ssh.go` | `Inventory`/`Heartbeat`/`Sample` over one bounded `ssh` child; no polling loop, history or event bus. |
| Go `internal/fleet`, `httpapi`, `events` | The existing scheduler, freshness, pagination, history and one cursor/resync stream. |
| `monitoring/supervisor.py` | Verifies identity, passes the SSH config in the private bootstrap, owns the Go service lifecycle. |
| `monitoring/api.py` | Public monitoring JSON projection plus `GET /v1/ssh/computer`. |

Execution is **not** implemented; the capability matrix always says `execution=false`.

## Configuration

| Variable | Meaning |
|---|---|
| `ODESSY_SSH_MONITORING=on` | Enables the SSH computer and status polling |
| `ODESSY_SSH_COMPUTER=on` | Enables inventory only; no status polling |
| `ODESSY_SSH_ALIAS` | Existing OpenSSH `Host` alias (required) |
| `ODESSY_SSH_HOST_ID` | Optional `host_` + 64 lowercase hex; default is the alias digest |
| `ODESSY_SSH_DISPLAY_NAME` | Optional display name |
| `ODESSY_SSH_CONFIG` | Optional explicit `ssh -F` file |
| `ODESSY_SSH_BIN` | Optional absolute `ssh` path; otherwise found on `PATH` |
| `ODESSY_MONITOR_BIN` | The existing Go monitor executable |

The host key must already be in `known_hosts`; an unknown key refuses before the Go
service starts. No key is copied, no config is edited, and agent forwarding is disabled.

## Check

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_ssh_connection.py
cd services/monitoring; go test ./...
```

The Python tests use a fake monitor; no SSH host or model key is required. A live check
needs an existing alias, `known_hosts` entry and working key/agent. The Go tests cover
inventory/heartbeat/sample and the shared event/pagination behavior.
