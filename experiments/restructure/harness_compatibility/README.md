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
| `mock_provider.py` | Deterministic local provider (Anthropic Messages + OpenAI chat SSE), request log, escalation trigger. `prompt_of()` skips the runtime's own user-role context snapshot, so a marker in the request ("slow", "tool …") selects the scenario |
| `mcp_echo_server.py` | Three-call MCP stdio server used only to prove external tool invocation |
| `install_plugin.py` | Installs the packaged bundle into an isolated profile using a workspace-local toolchain; it resolves the bundle from `odessy.runtime_backends.harness.resources`, so nothing is copied here |
| `src/odessy/runtime_backends/harness/bundle/odessy-stream-bridge/` | **The** bundle (`package.json`, `cordis.patch.yml`, `lib/index.js`), owned by the product package and declared in `harness-artifacts.json` with a digest per file. It forwards live frames **and** answers the runtime's `user-questions` seam over the same authenticated socket; its patch also mounts the upstream `tool-ask-user` row |
| `smoke_acceptance.py` | **The gate.** C0–C6 with assertions and non-zero exit |
| `smoke_sdk.py` | Same pin over the Python SDK: notification census, delta check, method probes, resume |
| `verify_no_tools.py` | Boots the pin with one or more overlays and reads the tool roster the provider received. Exits 1 unless the roster is within `--allow` (prompt 07 passes `--allow ask_user_question`). Used to design `odessy_narrow.patch.yml` |
| `smoke_p03.py` | Drives the **production** `HarnessRuntime` (`from_environment` → `start`/`run_turn`/`close`) against the pinned binary: version identity, allowlisted environment, only the clarification tool, settled text, no orphan |
| `probe_providers.py` | Prints the model catalogue the pin advertises and what it does with pairs it does not offer |
| `probe_piai_route.py` | Declares one `llm-pi-ai` route and reports whether it appears, is selectable, and reaches the endpoint (both wire apis) |
| `smoke_p04.py` | Model routes end to end against the pin: missing key refused, declared route used, context usage recorded, model switched in the same process |
| `smoke_p05.py` | Live event translation end to end against the pin with the real plugin: bridge attached, deltas before settlement, deltas join to the committed answer, no loss, no orphan |
| `smoke_p06.py` | Per-session Stop against the pin: two sessions generating, one cancelled (prompt settles `cancelled`), the other completes, the stopped session runs another turn, no orphan |
| `smoke_p07.py` | Clarification end to end against the pin with the real bundle: the runtime's `ask_user_question` executes, the question reaches the production broker, the answer returns, the same turn continues, and a Stop while waiting settles with no orphan |
| `probe_sandbox_vocabulary.py` | Asks the pin what sandbox vocabulary it exposes: composed `sandbox*` rows from `--dump-config`, accepted fields from `--dump-config-schema`, and every `session/new` config option (plus whether a set is accepted). This is where §22's "the pin accepts only `mode` and `workspaceRoot`" comes from |
| `verify_enforcement.py` | Runs real commands through every backend this host offers under a `network=deny` + hidden-path policy and prints what happened, with exit codes and the denial signatures. Reports a backend that fails its own control as **unusable**, not strict |
| `smoke_p08.py` | Pinned evidence for prompt 08: the pin composes Odessy's `sandbox-policy` (mode + workspace root), a widening overlay applied earlier loses, a real `SessionController` reports the plan it launched with and hands the child `DSH_PERMISSION_MODE`, a `required` policy refuses by name, and `close()` leaves no orphan |
| `plugin_backpressure.mjs` | The plugin alone, loaded with a stub context: a client that stops reading loses frames, the loss is reported, the connection survives |

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

# live event translation (needs the plugin installed in $HOME)
$SPIKE\venv\Scripts\python.exe experiments\restructure\harness_compatibility\smoke_p05.py `
  --exe $EXE --home $SPIKE\home-acp --work $SPIKE\work\p05 `
  --toolchain $SPIKE\toolchain          # only needed when the plugin is not installed yet

# the plugin alone, with a client that stops reading (workspace-local Node)
$TC\node-v22.19.0-win-x64\node.exe `
  experiments\restructure\harness_compatibility\plugin_backpressure.mjs

# per-session Stop (two sessions, one cancelled; no plugin needed)
$SPIKE\venv\Scripts\python.exe experiments\restructure\harness_compatibility\smoke_p06.py `
  --exe $EXE --home $SPIKE\home-acp --work $SPIKE\work\p06
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
- The bridge plugin is installed by hand (`install_plugin.py`) and its composed profile is not
  packaged yet; the production source of the bundle itself is the `odessy` package, and the
  production *client* for it lives in `src/odessy/runtime_backends/harness/bridge.py` (prompt 05),
  and both ends of the socket are bounded — see
  [`../../../../restructure/doc/reports/05.md`](../../../../restructure/doc/reports/05.md).
| `smoke_p09.py` | The guarded tool pipeline on the pin: the model is offered Odessy's own MCP server and no built-in execution row, an allowed command really runs and its result names the confining provider, an unapproved write is refused and leaves no file, and a call that tries to choose its sandbox mode is refused |
