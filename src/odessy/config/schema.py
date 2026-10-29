"""The declared ``ODESSY_*`` settings, and one validation pass over them.

Every setting the runtime reads is declared here, with the shape its value must
have. Validation runs at startup, over the same environment the runtime will read,
so a broken value is reported next to its cause instead of surfacing later as a
timeout, a stack trace from a library, or "file not found" from a tool.

Three rules, in order of how much they matter:

1. **A value that does not fit its declared shape is an error**, with the accepted
   form spelled out. ``ODESSY_MODEL_PROGRESS_TIMEOUT=2 min`` is not a number, and
   ``ODESSY_DEPLOYMENT=singel`` does not select a deployment.
2. **A setting the run needs must be present and, for a path, must exist.** "Needs"
   is per feature, not global: nobody has whisper.cpp installed just to run the text
   loop, so the voice paths are required only by ``odessy voice``. Nothing is
   unconditionally required, because every setting has a working default; treating a
   default as missing would refuse setups that work today.
3. **A name Odessy does not read is a warning, never silence.** This is the project's
   recorded lesson: configuration that is silently dropped is more dangerous than
   configuration that is refused. ``ODESSY_WHISPER_MODLE=...`` *looks* set and does
   nothing, and the symptom appears far from the typo. The warning names the closest
   real setting.

Values are never echoed unredacted, and a secret's value is never echoed at all: an
error message is printed to a terminal and may be pasted into a bug report.
"""

from __future__ import annotations

import difflib
import math
import os
import re
import sys
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, TextIO
from urllib.parse import urlparse

from odessy.authority.redaction import redact_text
from odessy.config.deployment import DEPLOYMENTS

Level = Literal["error", "warning"]

Kind = Literal[
    "text",
    "secret",
    "int",
    "float",
    "flag",
    "enum",
    "url",
    "url_list",
    "path",
    "file",
    "dir",
    "model",
    "backend",
    "cloud_backend",
    "backend_list",
]

#: Names built by the installer's connection probe; they carry a random token.
_IGNORED_PREFIXES = ("ODESSY_BENCHMARK_", "ODESSY_OK_", "ODESSY_TEST_")

#: Spellings a boolean setting may use where the runtime treats anything outside
#: ``0/off/false/no`` as on. Narrower settings list their own ``choices``.
_BOOLEAN_VALUES = ("on", "off", "true", "false", "yes", "no", "1", "0")

#: ``ODESSY_CLOUD_<BACKEND>_MODEL`` override the primary model for one fallback
#: backend. Kept in step with ``CLOUD_PROVIDERS`` by a test.
_CLOUD_FALLBACK_MODELS = ("claude", "openai", "deepseek", "qwen", "kimi", "custom")


@dataclass(frozen=True)
class Setting:
    """One declared setting: its shape, and when a run needs it."""

    name: str
    kind: Kind
    summary: str
    choices: tuple[str, ...] = ()
    minimum: float | None = None
    maximum: float | None = None
    #: ``0`` is not acceptable although it is a number (a threshold, a rate).
    exclusive_minimum: bool = False
    #: Contexts (``voice``, ``wake``) in which the setting must be present.
    required_in: tuple[str, ...] = ()
    #: The runtime's own default, used only when the setting is required in a
    #: context and unset: validation then checks what the runtime would use.
    fallback: str = ""
    #: A default that depends on the environment, such as the installed wake asset
    #: directory; used the same way as ``fallback``.
    fallback_provider: Callable[[Mapping[str, str]], str] | None = None
    #: A fix line for a missing required path, when "set the variable" is not the
    #: first thing the user should do (the default exists, it just is not installed).
    missing_fix: str | None = None
    #: The empty value has a documented meaning (unset, off, provider default).
    allow_empty: bool = False
    #: A bare value is a command looked up on PATH, not a path to check.
    command: bool = False
    #: Written by Odessy itself or by tooling; known, but never a typo to suggest.
    internal: bool = False


