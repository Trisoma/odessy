"""``python -m odessy doctor``: check this machine's setup and say how to fix it.

Every check is a plain function of the environment and a set of probes (PATH lookup,
HTTP, TCP, subprocess), so the checks are tested without a real Ollama or Node. Each
failed check carries the command that fixes it; the point is that a new user never
has to work out from a stack trace what is missing.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

from odessy.config.config_file import config_files
from odessy.config.deployment import (
    DEPLOYMENT_ENV,
    DeploymentConfigError,
    deployment_mode,
    local_os,
    remote_tool_server_urls,
)

Status = Literal["ok", "warn", "fail"]

DEFAULT_OLLAMA_ENDPOINT = "http://127.0.0.1:11434"
DEFAULT_LFM_MODEL = "lfm2.5-thinking:1.2b-q8_0"
DEFAULT_GEMMA_MODEL = "gemma3:1b"
#: The Node tool server's test suite needs 22 (its test glob); the server itself runs
#: on 20, which is past end-of-life.
MIN_NODE_MAJOR = 20
RECOMMENDED_NODE_MAJOR = 22


@dataclass(frozen=True)
class Check:
    section: str
    status: Status
    summary: str
    fix: str | None = None


def _which(command: str) -> str | None:
    return shutil.which(command)


def _http_get_json(url: str, timeout: float) -> Any:
    with urllib.request.urlopen(url, timeout=timeout) as response:  # noqa: S310 - local, user-configured
        return json.loads(response.read().decode("utf-8"))


def _tcp_reachable(host: str, port: int, timeout: float) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _command_output(argv: list[str], timeout: float) -> str | None:
    try:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def _node_tools_state() -> tuple[bool, bool]:
    """(dependencies installed, build current) for the repository's tools/ directory."""

    from odessy.tools.node_mcp import _build_is_current, _node_tools_dir, _public_repo_root

    tools_dir = _node_tools_dir(_public_repo_root())
    installed = (tools_dir / "node_modules" / "@modelcontextprotocol" / "sdk").exists()
    return installed, _build_is_current(tools_dir)


def _path_exists(path: str) -> bool:
    return Path(path).expanduser().exists()


def _list_server_tools(config: Any) -> list[Any]:
    """One configured third-party server's tools, through a connection of its own."""

    import asyncio

    from odessy.tools.external_tools import NamespacedServer

    return asyncio.run(asyncio.wait_for(NamespacedServer(config).list_tools(), timeout=config.timeout))


@dataclass(frozen=True)
class Probes:
    which: Callable[[str], str | None] = _which
    http_get_json: Callable[[str, float], Any] = _http_get_json
    tcp_reachable: Callable[[str, int, float], bool] = _tcp_reachable
    command_output: Callable[[list[str], float], str | None] = _command_output
    node_tools_state: Callable[[], tuple[bool, bool]] = _node_tools_state
    path_exists: Callable[[str], bool] = _path_exists
    list_server_tools: Callable[[Any], list[Any]] = _list_server_tools


def run_checks(
    backend: str | None = None,
    env: Mapping[str, str] | None = None,
    probes: Probes | None = None,
    *,
    voice: bool = False,
) -> list[Check]:
    source = os.environ if env is None else env
    probes = probes or Probes()
    checks = [_python_check(), *_config_checks(), *_settings_checks(source)]
    try:
        mode = deployment_mode(source)
    except DeploymentConfigError as error:
        return [*checks, Check("Deployment", "fail", str(error), f"Set {DEPLOYMENT_ENV} to single or split.")]
    checks.append(_deployment_check(mode, source))
    checks.extend(_model_checks(backend or source.get("ODESSY_MODEL_BACKEND") or "lfm", source, probes))
    if mode == "single":
        checks.extend(_node_checks(probes))
    elif paired_alias(source):
        checks.extend(_paired_checks(source))
    else:
        checks.extend(_remote_checks(source, probes))
    checks.extend(_server_checks(backend or source.get("ODESSY_MODEL_BACKEND") or "lfm", source, probes))
    checks.extend(_commands_checks(source))
    checks.extend(_hooks_checks(source))
    checks.extend(_voice_checks(source, probes))
    if voice:
        checks.extend(_wake_checks(source, probes))
    return checks


