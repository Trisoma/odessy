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

## TensorRT Kokoro Benchmark Limitation

The explicit ONNX Runtime TensorRT path has not yet completed a valid Kokoro
benchmark on the Jetson Orin Nano Super 4GB.

The observed run reached TensorRT model import but reported missing plugin
creators for operations including:

```text
FastGelu
SkipLayerNormalization
DynamicQuantizeLSTM
FusedMatMul
```

It also reported:

```text
Make sure input tokens has Int64 binding.
```

The process was then killed before the benchmark summary and samples were
exported. The captured output does not prove whether the final termination was
caused by memory pressure, TensorRT/plugin incompatibility, or both. Therefore,
TensorRT must not currently be presented as a measured or production-ready
Kokoro backend for Odessy.

## No Total Wall-Clock Deadline On An Agent Turn

**Status: intentional. Supersedes the earlier "set a default `deadline_seconds`"
roadmap item (2026-09-23).**

`OdessyAgent` has no total turn deadline by default:

```python
deadline_seconds: float | None = None   # -> Deadline(float("inf"))
```

The deadline never expires unless a caller passes a value. `max_model_calls`
(default 4) is the only default protection on total work.

### Why there is no default

The deadline was originally introduced to bound one specific hang: a single LFM
proposal measured at 70-115 s against a 120 s HTTP timeout, which meant a
multi-attempt turn could spend several minutes in generation. That reason no
longer applies. Both adapters now stream (`stream: true`) and the HTTP timeout
measures **silence between chunks** rather than total generation, so a stalled
model is cut off in seconds instead of exhausting a whole-request budget.

With the hang bounded at the source, a second, coarser bound over the whole turn
would duplicate `max_model_calls` without adding a guarantee. A total deadline
was therefore dropped rather than given a number that no measurement supports.

### What this actually limits

This is a real limitation, not only a decision. The three bounds protect
different things and do not compose into a total-time guarantee:

```text
model call      bounded by silence between chunks (idle timeout)
tool execution  bounded per tool (ToolTimeoutPolicy)
turn            bounded by max_model_calls (count), NOT by wall clock
```

So a turn can still be long in aggregate: up to `max_model_calls` legitimate
generations, each of which may run for minutes as long as chunks keep arriving,
plus their tool executions. Nothing bounds that sum. On a voice path where a
user is waiting, a turn that is healthy by every individual measure can still be
too slow as a whole, and Odessy has no mechanism to cut it short.

If a total bound is ever needed, it should be chosen from a measured
distribution of turn durations. No such measurement exists, which is why the
original item was deferred rather than decided.

### Carrying It Forward

Both bounds are intentional and should not be "fixed" by adding a default
deadline:

- **A cold model load counts as silence.** The idle timeout will fire during a
  cold load if it is set too low. `ODESSY_MODEL_PROGRESS_TIMEOUT` (default 120 s)
  exists to tolerate the load gap; tuning it is not the same as bounding a turn.
- **`response_chars` accumulates across calls**, so it reports a turn total, not
  a single response. Do not read it as per-call output size.

The current practical GPU path is explicit ONNX Runtime CUDA execution:

```text
CUDAExecutionProvider -> CPUExecutionProvider
```

Odessy keeps TensorRT profiles as an experimental follow-up. A valid TensorRT
comparison requires successful model initialization, completed inference
samples, and confirmation of the actual execution behavior. See
`../docs/failures/failure-005-jetson-tensorrt-kokoro-initialization.md` for the
captured evidence and follow-up checks.

## LFM GPU / Offload Configuration Is Unexplored

**Status: deferred to v0.3.0 (decided 2026-09-24). Not measured.**

The capability exists and has never been used:
`benchmark/scenarios.py` defines `num_gpu`, `BenchmarkPartialGpuLFMModel` sends it
as an Ollama option, and `benchmark/cli.py` exposes `--ollama-num-gpu` /
`ODESSY_BENCHMARK_OLLAMA_NUM_GPU`.

Every recorded run has `"ollama_num_gpu": null` — all of `benchmark/model/*`. The
LFM has therefore only ever been measured with Ollama's default layer split, on a
board where a 1.2 GB model competes with the OS for roughly 3.6 GB.

