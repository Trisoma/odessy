# Contribute to Connect a computer: SSH monitoring

One computer model carries its connection method and capabilities. Existing paired
computers and SSH computers share the monitoring fleet; SSH does not replace pairing,
identity or execution grants. The monitoring DTO retains `execution=false`,
`workspace=false`. A separate temporary **Custom command execution** selection
exposes `ssh.run`, with exact approval in every mode, pinned endpoint/key and no
remote sandbox. See the [command loop](../../../docs/setup/ssh-computers.md#agent-command-loop-custom-preview).
Start with the offline fixtures below, then
use an existing trusted target for an explicitly selected live check.

## First useful status with an address or existing alias

After [development setup](../../../CONTRIBUTING.md#development-setup), connect the
desktop to the local Odessy service. No model key is needed for SSH setup. An
installed coordinator needs its optional monitoring component; a source checkout
needs the existing Go monitor binary. See configuration below if it is not found.

1. Enter `user@hostname` and an optional port/password, then **Connect**. A blank
   password uses the existing key/agent. Optional aliases live under **Advanced**
   and fill the same Address field. Never bypass host checking.
2. Connect verifies identity and login, then starts the existing read-only monitor.
   Inspect source timestamps, stale groups and supported capabilities. Linux load
   averages and root-disk capacity are available; unsupported groups remain unavailable.
3. Connections created by Connect last only for the running service. **Disconnect**
   removes that runtime selection and closes its password lease without writing
   configuration or revoking independent OS SSH access. Reconnect after restart.
4. **Optional command target:** explicitly select **Use for agent commands** after
   login. New Custom chats discover `ssh.run` through the existing tool interface;
   the existing approval broker binds each command/target. Disconnect revokes old
   server objects. LAN selection is mutually exclusive; pairing grants are separate.

See the [connection guide](../../../docs/setup/ssh-computers.md) for trust,
password lifetime, retained legacy configuration and GitHub's Git-only behavior.

## One-laptop fixture checks

From the repository root, using the prepared Python environment:

```powershell
python -m pytest -q tests/test_ssh_execution.py tests/test_ssh_fresh_bootstrap.py tests/test_ssh_connections_api.py tests/test_ssh_connection.py tests/test_ssh_onboarding.py tests/test_ssh_password.py
```

Then from `services/monitoring`:

```powershell
go test ./... -count=1
```

These are the existing production API, provider, scheduler and private-bootstrap
fixtures, not a new test framework. Python tests isolate SSH/configuration; Go
tests substitute bounded local workers. No paid provider, model download, microphone,
GPU, VM or remote host is required. Dependencies/SDKs must already be prepared.
For cache-only Go checks set `GOPROXY=off` and `GOTOOLCHAIN=local` in the shell;
a missing cache is a setup prerequisite, not proof of a product failure.

The Python bootstrap fixture walks aliases → inspect → test → explicit save →
first status. API fixtures cover unknown/changed-key refusal, failed-save rollback,
and removal. Go fixtures cover fixed commands, unsupported collectors, scheduler
recovery, provider coexistence and lifecycle. Fixtures do not establish real SSH
authentication, installed support, remote Stop or physical network recovery.

For the native simulated-computer pipeline, build the existing helper and test
child into an isolated folder (no remote host or downloads):

```powershell
# From services/monitoring
go build -o ../../.qualification/ssh-direct-connect/odessy-monitor.exe ./cmd/odessy-monitor
go build -o ../../.qualification/ssh-direct-connect/fakessh.exe ./internal/testsupport/fakessh
# Return to the repository root
$env:ODESSY_TEST_MONITOR_BIN = (Resolve-Path .qualification/ssh-direct-connect/odessy-monitor.exe).Path
$env:ODESSY_TEST_SSH_BIN = (Resolve-Path .qualification/ssh-direct-connect/fakessh.exe).Path
python -m pytest -q tests/test_ssh_temporary_integration.py
```

The two native cases exercise key/agent and password paths through authenticated
application routes, real Go monitoring, fresh samples, hostname display, disconnect
and restart. Password mode invokes the actual askpass helper and checks rejection
of an incorrect password. The SSH child simulates authentication/remote commands;
OpenSSH config resolution and isolated known-host lookup remain real. This is
not a live SSH server or hardware qualification. The test-only child is never a
production fallback or release helper.

## Minimal provider/collector seam and lifecycle

| Piece | Owner and contract |
| --- | --- |
| [`identity.py`](identity.py) | Resolve with `ssh -G`, read existing trusted keys, construct restrictive argv. Resolving alone does not connect; inspection may discover advertised keys for comparison, never accept them. |
| [`application/ssh_connections.py`](../application/ssh_connections.py) | Temporary address/port/password connect and runtime rollback; legacy saved APIs retained; no polling, key installation or trust-file edits. |
| [`monitoring/supervisor.py`](../monitoring/supervisor.py) | Verify selected identity, pass private bootstrap configuration, own Go process/reconfigure/close and bounded child teardown. |
| [Go `link.Link`](../../../services/monitoring/internal/link/worker.go) | `Inventory(ctx)`, `Heartbeat(ctx, HostRef)`, `Sample(ctx, HostRef, groups)`, `Close()`. Preserve host/generation, source times and unsupported quality; use bounded contexts/output. |
| [Go SSH provider](../../../services/monitoring/internal/link/ssh.go) | Fixed read-only commands through one bounded OpenSSH child per request. No second polling loop, arbitrary command API, history or event bus. |
| [Go composition](../../../services/monitoring/internal/link/composite.go) | Route paired and SSH providers by host identity; reject conflicts and close owned providers. One existing fleet owns scheduling/freshness/history/events. |
| [`monitoring/api.py`](../monitoring/api.py) | Authenticated public projection; Flutter consumes typed computer/capability/monitoring records. |

Start with `internal/link/ssh_test.go`, `composite_test.go` and the command's
private-bootstrap tests when extending a collector. Follow the [monitoring service
contract](../../../services/monitoring/README.md) and [schemas](../../../contracts/monitoring/README.md).
No remote collector installation is required by this SSH slice. Paired telemetry's
Rust worker and its independent monitoring permission remain active consumers.

## Configuration and failure recovery

Prefer the temporary Connect UI over manually setting every variable. These legacy persisted inputs remain supported; temporary Connect does not change them. Advanced
checkout inputs are read by [`MonitoringSettings`](../monitoring/settings.py):

| Variable | Meaning |
| --- | --- |
| `ODESSY_SSH_MONITORING=on` | Enable SSH inventory and read-only status polling. |
| `ODESSY_SSH_COMPUTER=on` | Enable SSH inventory only. |
| `ODESSY_SSH_ALIAS` | OpenSSH alias or IPv4/DNS `user@hostname`. |
| `ODESSY_SSH_HOST_ID` | Optional `host_` + 64 lowercase hex; defaults to alias digest. |
| `ODESSY_SSH_DISPLAY_NAME` | Optional display name. |
| `ODESSY_SSH_CONFIG` | Optional explicit `ssh -F` file. |
| `ODESSY_SSH_BIN` | Optional absolute OpenSSH path; otherwise resolved on PATH. |
| `ODESSY_MONITOR_BIN` | Absolute Go monitor path when automatic checkout/installed resolution cannot find it. |
| `ODESSY_LINK_BIN` | Independent Rust paired-telemetry helper, when that provider is selected. Not required for SSH-only collection. |

For source work, build the monitor from `services/monitoring` with
`go build -o odessy-monitor.exe ./cmd/odessy-monitor` on Windows, or
`go build -o odessy-monitor ./cmd/odessy-monitor` on Linux, then set
`ODESSY_MONITOR_BIN` to that absolute file before starting the local service.
Installed builds resolve their packaged helper; they do not require Go or Node SDKs.

| Symptom | Check next |
| --- | --- |
| No aliases | Use a concrete Host entry, refresh aliases, and check the selected config/Include paths. |
| Unknown/changed key (409) | Verify identity independently and deliberately resolve your own OpenSSH trust; Odessy refuses to replace it. |
| BatchMode authentication failure | Check alias/user, key and unlocked agent with your own SSH client; or enter a password in the temporary Connect form. |
| Save returns 503 / monitoring unavailable | Check the monitor helper path/component and local-service diagnostic. Failed reconfiguration restores the previous target; do not assume it saved. |
| Online with missing metric | Inspect source timestamp and collector quality. Unsupported is not zero; transport success does not grant execution or monitoring consent. |
| Forget does not revoke SSH login | Expected: forgetting an app target is separate from OS key revocation. |

## Runtime ownership, upgrades and removal conditions

Harness owns migrated generic runtime mechanics; Odessy owns application policy,
experience and narrow extensions. SSH monitoring uses the same local application
service whichever session engine is selected. Keep the custom/legacy engine and
its providers/tools; never silently fall back or replay work across engines.

For a deliberate Harness upgrade, review the pinned runtime source/artifacts and
licences in [`harness-artifacts.json`](../runtime_backends/harness/harness-artifacts.json),
then test protocol/session compatibility, permissions, Stop and plugin lifecycle.
[`resources.py`](../runtime_backends/harness/resources.py) composes package-owned
profiles/plugins; `pin.py` and the installer derive the same declaration. The
stream bridge uses managed `ctx.effect` registrations for its socket/listeners,
declares no mandatory service injection and reads the tools registry opportunistically.
Keep disposal and unavailable-service behavior; do not add a second plugin manager.
Update the declaration/closure deliberately, run existing Harness resource/runtime
and installer tests, then build/audit coherent installed artifacts for the affected
target. No floating runtime download or sibling checkout is a production dependency.

Retain an adapter until every static, dynamic, external and packaged consumer has
a tested replacement, including legacy selection and paired telemetry. Similar
upstream names or an unused-code warning are insufficient removal evidence. The
compatibility spike remains an experiment with test consumers, not a shipped
execution feature; do not delete it merely because monitoring is implemented.

Live evidence must name the direction, OS/runtime and artifact: Windows → existing
Linux/Jetson is distinct from Linux → Linux or Windows-as-remote. Recheck real
identity/login, useful timestamps, disconnect/reconnect, permissions, coexistence
and owned-process cleanup for the selected direction. Signing, native wizard,
clean-machine and human release checks stay separate from these source fixtures.