def _commands_checks(env: Mapping[str, str]) -> list[Check]:
    """Commands approved in advance (commands.toml). Silent when there is no file."""

    from odessy.config.commands import CommandsConfigError, commands_file, load_preapproved_commands

    path = commands_file(env)
    if not path.is_file():
        return []
    try:
        approved = load_preapproved_commands(Path.cwd(), path)
    except CommandsConfigError as error:
        return [Check("Approved commands", "fail", str(error), f"Correct {path}; see policy/README.md.")]
    listed = ", ".join(" ".join(words) for words in approved) or "none"
    return [Check("Approved commands", "ok", f"for this folder, local terminal.run only, without asking: {listed}; laptop.terminal.run still requires confirmation")]


def _hooks_checks(env: Mapping[str, str]) -> list[Check]:
    """Hooks (hooks.toml). Silent when there is no file."""

    from odessy.runtime.hooks import EVENTS, HookConfigError, hooks_file, load_hooks

    path = hooks_file(env)
    if not path.is_file():
        return []
    try:
        hooks = load_hooks(Path.cwd(), path, env)
    except HookConfigError as error:
        return [Check("Hooks", "fail", str(error), f"Correct {path}; see docs/reference/hooks.mdx.")]
    if hooks is None:
        return [Check("Hooks", "ok", f"none apply to this folder ({path})")]
    missing = sorted({hook.command[0] for hook in hooks.hooks if _missing_program(hook.command[0])})
    listed = "; ".join(
        f"{event}: {', '.join(hook.name for hook in hooks.for_event(event))}" for event in EVENTS if hooks.for_event(event)
    )
    detail = f"{listed}. They run as you, outside the sandbox"
    if missing:
        return [Check("Hooks", "warn", f"{detail}; program not found: {', '.join(missing)}",
                      "Use the program's full path in command; a pre_tool hook that cannot start denies the call.")]
    return [Check("Hooks", "ok", detail)]


def _missing_program(program: str) -> bool:
    return shutil.which(program) is None and not Path(program).expanduser().is_file()


def _python_check() -> Check:
    version = ".".join(str(part) for part in sys.version_info[:3])
    if sys.version_info < (3, 10):
        return Check("Python", "fail", f"Python {version}; Odessy needs 3.10 or newer", "Install Python 3.10+.")
    return Check("Python", "ok", f"Python {version}")


def _config_checks() -> list[Check]:
    found = [str(path) for path in config_files() if path.exists()]
    if not found:
        return [Check("Config", "ok", "no config file found; using built-in defaults")]
    return [Check("Config", "ok", "loaded " + ", ".join(found))]


def _settings_checks(env: Mapping[str, str]) -> list[Check]:
    """Declared settings: the shape of each value, and names Odessy does not read.

    The same pass the CLI runs at startup, reported as checks so ``doctor`` can
    explain a configuration it would otherwise refuse.
    """

    from odessy.config.schema import validate_config

    report = validate_config(env)
    if not report.issues:
        return [Check("Configuration", "ok", "every ODESSY_* setting is declared and well formed")]
    return [
        Check(
            "Configuration",
            "fail" if issue.level == "error" else "warn",
            f"{issue.setting}: {issue.message}",
            issue.fix,
        )
        for issue in report.issues
    ]
def paired_alias(env: Mapping[str, str]) -> str:
    """The configured paired-host alias, read without importing the transport."""

    return env.get("ODESSY_PAIRED_HOST", "").strip()


def _deployment_check(mode: str, env: Mapping[str, str]) -> Check:
    if paired_alias(env):
        reason = f"set by ODESSY_PAIRED_HOST={paired_alias(env)}"
    elif env.get(DEPLOYMENT_ENV, "").strip():
        reason = f"set by {DEPLOYMENT_ENV}"
    elif mode == "split":
        reason = "a remote tool server URL is configured"
    else:
        reason = "no remote tool server is configured"
    where = "one computer runs everything" if mode == "single" else "this machine plus a laptop tool server"
    return Check("Deployment", "ok", f"{mode} ({where}; {reason}); this machine: {local_os(env)}")