@dataclass(frozen=True)
class Issue:
    level: Level
    setting: str
    message: str
    fix: str | None = None

    def line(self) -> str:
        suffix = f" (fix: {self.fix})" if self.fix else ""
        return f"{self.level}: {self.setting}: {self.message}{suffix}"


@dataclass(frozen=True)
class Report:
    issues: tuple[Issue, ...] = ()

    @property
    def errors(self) -> tuple[Issue, ...]:
        return tuple(issue for issue in self.issues if issue.level == "error")

    @property
    def warnings(self) -> tuple[Issue, ...]:
        return tuple(issue for issue in self.issues if issue.level == "warning")

    @property
    def ok(self) -> bool:
        return not self.errors

    def describe(self) -> str:
        """One line per issue, for a terminal or a bug report."""

        return "\n".join(issue.line() for issue in self.issues)


def _s(name: str, kind: Kind, summary: str, **options: object) -> Setting:
    return Setting(name=name, kind=kind, summary=summary, **options)  # type: ignore[arg-type]


def _wake_model_default(environ: Mapping[str, str]) -> str:
    """What ``voice --listen`` loads when ``ODESSY_WAKE_MODEL`` is unset.

    The default lives outside the repository and is installed by
    ``voice --setup-wake``, so requiring the variable would reject a setup the
    runtime supports.
    """

    from odessy.voice.assets import DEFAULT_WAKE_MODEL, asset_dir

    return str(asset_dir(dict(environ)) / DEFAULT_WAKE_MODEL)


