# Conversation and execution-host recovery

The model/coordinator and the optional execution host have separate lifecycles.
A failed or stopped execution host must not prevent starting a conversation or
using tools explicitly addressed to this computer.

- Devices → Connect authenticates the tool endpoint and caches its tool schemas.
  Session creation uses those schemas without contacting the selected executor.
- `terminal.run` is local. `laptop.terminal.run` remains remote. A failed remote
  command is never retried locally. Existing approvals and workspace restrictions
  still apply.
- When a LAN executor is selected, local Node supplies local command execution;
  remote filesystem/git tools retain their existing names. If local Node cannot
  start, the existing Python time/calculator/terminal backend remains available.
- Optional legacy remote discovery is bounded. Invalid legacy enrollment admits
  no remote target, but leaves local tools and chat available. Explicit
  configuration validation still reports invalid enrollment.
- A cold local Node build can take longer than a normal API read. Session creation
  has a 90-second response deadline; this does not change model/tool deadlines or
  replay requests. Other ordinary API reads retain their existing deadlines.
- After stopping/restarting an executor, use Devices → Disconnect, then Connect
  and approve Y in its terminal. Credentials remain memory-only for this prototype.
  Existing conversations can still chat or use local commands; reconnect refreshes
  the execution target through the existing session lifecycle.

## October 30 build outputs

Windows checkout preview: `apps/desktop/build/windows/x64/runner/Release/odessy_desktop.exe`.
It finds this checkout's Python virtual environment and source. Close the previous
app and reopen this executable to load the update. Keep the whole Release folder.

Jetson preview archive: `build/odessy-desktop-arm64.tar.gz`, with an adjacent
`.sha256` file. It contains `bundle/`, matching `src/odessy/` and `pyproject.toml`.
Built in ARM64 Docker on Ubuntu 22.04; this is not physical Jetson acceptance.
It uses the existing `/home/jaimef/odessy/.venv/bin/python` and checkout, and is
not a standalone runtime distribution. No model or API key is included.

The desktop `ui` extra includes `zeroconf` and `ifaddr` for device discovery.
Older source-backed previews may have installed `ui` before this dependency fix.
Update their existing environment with `python -m pip install -e '.[ui]'` from
`~/odessy`. A Flutter rebuild alone never installs Python dependencies. Headless
executors can use the smaller `pairing` extra. Offline package wheelhouses must
resolve the updated extras before assembly.

After transferring the archive to the Jetson home folder, close Odessy and run
in a terminal on its desktop:

```bash
mkdir -p ~/odessy-desktop-preview
tar -xzf ~/odessy-desktop-arm64.tar.gz -C ~/odessy-desktop-preview
cp -a ~/odessy-desktop-preview/src/odessy/. ~/odessy/src/odessy/
cd ~/odessy
source .venv/bin/activate
export PYTHONPATH="$HOME/odessy/src"
"$HOME/odessy-desktop-preview/bundle/odessy_desktop"
```

This updates application source, not saved settings, keys or models. Rebuilding
only Flutter does not update Python. Missing credentials/provider configuration
can still prevent model calls and must be corrected in Settings.

Verification: 84 affected Python tests; enhanced real remote-process integration
also rerun separately; Dart client 18 passed/2 opt-in skipped plus the real
Dart → Python → local mock cloud HTTP/SSE test; 16 focused Flutter tests (including
the two architecture checks); clean
Flutter analysis; Windows and ARM64 release builds. A pytest cleanup warning
concerned old inaccessible temporary trust state. No paid provider, physical
Jetson, audio, or clean-machine acceptance is claimed.