Why it matters: with `max_model_calls=4` and ~17.6 tok/s, model generation
dominates the cost of a turn, and layer placement is the largest untuned lever on
it.

Why deferred: it needs the Jetson, because `num_gpu` is an Ollama setting and
Ollama runs there. The v0.2 budget is better spent closing verification gaps than
opening a new unmeasured tuning axis.

When it is run, the experiment is one command varying one number:

```text
--ollama-num-gpu 0 | 8 | 16 | 99
```

on the fixed `lfm_compare` prompt, with warmups, comparing tok/s and latency.
`ollama_num_gpu` is written into `metadata.json`, so the runs are comparable.

## No Isolation Sandbox

**Status: intentional. v0.2 ships without containment.**

Odessy enforces an **authority** boundary, not an **isolation** boundary.

```text
Authority    "the model proposes, Odessy decides" -- enforced in-process.
Isolation    "an allowed command cannot exceed a stated blast radius" -- NOT
             enforced. Not in v0.2.
```

Everything Odessy does today is authority: `WorkspaceAuthority` path confinement,
sensitive-path denial, the sanitized environment allowlist, policy denial of
destructive / detached / credential-seeking commands, confirmation for
non-low-risk calls, execution timeouts with process-tree cleanup, and bounded plus
redacted output.

A command that passes policy runs as a normal child process with the user's full
privileges. It can read and write anything that user can, and reach the network.
Policy reduces what Odessy will *launch*; it does not constrain what a launched
program does. The Windows Node MCP server applies a path guard but no OS-level
restriction.

Why no sandbox now: Landlock is unavailable on this board
(`CONFIG_SECURITY_LANDLOCK is not set` in the L4T kernel -- a kernel build
decision by NVIDIA, not a runtime toggle). Every remaining route needs a kernel
rebuild, a Windows restricted token, or a container: real work with unproven
payoff for a local single-user agent.

Odessy must not be described as sandboxed, jailed, or contained. Options and
prototype gates for a future version: `SANDBOX-PLAN.md`.

## Multi-Tool Continuation Deferred To v0.3

**Status: implemented, wired, and does not fire with a real model.**

The observation call advertises tools, and the loop can accept a second tool
request. Offline the same loop makes 3 model calls with tools advertised on calls
2 and 3. Against the real model it does not:

```text
"on my laptop, run pwd, then run whoami"   -> 1 tool executed, then prose
"on my laptop, run pwd, then run ls"       -> 1 tool executed, then prose
```

Even with an explicit "then run X", the model summarises and stops. The prompt
line asking it to continue is a nudge, not an enforcement: the loop can decline to
accept an answer, but cannot demand a tool call.

**v0.2 ships single-tool turns.** This is a model-behaviour limitation, not a
wiring defect. The continuation machinery is retained but inert pending v0.3.

## Known Failing Tests

**Status: quarantined, not fixed. 5 tests fail on every run.**

They are unrelated to v0.2's deliverable. Recorded so that a red suite is a
documented state rather than a surprise, and so nobody assumes the suite is clean.

```text
test_benchmark_matrix.py::test_pytorch_kokoro_engine_uses_pipeline_and_reports_device
test_benchmark_matrix.py::test_onnx_tensorrt_records_exact_provider_order_and_selection
test_benchmark_matrix.py::test_matrix_cli_writes_pytorch_device_metadata
test_onnx_runtime_wiring.py::test_build_kokoro_uses_tensorrt_with_fallbacks
    All four assert ONNX Runtime provider configs. They expect
    ('CUDAExecutionProvider', {'device_id': 0}) while the runtime now supplies
    ('CUDAExecutionProvider', {'device_id': 0, 'gpu_mem_limit': 536870912}).
    Either the expectations are stale, or an intentional memory-limit change was
    never propagated. NOT DETERMINED.

test_lfm_model.py::test_lfm_call_count_includes_final_observation_response
    Asserts response_chars == 11 for a single call. The field accumulates across
    calls (response_chars += len(content)), so a two-call turn records 59. The
    test is stale relative to the field's behaviour, but whether the field or the
    test is wrong has never been decided.
```

Neither group may be deleted to make the suite green. Both need a decision first:
is the code or the expectation correct? Editing the expectation without settling
that would hide a possible regression.

