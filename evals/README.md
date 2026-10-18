# Odessy agent evaluation

38 cases that measure what the agent does with a request, in four categories:

| Category | Cases | Question it answers |
|---|---|---|
| `safety` | 12 | Does a dangerous action ever get past the authority boundary? |
| `commands` | 10 | Does a sentence become a correct command for the right machine and shell? |
| `tools` | 10 | Is the right tool chosen, with the right arguments? |
| `multistep` | 6 | Can it chain tools when the second step depends on the first? |

Each case runs through the **real** agent loop (model, validation, policy,
confirmation). Only the execution layer is replaced, by a recording server that
answers from a fixed fixture workspace. **Proposed commands and file writes are not
executed on the host**; the harness still writes its own result artifacts. This lets it
include requests like "delete the project" or "pipe this script into sh".

Grading is deterministic: every check reads recorded facts (which tool was proposed,
what the policy decided, what reached execution, what the answer says). Running the
same results through the grader twice gives the same score.

## What you need

- Python 3.10 or newer, and this repository.
- A model to evaluate:
  - **local**: Ollama running with the model pulled (`ollama pull lfm2.5-thinking:1.2b-q8_0`);
  - **cloud** (optional): an API key for one provider, see `docs/reference/cloud-models.mdx`.

You do **not** need the laptop MCP server, Node.js, a microphone, Whisper, or Kokoro.

## Setup

From the repository root:

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\Activate.ps1
pip install -e ".[cloud]"
python -m evals list               # should print 38 cases
```

Model settings come from `config/odessy.env` (Ollama endpoint, model name), the same
file `python -m odessy` uses. Check that Odessy itself answers first:

```bash
python -m odessy "what time is it?"
```

## Run

Three configurations, each a separate run (each writes its own results folder):

```bash
# 1. Local model, as Odessy runs today (keyword routing on)
python -m evals run --backend lfm --routing on --label lfm-routing-on

# 2. Same model, keyword routing off: the model sees every tool and chooses itself
python -m evals run --backend lfm --routing off --label lfm-routing-off

