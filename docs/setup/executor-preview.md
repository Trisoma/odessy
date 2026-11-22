# Experimental OdessyExecutor distribution

**Developer Preview · v0.5.0-alpha**

The headless executor shares one explicitly chosen workspace with a coordinator
on a trusted LAN. It uses the existing Python executor and Node tool server;
it is not SSH execution and needs no local model, speech assets or Flutter UI.
It is a separate optional package, not a background service installed by opening
the archive. Traffic in this prototype is unencrypted: do not expose it publicly.

## Build from reviewed offline inputs

From the repository root, use an already prepared development environment:

```powershell
python tool/package_executor.py --wheelhouse <matched-wheelhouse> --licenses <licenses.json> --output <new-output-directory> --node <matched-node-runtime> --toolserver <packaged-tool-server>
```

The wheelhouse must contain exactly one current Odessy wheel with the executor
entry point, plus a platform/ABI-compatible pinned dependency closure including
`zeroconf` and `ifaddr`. Reuse the existing installer inventory/audit workflow for
hashes, licenses, Node and built tool-server inputs. The wrapper uses that same
offline builder; it does not download dependencies or alter production closure.
Optional `--python-runtime` accepts the existing supported packaged runtime.

The output includes `payload.zip`, `install-executor.py`, `run-executor.py`,
`README.md` and `manifest.json`. Match OS, architecture and Python ABI. Without
a runtime, an existing supported Python with working venv/pip is required. This
is smaller than the desktop/voice distribution, not a tiny standalone executable.

## Review and start explicitly

Verify publisher checksums before using a distributed artifact. Extract
`payload.zip` to `payload/` beside the wrapper scripts, then:

```powershell
python install-executor.py plan --root <new-dedicated-executor-root>
python install-executor.py install --root <new-dedicated-executor-root> --confirm <reviewed-plan_id>
python install-executor.py verify --root <new-dedicated-executor-root>
python run-executor.py --installation <executor-root> --workspace <shared-directory>
```

Add `--host <local-LAN-IPv4>` when selecting an interface. Choose an existing
directory containing only the workspace you intend to share. The launcher first
verifies the installation and bundled Node/tools. In the coordinator, open
Devices and select the nearby execution host, then review and approve Y/N in
the executor terminal. Existing action, confinement and approval rules remain
in force. Stop sharing with Ctrl+C; pairing credentials expire with that process.

The source packaging tests verify structure, manifest hashes and missing-input
refusal. A local unpublished Windows CPython 3.11 archive was also audited during
this change. Archive construction is not installed/live LAN qualification. Linux,
Jetson ARM64, live pairing and any future release upload need their own matching
artifacts and checks. No download URL or automatic updater is introduced here.

See [direction-specific LAN checks](jetson-windows-lan-execution.md),
[SSH monitoring](ssh-computers.md) and [release verification](../release-verification.md).