SETTINGS: tuple[Setting, ...] = (
    _s("ODESSY_MONITORING", "flag", "enable optional monitoring", choices=("on", "off", "true", "false", "1", "0")),
    _s("ODESSY_MONITOR_LOCAL", "flag", "explicit local telemetry consent", choices=("on", "off", "true", "false", "1", "0")),
    _s("ODESSY_MONITOR_HOSTS", "text", "comma-separated enrolled monitoring host IDs", allow_empty=True),
    _s("ODESSY_MONITOR_PRESET", "enum", "monitoring cadence", choices=("default", "low-power")),
    _s("ODESSY_MONITOR_BIN", "file", "packaged Go monitoring executable"),
    _s("ODESSY_MONITOR_STATE_DIR", "path", "monitoring history directory"),
    # -- deployment ---------------------------------------------------------
    _s("ODESSY_DEPLOYMENT", "enum", "single or split", choices=DEPLOYMENTS,
       allow_empty=True),
    _s("ODESSY_LOCAL_OS", "enum", "the operating system model-facing commands run on",
       choices=("windows", "linux", "macos", "win32", "darwin", "mac", "osx")),
    _s("ODESSY_LAPTOP_OS", "enum", "the operating system of the remote laptop",
       choices=("windows", "linux", "macos", "win32", "darwin", "mac", "osx")),
    _s("ODESSY_TOOL_SERVER_URL", "url", "the laptop tool server's MCP endpoint",
       allow_empty=True),
    # Returned by the paired laptop when its own user accepted this coordinator.
    # Optional: an unpaired or token-free endpoint stays valid, and a secret's
    # value is never echoed in a report.
    _s("ODESSY_TOOL_SERVER_TOKEN", "secret",
       "bearer token returned by the paired laptop's tool server", allow_empty=True),
    _s("ODESSY_MCP_SERVER_URLS", "url_list", "comma-separated remote MCP endpoints",
       allow_empty=True),
    # -- local model --------------------------------------------------------
    _s("ODESSY_MODEL_BACKEND", "backend", "the selected model backend"),
    _s("ODESSY_MODEL_NAME", "model", "the model to request from Ollama"),
    _s("ODESSY_MODEL_ENDPOINT", "url", "the Ollama endpoint for the lfm backend"),
    _s("ODESSY_MODEL_PROGRESS_TIMEOUT", "int", "seconds of silence allowed between chunks",
       minimum=1),
    _s("ODESSY_MODEL_NUM_PREDICT", "int", "generated tokens allowed per call", minimum=1),
    _s("ODESSY_MODEL_NUM_CTX", "int", "the context window requested from Ollama",
       minimum=1),
    _s("ODESSY_MODEL_CALL_TIMEOUT", "int", "seconds allowed for one streamed call",
       minimum=1),
    _s("ODESSY_OLLAMA_URL", "url", "the Ollama endpoint for the ollama backend"),
    _s("ODESSY_OLLAMA_MODEL", "model", "the model the ollama backend requests"),
    _s("ODESSY_MAX_MODEL_CALLS", "int", "model calls allowed per turn", minimum=1),
    _s("ODESSY_TOOL_CONCURRENCY", "int", "tools allowed to overlap, from 1 to 8",
       minimum=1, maximum=8),
    _s("ODESSY_ISOLATION", "enum", "how strict this deployment is about isolation",
       choices=("required", "preferred", "not-required")),
    # -- automatic model selection -----------------------------------------
    _s("ODESSY_AUTO_FAST_MODEL", "model", "the model used for fast text-only requests"),
    _s("ODESSY_AUTO_CAPABLE_BACKEND", "enum", "the backend used for capable requests",
       choices=("lfm", "ollama", "llama_cpp")),
    _s("ODESSY_AUTO_CAPABLE_MODEL", "model", "the model used for capable requests"),
    _s("ODESSY_AUTO_CLOUD_FALLBACK", "flag", "on or off", choices=("on", "off"),
       allow_empty=True),
    _s("ODESSY_AUTO_CLOUD_BACKEND", "cloud_backend",
       "the cloud backend auto routing may fall back to", allow_empty=True),
    # -- cloud model --------------------------------------------------------
    _s("ODESSY_CLOUD_MODEL", "model", "the provider's model name", allow_empty=True),
    _s("ODESSY_CLOUD_BASE_URL", "url", "the provider's API endpoint", allow_empty=True),
    _s("ODESSY_CLOUD_API_KEY", "secret", "the API key for a custom endpoint",
       allow_empty=True),
    _s("ODESSY_CLOUD_TIMEOUT", "int", "seconds per cloud request", minimum=1),
    _s("ODESSY_CLOUD_MAX_TOKENS", "int", "Claude's output ceiling", minimum=1),
    _s("ODESSY_CLOUD_OPENAI_MAX_TOKENS", "int",
       "the OpenAI-compatible output ceiling", minimum=1),
    _s("ODESSY_CLOUD_EFFORT", "enum", "the provider's effort setting",
       choices=("low", "medium", "high", "xhigh", "max"), allow_empty=True),
    _s("ODESSY_CLOUD_FALLBACKS", "flag", "on or off", allow_empty=True),
    _s("ODESSY_CLOUD_PROMPT_CACHE", "flag", "on or off", allow_empty=True),
    _s("ODESSY_CLOUD_RETRIES", "int", "retries after a transient failure", minimum=0),
    _s("ODESSY_CLOUD_MAX_RESULT_CHARS", "int", "characters kept from one tool result",
       minimum=1),
    _s("ODESSY_CLOUD_FAILOVER", "backend_list",
       "comma-separated backends tried after the selected one", allow_empty=True),
    _s("ODESSY_CLOUD_FAILOVER_COOLDOWN", "float",
       "seconds a failed backend is skipped", minimum=0, allow_empty=True),
    _s("ODESSY_CLOUD_PRICE_INPUT", "float", "input price per million tokens",
       minimum=0, allow_empty=True),
    _s("ODESSY_CLOUD_PRICE_CACHED_INPUT", "float",
       "cached-input price per million tokens", minimum=0, allow_empty=True),
    _s("ODESSY_CLOUD_PRICE_OUTPUT", "float", "output price per million tokens",
       minimum=0, allow_empty=True),
    _s("ODESSY_CLOUD_PRICE_CURRENCY", "text", "the currency label for prices",
       allow_empty=True),
    _s("ODESSY_CLOUD_BUDGET_TOKENS", "int", "the per-run token ceiling",
       minimum=0, allow_empty=True),
    _s("ODESSY_CLOUD_BUDGET_COST", "float", "the per-run cost ceiling",
       minimum=0, allow_empty=True),
    # -- human interaction --------------------------------------------------
    _s("ODESSY_INTERACTION_MAX_QUESTIONS", "int",
       "questions allowed per turn", minimum=1, maximum=20),
    _s("ODESSY_INTERACTION_RESPONSE_TIMEOUT", "float",
       "seconds to wait for an answer", minimum=0, maximum=300, exclusive_minimum=True),
    _s("ODESSY_INTERACTION_STRONG_RESPONSE_TIMEOUT", "float",
       "seconds to wait for an approval", minimum=0, maximum=600, exclusive_minimum=True),
    _s("ODESSY_INTERACTION_PROMPT_TIMEOUT", "float",
       "seconds a question may stay unanswered", minimum=0, maximum=120,
       exclusive_minimum=True),
    # -- files and logs -----------------------------------------------------
    _s("ODESSY_CONFIG_FILE", "file", "the one config file to load", allow_empty=True),
    _s("ODESSY_SERVERS_FILE", "file", "the third-party MCP servers file",
       allow_empty=True),
    _s("ODESSY_COMMANDS_FILE", "file", "the approved commands file", allow_empty=True),
    _s("ODESSY_COLLABORATION_FILE", "file", "the model collaboration file",
       allow_empty=True),
    _s("ODESSY_AUDIT_FILE", "path",
       "the third-party call audit log (empty turns it off)", allow_empty=True),
    _s("ODESSY_EGRESS_FILE", "path",
       "the cloud egress log (empty turns it off)", allow_empty=True),
    _s("ODESSY_API_TOKEN", "secret", "the API server's bearer token", allow_empty=True),
    # -- voice --------------------------------------------------------------
    _s("ODESSY_STT_PROVIDER", "text", "registered name or trusted module:factory"),
    _s("ODESSY_TTS_PROVIDER", "text", "registered name or trusted module:factory"),
    _s("ODESSY_STT_EXECUTABLE", "file", "custom STT executable", command=True, allow_empty=True),
    _s("ODESSY_TTS_EXECUTABLE", "file", "custom TTS executable", command=True, allow_empty=True),
    _s("ODESSY_STT_MODEL", "file", "custom STT model", allow_empty=True),
    _s("ODESSY_TTS_MODEL", "file", "custom TTS model", allow_empty=True),
    _s("ODESSY_WHISPER_BIN", "file", "the whisper.cpp executable",
       required_in=("voice",), fallback="whisper-cli", command=True),
    _s("ODESSY_WHISPER_MODEL", "file", "the Whisper model file",
       required_in=("voice",), fallback="models/ggml-tiny.en.bin"),
    _s("ODESSY_KOKORO_MODEL", "file", "the Kokoro ONNX model",
       required_in=("voice",), fallback="kokoro-v1.0.int8.onnx"),
    _s("ODESSY_KOKORO_VOICES", "file", "the Kokoro voices file",
       required_in=("voice",), fallback="voices-v1.0.bin"),
    _s("ODESSY_KOKORO_PYTORCH_MODEL_DIR", "dir",
       "the Kokoro PyTorch model directory", allow_empty=True),
    _s("ODESSY_WAKE_MODEL", "file", "the openWakeWord model",
       required_in=("wake",), allow_empty=True,
       fallback_provider=_wake_model_default,
       missing_fix="install the default model with `python -m odessy voice --setup-wake`, "
                   "or point ODESSY_WAKE_MODEL at an openWakeWord model"),
    _s("ODESSY_WAKE_THRESHOLD", "float", "the wake-word score threshold",
       minimum=0, maximum=1, exclusive_minimum=True),
    _s("ODESSY_VOICE_INTERACTION", "enum", "wake or conversation",
       choices=("wake", "conversation")),
    _s("ODESSY_VOICE_SECONDS", "int", "seconds recorded per bounded turn", minimum=1),
    _s("ODESSY_VOICE_BLANK_RETRIES", "int",
       "extra recordings after an unusable transcript", minimum=0),
    _s("ODESSY_VOICE_SETTLING_MS", "int",
       "milliseconds of silence that end an utterance", minimum=0, maximum=10000),
    _s("ODESSY_VOICE_FOLLOWUP_SECONDS", "int",
       "seconds a follow-up window stays open", minimum=1),
    _s("ODESSY_VOICE_MAX_UTTERANCE_SECONDS", "int",
       "the ceiling on one utterance", minimum=1),
    _s("ODESSY_VOICE_STREAM_SPEECH", "flag", "on or off", allow_empty=True),
    _s("ODESSY_VOICE_TMPDIR", "path", "where recordings are written",
       allow_empty=True),
    _s("ODESSY_ONNX_PROVIDER", "enum", "the ONNX Runtime provider",
       choices=("auto", "tensorrt", "cuda", "cpu")),
    _s("ODESSY_ONNX_CACHE_DIR", "path", "the ONNX Runtime cache directory",
       allow_empty=True),
    _s("ODESSY_AUDIO_BACKEND", "enum", "native PortAudio or existing commands", choices=("auto","native","command")),
    _s("ODESSY_AUDIO_RECORDER", "text", "the recording command"),
    _s("ODESSY_AUDIO_PLAYER", "text", "the playback command"),
    _s("ODESSY_AUDIO_INPUT_DEVICE", "text", "the recording device", allow_empty=True),
    _s("ODESSY_AUDIO_OUTPUT_DEVICE", "text", "the playback device", allow_empty=True),
    # -- diagnostics --------------------------------------------------------
    _s("ODESSY_DEBUG_HTTP", "enum", "set to 1 to keep httpx logs", choices=("0", "1"),
       allow_empty=True),
    _s("ODESSY_DEBUG_MODEL_REPAIR", "enum", "set to 1 to trace model repairs",
       choices=("0", "1"), allow_empty=True),
    # -- written by Odessy or by the installer ------------------------------
    _s("ODESSY_PORTABLE_ROOT", "path", "verified portable payload root", internal=True),
    _s("ODESSY_PORTABLE_STATE", "path", "external portable configuration directory", internal=True),
    _s("ODESSY_NODE_TOOLS_DIR", "path", "bundled precompiled Node tool server", internal=True),
    _s("ODESSY_WORKSPACE_ROOT", "path", "the tool server's workspace", internal=True),
    _s("ODESSY_MCP_HOST", "text", "the tool server's bind address", internal=True),
    _s("ODESSY_MCP_PORT", "int", "the tool server's port",
       minimum=1, maximum=65535, internal=True),
    _s("ODESSY_MCP_TRANSPORT", "enum", "http or stdio",
       choices=("http", "stdio"), internal=True),
    _s("ODESSY_SHORTCUT_JSON", "text", "the installer's shortcut payload",
       internal=True),
    _s("ODESSY_ACL_TEST_PATH", "path", "the installer's access probe path",
       internal=True),
    _s("ODESSY_PROBE_READONLY", "text", "a probe marker, not a setting",
       internal=True),
    _s("ODESSY_PROBE_WRITABLE", "text", "a probe marker, not a setting",
       internal=True),
) + tuple(
    _s(f"ODESSY_CLOUD_{name.upper()}_MODEL", "model",
       f"the model used when failing over to {name}", allow_empty=True)
    for name in _CLOUD_FALLBACK_MODELS
)

