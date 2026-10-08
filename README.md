# Odessy: A Voice-Controlled Edge-AI Personal Agent and Device Orchestration Platform

> NOTE: This version (v0.2.0, under development) is highly experimental, completely unstable, and may be confusing. It is recommended for developers only, not regular users!!!

Odessy is a local, single-user edge-AI agent runtime. The Jetson hosts the model and voice loop; a laptop-side MCP server provides controlled command execution.

```text
Voice command
    -> local AI reasoning
    -> policy and intent validation
    -> device/tool orchestration
    -> observation and feedback
    -> spoken response
```

The model proposes actions. Python Odessy validates and authorizes them. MCP transports them. The model is never the authority.

## Current v0.2.0 (under development)

- LFM2.5-Thinking 1.2B through Ollama is the default model path.
- Gemma 3 1B remains available with `--model-backend ollama`.
- Native Ollama tool calls are preferred for LFM; text JSON is a compatibility fallback.
- Laptop commands use `laptop.terminal.run`.
- Voice uses Whisper.cpp STT and Kokoro ONNX TTS.
- Policy and confirmation remain in the Python runtime.

> The long-term technical category could be:
> Embodied Edge-AI Agent for Multimodal Device and Engineering Automation

## Deployment Roles

Odessy uses two cooperating machines:

```text
Jetson
  -> local LFM/Ollama model
  -> Whisper.cpp speech-to-text
  -> Kokoro text-to-speech
  -> Python Odessy runtime and policy

Laptop
  -> Node MCP HTTP server
  -> controlled workspace and command execution
```

The laptop is not an unrestricted remote shell. Requests still pass through
Odessy validation, workspace authority, policy, and confirmation before the MCP
server executes them.

## Quick Start

1. Start the laptop MCP server.
2. Configure the Jetson model and voice paths in `config/odessy.env`.
3. Install Odessy in a Python virtual environment.
4. Test text mode before testing voice mode.

The shortest working checks are:

```bash
python -m odessy "on my laptop, run pwd"
python -m odessy voice
```

## Install

Requirements:

- Python 3.10+
- Ollama
- Node.js 20+ for the laptop MCP server
- Whisper.cpp and a Whisper model for voice
- Kokoro ONNX model and voices for voice output

From this directory:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

On PowerShell:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e .
```

The default local configuration is in `config/odessy.env`. In this checkout,
the Windows path is:

```text
C:\Users\13647\OneDrive\Desktop\odessy\odessy\config\odessy.env
```

On the Jetson, edit the synced equivalent:

```text
/home/<user>/odessy/config/odessy.env
```

This file tells Odessy where the local voice model files are located. The files
do not need to be copied into the repository. Put them wherever you keep your
Jetson models, then set their absolute paths:

```dotenv
ODESSY_WHISPER_BIN=/home/<user>/whisper.cpp/build/bin/whisper-cli
ODESSY_WHISPER_MODEL=/home/<user>/whisper.cpp/models/ggml-tiny.en.bin
ODESSY_KOKORO_MODEL=/home/<user>/odessy-voice-test/kokoro-v1.0.int8.onnx
ODESSY_KOKORO_VOICES=/home/<user>/odessy-voice-test/voices-v1.0.bin
ODESSY_OLLAMA_MODEL=lfm2.5-thinking:1.2b-q8_0
ODESSY_MODEL_NAME=lfm2.5-thinking:1.2b-q8_0
ODESSY_MODEL_ENDPOINT=http://127.0.0.1:11434
ODESSY_TOOL_SERVER_URL=http://<laptop-ip>:8765/mcp
```

`ODESSY_WHISPER_MODEL` and `ODESSY_KOKORO_*` are filesystem paths.
`ODESSY_OLLAMA_MODEL` and `ODESSY_MODEL_NAME` are Ollama model names, not
local file paths. Download the LFM model into Ollama separately:

```bash
ollama pull lfm2.5-thinking:1.2b-q8_0
```

Existing environment variables take precedence over values in this file.

## Laptop MCP Server

From `tools/` on the laptop:

```powershell
npm install
npm run start:http
```

### The bind address matters

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

### Verify before running the agent

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

### Security

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

Text mode:

```bash
python -m odessy "on my laptop, run pwd"
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

## Demo

The reproducible release demonstration is documented in `docs/demo-v0.1.0.md`.

## Test

```bash
pytest -q
```

The benchmark suite includes fake hardware-free scenarios and real Jetson scenarios. Real voice runs require configured audio and model assets.

## Known Limits

See `docs/reference/current-limitations.mdx`. v0.3.0 is not a production network service, always-on voice assistant, or autonomous background agent.

It has an isolation layer, and that is **not** the same as being sandboxed. Coverage depends on the host: on the Jetson board most backends report unavailable, and each run states the enforcement it actually achieved rather than implying one. Do not describe Odessy as sandboxed, jailed, or contained without that qualifier.

The current policy and authority rules are introduced in `policy/README.md`.
