# Connect a computer with SSH

**Developer Preview · v0.5.0-alpha**

Connect using an ordinary SSH address. Login starts read-only computer monitoring.
You can separately enable **Custom engine SSH commands** for this service session.
Neither login nor monitoring alone grants command execution or workspace access.

## Agent command loop (Custom preview)

1. Choose the **Custom** runtime for new chats. Harness SSH execution is unsupported;
   Odessy refuses it explicitly instead of silently switching engines.
2. Connect below, then click **Use for agent commands**. This selects one temporary
   target and opens a new live chat to load its tools. Existing saved history remains.
3. Ask: **On the selected SSH computer, run hostname, whoami and pwd using ssh.run.**
4. Review the exact command and target in the approval, then approve once or decline.
   Approval is required in Ask, Auto and Full modes. Results return to the chat and
   recorded **Output** panel; command failures remain remote failures.
5. **Disconnect agent commands** revokes the selection and closes active local SSH
   channels. Disconnecting/replacing the SSH connection does the same.

Commands run as the SSH account using a POSIX shell, **without a remote sandbox**.
Timeout is 1–60 seconds; stdout and stderr are bounded. Stop/disconnect closes the
SSH channel but cannot promise termination of detached remote descendants or undo
effects. No interactive prompts, sudo password entry, PTY, jump hosts, remote file
inspector, automatic retries or local fallback are provided. Connections with
required sandbox isolation are refused. Host key/address/user/port and the exact
command are bound at dispatch; changed trust requires reconnecting.

SSH and experimental LAN execution cannot be selected simultaneously. LAN remains
experimental. The monitoring capability DTO remains monitoring-only; the separate
`GET/POST/DELETE /v1/ssh/execution` route owns explicit execution selection.

## Connect for this session

1. Open **Devices → Connect a computer** and enter **Address**, for example
   `jaimef@10.1.1.2`.
2. Optionally enter **Port**. Blank uses your OpenSSH configuration, normally 22.
3. Optionally enter **Password**. Blank uses your existing SSH key/agent. Unlock
   protected private keys in your own SSH agent.
4. Click **Connect**. Odessy checks the host identity, performs a bounded login and
   starts the existing read-only monitor. Check **Session connection**, freshness
   and the first status sample. Authentication alone does not prove fresh metrics.
5. Click **Disconnect** to stop this SSH selection and forget its password.
   Your independent OS SSH access and the remote machine remain unchanged.

In **Devices → Overview**, the connected computer appears with its remote hostname
when no alias or custom name was supplied. The address is shown separately. An
existing alias/custom name takes precedence; if the hostname is unavailable the
address remains the fallback. Names are display metadata, never identity or
permission evidence. Open **View details** for status and available readings.

For alpha these connections are temporary. Reconnect after the local Odessy
service restarts. Address, port and password from Connect are not written to the
configuration file. The password field clears when submitted; the service keeps
an in-memory password lease for at most eight hours, ending sooner on disconnect,
replacement or shutdown. Existing legacy saved targets remain readable; Connect
and Disconnect do not rewrite unrelated or legacy configuration. A previously
saved legacy target can therefore return after restart until deliberately removed.

**Stop waiting** stops waiting in the UI; the bounded service operation may still
finish. Refresh/reopen the page to learn the actual state before retrying.

## Host identity and advanced configuration

An **SSH alias** is an optional shortcut from your existing OpenSSH configuration.
Expand **Advanced: SSH aliases and host identity** to select one; it fills the same
Address field. An explicit Port overrides the configured port. Odessy does not
create aliases or change trust files. Direct entry supports IPv4/DNS
`user@hostname`; IPv6 literal entry is not supported by this form.

For a new host, verify its fingerprint through an independent trusted channel,
then establish trust with your OS SSH client, for example:

```powershell
ssh -p 22 jaimef@10.1.1.2
```

Confirm only after checking the fingerprint. Return to Odessy and click Connect.
A discovered key is not authenticated identity. Unknown or changed keys are
refused; nondefault ports use their own known-host entry. Password login through
proxy/jump hosts and keyboard-interactive/MFA are unsupported; use a configured
key/agent for those cases.

## GitHub is not a monitoring computer

`ssh -T git@ssh.github.com` can authenticate successfully while providing no shell.
Odessy displays **Authenticated · Git-only endpoint** and does not connect it for
computer monitoring. Use an actual Linux computer for these collectors.

## Troubleshooting

| Status | Next step |
| --- | --- |
| Host key unknown/changed | Verify the fingerprint independently; resolve OS SSH trust deliberately, then Connect again. |
| `ssh -G failed` before login | Start Odessy through its normal shortcut or launcher. Custom/test launchers must preserve Windows `ProgramData` and the user/profile environment; this error alone is not a password failure. Do not bypass host-key checks. |
| Authentication failed | Check username/password, or unlock the correct key in your SSH agent. |
| Password needed again | Enter it above and choose Reconnect; passwords expire and do not survive service restart. |
| Timeout/refused/name unresolved | Check address, network, SSH service and optional port. |
| Missing password helper | Use matching service/monitor builds; a Python-only update is insufficient. |
| Connected but stale/missing metrics | Check source timestamps, monitoring permissions and collector support. |

The current SSH collector targets Linux/POSIX computers; Windows-as-remote is not
qualified by this change. Linux load averages and root-disk usage are the current SSH collectors. Missing
collector groups remain unavailable. Experimental LAN execution uses a separate
connection method and separate permissions.

This source workflow requires matching desktop, Python service and Go helper
builds. Source tests/screenshots do not establish delivery by an installed update.
See [contributor interfaces](../../src/odessy/ssh/README.md),
[experimental LAN executor](executor-preview.md) and
[UI captures](../view/ssh-history-inspector/README.md).
