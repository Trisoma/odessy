# Odessy v0.1.0 Demo

This demo shows the release path without pretending that deferred optimization work is complete.

## 1. Start The Laptop MCP Server

From the laptop:

```powershell
cd C:\Users\13647\OneDrive\Desktop\odessy\odessy\tool-server
npm run start:http
```

Confirm that the server is listening on the configured laptop address.

## 2. Configure The Jetson

Edit `config/odessy.env` or set equivalent environment variables:

```text
ODESSY_OLLAMA_MODEL=lfm2.5-thinking:1.2b-q8_0
ODESSY_MODEL_NAME=lfm2.5-thinking:1.2b-q8_0
ODESSY_TOOL_SERVER_URL=http://10.1.1.1:8765/mcp
```

For the LFM Jetson runtime, start Ollama with unified memory when required by the hardware:

```bash
GGML_CUDA_ENABLE_UNIFIED_MEMORY=1 ollama serve
```

## 3. Text Checks

```bash
python -m odessy "what time is it?"
python -m odessy "calculate 12 * 8"
python -m odessy "on my laptop, run pwd"
python -m odessy "Show the files under the current directory on my laptop"
```

For a command requiring confirmation, inspect the displayed tool name, risk, reason, and arguments before answering `y`.

## 4. Voice Check

```bash
python -m odessy voice
```

Expected phase markers:

```text
Recording started
Recording stopped
Transcribing speech
Model is thinking
MCP transferring if a tool is needed
Synthesizing speech
Playing response
```

## 5. Fallback Model Check

Gemma remains available for comparison:

```bash
python -m odessy --model-backend ollama "what time is it?"
```

## Release Caveats

- Blank audio is currently not short-circuited before model inference.
- STT quality depends on the microphone and recording environment.
- Kokoro latency is not real-time.
- ONNX Runtime may print Jetson DRM device-discovery warnings.
- Memory/swap optimization and systematic LFM benchmarking remain post-release work.