#: Every declared name, for the unknown-setting warning and its suggestions.
KNOWN_NAMES: frozenset[str] = frozenset(setting.name for setting in SETTINGS)

_SETTINGS_BY_NAME: dict[str, Setting] = {setting.name: setting for setting in SETTINGS}

_MODEL_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@+-]*$")


def validate_config(
    environ: Mapping[str, str] | None = None,
    *,
    contexts: Iterable[str] = (),
) -> Report:
    """Every issue with the current environment, errors first.

    ``contexts`` names the features this run will actually use (``voice``,
    ``wake``). They decide which settings must be present, never which settings are
    checked: a malformed value is an error even in a run that would not use it,
    because the next run may.
    """

    source = os.environ if environ is None else environ
    active = frozenset(contexts)
    issues: list[Issue] = []
    for setting in SETTINGS:
        issues.extend(_check_setting(setting, source, active))
    issues.extend(_unknown_settings(source))
    return Report(tuple(issues))


def enforce_config(
    environ: Mapping[str, str] | None = None,
    *,
    contexts: Iterable[str] = (),
    stream: TextIO | None = None,
) -> Report:
    """Print warnings and refuse to start on errors.

    Called once at CLI startup, after the config files are loaded. ``doctor`` does
    not call this: it reports the same issues as checks and exits with their status.
    """

    report = validate_config(environ, contexts=contexts)
    output = stream if stream is not None else sys.stderr
    # A name Odessy never reads is the dangerous kind: it looks set and does
    # nothing. It is always named. Other warnings are reported individually only
    # while there are few of them; a machine that has not installed the voice
    # assets should not print five lines before every run.
    unknown = [issue for issue in report.warnings if issue.setting not in KNOWN_NAMES]
    rest = [issue for issue in report.warnings if issue.setting in KNOWN_NAMES]
    shown = [*unknown, *rest] if len(rest) <= 2 else unknown
    for issue in shown:
        print(f"[odessy] config: {issue.setting}: {issue.message}"
              + (f" (fix: {issue.fix})" if issue.fix else ""), file=output)
    if len(rest) > 2:
        print(f"[odessy] config: {len(rest)} further warning(s); "
              "run `python -m odessy doctor` to list them", file=output)
    if report.errors:
        lines = [f"Odessy found {len(report.errors)} configuration error(s):"]
        for issue in report.errors:
            lines.append(f"  - {issue.setting}: {issue.message}")
            if issue.fix:
                lines.append(f"    fix: {issue.fix}")
        lines.append("Run `python -m odessy doctor` to check this machine.")
        raise SystemExit("\n".join(lines))
    return report


