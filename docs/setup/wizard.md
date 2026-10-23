# Local setup wizard

The desktop sidebar's **Set up this computer** opens six steps: role, existing
components, model, optional voice, execution boundaries, and review/save.
This configures existing components; it does not install dependencies, start
services, download models, or grant remote execution authority.

## Run from a checkout

From `apps/desktop`, use the Flutter SDK with a Python environment containing
Odessy. If `python -m odessy` reports a missing module, point the helper explicitly
at the repository and its interpreter:

```powershell
flutter run -d windows --dart-define=ODESSY_SETUP_PYTHON=C:/path/odessy/.venv/Scripts/python.exe --dart-define=ODESSY_SETUP_ROOT=C:/path/odessy
```

On Linux/macOS use the same defines with your Python and checkout paths and the
corresponding desktop device. These are build-time defines, not shell variables.
The default helper is `python -m odessy.setup.bridge`. The wizard works without a
running coordinator API or token; its setup helper is local even in demo mode.

## Behavior

- Combined: local coordination and execution. Coordinator: model/voice with
  separately authorized execution nodes. Advanced execution node: no model or voice.
- Model detection is an explicit, bounded Ollama inventory request. It performs
  no inference or model pull. Select any compatible installed model or leave it
  unconfigured. Cloud credentials stay in environment references, outside forms.
- Voice is optional. Diagnostics check existing executables/assets and missing
  configuration; presence is not physical audio or model compatibility acceptance.
- Choose a new output path in an existing directory. Review binds the selection,
  format and destination; edits invalidate consent. Save requires a separate click.
  JSON stores the selection; runtime env exports require a configured model.
- Files are never overwritten. Saving does not automatically activate settings.
- Cancel stops the owned helper and discards late replies. An interrupted save may
  have completed its atomic write: **Inspect saved state** reads the destination
  without repeating save. Missing requires review; conflict requires a new path.
  After reopening the app, re-enter the same selection, format and destination to
  inspect it. No configuration journal or credentials are stored by the UI.
- Diagnostics, discovery and review can be retried. Mutating saves are never
  automatically retried. Shell commands and arbitrary executables are not accepted
  through the protocol; only the configured Python and fixed module are launched.

The helper limits requests to 64 KiB, stdout to 4 MiB and stderr to 64 KiB.
Response waiting has a 30-second timeout; OS process creation is outside that
response timeout. This is not a hard end-to-end installation deadline.

## Checks

```powershell
python -m pytest tests/test_setup_bridge.py tests/test_setup_configuration.py
cd apps/desktop
$env:ODESSY_TEST_PYTHON = 'C:/path/odessy/.venv/Scripts/python.exe'
flutter test --no-pub
flutter analyze --no-pub
```

The opt-in native integration tests execute the real Python helper, check
create-only save/recovery, and cancel a stalled local inventory request. Without
`ODESSY_TEST_PYTHON` those two integration tests are explicitly skipped.
Windows release compilation is separate from Linux/macOS installation acceptance.
Native installers, service lifecycle, GUI pairing/revocation and packaged-device
qualification remain future work. Use existing authorized pairing CLI workflows.


## Native lifecycle continuation

Headless offline bootstrap, per-user service commands and conservative
repair/rollback/uninstall are now implemented separately. See
[native lifecycle](native-lifecycle.md). The six-screen GUI still configures existing
components; it does not silently invoke these newly added mutating commands.
Native service permission and real-device acceptance remain qualification gates.