def _model_checks(backend: str, env: Mapping[str, str], probes: Probes) -> list[Check]:
    from odessy.models.cloud_model import CLOUD_PROVIDERS, CloudModelConfigError, build_cloud_model

    backend = backend.lower()
    if backend == "auto":
        from odessy.models.routed_model import RoutingConfig
        try:
            config = RoutingConfig.from_env(env)
        except ValueError as error:
            return [Check("Model", "fail", str(error), "See docs/reference/model-selection.mdx.")]
        checks = [Check("Model", "ok", "auto routing is opt-in; fast text-only and capable local roles")]
        for name in dict.fromkeys((config.fast_model, config.capable_model)):
            checks.extend(_model_checks("lfm", {**env, "ODESSY_MODEL_NAME": name}, probes))
        if config.cloud_fallback:
            checks.extend(_model_checks(config.cloud_backend, env, probes))
        return checks
    if backend in CLOUD_PROVIDERS:
        from odessy.models.failover import FailoverModel, build_cloud_model_with_failover
        try:
            model = build_cloud_model(backend, dict(env))
        except CloudModelConfigError as error:
            return [Check("Model", "fail", f"cloud backend {backend}: {error}", "See docs/reference/cloud-models.mdx.")]
        except ImportError as error:
            return [Check("Model", "fail", f"cloud backend {backend}: {error}", 'pip install -e ".[cloud]"')]
        checks = [
            Check(
                "Model",
                "ok",
                f"cloud backend {backend}, model {model.model} (API key found; not contacted)",
            )
        ]
        try:
            # Local fallbacks are named here, not probed: the local model checks cover them.
            chained = build_cloud_model_with_failover(backend, dict(env), local_factory=lambda name: _LocalName(name))
        except CloudModelConfigError as error:
            checks.append(Check("Model", "fail", f"failover: {error}", "See docs/reference/cloud-models.mdx#failover."))
        else:
            if isinstance(chained, FailoverModel):
                checks.append(Check("Model", "ok", f"failover: {chained.chain()} (API keys found; not contacted)"))
        return [*checks, _cloud_usage_check(env), _context_check(env), _egress_check(env)]
    if backend in {"lfm", "llama_cpp"}:
        endpoint = env.get("ODESSY_MODEL_ENDPOINT") or DEFAULT_OLLAMA_ENDPOINT
        name = env.get("ODESSY_MODEL_NAME") or DEFAULT_LFM_MODEL
    elif backend == "ollama":
        endpoint = env.get("ODESSY_OLLAMA_URL") or DEFAULT_OLLAMA_ENDPOINT
        name = env.get("ODESSY_MODEL_NAME") or env.get("ODESSY_OLLAMA_MODEL") or DEFAULT_GEMMA_MODEL
    else:
        return [Check("Model", "fail", f"unknown model backend {backend!r}", "Use lfm, ollama, or a cloud backend.")]

    try:
        tags = probes.http_get_json(endpoint.rstrip("/") + "/api/tags", 5.0)
    except (urllib.error.URLError, OSError, ValueError) as error:
        return [
            Check(
                "Model",
                "fail",
                f"Ollama is not reachable at {endpoint} ({error})",
                "Install Ollama from https://ollama.com and start it, or set ODESSY_MODEL_ENDPOINT.",
            )
        ]
    pulled = {
        str(entry.get("name"))
        for entry in (tags.get("models") or [] if isinstance(tags, dict) else [])
        if isinstance(entry, dict)
    }
    checks = [Check("Model", "ok", f"Ollama reachable at {endpoint}")]
    if _model_pulled(name, pulled):
        checks.append(Check("Model", "ok", f"model {name} is pulled"))
    else:
        checks.append(Check("Model", "fail", f"model {name} is not pulled in Ollama", f"ollama pull {name}"))
    return checks


class _LocalName:
    def __init__(self, name: str) -> None:
        self.model = f"local {name}"


