# Odessy: A Voice-Controlled Edge-AI Personal Agent and Device Orchestration Platform

> v0.3.0 was released privately on October 19, 2026, and is marked as a prerelease.
> See [release notes](docs/releases/v0.3.0.md) for changes and verification limits.
> Future feature work belongs to the [v0.4.0 roadmap](docs/releases/v0.4.0.md).

Odessy is a local, single-user edge-AI agent runtime. It runs on one computer, or split across a Jetson (model and voice) and a laptop (controlled command execution). An optional cloud model backend can replace the local model; policy, authorization, and confirmation stay local in every configuration. See `docs/reference/cloud-models.mdx`.

```text
Voice command
    -> local AI reasoning
    -> policy and intent validation
    -> device/tool orchestration
    -> observation and feedback
    -> spoken response
```

The model proposes actions. Python Odessy validates and authorizes them. MCP transports them. The model is never the authority.

## Released v0.3.0 capabilities

- LFM2.5-Thinking 1.2B through Ollama is the default model path.
- Gemma 3 1B remains available with `--model-backend ollama`.
- Native Ollama tool calls are preferred for LFM; text JSON is a compatibility fallback.
- Runs on one computer by default; the Jetson + laptop layout is the `split` deployment.
- Voice uses Whisper.cpp STT and Kokoro ONNX TTS.
- Policy and confirmation remain in the Python runtime.
- Optional cloud backends support native tool conversations; DeepSeek accepts
  configured reasoning effort and retains returned reasoning content.
- The agent evaluation suite covers 56 safety, command, tool, multi-step, and hard cases
  using recorded execution, with code-provenance diagnostics in run metadata.

> The long-term technical category could be:
> Embodied Edge-AI Agent for Multimodal Device and Engineering Automation

## Deployment

Odessy runs in one of two deployments:

| | `single` (default) | `split` |
|---|---|---|
| Machines | One computer runs everything | A Jetson runs the model and voice; a laptop runs the tool server |
| Tool server | Started automatically on loopback for each run | Started by hand on the laptop, reached over the network |
| Command tool | `terminal.run`, on this computer | `terminal.run` on the Jetson, `laptop.terminal.run` on the laptop |
| Selected by | Default when no remote tool server is configured | Setting `ODESSY_TOOL_SERVER_URL` (or `ODESSY_DEPLOYMENT=split`) |

In both, requests pass through Odessy validation, workspace authority, policy, and
confirmation before anything executes. The model is told which operating system and
shell its commands run in.

## Quick Start (one computer)

Works on Windows, macOS, and Linux.

Requirements:

- Python 3.10+
- [Ollama](https://ollama.com), or an API key for a cloud model (see `docs/reference/cloud-models.mdx`)
- Node.js 22+ for the tool server (recommended; without it Odessy still runs, with fewer tools)

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\Activate.ps1
pip install -e .
cd tools && npm install && npm run build && cd ..
ollama pull lfm2.5-thinking:1.2b-q8_0
```

Check the setup. Every problem it finds comes with the command that fixes it:

```bash
python -m odessy doctor
```

Then run from the folder you want Odessy to work in (that folder is its workspace):

```bash
python -m odessy "what time is it?"
python -m odessy "list the files here"
python -m odessy "git status"
```

Without Node.js, Odessy says so on startup and runs with time, calculator, and an
unconfined `terminal.run` only (no file, search, or git tools).

Voice (`python -m odessy voice`) currently needs Whisper.cpp, Kokoro, and the Linux
`arecord`/`aplay` programs; see [Voice](#voice).

## Configuration

Settings are environment variables, read in this order (first wins):

1. the environment;
2. your own file, `~/.config/odessy/odessy.env` (create it; it is never committed);
3. the repository defaults, `config/odessy.env`.

Put machine-specific values (model paths, the laptop address) in your own file rather
than editing `config/odessy.env`. `ODESSY_CONFIG_FILE=<path>` loads that one file
instead of both. `python -m odessy doctor` shows which files it found.

Third-party MCP servers are listed in `~/.config/odessy/servers.toml`. Their tools are
offered as `<server>.<tool>`, and every call to a server not marked `trusted` needs
your confirmation. See `docs/reference/external-servers.mdx`.

`ODESSY_MAX_MODEL_CALLS` caps the model calls in one turn, counting corrections and
follow-up steps: 8 by default for a cloud backend (about 2 s a call), 4 for a local
one (about 30 s a call on the Jetson).

## Voice

Voice uses Whisper.cpp STT and Kokoro ONNX TTS. Set their paths in
`~/.config/odessy/odessy.env`; the files do not need to be in the repository:

```dotenv
ODESSY_WHISPER_BIN=/home/<user>/whisper.cpp/build/bin/whisper-cli
ODESSY_WHISPER_MODEL=/home/<user>/whisper.cpp/models/ggml-tiny.en.bin
ODESSY_KOKORO_MODEL=/home/<user>/odessy-voice-test/kokoro-v1.0.int8.onnx
ODESSY_KOKORO_VOICES=/home/<user>/odessy-voice-test/voices-v1.0.bin
```

`ODESSY_WHISPER_MODEL` and `ODESSY_KOKORO_*` are filesystem paths. The model names
(`ODESSY_OLLAMA_MODEL`, `ODESSY_MODEL_NAME`) are Ollama model names, not paths.

## Two-Machine Setup (Jetson + laptop)

The original deployment: the Jetson hosts the model and voice loop, and the laptop
runs the tool server. On the Jetson, put the laptop's address in
`~/.config/odessy/odessy.env`, which selects `split`:

```dotenv
ODESSY_TOOL_SERVER_URL=http://<laptop-ip>:8765/mcp
# Optional: tells the model which shell the laptop uses
ODESSY_LAPTOP_OS=windows
ODESSY_OLLAMA_MODEL=lfm2.5-thinking:1.2b-q8_0
ODESSY_MODEL_NAME=lfm2.5-thinking:1.2b-q8_0
ODESSY_MODEL_ENDPOINT=http://127.0.0.1:11434
```

> **Upgrading an existing Jetson:** `config/odessy.env` no longer sets
> `ODESSY_TOOL_SERVER_URL`. Add that line to `~/.config/odessy/odessy.env` on the
> Jetson, or Odessy will start in `single` mode.

### Laptop MCP Server

From `tools/` on the laptop:

```powershell
npm install
npm run start:http
```

#### The bind address matters

The server binds `127.0.0.1` by default, which is **loopback only** — reachable
only from the laptop itself. For the Jetson to reach it over the network, set the
bind host explicitly:

```powershell
$env:ODESSY_MCP_HOST="0.0.0.0"
npm run start:http
```

The startup banner reports the bind, so confirm it:

```text
Odessy MCP HTTP server listening on http://0.0.0.0:8765/mcp
```

If it still reads `127.0.0.1`, the variable did not reach the `npm` process.

Set `ODESSY_TOOL_SERVER_URL` in the Jetson configuration to the laptop MCP URL,
for example `http://10.1.1.1:8765/mcp`.

#### Verify before running the agent

From the Jetson:

```bash
curl -v --max-time 5 http://<laptop-ip>:8765/mcp
```

A `400 Bad Request` with `Missing or invalid MCP session` is the **correct**
response — it means the server answered. The failure modes are distinguishable:

```text
Connection timed out     firewall is dropping the port
Connection refused       nothing listening on that interface (still loopback-only)
400 + MCP session text   reachable and working
```

#### Security

`ODESSY_MCP_HOST=0.0.0.0` exposes an **unauthenticated** command executor to
every network the laptop joins. Set it per session rather than persistently, and
prefer a point-to-point Ethernet link or an SSH tunnel over a shared network.

To tunnel instead of exposing the port, run this from the laptop:

```bash
ssh -R 8765:127.0.0.1:8765 <user>@<jetson>
```

Then point the Jetson at `http://127.0.0.1:8765/mcp`. That keeps the loopback
bind, needs no firewall rule, and is authenticated.

## Run

Text mode (in `split`, add "on my laptop" to run a command on the laptop):

```bash
python -m odessy "run pwd"
```

Voice mode:

```bash
python -m odessy voice
```

Hardware-free voice plumbing test:

```bash
python -m odessy voice --fake-model
```

Gemma fallback:

```bash
python -m odessy --model-backend ollama "what time is it?"
```

Optional cloud model (Claude, GPT, DeepSeek, Qwen, Kimi, or any OpenAI-compatible
API). The cloud model only proposes; policy and confirmation still run locally, and
requests plus redacted tool output are sent to the provider:

```bash
pip install -e .[cloud]   # needed for Claude only
export DEEPSEEK_API_KEY=...
export ODESSY_CLOUD_MODEL=deepseek-flash
export ODESSY_CLOUD_EFFORT=low
python -m odessy --model-backend deepseek "on my laptop, run pwd"
```

This explicitly selects `deepseek-flash` with low reasoning effort. Without these
overrides the DeepSeek preset remains `deepseek-chat` with provider-default effort.
Providers, PowerShell equivalents, and data-flow details are in
[Cloud Model Backends](docs/reference/cloud-models.mdx).

## Demo

The reproducible release demonstration is documented in `docs/demo-v0.1.0.md`.

## Test

```bash
pytest -q
```

The benchmark suite includes fake hardware-free scenarios and real Jetson scenarios. Real voice runs require configured audio and model assets.

GitHub Actions runs Python and Node checks on Ubuntu and Windows; see
[the workflow](.github/workflows/ci.yml) for its version matrix. Workflow coverage
is not a claim of Jetson hardware or live-provider validation.

## Evaluate

`evals/` measures agent behaviour with a real model: safety (does a dangerous
action ever execute), command generation, tool selection, and multi-step tasks.
Nothing is executed on the host. See `evals/README.md`.

```bash
python -m evals run --backend lfm
```

For a DeepSeek check using the cloud settings above:

```bash
python -m evals run --backend deepseek --label deepseek-low-smoke --case tools-time --repeat 1
python -m evals run --backend deepseek --label deepseek-low
```

New `metadata.json` files add `git_commit_note`: a nonempty `ODESSY_COMMIT`
override wins, otherwise Git runs from the repo root, then a labeled source-tree
hash is recorded with failure details if Git is unavailable. A null commit always
has a note. Older run files are not repaired retroactively. See
[the eval guide](evals/README.md#code-provenance) for coverage and comparison limits.

## Known Limits

See `docs/reference/current-limitations.mdx`. v0.3.0 is not a production network service, always-on voice assistant, or autonomous background agent.

It has an isolation layer, and that is **not** the same as being sandboxed. Coverage depends on the host: on the Jetson board most backends report unavailable, and each run states the enforcement it actually achieved rather than implying one. Do not describe Odessy as sandboxed, jailed, or contained without that qualifier.

The current policy and authority rules are introduced in `policy/README.md`.
