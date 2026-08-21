# Odessy Current Limitations

## Voice Smoke Test Limitations

The voice path is wired and runnable, but the first live smoke test exposed several limitations that should not be confused with wiring failure.

### STT Quality

Ignore this for now during runtime wiring tests.

The transcript was poor:

```text
What are you doing for you? Hi, are you doing? What are you?
```

That means the model was already receiving noisy input before tool selection.

### ONNX Runtime DRM Device Warnings

Observed warning:

```text
Failed to detect devices under "/sys/class/drm/card1"
Failed to detect devices under "/sys/class/drm/card0"
```

This is likely harmless. ONNX Runtime is probing Linux DRM GPU device nodes, but Jetson's device layout does not expose the `vendor` files the way ONNX Runtime expects. Kokoro still ran, so this is a warning, not a failure.

Possible fixes:

```bash
export ORT_LOG_SEVERITY_LEVEL=3
```

Or configure the Kokoro/ONNX Runtime provider explicitly in code, for example by forcing or silencing the CPU provider if the wrapper supports provider selection. The real fix is explicit ONNX Runtime provider configuration; the quick fix is suppressing warning noise.

### Kokoro Word Count Mismatch Warning

Observed warning:

```text
words count mismatch
```

This is probably from Kokoro text/phoneme alignment. The final text included raw JSON:

```json
{"iso_time": "...", "timezone": "local"}
```

That is unnatural TTS input, so the tokenizer or phonemizer alignment can complain. Once final responses are plain speech text, this warning should become rarer.

