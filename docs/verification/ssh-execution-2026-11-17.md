# SSH execution preview — 2026-11-17

Historical source-preview receipt on `fix/ssh-history-inspector`, recorded over
`d502bff` before its feature commits. Existing voice and monitoring changes were
preserved. The later [installed update receipt](ssh-voice-installed-update-2026-11-18.md)
records the committed build, upgrade and current verification boundaries.

## Implemented loop

Connect verifies login. A separate **Use for agent commands** action selects one
temporary SSH target. New Custom sessions discover `ssh.run` through the existing
tool-server composition. The existing action broker requires exact approval for
every command in Ask, Auto and Full. Returned stdout/stderr feed the assistant
and the existing durable recorded Output projection. Decline never dispatches.
Disconnect/reconnect/replacement revokes old server objects and closes local SSH
processes. Remote failures never execute locally or retry automatically.

Dispatch binds address, user, port, selected public key and connection instance;
it rechecks current trust and authorization. It ignores mutable SSH configuration
at dispatch, disables proxy/local commands, forwarding and multiplexed channels,
and uses an ephemeral public-key-only known-host file. Passwords use the existing
memory-only askpass lease. Stdout/stderr are bounded to 32 KiB each (the existing
durable projection bounds them further); timeout is 1–60 seconds. Stale trust,
expired credentials and required remote isolation fail closed.

## Checks

- Focused backend suite: **91 passed** across SSH execution/connection APIs,
  access modes, exact approvals, bootstrap and workspace inspector.
- Added production composition/isolation check afterward: SSH file **12 passed**.
- Flutter connection/execution/LAN-page regression files: **26 passed**.
- Dart SSH transport contracts: **6 passed**.
- Analysis: touched desktop/client production and new test files clean.
- Contract validator: **107 settings validated**. `git diff --check` clean.
- Native Windows release build succeeded using cached dependencies.
- Real source service subprocess starts; authenticated `/v1/ssh/execution`
  returns no selection; stdin closure exits cleanly (exit 0).

The HTTP integration uses a deterministic model and a clearly simulated remote
child process with the real agent, broker, authenticated routes and durable store.
It does not prove physical Jetson authentication. No new dependencies downloaded.
The old Python venv launchers referenced removed interpreters; checks used the
existing Python 3.11 runtime with the matching cached `.venv` dependency directory.

## Try the source build

Executable: `apps/desktop/build/windows/x64/runner/Release/odessy_desktop.exe`.
Built from production `lib/main.dart`, with `ODESSY_LOCAL_DESKTOP=true`,
`ODESSY_DESKTOP_ROOT` set to this checkout and `ODESSY_DESKTOP_PYTHON` pointing
to the existing Python 3.11 runtime. It starts a real local service, not `--demo`.
The corrected preview restores the user's saved **source** component selection
through `odessy.setup.configured`, owned by `OdessyApp`. The former preview
launcher incorrectly forced a placeholder `lfm`/Ollama service and omitted the
configuration/voice lifecycle; saved DeepSeek settings therefore did not affect
that service. That launcher is no longer used by the native preview.
The workspace comes from the verified saved configuration. Connector/link state
remains under `build/local-desktop/state`. Session-only keys stay in desktop
memory and reach owned children through private stdin; re-enter them in a new
process. No model or API key is sent to the SSH computer.
Existing cached dependencies are linked; the Go monitoring/password helper was
rebuilt. This checkout-dependent build is not a portable distribution.

### Configuration regression verification

- Full desktop suite: **644 passed, 15 skipped**; log
  `apps/desktop/build/source-model-preview-suite.log` (local build output).
- Opt-in real source helper/service test plus launcher/SSH tests: **15 passed**.
  `source_model_integration_test.dart` saves an isolated selection, launches the
  real authenticated service, and sends a `deepseek-flash` request to a loopback
  deterministic provider with a dummy session key. It asserts no startup
  inference, correct model/auth header, returned assistant text, and no key in
  the saved selection or model request body. No paid provider call or vault write.
- Focused Python configured/credential/voice/SSH tests: **54 passed, 1 skipped**.
- Full desktop analysis clean; touched Dart formatting and contract validator
  clean (**107 settings**). Native release build succeeded with cached tools.
- Native source window inspected: **CONNECTED**, next-turn model
  **deepseek-flash · deepseek**. This is startup evidence, not live provider
  inference or Jetson acceptance.

The build uses both `ODESSY_DESKTOP_ROOT` / `ODESSY_DESKTOP_PYTHON` and
`ODESSY_SETUP_ROOT` / `ODESSY_SETUP_PYTHON`, plus explicit
`ODESSY_SETUP_PYTHONPATH` for the matching cached dependency directory and its
Windows `win32`, `win32/lib`, `pywin32_system32` directories. Packaged interpreters
retain their isolation; this explicit path is only for checkout runs.
The local manifest includes `data/app.so` as well as the native runner: Flutter
Dart changes can change `app.so` while the runner executable hash stays the same.

Choose Custom for new chats, connect with `jaimef@10.1.1.2` and the password,
select **Use for agent commands**, then ask:

> On the selected SSH computer, run hostname, whoami and pwd using ssh.run.

Inspect the exact target/command, approve once, and compare the returned identity
with the Jetson. Also decline a command and disconnect before approving another.
Do not send passwords through chat.

## Remaining boundaries

Live Jetson/hardware acceptance is pending. POSIX direct connections only; no
Harness execution, jump hosts, sudo prompts, interactive PTY or remote file
inspector. SSH commands run with the account's privileges without a sandbox.
Stop closes the local SSH channel; detached remote descendants may survive and
completed effects are not rolled back. Monitoring is a separate capability and
may remain unavailable independently of command execution. LAN is experimental.
At this source-preview checkpoint no installer rebuild, installed-app update,
commit, push or merge had been performed; see the installed follow-up above.

Artifact and changed-source hashes are in the local ignored receipt
`.qualification/ssh-execution-20261117/manifest.json`. The build also contains the
pre-existing dirty voice changes; it must not be represented by HEAD alone.