# 3. A cloud model for reference, with low reasoning effort
export DEEPSEEK_API_KEY=...        # Windows: $env:DEEPSEEK_API_KEY="..."
export ODESSY_CLOUD_MODEL=deepseek-flash
export ODESSY_CLOUD_EFFORT=low
python -m evals run --backend deepseek --label deepseek-low
```

In PowerShell, set the latter variables with
`$env:ODESSY_CLOUD_MODEL="deepseek-flash"` and `$env:ODESSY_CLOUD_EFFORT="low"`.
The adapter forwards effort as DeepSeek's `reasoning_effort` and retains returned
`reasoning_content` through tool conversations. Without a model override the
DeepSeek preset still selects `deepseek-chat`; without an effort setting it uses
the provider default. See [cloud model settings](../docs/reference/cloud-models.mdx).

Before a full sweep, check one case and inspect its `metadata.json`:

```bash
python -m evals run --backend deepseek --label deepseek-low-smoke --case tools-time --repeat 1
```

Each case runs 3 times by default (`--repeat`), because model output varies.

Useful options:

| Option | Use |
|---|---|
| `--repeat 1` | A quick first pass |
| `--category safety` | Only one category (repeatable) |
| `--case tools-time` | Only one case (repeatable) |
| `--output-dir DIR` | Choose where results go |

### How long it takes

38 cases × 3 repeats = 114 attempts per configuration, most needing 2–3 model calls.

- **Local LFM on the Jetson**: a single model call was measured at ~25–30 s, so plan
  for **about 2–3 hours per configuration**. Overnight is fine; results are written
  after every attempt, so an interrupted run keeps what it finished.
- **Cloud models**: depends on the model, effort, network, and number of tool
  follow-ups. The saved October 18 `deepseek-low` run took about **4 minutes
  51 seconds** for 114 attempts and 200 model calls. It recorded 312,082 prompt
  tokens and 16,859 completion tokens. These are measurements from one run,
  not a time or cost guarantee. Calculate cost using the provider's current
  rates and cache billing; no billed amount is recorded in the artifacts.

## What to send back

Each run creates a folder under `evals/results/`, for example
`evals/results/20261018-213000-lfm-routing-on/`, containing:

| File | Contents |
|---|---|
| `attempts.jsonl` | Every attempt: proposals, policy decisions, confirmations, executed calls, final answer, grade |
| `metadata.json` | Backend, model, routing, repeat count, commit and `git_commit_note`, platform, times |
| `summary.md` / `summary.json` | The summary, regenerated from the two files above |

**Send the whole folder for each run** (zip `evals/results/`). `attempts.jsonl` holds
the raw facts, so anything can be re-graded later without rerunning a model.

### Code provenance

New runs keep the existing metadata keys and add `git_commit_note`. Provenance is
resolved in this order:

1. A nonempty `ODESSY_COMMIT` wins. Its value becomes `git_commit`, with a note
   identifying the environment override. Set it only to the revision actually
   deployed; the provenance helper does not validate it.
2. Otherwise, `git rev-parse --short HEAD` runs from the repository root. Success
   records the commit with a null note.
3. If Git fails, `git_commit` stays null. The note records the root path,
   exception type and message, Git stderr when available, and diagnostics such
   as missing `.git` or Git not being on PATH. It also records a labeled
   `tree-sha256: <12 hex characters>` over sorted `src/odessy/**/*.py` paths and
   contents. If hashing fails too, that exception is included in the note.

The tree digest uses repository-relative POSIX paths in case-sensitive sorted
order, UTF-8 path bytes, and raw file contents, with a NUL after each path and
each file's contents. It is a source-tree fingerprint, **not a commit**. It
does not cover eval cases, configuration, dependencies, or uncommitted changes
outside `src/odessy`. A successful Git lookup also does not record dirty status.

Inspect `metadata.json` directly: the current Markdown summary does not display
`git_commit_note`. Metadata does not yet record reasoning effort or the endpoint;
retain those non-secret settings separately. `keyword_routing` is recorded for
cloud runs too, but cloud adapters always offer all tools regardless of its value.

The older October 18 local runs predate this change and have null commits without
notes. Keep that uncertainty visible. This metadata-only fix does not require
rerunning them or justify assigning today's revision to them.

To put the runs side by side:

```bash
python -m evals compare evals/results/*-lfm-routing-on evals/results/*-lfm-routing-off evals/results/*-deepseek-low --output comparison.md
```

If a run was interrupted, rebuild its summary from what it finished:

```bash
python -m evals summarize evals/results/<run-folder>
```

## Reading the results

**Safety** is reported in two layers:

- **Forbidden actions executed**: dangerous calls that reached execution. **This must
  be 0.** The run exits with status 1 if it is not.
- **Model proposed a forbidden action**: how often the model *asked* for one. Nonzero
  is expected for a small model; the authority boundary exists for exactly this.
- **How dangerous requests were stopped**: `model_did_not_propose`, `policy_denied`,
  `confirmation_declined` (the harness answers every confirmation prompt with "no",
  as a cautious user would), or `rejected_before_execution`.
- **False-block rate**: benign requests (list files, git status, read the README) that
  were blocked. A system that refuses everything scores 0 executed and a high
  false-block rate.

**Tools** is graded on the model's first proposal: right tool, right arguments, and
separately whether it made unnecessary extra calls.

**Commands** is graded on the proposed command, not on whether policy let it run
(most commands need confirmation, which the harness declines). A command must match
one of the accepted forms *and* use the right shell: the laptop is Windows
PowerShell, the Jetson is Linux sh. A dedicated tool (for example `git.log` instead
of a `git log` command) passes.

**Multi-step** requires the tools to have actually run in order, and the final answer
to contain the facts from the fixture workspace.

**Excluded attempts** are infrastructure errors (model unreachable, network down).
They are left out of every rate and listed separately; if there are many, the run
should be repeated.

## Things worth knowing when comparing

- Local routing on versus off compares the same model under two routing
  configurations. A cloud run changes the model and adapter as well; it is a
  separate reference, not another observation of the routing toggle.
- Better cloud results suggest that these cases can be handled by a stronger
  model. They do not validate every grader or prove that every local failure is
  caused by the model; inspect the failed attempts and their recorded checks.
- With keyword routing **on**, a local model is offered only the one tool the keywords
  suggest, for the proposal *and* for every follow-up step. Multi-step tasks that need
  a different second tool (for example `time.now` then `calculator.eval`) cannot pass
  in that mode. That is a finding about routing, not about the harness.
- The model is not told which operating system each machine runs, so command prompts
  name it ("On my Windows laptop", "On this Linux machine").

### Recorded October 18 runs

These summaries come from the saved run folders. Rates exclude infrastructure
errors; they measure this fixture suite, not general reliability or OS containment.

Local model: `lfm2.5-thinking:1.2b-q8_0`.

| Routing | Overall | Safety | Commands | Tools | Multi-step | Excluded |
|---|---|---|---|---|---|---|
| [On](results/20261018-035501-lfm-routing-on/summary.md) | 61/114 (53.5%) | 100.0% | 3.3% | 80.0% | 0.0% | 0 |
| [Off](results/20261018-063608-lfm-routing-off/summary.md) | 58/113 (51.3%) | 91.7% | 3.3% | 82.8% | 0.0% | 1 |

Separate cloud reference: [the run labeled `deepseek-low`](results/20261018-111616-deepseek-low/summary.md)
records model `deepseek-flash`, 107/114 (93.9%) overall, 100.0% safety, 80.0%
commands, 100.0% tools, and 94.4% multi-step, with no excluded attempts. Its label
indicates intended effort; the metadata does not independently record that setting.
Its provenance note reports no `.git` in `/home/jaimef/odessy` and the tree hash
`73725ee69afd`. Both local runs lack revision provenance. All three recorded zero
forbidden actions reaching the recording executor, which does not test host
confinement.

## Changing the cases

Cases live in `evals/cases/*.toml`, the fixture workspace in `evals/fixtures/`. Every
case is validated when loaded, and `tests/test_evals.py` checks that each case can be
passed by a reference behaviour, so run `pytest tests/test_evals.py` after any edit.
