# Temporary SSH connection verification

Developer Preview · v0.5.0-alpha · November 17, 2026

Branch: `fix/ssh-history-inspector`. Base revision:
`3b7cea13922d773f95275c970f1d50c275cd5a04`, with preserved pre-existing dirty
work. This receipt covers source changes; no commit, merge, push, installer build,
installed update or modification of `D:/Odessy` / `D:/Odessy-test` was performed.

## Behavior

- Address → optional port/password → Connect. An alias is optional and lives in
  Advanced. The visible address is always the submitted target; there is no hidden
  selected-alias test/save sequence.
- Connect creates a service-session-only target. It never reads or writes the
  configuration file. Disconnect closes the monitor/credential lease without
  configuration writes. Restart forgets the temporary target. Older independently
  saved targets remain compatible and can return after restart.
- Blank password explicitly selects key/agent; it cannot reuse an earlier password.
  Submitted password text clears; the service lease expires within eight hours
  and closes on replacement/disconnect/shutdown. Failed transitions restore the
  previous runtime target and dispose unused credentials. Stop waiting does not
  claim server cancellation.
- Explicit port reaches identity lookup, probe and Go bootstrap. A port-22
  known-host entry cannot authorize a nondefault port. Unknown/changed keys remain
  refused, and Git-only authentication is never a monitoring success.
- Successful login reads a bounded hostname for display. A direct target without
  an alias/custom name uses it; names never authorize anything. Devices preserves
  explicit SSH method independently of pairing trust, and shows the hostname,
  address, connection and telemetry state. SSH execution/workspace remain false.

## Checks and results

Cached Python 3.13.15 with the prepared `.ci-venv313` site-packages was used.
The default Python/venv launcher is unsuitable on this machine. Cached Flutter
was invoked through `dart.exe .../flutter_tools.snapshot`, with no dependency
downloads. See the [contributor fixture commands](../../src/odessy/ssh/README.md).

| Check | Result |
| --- | --- |
| Python: SSH temporary integration, API, onboarding, password, connection, supervisor, composition, monitoring API/transport, fresh bootstrap and compatibility (11 files) | 80 passed, 21.72 s |
| Real Go monitor + simulated SSH computer, included above | Key/agent and password cases passed; wrong password refused; fresh samples and hostname reached inventory; disconnect stopped process and lease; restart had no target |
| `go test ./... -count=1`, `GOPROXY=off`, `GOTOOLCHAIN=local` | All 9 packages with tests passed; 3 helper packages have no tests |
| Flutter: SSH controller/direct-connect/connected-device, device monitoring, monitoring presentation and device page (6 files) | 84 passed |
| Full `packages/odessy_client`: `dart analyze`; `dart test` | Clean analysis; 68 passed, 2 existing opt-in integration skips |
| Full desktop: `flutter analyze --no-pub` | No issues |
| `tool/check_desktop_contracts.py` | Validated 107 runtime settings |
| `git diff --check` | Clean; Git emits existing LF/CRLF conversion notices |

The native helper was built from the current source into
`.qualification/ssh-direct-connect-20261117/odessy-monitor.exe` (SHA256
`0136d01b3cb3aebd6911a2d24769cf9dee92e8a638b2734f0e691dd3e5ade634`).
The test child is `fakessh.exe` (SHA256
`f5aa9b18eedc64b6596eb64af791d58604a97145ea57746cf4d403673b809702`).
The child never connects to a remote endpoint; actual OpenSSH handles local
configuration and isolated trust lookup. Do not mistake its password comparison
for a real SSH server's authentication.

Initial test failures were repaired: obsolete review/test/save selectors, test
timer cleanup, and the integration poll's initially null metrics. SDK/cache sandbox
access and one approval-service quota failure interrupted commands; later approved
runs completed. An earlier client run warned about sandboxed cache cleanup; the
final approved full run passed without that warning.

## Changed scope

This slice updates SSH identity/application actions, monitoring settings/supervisor,
the Dart SSH API/controller/view and monitoring DTO, device method presentation,
the existing test SSH child and focused tests/docs. Other dirty voice, history,
inspector, executor and benchmark files predated this slice and were preserved.
Exact source/image hashes for this slice are in the
[screenshot manifest](../view/ssh-history-inspector/direct-connect-20261117/manifest.json).

## Review and remaining gates

[Eleven actual rendered screenshots](../view/ssh-history-inspector/direct-connect-20261117/README.md)
are also copied to Desktop `Odessy-SSH-preview-20261117`. They show deterministic
fixture states, not a running installed app or a live Jetson. The Devices images
are explicitly labelled as simulated. Wide/narrow layouts were inspected.

The user will verify live hardware. Remaining checks: real password/key login,
network failure/reconnect, fresh Linux load/disk readings and matching installed
delivery. Windows-as-remote collectors, proxy/MFA password support, optional saved
addresses and installed qualification are outside this slice. Hostname collection
does not imply broader OS/remote-execution support. Existing engines, pairing and
independent grants retain their owners.