def setting(name: str) -> Setting | None:
    """The declaration for ``name``, or None when Odessy does not read it."""

    return _SETTINGS_BY_NAME.get(name)


def _check_setting(setting: Setting, source: Mapping[str, str], active: frozenset[str]) -> list[Issue]:
    if ((setting.name.startswith('ODESSY_WHISPER_') and source.get('ODESSY_STT_PROVIDER', 'whisper.cpp') != 'whisper.cpp') or
        (setting.name in ('ODESSY_KOKORO_MODEL', 'ODESSY_KOKORO_VOICES') and source.get('ODESSY_TTS_PROVIDER', 'kokoro') != 'kokoro')) and setting.name not in source:
        return []
    if setting.name in ('ODESSY_STT_PROVIDER', 'ODESSY_TTS_PROVIDER') and source.get(setting.name):
        from odessy.voice.providers import validate_selector
        try:
            validate_selector(source[setting.name])
        except ValueError as error:
            return [_issue('error', setting, str(error))]
    optional_audio = 'hybrid' in active and bool(set(setting.required_in) & {'voice', 'wake'})
    required = not optional_audio and bool(setting.required_in) and bool(active.intersection(setting.required_in))
    present = setting.name in source
    raw = source.get(setting.name, "")
    using_default = False
    if not raw.strip():
        if required:
            # The runtime's own default is what this run would use; check that.
            raw = _runtime_default(setting, source)
            using_default = True
            if not raw.strip():
                return [_issue("error", setting,
                               "is required by this command but is not set",
                               _required_fix(setting))]
        elif not present:
            return []
        elif setting.allow_empty or setting.kind in {"text", "secret", "path", "file", "dir", "flag"}:
            return []
        else:
            return [_issue("error", setting,
                           "is empty; an empty line is not a value for this setting",
                           f"remove the line to use the default, or set {setting.summary}")]
    issues = _check_value(setting, raw, required=required, using_default=using_default)
    if optional_audio:
        # Only voice assets degrade: unrelated malformed model/policy settings must
        # still stop startup. The audio adapter reports missing defaults on opening.
        return [Issue('warning', issue.setting, issue.message, issue.fix) for issue in issues]
    return issues