def _context_check(env: Mapping[str, str]) -> Check:
    from odessy.models.cloud_model import (
        DEFAULT_CLOUD_CONTEXT_CHARS,
        DEFAULT_KEEP_RECENT_RESULTS,
        _non_negative_int,
    )

    chars = _non_negative_int(env.get("ODESSY_CLOUD_CONTEXT_CHARS"), DEFAULT_CLOUD_CONTEXT_CHARS)
    keep = _non_negative_int(env.get("ODESSY_CLOUD_KEEP_RECENT_RESULTS"), DEFAULT_KEEP_RECENT_RESULTS)
    compaction = (
        f"earlier tool results compacted past {chars:,} characters a turn, latest {keep} kept whole"
        if chars
        else "tool-result compaction off"
    )
    limit = (env.get("ODESSY_MAX_MODEL_CALLS") or "").strip()
    calls = f"{limit} model calls a turn" if limit else "8 model calls a spoken turn, 20 a typed one"
    return Check("Model", "ok", f"context: {compaction}; {calls}")


def _egress_check(env: Mapping[str, str]) -> Check:
    from odessy.authority.egress import EGRESS_FILE_ENV, egress_file

    path = egress_file(env)
    if path is None:
        return Check("Model", "ok", f"egress log off ({EGRESS_FILE_ENV} is empty)")
    return Check("Model", "ok", f"egress log: {path} (what was sent to cloud providers; python -m odessy egress)")


def _cloud_usage_check(env: Mapping[str, str]) -> Check:
    from odessy.models.usage import CloudUsageConfigError, UsageLedger

    try:
        ledger = UsageLedger.from_env(env)
    except CloudUsageConfigError as error:
        return Check("Model", "fail", f"cloud usage settings: {error}", "See docs/reference/cloud-models.mdx#cost-and-usage.")
    prices = (
        f"prices per million tokens {ledger.pricing.input:g} / {ledger.pricing.cached_input:g} cached / "
        f"{ledger.pricing.output:g} out {ledger.pricing.currency}"
        if ledger.pricing
        else "no prices set (tokens only)"
    )
    budget = ledger.budget_line() if (ledger.budget_tokens is not None or ledger.budget_cost is not None) else "no budget"
    return Check("Model", "ok", f"cloud usage: {prices}; {budget.replace('0 of ', 'limit ')}")


def _model_pulled(name: str, pulled: set[str]) -> bool:
    # Ollama lists an untagged pull as "name:latest".
    return name in pulled or (":" not in name and f"{name}:latest" in pulled)


def _node_checks(probes: Probes) -> list[Check]:
    degraded = (
        "without it Odessy still runs, but only with time, calculator and an unconfined "
        "terminal.run (no file, search or git tools)"
    )
    if probes.which("node") is None:
        return [
            Check(
                "Tool server",
                "warn",
                f"Node.js not found; {degraded}",
                f"Install Node.js {RECOMMENDED_NODE_MAJOR} or newer from https://nodejs.org",
            )
        ]
    version = probes.command_output(["node", "--version"], 10.0) or ""
    major = _node_major(version)
    checks: list[Check] = []
    if major is not None and major < MIN_NODE_MAJOR:
        return [
            Check(
                "Tool server",
                "warn",
                f"Node.js {version} is too old; {degraded}",
                f"Install Node.js {RECOMMENDED_NODE_MAJOR} or newer.",
            )
        ]
    if major is not None and major < RECOMMENDED_NODE_MAJOR:
        checks.append(Check("Tool server", "warn", f"Node.js {version} works but is end-of-life", f"Upgrade to Node.js {RECOMMENDED_NODE_MAJOR}+."))
    else:
        checks.append(Check("Tool server", "ok", f"Node.js {version or '(version unknown)'}"))
    if probes.which("npm") is None:
        checks.append(Check("Tool server", "warn", f"npm not found; {degraded}", "Reinstall Node.js with npm."))
        return checks
    installed, built = probes.node_tools_state()
    if not installed:
        checks.append(Check("Tool server", "warn", f"tools/ dependencies are not installed; {degraded}", "cd tools && npm install && npm run build"))
        return checks
    if not built:
        checks.append(
            Check(
                "Tool server",
                "warn",
                "tools/ build is missing or out of date; every start will compile first (about 10 s)",
                "cd tools && npm run build",
            )
        )
    else:
        checks.append(Check("Tool server", "ok", "tools/ is installed and built; started automatically on loopback"))
    return checks


def _node_major(version: str) -> int | None:
    digits = version.strip().lstrip("v").split(".", 1)[0]
    return int(digits) if digits.isdigit() else None


