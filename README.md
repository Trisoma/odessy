# Odessy

> **Unstable developer preview.** Odessy is experimental software. Expect bugs,
> breaking changes and rough edges. No stable release exists yet. See the support
> matrix below for tested platforms and known limitations.

Hi, this is Odessy, not Odyssey! ⛵

Odessy is a local, single-user AI agent runtime. A model proposes actions; Odessy's
Python policy layer validates them, asks you to confirm the risky ones, and runs
tools through an MCP tool server. It runs on one computer, or split across a Jetson
(model and voice) and a laptop (controlled command execution). An optional cloud
backend can replace the local model; policy, authorization and confirmation stay
local in every configuration.

| Platform | Support status |
|---|---|
| Windows 11 x64, from source | Supported preview; the only path with recent end-to-end evidence |
| Linux x86_64, from source | Experimental; CI tests only |
| Jetson Orin Nano 4GB | Cloud backend recommended. Local model plus desktop UI hits GPU out-of-memory. |
| macOS | Untested / unsupported |

No minimum or recommended hardware has been established. The installer, monitoring,
custom wake word and MCU devices are not shipped in this preview. See
[limitations](LIMITATION.md), the
[release notes](docs/releases/v0.5.0-unstable-notes.md) and the
[changelog](CHANGELOG.md).

## Build Odessy with us

> Curious how this started? [Read the story behind Odessy](Story.md).

We’re a small team of two regular contributors, with three others helping out on a short-term basis—and we’d love some company. Help fix a bug, improve a component, or work with us on a larger part of the project.

Some areas we'd love help with:

- **AI agents:** orchestration, tool calling, model integration and memory.
- **Hardware exploration:** Jetson, Raspberry Pi, BLE and MCU communication.
  These are areas of interest, not claims of current device support.
- **Systems:** Rust, networking, mTLS and secure execution.
- **Desktop:** Flutter, Dart, accessibility and UI/UX.
- **Infrastructure:** Go, monitoring, telemetry and multi-host management.
- **Voice:** STT, TTS, wake-word detection and audio processing.

Odessy is an independent, volunteer-driven project. Contributions are unpaid,
and there's no fixed time commitment—help when you can.