def _runtime_default(setting: Setting, source: Mapping[str, str]) -> str:
    if setting.fallback:
        return setting.fallback
    if setting.fallback_provider is not None:
        return setting.fallback_provider(source)
    return ""


def _check_value(
    setting: Setting, raw: str, *, required: bool = False, using_default: bool = False
) -> list[Issue]:
    value = raw.strip()
    kind = setting.kind
    shown = _show(setting, value)

    if kind in {"text", "path"}:
        return []
    if kind == "secret":
        return []

    if kind == "int":
        try:
            number = int(value)
        except ValueError:
            return [_issue("error", setting, f"must be a whole number (got {shown})",
                           _range_fix(setting))]
        return _check_bounds(setting, number, shown)

    if kind == "float":
        try:
            number = float(value)
        except ValueError:
            return [_issue("error", setting, f"must be a number (got {shown})",
                           _range_fix(setting))]
        if not math.isfinite(number):
            return [_issue("error", setting, f"must be a finite number (got {shown})",
                           _range_fix(setting))]
        return _check_bounds(setting, number, shown)

    if kind == "flag":
        allowed = setting.choices or _BOOLEAN_VALUES
        if value.lower() not in allowed:
            return [_issue("error", setting,
                           f"must be one of: {', '.join(allowed)} (got {shown})",
                           f"use one of: {', '.join(allowed)}")]
        return []

    if kind == "enum":
        choices = setting.choices
        if value.lower() not in choices:
            return [_issue("error", setting,
                           f"must be one of: {', '.join(choices)} (got {shown})",
                           f"use one of: {', '.join(choices)}")]
        return []

    if kind == "url":
        return _check_url(setting, value, shown)

    if kind == "url_list":
        for item in _split_list(value):
            problem = _check_url(setting, item, _show(setting, item))
            if problem:
                return problem
        return []

    if kind in {"file", "dir"}:
        return _check_path(setting, value, shown, required=required, using_default=using_default)

    if kind == "model":
        if not _MODEL_NAME.match(value):
            return [_issue("error", setting,
                           f"must be a model name without spaces (got {shown})",
                           "use the name exactly as the provider or Ollama lists it")]
        return []

    if kind == "backend":
        backend = value.lower()
        if backend not in _backend_names():
            return [_issue("error", setting,
                           f"must be one of: {', '.join(_backend_names())} (got {shown})",
                           "use one of the listed backends")]
        return []

    if kind == "cloud_backend":
        backend = value.lower()
        if backend not in _cloud_backend_names():
            return [_issue("error", setting,
                           f"must be one of: {', '.join(_cloud_backend_names())} (got {shown})",
                           "use a cloud backend, or remove the setting")]
        return []

    if kind == "backend_list":
        allowed = (*_cloud_backend_names(), "lfm", "ollama")
        for item in _split_list(value):
            if item.lower() not in allowed:
                return [_issue("error", setting,
                               f"{shown} is not a backend; expected some of: "
                               f"{', '.join(allowed)}",
                               "list backends separated by commas")]
        return []

    return []  # pragma: no cover - every Kind is handled above


