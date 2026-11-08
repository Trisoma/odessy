# Harness compatibility spike (experiment)

Non-production. Nothing in `src/odessy` imports this directory, no installer entry
references it, and no Odessy default reads it. It answers one question with executable
evidence: **can the pinned DeepSeek Harness runtime meet Odessy's interactive requirements,
and through which transport?**

Pin (2026-11-08): `deepseek-harness-sdk` + `deepseek-harness-runtime-bin` **0.2.1rc2**,
source tag `dsh-v0.2.1-rc.2`. Hashes in [`manifest.lock.json`](manifest.lock.json).

## Result

| Requirement | Transport / mechanism | Verdict |
|---|---|---|
| Boot + handshake | ACP `--profile acp`, SDK `--profile sdk` | **proven** (real executable, isolated home) |
| Controlled prompt | both | **proven** (deterministic local provider) |
| Per-session cancellation | ACP `session/cancel` | **proven**; the other concurrent turn completed normally |
| Approvals | ACP `session/request_permission` | **proven** (raised, answered, honoured; nothing written) |
| Questions | — | **absent** at this pin (no ACP channel) |
| Resume | ACP `session/close` + `session/resume` | **proven** (history replayed) |
| MCP tool invocation | ACP `session/new.mcpServers` | **proven** (`mcp__spike__spike_echo`) |
| Live text deltas | bridge plugin over `agent/assistant-stream` | **proven** (5 deltas, first at 0.18–0.22 s of a 0.79–0.83 s turn) |
| Live text on raw SDK/ACP | — | **absent** (text only at settlement) |
| Live tool progress | ACP `tool_call` updates | **proven** (mid-turn) |
| External plugin install | `dsh plugin add file:` | **proven** with a workspace-local pnpm |
| One tool surface | overlay disables every built-in tool row | **proven** (roster = only the MCP tool; enforcement *not* tested here — 08–09) |
| Network denial / hidden paths | — | **absent** by design; retained through Odessy's own MCP tool server |

`smoke_acceptance.py` is the gate: **34 assertions, exit code 1 on any failure**. Three
consecutive runs passed.

## Files

| File | Purpose |
|---|---|
| `manifest.lock.json` | Pinned versions, wheel/executable/dependency hashes, upgrade procedure |
| `acp_client.py` | Minimal ACP v1 stdio client; per-turn update queues so concurrent turns stay separate |
| `stream_bridge.py` | Client for the bridge plugin (token auth, frame collection with receive times) |
| `mock_provider.py` | Deterministic local provider (Anthropic Messages + OpenAI chat SSE), request log, escalation trigger |
| `mcp_echo_server.py` | Three-call MCP stdio server used only to prove external tool invocation |
| `install_plugin.py` | Installs the spike bundle into an isolated profile using a workspace-local toolchain |
| `plugin/odessy-stream-bridge/` | The bundle: `package.json`, `cordis.patch.yml`, `lib/index.js` |
| `smoke_acceptance.py` | **The gate.** C0–C6 with assertions and non-zero exit |
| `smoke_sdk.py` | Same pin over the Python SDK: notification census, delta check, method probes, resume |

## Prerequisites

1. An isolated Python 3.10+ interpreter. The recorded runs used a dedicated venv from
   Python 3.11.9, with no checkout on `sys.path`.
2. The two pinned wheels (hashes in `manifest.lock.json`). Downloading needs network;
   running the smoke does not.
3. A **workspace-local** Node + pnpm for the one-time plugin install. The packaged runtime
   does not bundle pnpm, and installing it globally would change the developer machine.

```powershell
# inside the workspace, outside the repository
$TC = "<workspace>\.harness-spike\toolchain"
npm pack pnpm@11.7.0 --pack-destination $TC
tar -xzf "$TC\pnpm-11.7.0.tgz" -C $TC
Invoke-WebRequest 'https://nodejs.org/dist/v22.19.0/node-v22.19.0-win-x64.zip' -OutFile "$TC\node22.zip"
Expand-Archive "$TC\node22.zip" -DestinationPath $TC
# $TC\bin\pnpm.cmd must run: "<TC>\node-v22.19.0-win-x64\node.exe" "%~dp0..\package\bin\pnpm.cjs" %*
```

## Run

From the repository root, with `SPIKE=<absolute path outside this repository>`:

```powershell
py -3.11 -m venv $SPIKE\venv
$SPIKE\venv\Scripts\python.exe -m pip install `
  "deepseek-harness-sdk==0.2.1rc2" "deepseek-harness-runtime-bin==0.2.1rc2"
$EXE = "$SPIKE\venv\Lib\site-packages\deepseek_harness_runtime\runtime\deepseek-harness-sdk-runtime-win-x64.exe"

# one-time, per isolated home
$SPIKE\venv\Scripts\python.exe experiments\restructure\harness_compatibility\install_plugin.py `
  --exe $EXE --home $SPIKE\home-acp --toolchain $SPIKE\toolchain

# the gate
$SPIKE\venv\Scripts\python.exe experiments\restructure\harness_compatibility\smoke_acceptance.py `
  --exe $EXE --home $SPIKE\home-acp --work $SPIKE\work\accept

# transport comparison, not a gate
$SPIKE\venv\Scripts\python.exe experiments\restructure\harness_compatibility\smoke_sdk.py `
  --home $SPIKE\home-sdk --work $SPIKE\work\sdk
```

`acceptance.json` in the work directory records every check with its detail.

## Boundaries

- Harness home, session workspace and provider logs all live in the directories passed on
  the command line. Nothing is written to `~/.dsh`, `%APPDATA%` or the user's workspace.
- The only tool call that leaves the session workspace is deliberately denied by the
  sandbox; its escalated retry is deliberately **rejected** by the client, and the gate
  asserts the marker file does not exist.
- The deterministic provider is a fixture. A green run proves transport behaviour, not
  model quality, latency or provider compatibility. No real-provider evidence is claimed.
- The bridge plugin is a spike. It is not packaged, has one client, and has no backpressure
  policy; promoting it is prompt 05's work.
