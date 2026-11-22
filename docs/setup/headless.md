# Headless configuration and export

This v0.5.0 feature configures **existing** components. It does not yet install
Python/Node/Rust, download models, enroll peers, grant workspaces or install a
background service. The separate Flutter setup wizard and installed-service lifecycle are documented in [installed setup](wizard.md); this CLI only plans and exports configuration.
The command works without a model, microphone, display or optional voice libraries.

## Roles

| Role | Meaning |
|---|---|
| `coordinator` | Model/session/policy host; plans split execution and a paired node |
| `execution` | Headless Node/Rust execution node; no LLM or voice settings |
| `combined` | Coordinator and local tool execution on this machine |

```powershell
python -m odessy setup --role execution --non-interactive
python -m odessy setup --role combined --non-interactive
```

Both print a JSON plan with zero downloads. No model is selected by default.
Without a role, a terminal asks which role to configure; redirected/noninteractive
input requires `--role`. `python -m odessy.setup.cli` is an equivalent lightweight
entrypoint. Setup bypasses existing runtime configuration validation, so broken
model/voice defaults do not prevent repairing the configuration.

## Discover and select an existing model

Only explicit `--discover` contacts the selected Ollama endpoint. It reads
`GET /api/tags`; it never pulls models, runs inference or follows redirects.

```powershell
python -m odessy setup --role combined --discover --endpoint http://127.0.0.1:11434
python -m odessy setup --role combined --provider ollama --model YOUR_INSTALLED_MODEL --endpoint http://127.0.0.1:11434 --discover --verify
```

An empty inventory, malformed response and unreachable service are distinct.
A model absent from a successful inventory is rejected rather than downloaded.
An inventory listing does not establish inference/tool compatibility. Limits:
1 MiB response, 256 models, timeout greater than 0 and at most 10 seconds. The
timeout bounds network I/O and is checked between body reads; one blocking read
can extend total duration beyond that interval. It is not a hard wall-clock SLA.

`--provider lfm` uses the existing LFM-capable adapter with an explicitly named
model; it still never installs LFM. Auto/multi-profile setup and new model adapters
are not implemented here. Unknown providers are rejected explicitly.

## Cloud-only configuration

```powershell
python -m odessy setup --role coordinator --provider deepseek --model YOUR_CLOUD_MODEL --verify
```

Supported cloud provider names match the existing runtime adapters: `claude`,
`openai`, `deepseek`, `qwen`, `kimi`, `custom`. Custom requires an explicit
`--endpoint`. Credentials remain in their provider environment variable (or
`ODESSY_CLOUD_API_KEY`); do not pass a key as a model name/endpoint or put it in
selection JSON. `--credential-env` names a variable, not a secret value. Runtime
env export permits only the existing runtime's recognized credential variables.
Verification checks presence without echoing the key or making a cloud request.

## Review and save explicitly

Plans are read-only by default. Configuration export is create-only; it cannot
overwrite a user's existing file. The parent directory must already exist.
The consent ID binds selection and destination; change either and review again.

```powershell
$setupArgs = @('setup', '--role', 'combined', '--provider', 'ollama', '--model', 'YOUR_INSTALLED_MODEL', '--output', '.\my-setup.json')
$review = python -m odessy @setupArgs | ConvertFrom-Json
$review
python -m odessy @setupArgs --confirm-plan $review.plan_id
python -m odessy setup --config .\my-setup.json --verify
```

The JSON export contains versioned selection metadata, not private credentials.
It is not automatically loaded by the runtime. To create a runtime env file:

```powershell
$envArgs = @('setup', '--config', '.\my-setup.json', '--write-env', '.\my-odessy.env')
$review = python -m odessy @envArgs | ConvertFrom-Json
python -m odessy @envArgs --confirm-plan $review.plan_id
$env:ODESSY_CONFIG_FILE = (Resolve-Path .\my-odessy.env).Path
```

Existing shell variables take precedence over a loaded env file. The export
refuses an unconfigured model instead of accidentally invoking the runtime's
default LFM model. Coordinator execution-node connection/grants still require
separate setup; an env file alone cannot establish remote execution authority.
Foreign platform/architecture selections may be reviewed but cannot be activated
or verified against this machine. Keep paths relative to their owning host.

## Optional voice

This advanced CLI configures existing assets; it does not perform the desktop
installer's automatic voice downloads. Piper is recommended for new Windows setup;
use [the installer](../GOLDEN_PATH.md#1-install-and-open-odessy) for that flow.
For a headless Piper selection, explicitly set `tts_provider`, `tts_executable`
and `tts_model`, along with Whisper input paths. The executable may be the Python
interpreter containing Piper; keep the matching `.onnx.json` beside the model:

```json
{
  "role": "combined",
  "model": {"provider": "ollama", "name": "YOUR_INSTALLED_MODEL"},
  "voice": {
    "enabled": true,
    "tts_provider": "piper",
    "tts_executable": "/path/to/python-with-piper",
    "tts_model": "/path/to/voice.onnx",
    "whisper_bin": "/path/to/whisper-cli",
    "whisper_model": "/path/to/ggml-base.en.bin"
  }
}
```

The older CLI default remains Kokoro for compatibility. An explicit existing-asset
Kokoro configuration is:

```json
{
  "role": "combined",
  "model": {"provider": "ollama", "name": "YOUR_INSTALLED_MODEL"},
  "voice": {
    "enabled": true,
    "whisper_bin": "/path/to/whisper-cli",
    "whisper_model": "/path/to/ggml-tiny.en.bin",
    "kokoro_model": "/path/to/kokoro-v1.0.int8.onnx",
    "kokoro_voices": "/path/to/voices-v1.0.bin"
  }
}
```

Pass this as `--config selection.json --verify`. Windows paths must use JSON
escaping or forward slashes. Equivalent CLI flags are `--voice`, `--whisper-bin`,
`--whisper-model`, `--tts-provider piper`, `--tts-executable` and `--tts-model`;
Kokoro uses `--kokoro-model` and `--kokoro-voices`. Missing optional assets
are reported without downloading anything or mutating the working text setup.
Voice path/package presence is not model compatibility or physical audio acceptance.
Wake models and custom STT/TTS adapters remain separately configured/supported.

## Reports and boundaries

JSON includes `schema_version`, destination-bound `plan_id`, `selection`,
`components`, empty `downloads`, proposed `environment`, `readiness`, warnings,
`writes`, and optional `discovery`. `--verify` wraps that plan with observational
component/model/voice results. Exit 0 means a report/export succeeded, **not** that
the installed application is ready; this configuration/export command does not
perform installation or pairing. Validation/export failures return 2. Argparse errors also exit 2.

Core modules are in `src/odessy/setup/`: contracts, pure planning, bounded
discovery, create-only persistence, verification and the thin CLI. No imports
from the root installer tree, Tk or Flutter are required. These configuration/export Python
functions are synchronous. The Flutter wizard now runs them off the GUI event
loop through an owned, one-request stdio helper with cancellation and read-only
save recovery. See [the wizard guide](wizard.md). Resumable installation and
native service installation are outside this configuration/export interface;
its recovery only inspects configuration exports. The packaged wizard has its
own implemented installation and maintenance lifecycle.

Reuse existing authorization and doctor checks for future integration, but avoid
running the entire doctor suite on every screen: some existing checks start or
contact configured servers. New setup verification is observational only.
See the packaged wizard guide for installation progress, cancellation and recovery.