def _check_bounds(setting: Setting, number: float, shown: str) -> list[Issue]:
    minimum, maximum = setting.minimum, setting.maximum
    if minimum is not None:
        too_small = number <= minimum if setting.exclusive_minimum else number < minimum
        if too_small:
            comparison = "greater than" if setting.exclusive_minimum else "at least"
            return [_issue("error", setting,
                           f"must be {comparison} {_number_text(minimum)} (got {shown})",
                           _range_fix(setting))]
    if maximum is not None and number > maximum:
        return [_issue("error", setting,
                       f"must be at most {_number_text(maximum)} (got {shown})",
                       _range_fix(setting))]
    return []


def _check_url(setting: Setting, value: str, shown: str) -> list[Issue]:
    try:
        parsed = urlparse(value)
        hostname = parsed.hostname
        has_host = bool(hostname) and not any(char.isspace() for char in hostname)
        # Accessing .port validates numeric spelling and the 0-65535 range.
        # netloc alone accepts both an empty hostname and a malformed port.
        _ = parsed.port
    except ValueError:
        # A malformed IPv6 literal is the usual way `urlparse` raises.
        parsed, has_host = None, False
    if parsed is None or parsed.scheme not in {"http", "https"} or not has_host:
        return [_issue("error", setting,
                       f"must be an http(s) URL with a host (got {shown})",
                       "write it as http://host:port (or https://…)")]
    return []


