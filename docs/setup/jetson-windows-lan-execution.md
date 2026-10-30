# Pair Jetson with a Windows laptop execution host

This guide reproduces the current trusted-LAN prototype using a Jetson coordinator
with the desktop UI and a Windows laptop running the headless executor. The laptop
does not need the assistant UI, a model, STT or TTS.

**Security:** traffic is unencrypted. Use only a trusted private network and approve
only requests you expect. Advertised names/device IDs are not cryptographic proof
of identity. Connecting does not bypass tool approvals or workspace policy.

## 1. Check the prerequisites once

Use matching current Odessy Python source and desktop builds. Updating Flutter
alone does not update Python or install its dependencies.

On **Windows PowerShell**, using the existing checkout and virtual environment:

```powershell
cd C:\Users\13647\OneDrive\Desktop\MiMundo\GitHub\odessy\odessy
.\.venv\Scripts\python.exe -m pip install -e ".[pairing]"
node --version
npm --version
```

If Node/npm are missing, install Node.js before continuing. Prepare the tool server
once, or after changing its sources/dependencies:

```powershell
Push-Location tools
npm ci
npm run build
Pop-Location
```

On **Jetson**, using its existing checkout and environment:

```bash
cd ~/odessy
source .venv/bin/activate
python -m pip install -e '.[ui]'
python -c "import zeroconf, ifaddr; print('Discovery dependencies available')"
```

Older source snapshots may declare discovery only in the `pairing` extra; install
`'.[ui,pairing]'` instead if using one of those snapshots. Missing `zeroconf` in the
old scanner silently returned `[]`; this was the confirmed cause of the October 30
discovery failure, not evidence that the Jetson or hotspot was faulty.

Jetson needs a configured working LLM for the later conversation/tool test. Use
Settings to select an existing local model or cloud provider and configure its
credential. No model download or inference is required just to discover/connect.

## 2. Choose the laptop's network address

Both computers must have a reachable address on the same network. On Windows:

```powershell
ipconfig
```

Use the **IPv4 address of the adapter connecting to Jetson**:

| Connection | Laptop address in the recorded trial | Jetson address in that trial |
| --- | --- | --- |
| Same phone hotspot / Wi-Fi | `172.28.199.192` | `172.28.199.40` |
| Direct Ethernet cable | `10.1.1.1` | `10.1.1.2` |

These are examples from the maintainer's machines, not universal defaults. Hotspot
addresses can change. Do not use WSL/VMware/Docker virtual-adapter addresses.
The executor currently auto-selects one adapter; `--host` avoids selecting Ethernet
when you intended Wi-Fi, or vice versa. Wi-Fi can stay connected for cloud access
while Ethernet carries laptop execution traffic.

For the trusted adapter, Windows Settings → Network & internet → connection →
Network profile should be **Private**. Allow the actual Python and Node executables
through Windows Firewall on Private networks. Do not disable the firewall.
Some VPNs/hotspots restrict local traffic; discovery success must be tested rather
than assumed from sharing a network name.

## 3. Start the Windows executor

Run this in **Windows PowerShell**; replace only `172.28.199.192` if the chosen
adapter has a different current IPv4 address:

```powershell
cd C:\Users\13647\OneDrive\Desktop\MiMundo\GitHub\odessy\odessy
New-Item -ItemType Directory -Force "$env:USERPROFILE\odessy-test"
Set-Content "$env:USERPROFILE\odessy-test\pairing-check.txt" "Hello from laptop"

.\.venv\Scripts\python.exe -m odessy.application.executor --workspace "$env:USERPROFILE\odessy-test" --host 172.28.199.192
```

For the recorded direct-cable setup, use `--host 10.1.1.1` instead. The selected
address must belong to this laptop. Keep this terminal open after **Ready**.
Changing networks requires stopping with Ctrl+C and starting with the new address.

## 4. Connect from the Jetson UI and approve on Windows

Launch Odessy from a terminal on the **Jetson desktop**, not an SSH X11 session:

```bash
cd ~/odessy
source .venv/bin/activate
export PYTHONPATH="$HOME/odessy/src"
"$HOME/odessy-desktop-preview/bundle/odessy_desktop"
```

