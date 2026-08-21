# Odessy v0.1.0

> NOTE: NOTE: This version is highly experimental, completely unstable, and may be confusing. It is recommended for developers only, not regular users!!!

Odessy is a local, single-user edge-AI agent runtime. The Jetson hosts the model and voice loop; a laptop-side MCP server provides controlled command execution.

```text
text or voice
  -> local model
  -> Odessy validation and policy
  -> confirmation when required
  -> MCP
  -> controlled tool execution
  -> observation
  -> final response
```

The model proposes actions. Python Odessy validates and authorizes them. MCP transports them. The model is never the authority.

## Current v0.1.0

- LFM2.5-Thinking 1.2B through Ollama is the default model path.
- Gemma 3 1B remains available with `--model-backend ollama`.
- Native Ollama tool calls are preferred for LFM; text JSON is a compatibility fallback.
- Laptop commands use `laptop.terminal.run`.
- Voice uses Whisper.cpp STT and Kokoro ONNX TTS.
- Policy and confirmation remain in the Python runtime.

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

- Python 3.11+
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

From `tool-server/` on the laptop:

```powershell
npm install
npm run start:http
```

Set `ODESSY_TOOL_SERVER_URL` in the Jetson configuration to the laptop MCP URL.

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

See `docs/reference/current-limitations.mdx`. v0.1.0 is not a kernel sandbox, production network service, always-on voice assistant, or autonomous background agent.

The current policy and authority rules are introduced in `policy/README.md`.