Start with [Contributing](CONTRIBUTING.md), browse
[open issues](https://github.com/Trisoma/odessy/issues), or suggest an idea.
For larger changes, discuss the approach with us before starting.

### Community and contributing

- **Contributing:** [CONTRIBUTING.md](CONTRIBUTING.md)
- **Code of conduct:** [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)
- **Bugs and feature requests:** [issue chooser](https://github.com/Trisoma/odessy/issues/new/choose) · [hardware report form](.github/ISSUE_TEMPLATE/hardware_report.yml)
- **What is planned:** [roadmap](docs/ROADMAP.md)
- **How it fits together:** [architecture](docs/ARCHITECTURE.md)
- **Security issues:** report them privately per [SECURITY.md](SECURITY.md) — never in a public issue

## Documentation

| Goal | Start here |
|---|---|
| Install and configure a model | [Headless setup](docs/setup/headless.md) · [Cloud models](docs/reference/cloud-models.mdx) |
| Understand the design | [Architecture](docs/ARCHITECTURE.md) · [Policy and authority](policy/README.md) |
| Change what tools may do | [Permissions](docs/reference/permissions.mdx) · [Hooks](docs/reference/hooks.mdx) · [Sandbox](docs/reference/sandbox.mdx) |
| Run two machines | [Host pairing](docs/reference/host-pairing.mdx) · [Jetson + Windows guide](docs/setup/jetson-windows-lan-execution.md) |
| Add voice | [Voice reference](docs/reference/voice-integration.mdx) |

## Get started

Odessy has two entry points: the **desktop app** and the **terminal**. Both need the same
runtime — Python 3.10-3.13, Node 22, and an existing Ollama model or a cloud API key. The
desktop app additionally needs Flutter.
As an unstable version, we recommend trying the UI under windows environment for quick start, skilled devs could also try other OS.

### Step 1 — install the runtime (required by both)

From the repository root on Windows 11 x64:

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

`doctor` reports every problem with the command that fixes it. Fix what it names before
going further.

### Step 2 — choose desktop or terminal

#### Desktop app (Unstable)

There is no installer. The app is a **UI client**: it needs the backend from Step 1 running
alongside it, and it cannot run on its own.

**Get the app** — download it, or build it:

*Download a prebuilt archive* from the
[v0.5.0-unstable release](https://github.com/Trisoma/odessy/releases/tag/v0.5.0-unstable):

| Platform | Archive |
|---|---|
| Windows 11 x64 | `odessy-desktop-v0.5.0-unstable-windows-x64-ui-client.zip` |
| Linux x86_64 | `odessy-desktop-v0.5.0-unstable-linux-x86_64-ui-client.tar.gz` |
| Linux arm64 | `odessy-desktop-v0.5.0-unstable-linux-arm64-ui-client.tar.gz` |

Verify it against `SHA256SUMS.txt` from the same release, then extract it **outside** any
source checkout:

```powershell
Get-FileHash -Algorithm SHA256 .\odessy-desktop-v0.5.0-unstable-windows-x64-ui-client.zip
Expand-Archive .\odessy-desktop-v0.5.0-unstable-windows-x64-ui-client.zip -DestinationPath C:\odessy-ui
```

Keep the extracted folder together — the DLLs and `data\` beside the executable are
required, and copying the exe alone will not run.

> These archives are **unsigned**, so SmartScreen warns you, and they have **not been run
> end to end by the maintainers** yet. They are experimental UI clients, not installers.

*Build it yourself* instead if you are on a platform with no archive, or want your own
build. With Flutter 3.47.7 stable, from `apps\desktop`:

```powershell
flutter pub get
flutter build windows --release
```

The result is `apps\desktop\build\windows\x64\runner\Release\odessy_desktop.exe`, with the
same folder rule. On Linux, `flutter build linux --release` produces
`build/linux/x64/release/bundle/` — run `./bundle/odessy_desktop`. Linux is experimental:
CI-tested, with no GUI acceptance.

**Then, for either route:**

1. Start the backend and leave the window open:

   ```powershell
   $env:ODESSY_API_TOKEN = python -c "import secrets; print(secrets.token_urlsafe(32))"
   python -m odessy serve
   ```

   It prints `[odessy] HTTP API: http://127.0.0.1:8765`. Use the value of
   `$env:ODESSY_API_TOKEN` when connecting; the backend never logs it.

2. Launch `odessy_desktop.exe`, enter that address and token, then configure your existing
   model in **Settings**.

3. Send a message that needs a tool, and check both answers: **approve** runs it, **reject**
   refuses it and tells the model so.

#### Terminal

Fastest way to confirm the runtime works, with no Flutter build:

```powershell
python -m odessy "what time is it?"
```

Then follow [Running Odessy](#running-odessy) for model setup, approval behaviour and
troubleshooting.

## Security

The experimental LAN executor uses **unencrypted HTTP**; use it only on a trusted LAN or a
point-to-point link. `ODESSY_MCP_HOST=0.0.0.0` exposes an **unauthenticated** command
executor. Prefer an SSH tunnel or the Rust/mTLS pairing path.

Command isolation depends on the host: Windows is partial, Jetson confinement is largely
unavailable, and `terminal.run` without Node.js is unconfined. Do not describe Odessy as
sandboxed without that qualifier.

Read [SECURITY.md](SECURITY.md) and report vulnerabilities privately, not in public issues.

Third-party runtime and model licenses are listed in the
[license inventory](docs/reference/third-party-licenses.md) and
[attribution notices](THIRD-PARTY-NOTICES.md). Odessy's Apache-2.0 license does
not replace model-specific terms, including the Hey Jarvis weights' noncommercial
restriction or the default phonemizer stack's GPL licenses.

## How it works

```text
Voice command
    -> local AI reasoning
    -> policy and intent validation
    -> device/tool orchestration
    -> observation and feedback
    -> spoken response
```

The model proposes actions. Python Odessy validates and authorizes them. MCP transports them. The model is never the authority.

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

## Running Odessy

Run from the folder you want Odessy to work in. That folder **is** its workspace, and
it is the only place tools may act without asking.

### 1. Check the installation

```bash
python -m odessy doctor
```

It reports every problem with the command that fixes it: Python version, the Node tool
server, model reachability, voice assets, configuration files and the sandbox this
machine can enforce. Run it again after each fix.

### 2. Point Odessy at a model you already have

Local, through Ollama:

```bash
ollama list                                  # what is already installed
ollama serve                                 # if it is not running
```

```dotenv
# ~/.config/odessy/odessy.env
ODESSY_OLLAMA_MODEL=<exact-name-from-ollama-list>
```

No model is downloaded by Odessy. For a cloud provider instead, see
[Model backends and voice](#model-backends-and-voice).

### 3. Run a first prompt

```bash
python -m odessy "what time is it?"          # no tool server needed
python -m odessy "list the files here"       # file tools
python -m odessy "git status"
```

Odessy prints the tool call it wants to make. Anything that writes, executes or leaves
the workspace waits for a Y/N answer on the terminal — read it before you answer. `n`
refuses the call and the model is told it was refused.

### 4. See how a command would be judged, without running it

```bash
python -m odessy permissions check "rm -rf ~"
```

This shows the decision (`allow`, `confirm`, `deny`) so you can tune
`~/.config/odessy/sandbox.toml` and your hook rules safely.

### 5. Talk to it

```bash
python -m odessy voice                       # needs voice assets configured
python -m odessy voice --fake-model          # plumbing test, no model or audio
python -m odessy serve                       # backend for the desktop app
```

### If it does not work

| Symptom | Likely cause | Fix |
|---|---|---|
| `doctor` reports the tool server as missing | Node.js not installed, or `tools/` not built | `cd tools && npm ci && npm run build` |
| Only time and calculator are available | Node tool server did not start | see the row above; Odessy says so on startup |
| Every turn fails on the model | configured model name does not exist | `ollama list`, then correct `ODESSY_OLLAMA_MODEL` |
| Settings seem ignored | a misspelled name | `doctor` reports unknown names with the closest real setting |
| Commands run unconfined | no sandbox backend on this host | `doctor` shows what is enforced; read [sandbox](docs/reference/sandbox.mdx) |
| Desktop app cannot connect | backend not started, or wrong token | Set `ODESSY_API_TOKEN` as above, run `python -m odessy serve`, and connect using that token |

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

An interactive voice session also accepts keyboard input, for example
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

## Model backends and voice

In `split`, add "on my laptop" to a prompt to run that command on the laptop.

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
[reference](docs/reference/current-limitations.mdx). Odessy is not a production network service, always-on voice assistant, or autonomous background agent.

It has an isolation layer, and that is **not** the same as being sandboxed. Coverage depends on the host: on the Jetson board most backends report unavailable, and each run states the enforcement it actually achieved rather than implying one. Do not describe Odessy as sandboxed, jailed, or contained without that qualifier.

The current policy and authority rules are introduced in `policy/README.md`.

## Contributing

Start with [CONTRIBUTING.md](CONTRIBUTING.md) and the [architecture map](docs/ARCHITECTURE.md).
Open an issue with the [chooser](https://github.com/Trisoma/odessy/issues/new/choose) or share measurements using the
[hardware report template](.github/ISSUE_TEMPLATE/hardware_report.yml).