def _paired_checks(env: Mapping[str, str]) -> list[Check]:
    """Paired-host status: configured, binary present, enrolment, alias match.

    Runs `odessy-link peers list`, which reads the local registry only: no connection
    is attempted and no key material or fingerprint is printed.
    """

    from odessy.tools.paired import PairedConfigError, paired_host, probe

    try:
        host = paired_host(env)
    except PairedConfigError as error:
        return [Check("Paired host", "fail", str(error), "Fix ODESSY_PAIRED_HOST or ODESSY_LINK_BIN.")]
    if host is None:  # pragma: no cover - guarded by the caller
        return []
    result = probe(host)
    if not result.get("ok"):
        return [
            Check(
                "Paired host",
                "fail",
                f"{host.alias}: {result.get('detail')}",
                "Build odessy-link (`cargo build --release` in native/odessy-link) or set ODESSY_LINK_BIN.",
            )
        ]
    checks = [
        Check(
            "Paired host",
            "ok",
            f"{host.alias} via {result.get('binary')}; "
            f"{result.get('enrolled_peers', 0)} enrolled peer(s); "
            f"grant revision {result.get('grant_revision', '?')}",
        )
    ]
    if not result.get("configured_alias_present"):
        checks.append(
            Check(
                "Paired host enrolment",
                "fail",
                f"{host.alias} is configured but not enrolled on this machine",
                f"odessy-link pair add --alias {host.alias} --certificate <bundle> --expect-sha256 <fingerprint>",
            )
        )
    elif not result.get("capability_mcp"):
        checks.append(
            Check(
                "Paired host permission",
                "fail",
                f"{host.alias} is enrolled but holds no mcp capability on this machine",
                f"odessy-link peers grant {host.alias} --capability mcp --workspace <path>",
            )
        )
    else:
        checks.append(
            Check(
                "Paired host permission",
                "ok",
                f"{host.alias} holds mcp for {result.get('workspace')}",
            )
        )
    return checks


def _remote_checks(env: Mapping[str, str], probes: Probes) -> list[Check]:
    urls = remote_tool_server_urls(env)
    if not urls:
        return [
            Check(
                "Tool server",
                "warn",
                "split mode but no laptop tool server is configured; only this machine's tools are available",
                "Set ODESSY_TOOL_SERVER_URL=http://<laptop-ip>:8765/mcp in ~/.config/odessy/odessy.env",
            )
        ]
    checks = []
    for url in urls:
        parsed = urlparse(url)
        host, port = parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80)
        if not host:
            checks.append(Check("Tool server", "fail", f"{url} is not a valid URL", "Use http://<laptop-ip>:8765/mcp"))
        elif probes.tcp_reachable(host, port, 5.0):
            checks.append(Check("Tool server", "ok", f"{url} is reachable"))
        else:
            checks.append(
                Check(
                    "Tool server",
                    "fail",
                    f"{url} is not reachable",
                    "On the laptop: set ODESSY_MCP_HOST=0.0.0.0 and run `npm run start:http` in tools/ "
                    "(see README, 'Laptop MCP Server').",
                )
            )
    return checks