def _check_path(
    setting: Setting,
    value: str,
    shown: str,
    *,
    required: bool = False,
    using_default: bool = False,
) -> list[Issue]:
    path = Path(value).expanduser()
    # A bare command name is found on PATH by the runtime, not by this check.
    if setting.command and not any(separator in value for separator in ("/", "\\")):
        return []
    try:
        exists, is_file, is_dir = path.exists(), path.is_file(), path.is_dir()
    except (OSError, ValueError):
        return [_issue("error", setting, f"is not a usable path: {shown}",
                       f"point {setting.name} at {setting.summary}")]
    if not exists:
        fix = (setting.missing_fix if using_default else None) or (
            f"point {setting.name} at {setting.summary}, or remove the line"
        )
        return [_issue("error" if required else "warning", setting,
                       f"does not exist: {shown}", fix)]
    if setting.kind == "file" and not is_file:
        return [_issue("error", setting, f"is not a file: {shown}",
                       f"point {setting.name} at {setting.summary}")]
    if setting.kind == "dir" and not is_dir:
        return [_issue("error", setting, f"is not a directory: {shown}",
                       f"point {setting.name} at {setting.summary}")]
    return []


def _unknown_settings(source: Mapping[str, str]) -> list[Issue]:
    known = KNOWN_NAMES
    issues: list[Issue] = []
    for name in sorted(source):
        upper = name.upper()
        if not upper.startswith("ODESSY_") or name in known:
            continue
        if any(upper.startswith(prefix) for prefix in _IGNORED_PREFIXES):
            continue
        close = difflib.get_close_matches(name, sorted(known), n=1, cutoff=0.72)
        fix = (
            f"did you mean {close[0]}?"
            if close
            else "remove it: Odessy never reads this name"
        )
        issues.append(Issue("warning", name, "is not a setting Odessy reads", fix))
    return issues


def _issue(level: Level, setting: Setting, message: str, fix: str | None) -> Issue:
    return Issue(level, setting.name, message, fix)


def _required_fix(setting: Setting) -> str:
    return f"set {setting.name} to {setting.summary} in ~/.config/odessy/odessy.env"


def _range_fix(setting: Setting) -> str | None:
    if setting.minimum is None and setting.maximum is None:
        return None
    if setting.minimum is not None and setting.maximum is not None:
        lower = ">" if setting.exclusive_minimum else ""
        return f"use a number {lower}{_number_text(setting.minimum)} to {_number_text(setting.maximum)}"
    if setting.minimum is not None:
        lower = "greater than" if setting.exclusive_minimum else "at least"
        return f"use a number {lower} {_number_text(setting.minimum)}"
    return f"use a number at most {_number_text(setting.maximum)}"


def _number_text(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else f"{value:g}"


def _show(setting: Setting, value: str) -> str:
    if setting.kind == "secret":
        return "a secret value (not shown)"
    # A URL can carry credentials in its userinfo; they are never printed, and they
    # are not something the redaction patterns are guaranteed to recognize.
    if "://" in value:
        try:
            parsed = urlparse(value)
        except ValueError:
            parsed = None
        if parsed is not None and (parsed.username or parsed.password):
            value = parsed._replace(netloc=parsed.hostname or "").geturl()
    clipped = value if len(value) <= 60 else value[:57] + "..."
    return repr(redact_text(clipped))


def _split_list(value: str) -> list[str]:
    return [item.strip() for item in value.replace(";", ",").split(",") if item.strip()]


def _backend_names() -> tuple[str, ...]:
    # Imported here: the model package imports configuration, and this module is
    # imported by the CLI before a backend exists.
    return ("lfm", "ollama", "llama_cpp", "auto", *_cloud_backend_names())


def _cloud_backend_names() -> tuple[str, ...]:
    from odessy.models.cloud_model import CLOUD_PROVIDERS

    return tuple(CLOUD_PROVIDERS)
