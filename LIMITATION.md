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

## Isolation: Implemented In v0.3.0, Largely Inactive On This Board

**Status: the layer exists. Do not describe this board as sandboxed.**

Odessy enforced only an **authority** boundary through v0.2. v0.3.0 adds an
**isolation** layer. The distinction is still the one that matters:

```text
Authority    "the model proposes, Odessy decides" -- enforced in-process.
             Reduces what Odessy will LAUNCH.
Isolation    "an allowed command cannot exceed a stated blast radius" -- now
             implemented, and enforced by the platform where a backend exists.
             Constrains what a launched program CAN REACH.
```

### What was built

Provider-selected confinement, with the backend chosen by *what the host can
actually enforce* rather than by platform name:

```text
bwrap            Linux, namespaces
landlock         Linux, kernel LSM      (needs the landlock-run launcher)
seatbelt         macOS
windows-acl      Windows, restricted token + ACL   -> PARTIAL enforcement
```

Every executor routes commands through `confine()`, including runs with no backend
configured, so a run always **states** its enforcement rather than leaving it to be
inferred. `required` isolation can never be satisfied by a mode that provides no
confinement — not by selection, not by escalation, not by omission.

### Why this board is still not contained

**This is the part that must not be lost.** Implementing the layer does not confine
the Jetson:

- **Landlock is off in this kernel.** `CONFIG_SECURITY_LANDLOCK is not set` in the
  L4T build — a kernel decision by NVIDIA, not a runtime toggle. The Python Landlock
  provider therefore reports unavailable.
- **Odessy ships no `landlock-run` launcher.** The Python Landlock backend needs a
  native launcher on `PATH`; without one it reports unavailable even where Landlock
  is compiled in.
- **The Node side cannot read the ABI.** Node cannot issue
  `landlock_create_ruleset`, so the TypeScript provider reports the ABI as unknown on
  every Linux host rather than guessing it.
- **Python has no Windows backend.** On Windows the Python bridge reports
  `unconfined`, while the TypeScript host can genuinely confine.

The practical result: **`bwrap` is the only backend with a real chance of confining
on this board**, and only if it is installed and can actually sandbox. Everything
else reports unavailable, which is the intended behaviour — an unavailable backend is
a fact the report states, not a failure to conceal.

### What the Windows backend does not cover

`windows-acl` reports **`partial`**, and the gaps are named rather than implied:

1. `WRITE_RESTRICTED` must retain `Everyone` for process initialisation, so an
   external object whose DACL grants Everyone a write right stays writable.
2. NTFS hard links alias a *file object*, so a granted workspace file is writable
   through an external alias.

Reads, network, and process visibility are **not restricted** on Windows. Derived but
undocumented device and ioctl boundaries are deliberately excluded from the reported
limitation set.

### Still absent

- **Host pairing.** The host is a local subprocess; there is nothing remote to pair
  with. A server must be explicitly marked an `ExecutionTarget` to be executable.
- **Any device backend.** ESP32 / STM32 / RP2040 are declared and unbacked.
- **A total wall-clock turn deadline.** Unchanged from the section above.

### How to describe it

```text
Allowed              "Odessy has an isolation layer; its coverage depends on the
                      host, and each run reports what it achieved."
Not allowed          "Odessy is sandboxed", "jailed", or "contained" -- without the
                      qualifier, because on this board it would be false.
```

Full inventory of what is declared and unbacked:
`docs/execution/PROVIDER-PLACEHOLDERS.md`. Design detail:
`docs/execution-layer-design.md` in the repository root.

## Cloud Model Backends Are Optional And Off By Default

**Status: implemented, opt-in, off by default.**

A cloud backend (Claude, or any OpenAI-compatible API) can replace the local LFM/Gemma
model. It is a stronger *proposer*, never a stronger authority: tool-call validation,
workspace authority, policy, and confirmation still run in the Python runtime, and a
cloud tool call passes through the same validation functions the local adapter uses.

What changes is where data goes, not who decides.

```text
Local backend (default)   Nothing leaves the machine.
Cloud backend selected    The user's text and that turn's tool observations are sent to
                          the provider, after passing through redact_text.
```

The redaction on that path is the same best-effort, name-based filter described above.
It does widen coverage, because MCP tool results are otherwise unredacted — but it is
still a filter, not a policy.

**What this does not establish:** no requirement states what may leave the host.
Until one does, the only control over egress is *which backend was selected*, and every
run prints a notice on stderr saying the request is leaving the machine.

```text
Allowed              "Odessy is local by default; selecting a cloud backend sends the
                      request and redacted tool output to that provider."
Not allowed          "Odessy runs locally", unqualified, once a cloud backend is selected.
```

Reference: `docs/reference/cloud-models.mdx`. Published register:
`docs/reference/current-limitations.mdx` (the **Data egress** row).

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

## Previously Failing Tests: Resolved

**Status: resolved. The suite is green.**

These five were quarantined rather than fixed. In each case the stale side was the
expectation, not the runtime, and the expectations now match. Kept as a record of what
was decided, not as outstanding work.

```text
test_benchmark_matrix.py::test_pytorch_kokoro_engine_uses_pipeline_and_reports_device
test_benchmark_matrix.py::test_onnx_tensorrt_records_exact_provider_order_and_selection
test_benchmark_matrix.py::test_matrix_cli_writes_pytorch_device_metadata
test_onnx_runtime_wiring.py::test_build_kokoro_uses_tensorrt_with_fallbacks
    All four assert ONNX Runtime provider configs. They expect
    ('CUDAExecutionProvider', {'device_id': 0}) while the runtime now supplies
    ('CUDAExecutionProvider', {'device_id': 0, 'gpu_mem_limit': 536870912}).
    Resolved as stale expectations: the memory limit is part of the runtime's
    provider configuration, and these tests exist to pin provider order and
    selection. They now assert the limit as well.

test_lfm_model.py::test_lfm_call_count_includes_final_observation_response
    Asserts response_chars == 11 for a single call. The field accumulates across
    calls (response_chars += len(content)), so a two-call turn records 59.
    Resolved as a stale test: the field records the whole turn, so a two-call turn
    legitimately reports both responses. The assertion now sums them.
```

Neither group was deleted to make the suite green; both were decided first. The rule
still holds for future failures: settle whether the code or the expectation is wrong
before editing either, because editing an expectation without settling that hides a
possible regression.