def _server_checks(backend: str, env: Mapping[str, str], probes: Probes) -> list[Check]:
    """Third-party MCP servers in servers.toml. Silent when there is no servers file.

    A malformed file fails (Odessy refuses to start with it). A server that cannot be
    started or reached only warns: Odessy runs without it.
    """

    from odessy.authority.audit import AUDIT_FILE_ENV, audit_file
    from odessy.models.cloud_model import CLOUD_PROVIDERS
    from odessy.tools.external_tools import (
        MAX_EXTERNAL_TOOLS_CLOUD,
        MAX_EXTERNAL_TOOLS_LOCAL,
        ServerConfigError,
        load_server_configs,
        servers_file,
        tool_from_mcp,
    )

    section = "Third-party servers"
    path = servers_file(env)
    try:
        configs = load_server_configs(path)
    except ServerConfigError as error:
        return [Check(section, "fail", str(error), f"Correct {path}; see docs/reference/external-servers.mdx.")]
    if not configs:
        return []

    checks = [Check(section, "ok", f"{len(configs)} configured in {path}")]
    offered = 0
    for config in configs:
        label = f"{config.name} ({config.transport}, {config.trust})"
        if config.transport == "stdio":
            program = config.command[0]
            if probes.which(program) is None and not probes.path_exists(program):
                checks.append(
                    Check(section, "warn", f"{label}: command not found: {program}", f"Install {program}, or put its full path in command.")
                )
                continue
        try:
            listed = probes.list_server_tools(config)
        except Exception as error:  # noqa: BLE001 - any failure means "not available"
            where = config.url if config.transport == "http" else " ".join(config.command)
            checks.append(
                Check(
                    section,
                    "warn",
                    f"{label}: not available ({error.__class__.__name__}: {error})",
                    f"Start the server at {where}, or remove it from {path.name}.",
                )
            )
            continue
        usable = 0
        for raw in listed:
            tool, problem = tool_from_mcp(config, raw)
            if problem:
                checks.append(Check(section, "warn", problem, "Left out; the server's other tools are offered."))
            else:
                usable += 1
        offered += usable
        checks.append(Check(section, "ok", f"{label}: {usable} tool(s)"))

    cloud = backend.lower() in CLOUD_PROVIDERS
    limit = MAX_EXTERNAL_TOOLS_CLOUD if cloud else MAX_EXTERNAL_TOOLS_LOCAL
    if offered > limit:
        checks.append(
            Check(
                section,
                "warn",
                f"{offered} third-party tools; only the first {limit} are offered to a {'cloud' if cloud else 'local'} model",
                f"Deny unneeded tools in {path.name} or remove a server.",
            )
        )

    log = audit_file(env)
    if log is None:
        checks.append(Check(section, "warn", f"audit log is off ({AUDIT_FILE_ENV} is empty)", f"Unset {AUDIT_FILE_ENV} to record third-party calls."))
    else:
        checks.append(Check(section, "ok", f"audit log: {log}"))
    return checks


def _voice_checks(env: Mapping[str, str], probes: Probes) -> list[Check]:
    """Voice is optional, so a missing piece is a warning, never a failure."""

    checks: list[Check] = []
    whisper_bin = env.get("ODESSY_WHISPER_BIN") or "whisper-cli"
    if env.get("ODESSY_STT_PROVIDER", "whisper.cpp") == "whisper.cpp" and probes.which(whisper_bin) is None and not probes.path_exists(whisper_bin):
        checks.append(Check("Voice (optional)", "warn", f"speech-to-text program not found: {whisper_bin}", "Build whisper.cpp and set ODESSY_WHISPER_BIN."))
    for env_name, label, default in (
        ("ODESSY_WHISPER_MODEL", "Whisper model", "models/ggml-tiny.en.bin"),
        ("ODESSY_KOKORO_MODEL", "Kokoro model", "kokoro-v1.0.int8.onnx"),
        ("ODESSY_KOKORO_VOICES", "Kokoro voices", "voices-v1.0.bin"),
    ):
        if env_name.startswith('ODESSY_WHISPER') and env.get('ODESSY_STT_PROVIDER', 'whisper.cpp') != 'whisper.cpp':
            continue
        if env_name.startswith('ODESSY_KOKORO') and env.get('ODESSY_TTS_PROVIDER', 'kokoro') != 'kokoro':
            continue
        path = env.get(env_name) or default
        if not probes.path_exists(path):
            checks.append(Check("Voice (optional)", "warn", f"{label} not found: {path}", f"Download it and set {env_name}."))
    for env_name, default in (("ODESSY_AUDIO_RECORDER", "arecord"), ("ODESSY_AUDIO_PLAYER", "aplay")):
        command = env.get(env_name) or default
        if probes.which(command) is None:
            checks.append(Check("Voice (optional)", "warn", f"audio program not found: {command}", f"Set {env_name} (arecord/aplay are Linux-only)."))
    for key, default in (('ODESSY_STT_PROVIDER', 'whisper.cpp'), ('ODESSY_TTS_PROVIDER', 'kokoro')):
        if env.get(key, default) != default:
            checks.append(Check('Voice (optional)', 'warn', f'{key}: custom trusted-code provider configured; not initialized or verified', 'Verify with a controlled voice test.'))
    if not checks:
        checks.append(Check("Voice (optional)", "ok", "speech-to-text, text-to-speech and audio programs found"))
    return checks