1. Open **Devices** and refresh nearby execution hosts if necessary.
2. Select the intended laptop and click **Connect**.
3. On the **laptop terminal**, check the requester and exact shared directory.
4. Type **Y**, then Enter, to allow; **N**, then Enter, to decline.
5. Wait for Jetson to report **Connected** and a tool count. Acceptance alone is
   not sufficient: the coordinator checks the actual authenticated MCP endpoint.

No certificate export, fingerprint copying, token copying, Rust executable path
or manual `ODESSY_TOOL_SERVER_URL` change is needed for this flow.

### Recorded maintainer terminal transcript — October 30, 2026

```text
PS C:\Users\13647\OneDrive\Desktop\MiMundo\GitHub\odessy\odessy> .\.venv\Scripts\python.exe -m odessy.application.executor --workspace "$env:USERPROFILE\odessy-test" --host 172.28.199.192
Odessy execution host: FZWforever
Device ID: ff920595-0a30-4fe1-a3c8-4e345f214cfb
Workspace: C:\Users\13647\odessy-test
Experimental trusted-LAN mode (unencrypted). Only approve requests you expect. Close this terminal or press Ctrl+C to stop sharing.
Ready. On the coordinator, open Devices and click Connect beside this host.

ubuntu requests access to C:\Users\13647\odessy-test. Allow connection? [Y/N] Y
```

`ubuntu` is the Jetson's hostname in this trial. The transcript confirms that the
request reached Windows and the maintainer entered Y. It does not by itself prove
a successful remote tool operation. Discovery on both hotspot and Ethernet was
also confirmed after installing `zeroconf`.

## 5. Verify actual execution, not just the connection label

In Jetson's **Assistant**, ask:

> On the connected laptop, read pairing-check.txt in its shared workspace.

The answer must contain **Hello from laptop**. Then ask:

> On the connected laptop, create pairing-result.txt containing “Pairing works”.

Review and approve the exact action if prompted. The initial terminal Y grants
the connection; it does not preapprove every tool action. Check on Windows that
`C:\Users\13647\odessy-test\pairing-result.txt` actually exists and has the expected
content. A model claiming it wrote the file is not sufficient evidence.

Check failure isolation:

1. Stop sharing with **Ctrl+C** on Windows. Its credential is no longer valid.
2. On Jetson ask “What is 1+1?” — model-only conversation should still work.
3. Ask for the time **on this computer** — local tools should remain usable.
4. Request a new laptop operation — it must fail, never silently run on Jetson.
5. Restart the executor, disconnect/reconnect from Devices, and answer **N**.
   The connection must be declined while conversation remains usable.

The current prototype admits one coordinator per executor run. After an accepted
connection needs replacement, restart the executor and explicitly reconnect.
Only selection metadata persists; connection credentials remain in memory.

## Troubleshooting an empty device list

First confirm the laptop still shows **Ready**, the selected address matches the
active adapter, and discovery imports succeed in the **same Python environment
used by the Jetson UI**. Then scan directly:

```bash
~/odessy/.venv/bin/python -c "from odessy.setup.lan_pair import scan_execution_hosts; print(scan_execution_hosts(timeout=8))"
```

If this finds hosts but the UI does not, check the UI's Python/source selection and
restart the app. If it returns `[]`, inspect dependencies and multicast/network
filtering. Ping loss alone does not prove a broken network: Windows may block
ICMP. In the cable trial, Jetson's ARP entry was REACHABLE despite ping loss.

For an approved request followed by a connection failure, check Python/Node firewall
permissions and that sharing is still running. Do not copy tokens or disable
authorization as a workaround. See [failure history](../../../docs/failures/failure-007-pairing-integration-and-discovery.md).

## Other coordinator and execution-host combinations

The design is role-based rather than restricted to these two machines. Other
supported hosts should use the same discovery/request/MCP flow with their own
native Python/Node runtimes, addresses and workspace paths. That is a portability
expectation, not recorded native acceptance for every OS or architecture. Windows
and ARM64 preview builds, automated local integration, and the human observations
above must not be presented as a completed cross-platform or clean-machine release.
