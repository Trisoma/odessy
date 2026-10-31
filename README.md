# Odessy: A Voice-Controlled Edge-AI Personal Agent and Device Orchestration Platform

> **v0.5.0-unstable - unstable developer preview.** Expect bugs, breaking changes
> and rough edges. This is a source-first pre-release, not a production service.

| Platform | Support status |
|---|---|
| Windows 11 x64, from source | Supported preview; the only path with recent end-to-end evidence |
| Linux x86_64, from source | Experimental; CI tests only |
| Jetson Orin Nano 4GB | Experimental, headless; cloud backend recommended. Local model plus desktop UI hits GPU out-of-memory. |
| macOS | Untested / unsupported |

No minimum or recommended hardware has been established. Voice and split/LAN
execution are experimental. The installer, monitoring, custom wake word and MCU
devices are not supported or shipped in this preview. See the
[release notes](docs/releases/v0.5.0-unstable-notes.md) and [limitations](LIMITATION.md).

## From-source quick start

Use Python 3.10-3.13, Node 22, and an existing Ollama model or cloud API key.
On Windows 11 x64, from the repository root:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[ui]"
cd tools
npm ci --no-audit --no-fund
npm run build
cd ..
python -m odessy doctor
```

Configure your existing model with the [headless setup guide](docs/setup/headless.md)
or [cloud backend guide](docs/reference/cloud-models.mdx), then run
`python -m odessy "list the files here"`. `python -m odessy setup --role combined`
reviews a headless configuration with no default model download; it configures
existing components, not a completed installer or Flutter wizard.

### Desktop first run

1. Start `python -m odessy serve` and note the address and token it prints.
2. With Flutter 3.47.7 (stable), run `flutter pub get` and `flutter build windows`
   from `apps/desktop/`, then launch the built desktop app.
3. Connect the app to that backend using its address and token.
4. Configure an existing Ollama model or cloud backend in **Settings**, then chat.
   Try a policy-controlled tool call and check both approve and reject.

There is no installer in this release. Any optional Windows desktop zip is a
**UI client only** requiring a separately started source backend, and must pass
clean-machine acceptance before attachment. Binaries are unsigned; SmartScreen
warnings are expected. Voice needs separately fetched assets.

**Security:** the experimental LAN executor uses unencrypted HTTP; use it only on
a trusted LAN or point-to-point link. `ODESSY_MCP_HOST=0.0.0.0` exposes an
unauthenticated executor. Prefer an SSH tunnel or Rust/mTLS pairing. Command
isolation depends on the host; Windows is partial, Jetson confinement is largely
unavailable, and `terminal.run` without Node.js is unconfined. Read
[SECURITY.md](SECURITY.md) and report vulnerabilities privately.

Third-party runtime and model licenses are listed in the
[license inventory](docs/reference/third-party-licenses.md) and
[attribution notices](THIRD-PARTY-NOTICES.md). Odessy's Apache-2.0 license does
not replace model-specific terms, including the Hey Jarvis weights' noncommercial
restriction or the default phonemizer stack's GPL licenses.

Historical v0.4.0 was released on October 23, 2026; see its
[release record](docs/releases/v0.4.0.md). Future work remains in the
[v0.5.0 roadmap](docs/releases/v0.5.0.md), including finished onboarding with
model detection/choice, Go multi-host monitoring, the custom Odessy wake word,
GitHub Pages documentation and the deferred Windows installation wizard.
These roadmap goals are not already available.

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

## History: v0.3.0 capabilities

- LFM2.5-Thinking 1.2B through Ollama is the default model path.
- Gemma 3 1B remains available with `--model-backend ollama`.
- Native Ollama tool calls are preferred for LFM; text JSON is a compatibility fallback.
- Runs on one computer by default; the Jetson + laptop layout is the `split` deployment.
- Voice uses Whisper.cpp STT and Kokoro ONNX TTS.
- Policy and confirmation remain in the Python runtime.
- Optional cloud backends support native tool conversations; DeepSeek accepts
  configured reasoning effort and retains returned reasoning content.
- The agent evaluation suite covers 56 safety, command, tool, multi-step, and hard cases
  (v0.4.0 adds 24 third-party server, conversation, and batch cases, and 2 long-context hard cases)
  using recorded execution, with code-provenance diagnostics in run metadata.

> The long-term technical category could be:
> Embodied Edge-AI Agent for Multimodal Device and Engineering Automation

## History: v0.4.0 capabilities and v0.5.0 plans

Extra features beyond the original plan now include model collaboration and the
session API, mid-task clarification, cloud caching/usage budgets, and cloud
resilience. PR #23 (`d05e1b8`) merged configured cloud failover, sentence-level cloud
wake speech and a content-free egress log; PR #24 merged risk-aware voice approval
and clarification. These are included in v0.4.0; selected live-provider and hardware acceptance
are pending. See the [updated roadmap](docs/releases/v0.4.0.md).

Configurable collaboration is included in v0.4.0. It
adds named cloud/local/coding-agent participants,
attributed consultations, confirmed jobs, and shared CLI/voice sessions. It is
off until configured. An optional loopback bearer API and diagnostic client run
with `python -m odessy serve` (`pip install -e '.[ui]'`). See
[model collaboration](docs/reference/model-collaboration.mdx) for setup and limits.

Third-party MCP servers are an extra feature beyond the original v0.4.0 roadmap,
merged through PR #11. Configure HTTP or stdio servers in `servers.toml` to offer
their namespaced tools under Odessy's validation and authorization path. See
[external servers](docs/reference/external-servers.mdx) for trust and execution
boundaries, and the [roadmap](docs/releases/v0.4.0.md) for status and follow-ups.
This feature is not part of the released v0.3.0 private prerelease.

Opt-in automatic selection is also available in v0.4.0 with
`--model-backend auto`: narrow text tasks use a tool-free fast model, while
uncertain/tool requests use the configured capable model. Cloud fallback is off
by default. See [model selection](docs/reference/model-selection.mdx); Jetson
speed and memory benefits have not been established.

Wake listening, confirmation UX (PR #17), automatic model selection (PR #19), and
batch confirmation follow-ups (PR #18) are included in v0.4.0. `python -m odessy voice --listen` uses the integrated wake session.
October 20 Jetson smoke tests reached transcription, model execution, and speaking;
full hardware acceptance remains pending. Recorded Jetson greetings initially selected Gemma, then visibly fell back to Q8 after empty fast-model output; this does not establish a speedup. Follow
[Jetson wake testing](docs/jetson-wake-testing.md), including the auxiliary
openWakeWord model downloads, the integrated VAD correction, and the Q8 profile.

## Python source layout

Implementation modules under `src/odessy/` are grouped by responsibility:

| Package | Responsibility |
|---|---|
| `runtime/` | Agent loop, shared message types, repeat guard, and tool deadlines |
| `models/` | Cloud, Ollama, LFM, and fake model adapters |
| `tools/` | MCP servers, external tools, tool schemas, and calculator |
| `authority/` | Policy, workspace boundaries, command bridge, redaction, and output retention |
| `config/` | Configuration files, deployment selection, and setup diagnostics |
| `execution/` | Execution contracts, providers, isolation, and escalation |
| `devices/` | Device descriptors and profiles |
| `voice/` | Audio input/output, STT, TTS, and voice loop |

`cli.py` and `__main__.py` remain at the package root, so CLI commands and console
entry points stay the same. New Python code should use the grouped imports, such
as `from odessy.runtime.agent_loop import OdessyAgent`. Historical imports such as
`odessy.agent_loop` still resolve through the small aliases in `_compat/`; these
return the canonical module rather than duplicating classes or module state.

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

Windows 11 x64 from source is the supported preview path. Linux x86_64 is
experimental; Jetson Orin Nano 4GB is experimental and headless, with a cloud
backend recommended. macOS is untested and unsupported.

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

For the current experimental headless LAN executor, follow the
[Jetson + Windows step-by-step guide](docs/setup/jetson-windows-lan-execution.md).
It covers dependencies, Wi-Fi/Ethernet selection, terminal Y/N approval and an
actual remote tool check. This experimental prototype is unencrypted HTTP, for a
trusted LAN or point-to-point link only. Prefer the authenticated Rust/mTLS
enrollment flow below.

Optional authenticated split deployment uses `ODESSY_PAIRED_HOST=<enrolled-alias>`
and the Rust `odessy-link` transport. Enroll both hosts, then grant the permitted
workspace on the execution host; see [host pairing](docs/reference/host-pairing.mdx).
Windows loopback tests pass; Jetson, real-network and Linux/ARM64 qualification
remain pending. This preserves existing tool policy and confirmation requirements.

Settings are environment variables, read in this order (first wins):

1. the environment;
2. your own file, `~/.config/odessy/odessy.env` (create it; it is never committed);
3. the repository defaults, `config/odessy.env`.

Put machine-specific values (model paths, the laptop address) in your own file rather
than editing `config/odessy.env`. `ODESSY_CONFIG_FILE=<path>` loads that one file
instead of both. `python -m odessy doctor` shows which files it found.

Every `ODESSY_*` setting is declared with the shape its value must have
(`src/odessy/config/schema.py`), and the values a run will use are checked before it
starts: a malformed number, an unknown deployment, a bad URL, or a required path that
does not exist stops the run with the setting named and the fix. A name Odessy does
not read is reported as a warning with the closest real setting, because a misspelled
setting that is silently ignored is worse than one that is refused. The voice paths
are required only by `python -m odessy voice`. `python -m odessy doctor` reports the
same issues and still starts when the configuration is broken.

Third-party MCP servers are listed in `~/.config/odessy/servers.toml`. Their tools are
offered as `<server>.<tool>`. Calls require confirmation by default; explicit
per-tool config may allow or deny them, and trusted read-only annotations may
allow them under the documented rules. Each call is recorded in an audit log, and
`python -m odessy doctor` checks the servers. See `docs/reference/external-servers.mdx`.

Hooks are programs you list in `~/.config/odessy/hooks.toml` to run before a tool
call, before a sandbox escalation, after a call, or when a turn ends; rules in the
same file deny or confirm calls by pattern without a program, and
`presets = ["recommended"]` adds rules for force pushes, piping downloads into a
shell, publishing packages and the like. They can deny a call or ask for
confirmation, never allow more than the policy does; a post-tool hook can add a
note, mark the turn for later rules, stop it, or withhold a result from the model.
See [hooks](docs/reference/hooks.mdx) and [permissions](docs/reference/permissions.mdx);
`python -m odessy permissions check "<command>"` shows how a call would be decided
without running it.

Commands run in a per-call sandbox (bwrap on Linux, seatbelt on macOS, a partial
windows-acl boundary on Windows). By default a command run without asking has no
network and cannot read credential folders such as `~/.ssh` (where the backend can
enforce it), and every command gets memory, file-size and CPU limits.
`~/.config/odessy/sandbox.toml` tunes this, and `python -m odessy doctor` shows what
this machine enforces. See [sandbox](docs/reference/sandbox.mdx).

`ODESSY_MAX_MODEL_CALLS` caps the model calls in one turn, counting corrections and
follow-up steps. With a cloud backend (about 2 s a call) the default is 8 for a spoken
turn and 20 for a typed turn or the session API; with a local one (about 30 s a call
on the Jetson) it is 4. A cloud model's last allowed call has no tools, so it answers
with what was done and what is left instead of stopping mid-task.

## Voice

v0.4.0 adds keyboard input throughout an interactive voice session:
`python -m odessy voice --listen --fake-model`. Type a follow-up at any time while
idle; `/text` pauses audio, `/voice` retries it, `/stop` stops the current turn,
and `/exit` closes the session. Busy submissions are rejected rather than queued.
Answers are displayed once, and voice requests are also spoken when available.
Use `--no-text-fallback` for the previous audio-only interface. Redirected stdin
is never consumed as requests or approvals. Jetson smoke checks passed; physical echo/pre-cue and packaged acceptance remain deferred; see the
[voice reference](docs/reference/voice-integration.mdx).

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

#### SSH tunnel (preferred legacy split path)

The server binds `127.0.0.1` by default, which is loopback only, reachable only
from the laptop itself. Keep that bind and run this from the laptop:

```bash
ssh -R 8765:127.0.0.1:8765 <user>@<jetson>
```

Then point the Jetson at `http://127.0.0.1:8765/mcp`. This overrides the direct-LAN
URL example above, needs no firewall rule, and is authenticated. For the separate
Rust/mTLS path, see [host pairing](docs/reference/host-pairing.mdx).

#### Warning: direct LAN bind exposes an unauthenticated executor

`ODESSY_MCP_HOST=0.0.0.0` exposes an **unauthenticated** command executor to
every network the laptop joins. This is insecure by design on untrusted networks.
Use only a trusted LAN or point-to-point Ethernet link, and set it per session
rather than persistently. Prefer the SSH tunnel above.

To let the Jetson reach it directly over the network, set the bind host explicitly:

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
[Cloud Model Backends](docs/reference/cloud-models.mdx). There you can also set
fallback providers for outages (`ODESSY_CLOUD_FAILOVER`). Every cloud request is
recorded without content in an egress log (`python -m odessy egress`). Voice
sessions speak a cloud answer sentence by sentence as it arrives.

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

See [current limitations](LIMITATION.md) and the
[reference](docs/reference/current-limitations.mdx). v0.5.0-unstable is not a production network service, always-on voice assistant, or autonomous background agent.

It has an isolation layer, and that is **not** the same as being sandboxed. Coverage depends on the host: on the Jetson board most backends report unavailable, and each run states the enforcement it actually achieved rather than implying one. Do not describe Odessy as sandboxed, jailed, or contained without that qualifier.

The current policy and authority rules are introduced in `policy/README.md`.