def _wake_checks(env: Mapping[str, str], probes: Probes) -> list[Check]:
    """Everything ``voice --listen`` needs, checked without opening the microphone.

    These are warnings, not failures: a host can run text mode perfectly while the
    wake session is impossible. Each one names the command that fixes it, because the
    first failure a new user hits is "it says wake support is missing" with no next
    step.
    """

    checks: list[Check] = []
    section = "Wake session (optional)"

    try:
        import openwakeword  # noqa: F401
    except ImportError:
        checks.append(
            Check(
                section,
                "warn",
                "wake-word support is not installed (openwakeword)",
                "pip install -e '.[wake]'",
            )
        )

    from odessy.voice.assets import DEFAULT_WAKE_MODEL, asset_dir, load_manifest

    configured = env.get("ODESSY_WAKE_MODEL", "").strip()
    model_path = Path(configured).expanduser() if configured else asset_dir(env) / DEFAULT_WAKE_MODEL
    if not probes.path_exists(str(model_path)):
        checks.append(
            Check(
                section,
                "warn",
                f"wake model not found: {model_path}",
                "odessy voice --setup-wake",
            )
        )
    else:
        manifest = load_manifest(model_path.parent)
        verified = manifest.get(model_path.name)
        if verified is True:
            checks.append(Check(section, "ok", f"wake model verified: {model_path.name}"))
        elif verified is False:
            checks.append(
                Check(
                    section,
                    "warn",
                    f"wake model is present but its digest is not pinned: {model_path.name}",
                    "Pin the printed sha256 in odessy.voice.assets.WAKE_ASSETS.",
                )
            )

    recorder = env.get("ODESSY_AUDIO_RECORDER") or "arecord"
    player = env.get("ODESSY_AUDIO_PLAYER") or "aplay"
    capture = probes.command_output([recorder, "-l"], 5) if probes.which(recorder) else None
    if probes.which(recorder) and (capture is None or "card" not in capture.lower()):
        checks.append(
            Check(
                section,
                "warn",
                f"no capture device reported by {recorder} -l",
                "Connect a microphone or set ODESSY_AUDIO_INPUT_DEVICE.",
            )
        )
    playback = probes.command_output([player, "-l"], 5) if probes.which(player) else None
    if probes.which(player) and (playback is None or "card" not in playback.lower()):
        checks.append(
            Check(
                section,
                "warn",
                f"no playback device reported by {player} -l",
                "Connect headphones or set ODESSY_AUDIO_OUTPUT_DEVICE.",
            )
        )

    # Simultaneous readiness: the session listens while it speaks, so one device for
    # both directions is the classic way this fails on ALSA. It is checkable without
    # playing anything.
    input_device = (env.get("ODESSY_AUDIO_INPUT_DEVICE") or "").strip()
    output_device = (env.get("ODESSY_AUDIO_OUTPUT_DEVICE") or "").strip()
    if input_device and input_device == output_device:
        checks.append(
            Check(
                section,
                "warn",
                f"capture and playback use the same device: {input_device}",
                "Set different ODESSY_AUDIO_INPUT_DEVICE and ODESSY_AUDIO_OUTPUT_DEVICE, "
                "or use a headset that exposes both.",
            )
        )

    if not checks:
        checks.append(
            Check(
                section,
                "ok",
                "wake model, capture and playback devices are available together",
            )
        )
    return checks


_MARKS = {"ok": "[ok]  ", "warn": "[warn]", "fail": "[FAIL]"}

def render(checks: list[Check]) -> str:
    lines = ["Odessy doctor", ""]
    section = None
    for check in checks:
        if check.section != section:
            section = check.section
            lines.append(section)
        lines.append(f"  {_MARKS[check.status]} {check.summary}")
        if check.fix and check.status != "ok":
            lines.append(f"         fix: {check.fix}")
    failures = sum(check.status == "fail" for check in checks)
    warnings = sum(check.status == "warn" for check in checks)
    lines.append("")
    if failures:
        lines.append(f"{failures} problem(s) to fix before Odessy can run; {warnings} warning(s).")
    else:
        lines.append(f"Ready to run. {warnings} warning(s).")
    return "\n".join(lines)


def main(backend: str | None = None, *, voice: bool = False) -> int:
    checks = run_checks(backend, voice=voice)
    print(render(checks))
    return 1 if any(check.status == "fail" for check in checks) else 0
