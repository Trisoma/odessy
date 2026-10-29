# Secure model credentials implementation ledger

Branch: `feat/secure-model-credentials`, based on `a8a3a38`. The pairing checkout
and its uncommitted work are outside this implementation. No commits or pushes.

Contract: fixed local setup operation `model-credential`, with `action` of
`describe`, `set`, or `remove`; scope fields `provider`, `endpoint`,
`credential_env`; writes additionally carry `storage` (`keyring` or `session`)
and `value` for set only. Values are stdin-only. Replies contain scope identity
and configured/source/writable/persistence metadata, never the value.

Sources: env > session > keyring > none. Environment precedence within a scope
preserves the explicit reference, otherwise generic key before provider key.
Only Windows Credential Manager and Linux Secret Service are approved. No file
fallback. Session secrets live only in desktop/coordinator memory and private
stdio; they do not enter configuration drafts or persistent settings.

Ruling: retain the shared cloud-error conversion, including known CLI-wrapped
configuration exceptions, rather than duplicate endpoint handlers.

Ruling: existing `CredentialStore` handles local API bearer tokens, not provider
keys. Keep those contracts distinct. Secure storage mitigates routine disclosure;
it does not isolate arbitrary code running as the same OS user.

Progress:
- [x] Isolated worktree created; original checkout remains on its branch.
- [x] Cloud error fix and regression tests carried across, excluding pairing code.
- [x] Credential resolver, bridge and provider integrations.
- [x] Desktop Settings/session lifecycle and child environment protection.
- [x] Available native storage, mock-provider, client/UI and Windows build checks;
  the platform and full-suite boundaries below remain explicit.

Native/platform acceptance and exact test evidence are recorded below.

## Use the feature

Open Settings → Models and connections. Select the provider and its endpoint,
enter the key in the masked **API key** field, and choose **Save securely**.
The key is stored separately from the model configuration. Save changed model
settings using their existing section save control, then connect the saved
configuration. A failed prior start can be retried without closing the app.

If the key comes from an environment variable, the field is read-only. Remove
that environment value and reopen Odessy before managing the key here. The app
does not silently change or delete Windows user variables or shell settings.

**Use for this session only** is explicit: closing Odessy forgets that value.
Removing a session key reveals any underlying saved OS-store key; status reports
the resulting source. Secure storage failures never produce a plaintext file.

## Packaging and review

New desktop setup payloads must contain a keyring 25.x wheel, its resolved UI
dependency closure and license inventory. The bundle builder rejects a desktop
payload missing keyring; wheel hashes and notices remain in the existing manifest.
The bootstrap contains stdlib-only provider metadata, so status checks need not
import the model runtime. Linux portable builds already install `odessy[ui,cloud]`
from their explicit offline wheelhouse. Historical pinned installer releases are
unchanged and require a separately rebuilt runtime before gaining secure storage.

Independent review found and corrected: active-key redaction after rotation,
configuration-error conversion for invalid scopes, bounded update acknowledgement,
extracted-bootstrap imports and stopped-handle recovery. Private stdin write,
flush and acknowledgement share a five-second deadline; closing an owned child
also bounds stdin closure. No credentials are returned on that channel.

Qualification uses dummy credentials and local mock providers only. Windows vault
save/reopen/remove and actual Dart → helper → coordinator → cloud-fixture rotation
were exercised. Linux Secret Service code is implemented but native Linux/D-Bus,
Jetson, clean-machine and live provider acceptance are not established here.

## Verification record — October 29, 2026

- Keyring 25.7.0 selected the approved native Windows `WinVaultKeyring` backend.
  Dummy-key save, fresh-process read and removal passed. No real key was read or
  modified by qualification tests.
- Actual Dart local bridge → OS vault → reopened helper → owned coordinator →
  mock cloud succeeded. Session-only key replacement and clearing were verified
  through actual requests after private-channel acknowledgement, not only mocks.
- Final focused Python set: **151 passed, 1 skipped** (native storage has a
  separate opt-in run). Desktop payload/bootstrap tests and package imports passed.
- Complete Flutter suite with available integration fixtures enabled: **175 passed,
  2 skipped**. Remaining skips require their independent broker/service fixtures.
- Dart client: **18 passed, 2 skipped**, analysis clean. Flutter analysis is also
  clean; the Windows release build passed in this isolated worktree.
- Final opt-in native credential file: **15 passed**, including real Windows
  Credential Manager persistence/removal and an actual filtered child process.
- An installed wheel performed native save/reopen/remove from outside the checkout;
  the final wheel also imported the shared provider metadata under isolated Python.
- Full Python run before the final fixture corrections: **2389 passed, 2 failed,
  19 skipped, 101 subtests passed**. One failure asserted the old global-env key
  injection; its replacement verifies actual explicit-reference resolution and
  unchanged global env, and passes in the focused rerun.
- The other failure, `tests/test_evals.py::test_every_case_is_passable[multi-grep-then-read-class]`,
  also fails against exported, unchanged `a8a3a38` source. No evaluator assertion
  was weakened, and no full-suite-green claim is made.
- Enlarged-text credential-panel rendering is captured at
  `apps/desktop/build/preview/secure-credentials.png`; this is a widget render,
  not native window-manager, Narrator or clean-machine acceptance.

Generated wheels, checksums, dependency notices, inventories and test logs live
under ignored `.qualification/`. The known-good Windows Release is not rebuilt
or modified. No commit, merge or push was performed.

Final artifact inventory contains 15 Windows Release files and 10 dependency/app
wheels. Hashes are recorded in `.qualification/windows-release-sha256.json` and
`.qualification/SHA256SUMS.txt`. Final `git diff --check` passed. The historical
backup directory was unavailable during final closeout, so no fresh hash comparison
against that backup is claimed.

Development executable:
`apps/desktop/build/windows/x64/runner/Release/odessy_desktop.exe` in this worktree.
It uses this worktree's Python environment; it is not a standalone portable release.
Keep the complete Release folder and the configured backend environment available.

Post-verification cleanup removed baseline exports, disposable installed-wheel
trees, test fixtures and Dart/test caches. Only about 1.45 MB of credential wheels,
notices, checksums and verification logs remain under this worktree's
`.qualification/`. Both runnable Release folders were preserved: all 30 files
matched their hashes before and after cleanup. The pairing checkout is clean.
An older outer `ux-full-python` fixture still contains OS-permission-protected
test state; attempts to remove it and reset its disposable ACL were denied.
