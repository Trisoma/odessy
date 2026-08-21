# Odessy v0.1.0

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

The default local configuration is in `config/odessy.env`. Existing environment variables take precedence.

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
