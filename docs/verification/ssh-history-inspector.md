# SSH and conversation usability verification

**Developer Preview · v0.5.0-alpha — November 17, 2026**

Source changes are on `fix/ssh-history-inspector`, based on `3b7cea13922d773f95275c970f1d50c275cd5a04` with
uncommitted changes. Nothing was committed, pushed, merged, published or applied
to an existing installation. Earlier voice optimization is in this base and is
included in the newly verified wheel. User settings/history/models, SSH trust
files and remote permissions were not modified.

## Behavior changed

- Devices accepts direct IPv4/DNS `user@hostname`, or an existing alias. Password
  entry is masked and session-only; Python owns a bounded loopback credential
  lease and the packaged Go monitor supplies OpenSSH askpass. Expiry, replacement,
  rollback, forgetting the target and service shutdown clear/reject that lease.
  Passwords never become command arguments, saved configuration or URLs.
- GitHub's actual `printf` response is `Invalid command`, despite successful
  authentication. The corrected GitHub probe uses the no-command greeting and
  reports authenticated/Git-only, then refuses monitoring registration.
- Selecting a saved chat opens it directly. Compatible Custom history resumes
  through the existing service owner; existing matching live sessions are reused.
  Harness history-only and refusal states remain readable. No message/tool replay.
  Right-click and keyboard-accessible overflow retain real management actions.
- At narrow widths, the inspector docks below the conversation. The composer
  remains usable, retains drafts and scrolls in short layouts; large-text cases
  are tested at 1.0, 1.3 and 1.6.
- `tool/package_executor.py` wraps the existing offline packaging/installation
  owner for an optional experimental LAN executor. It adds no runtime dependency,
  Flutter app, model, voice asset, automatic service or SSH execution capability.

## Verification

| Check | Result and scope |
| --- | --- |
| Affected Python API/SSH/password/monitoring/packaging selection | **65 passed**; includes a real rebuilt native askpass executable, denial/close/expiry and no-disk-secret tests. |
| Full desktop `flutter test --no-pub --reporter expanded` | **632 passed, 14 skipped**. After two brace-only analyzer fixes, affected capture tests were rerun: **31 passed**. |
| Dart client `dart test --reporter expanded` | **66 passed, 2 skipped**; authenticated password request bodies, redirect rejection and bounded diagnostics. |
| Go `go test ./...` | All tested packages passed; dependencies reused offline. |
| `flutter analyze --no-pub`, `dart analyze packages/odessy_client` | No issues. |
| `python tool/check_desktop_contracts.py --check` | **107** runtime settings validated. |
| Formatting and `git diff --check` | Changed Dart formatted, changed Go formatted; diff check passed. |
| `flutter build windows --release --no-pub` | Production Windows x64 executable built; launched with isolated profile and `--demo`, observed/captured with Windows computer use, then closed. |
| Dart `LocalApi` → authenticated Python HTTP → OS OpenSSH → GitHub | Authentication succeeded; `SSH_NO_SHELL`; monitoring save rejected HTTP 409, target remained unset. Unauthenticated request rejected. No config/trust writes. |
| Wheel inventory | **233** shipped source/resource files compared byte-for-byte; new password module and previous TTS optimization included. Windows desktop/helper PE x64 identities checked. |
| Executor archive | Built from the current wheel and hash-verified cached Node/tools/wheels. Existing payload audit and every setup-context hash passed; no desktop/voice/model/helper payload. Real plan-only command checked separately; no sharing or installation started. |

The source-equivalent command for the affected Python selection is:

```powershell
python -m pytest -q tests/test_ssh_connection.py tests/test_ssh_connections_api.py tests/test_ssh_onboarding.py tests/test_ssh_password.py tests/test_ssh_fresh_bootstrap.py tests/test_executor_package.py tests/test_monitoring_supervisor.py tests/test_monitoring_composition.py tests/test_monitoring_api.py tests/test_setup_bundle.py
```

Set `ODESSY_ASKPASS_BINARY` to an absolute current `odessy-monitor` executable
to enable the native-helper case; otherwise that case deliberately skips.
Cached Windows Python/Flutter tools were invoked directly for this machine's
checks; no dependencies or models were downloaded. Full desktop/client skips
remain skips, not passes; details are retained in the local qualification logs.
Earlier failed/partial commands were superseded: one test selection named a
nonexistent bootstrap file, the first analysis found two style diagnostics, and
one analysis redirection used the wrong directory. The first real executor plan rejected an empty bundled-Python component declaration; the executor wrapper now declares only its physical Node/tools components, with regression coverage and a successful corrected plan. The corrected commands above
completed successfully; no assertion was weakened or skipped to hide a failure.

## Artifacts and captures

The exact source, image and artifact identities are in the
[manifest](../view/ssh-history-inspector/manifest.json).
The [gallery](../view/ssh-history-inspector/README.md) distinguishes fixture-backed
production widgets from the native demo. They are visual/source evidence, not
installed or real-device acceptance.

| Artifact | SHA-256 |
| --- | --- |
| Python wheel | `7072f8edb1e94afb0d48e95d1db6239dc2a43a812dc548151496346981c37f5a` |
| Windows desktop | `4a1a91a9bd67433cc5fb6fcdf4c6e62c992109f9489de88cdd5f090da05cf25c` |
| Monitoring helper | `0136d01b3cb3aebd6911a2d24769cf9dee92e8a638b2734f0e691dd3e5ade634` |
| Experimental Windows CPython 3.11 executor archive | `74b5b51a3c3e526762689e8645ed40017b53c80d1bae08c5ee189f371f01d0ed` |

The local executor archive is 86,031,130 bytes
(about 83 MiB). It is smaller than the desktop/model distribution, **not a tiny
self-contained binary**. It requires matching Python 3.11 with venv/pip; it is not
a Jetson ARM64 artifact. The package has not been published or installed.
Preliminary local wheel/archive builds are superseded; use the `wheel-final2/`
and `final-candidate-v2/` paths recorded in the manifest.

## Remaining acceptance and next step

- Jetson `jaimef@10.1.1.2` timed out during a bounded read-only check. No password
  was supplied/guessed and no remote provisioning/trust change was attempted.
  Real password login, first fresh metrics, reconnect and collector shutdown
  on that device remain unqualified. It does not need to stay powered on for
  the completed local checks.
- A new coherent installer/update must combine this desktop, monitor and wheel
  with the prior voice optimization, audit its closure, then verify preserved
  user data and installed voice/SSH behavior. **No installed app was updated.**
- The optional executor still needs platform-specific installed/live LAN checks,
  and deliberate release attachment. Linux/Jetson packaging, hosted CI, signing,
  publication and human acceptance were not performed.

See [SSH setup](../setup/ssh-computers.md),
[executor packaging](../setup/executor-preview.md) and
[release verification](../release-verification.md). Do the later combined update
once the owner is ready; do not infer installation readiness from these tests.


## Subsequent installed update

This source-preview receipt predates the later installed check. The [November 17 combined update](combined-update-2026-11-17.md) now records delivery to `D:\Odessy-test`, with preservation, native helper, TTS and launch verification. Jetson and live LAN gates remain separate.
