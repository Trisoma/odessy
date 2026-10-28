"""Hooks: programs you configure to run at fixed points of a turn (``hooks.toml``).

Three events, each a list of programs::

    [[pre_tool]]
    name = "no-force-push"
    match = ["terminal.run", "laptop.terminal.run"]
    command = ["python", "C:/Users/me/hooks/no_force_push.py"]

    [[post_tool]]
    name = "lint"
    match = "filesystem.write*"
    workspace = "D:/projects/odessy"
    command = ["ruff", "check", "--quiet", "."]

    [[turn_end]]
    name = "notify"
    command = ["notify-send", "Odessy finished a turn"]

* ``pre_tool`` runs after the policy has decided and before anyone is asked. It can
  only make the decision stricter: deny the call, or ask for confirmation of a call
  the policy would have allowed. It cannot allow what the policy denies, skip a
  confirmation, or widen a command's sandbox mode.
* ``post_tool`` runs after a call. What it prints is added to the tool result as a
  note for the model; the tool's own result is never changed.
* ``turn_end`` runs after the turn has its answer, in the background. Its output is
  ignored.

The file lives with the user's config (``~/.config/odessy/hooks.toml``, or
``ODESSY_HOOKS_FILE``), never in the workspace: a repository must not be able to run
programs of its own choosing. A hook runs as you, outside the sandbox, with the
workspace as its working directory and an environment without API keys. It reads one
JSON object on stdin; ``docs/reference/hooks.mdx`` describes the protocol.
"""

from __future__ import annotations

import asyncio
import fnmatch
import json
import os
import signal
import re
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Literal

from odessy.authority.redaction import redact_text, redact_value
from odessy.runtime.types import PolicyDecision, ToolRequest

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - exercised on 3.10 only
    import tomli as tomllib

HOOKS_FILE_ENV = "ODESSY_HOOKS_FILE"

Event = Literal["pre_tool", "post_tool", "turn_end"]
EVENTS: tuple[Event, ...] = ("pre_tool", "post_tool", "turn_end")

_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
_RISKS = ("low", "medium", "high")
_DEFAULT_TIMEOUT = {"pre_tool": 5.0, "post_tool": 10.0, "turn_end": 10.0}
_MAX_TIMEOUT = 60.0
#: Hooks per event. Every pre-tool hook runs before a matching call, so a long list
#: makes every turn slower.
MAX_HOOKS_PER_EVENT = 8
#: Characters of a hook's output kept: a deny reason, or a note given to the model.
MAX_REASON_CHARS = 300
MAX_NOTE_CHARS = 2000
#: Characters of the final answer a turn-end hook receives.
MAX_FINAL_TEXT_CHARS = 2000

#: The only variables a hook inherits. API keys and tokens live in the environment
#: (never in odessy.env), so a hook gets an allowlist rather than a filtered copy.
_INHERITED_ENV = (
    "PATH", "PATHEXT", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "TEMP", "TMP",
    "TMPDIR", "HOME", "USERPROFILE", "HOMEDRIVE", "HOMEPATH", "APPDATA", "LOCALAPPDATA",
    "LANG", "LC_ALL", "LC_CTYPE", "TZ",
)

_STRICTNESS = {"allow": 0, "confirm": 1, "deny": 2}


class HookConfigError(ValueError):
    """The hooks file is malformed. Raised with the file and entry named."""


@dataclass(frozen=True)
class Hook:
    event: Event
    name: str
    command: tuple[str, ...]
    #: Tool-name patterns (``fnmatch``, case-sensitive); pre/post-tool hooks only.
    match: tuple[str, ...] = ("*",)
    #: Policy risk levels the hook runs for; empty means every level.
    risk: frozenset[str] = frozenset()
    timeout: float = 5.0
    #: Pre-tool hooks only: the decision when the hook fails, times out, or prints
    #: something that is not a decision. A failing hook never allows a call.
    on_error: Literal["deny", "confirm"] = "deny"

    def applies(self, tool: str, risk: str) -> bool:
        if self.risk and risk not in self.risk:
            return False
        return any(fnmatch.fnmatchcase(tool, pattern) for pattern in self.match)


@dataclass(frozen=True)
class HookVerdict:
    """What the pre-tool hooks said about one call. ``hook`` is None when none ran."""

    action: Literal["allow", "confirm", "deny"] = "allow"
    reason: str = ""
    hook: str | None = None
    #: Every hook that ran and what it decided, for the turn log.
    results: tuple[dict[str, Any], ...] = ()


ALLOW = HookVerdict()


@dataclass(frozen=True)
class HookRun:
    """One hook process: its exit code and output, or why it did not finish."""

    code: int | None = None
    stdout: str = ""
    stderr: str = ""
    error: str | None = None


def tighten(decision: PolicyDecision, verdict: HookVerdict) -> PolicyDecision:
    """The policy decision made stricter by the hooks' verdict, never looser.

    A call the policy allowed and a hook wants confirmed keeps the policy's sandbox
    mode (``mode_outcome``): confirming a read-only command must not let it write.
    """

    if verdict.hook is None or decision.action == "deny" or verdict.action == "allow":
        return decision
    rule = f"hook:{verdict.hook}"
    if verdict.action == "deny":
        reason = f"blocked by the {verdict.hook} hook: {verdict.reason or 'no reason given'}"
        return replace(decision, action="deny", reason=reason, rule=rule, preapproved=False)
    reason = f"{decision.reason}; the {verdict.hook} hook asks for confirmation: {verdict.reason or 'no reason given'}"
    return replace(
        decision,
        action="confirm",
        reason=reason,
        rule=rule,
        preapproved=False,
        mode_outcome=decision.mode_outcome or decision.action,
    )


@dataclass(frozen=True)
class HookSet:
    """The hooks that apply to one workspace."""

    hooks: tuple[Hook, ...]
    workspace_root: Path
    source: Path | None = None
    environ: Mapping[str, str] = field(default_factory=lambda: os.environ, repr=False, compare=False)

    def for_event(self, event: Event) -> tuple[Hook, ...]:
        return tuple(hook for hook in self.hooks if hook.event == event)

    def matching(self, event: Event, tool: str, risk: str) -> tuple[Hook, ...]:
        return tuple(hook for hook in self.for_event(event) if hook.applies(tool, risk))

    async def pre_tool(self, request: ToolRequest, decision: PolicyDecision) -> HookVerdict:
        """Run the matching pre-tool hooks in order; the strictest answer wins.

        A deny stops the remaining hooks: nothing they say could loosen it.
        """

        hooks = self.matching("pre_tool", request.name, decision.risk)
        if not hooks:
            return ALLOW
        payload = {
            "event": "pre_tool",
            "tool": request.name,
            "arguments": redact_value(dict(request.arguments)),
            "policy": {"decision": decision.action, "risk": decision.risk},
            "workspace": str(self.workspace_root),
        }
        verdict = ALLOW
        results: list[dict[str, Any]] = []
        for hook in hooks:
            action, reason = _pre_tool_answer(hook, await self.run(hook, payload))
            results.append({"hook": hook.name, "decision": action, "reason": reason})
            if _STRICTNESS[action] > _STRICTNESS[verdict.action] or verdict.hook is None:
                verdict = HookVerdict(action, reason, hook.name)
            if action == "deny":
                break
        return replace(verdict, results=tuple(results))

    async def post_tool(self, request: ToolRequest, result: Mapping[str, Any], risk: str) -> list[dict[str, Any]]:
        """Run the matching post-tool hooks; each returns a note or a failure record."""

        hooks = self.matching("post_tool", request.name, risk)
        if not hooks:
            return []
        payload = {
            "event": "post_tool",
            "tool": request.name,
            "arguments": redact_value(dict(request.arguments)),
            "result": redact_value(dict(result)),
            "workspace": str(self.workspace_root),
        }
        records = []
        for hook in hooks:
            run = await self.run(hook, payload)
            if run.error is not None:
                records.append({"hook": hook.name, "error": run.error})
                continue
            note = _note(run)
            if note:
                records.append({"hook": hook.name, "exit_code": run.code, "note": note})
        return records

    async def turn_end(self, payload: Mapping[str, Any]) -> list[dict[str, Any]]:
        """Run every turn-end hook; returns the failures, for the log."""

        failures = []
        for hook in self.for_event("turn_end"):
            run = await self.run(hook, {"event": "turn_end", **payload, "workspace": str(self.workspace_root)})
            if run.error is not None:
                failures.append({"hook": hook.name, "error": run.error})
        return failures

    async def run(self, hook: Hook, payload: Mapping[str, Any]) -> HookRun:
        return await run_hook(hook, payload, cwd=self.workspace_root, environ=self.environ)


def hook_environment(environ: Mapping[str, str], *, event: str, workspace: Path) -> dict[str, str]:
    env = {name: environ[name] for name in _INHERITED_ENV if name in environ}
    env.update(
        PYTHONIOENCODING="utf-8",
        PYTHONUTF8="1",
        ODESSY_HOOK_EVENT=event,
        ODESSY_WORKSPACE=str(workspace),
    )
    return env


async def run_hook(
    hook: Hook,
    payload: Mapping[str, Any],
    *,
    cwd: Path,
    environ: Mapping[str, str] | None = None,
) -> HookRun:
    """Run one hook with ``payload`` as JSON on stdin, within its timeout."""

    env = hook_environment(os.environ if environ is None else environ, event=hook.event, workspace=cwd)
    data = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
    command = hook.command
    if os.name == "nt":
        # The supervisor joins a kill-on-close job before spawning the hook.
        command = (sys.executable, "-I", str(Path(__file__).with_name("_hook_host.py")), *command)
    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(cwd),
            env=env,
            limit=8192,
            **({"creationflags": 0x08000000} if os.name == "nt" else {"start_new_session": True}),
        )
    except (OSError, ValueError) as error:
        return HookRun(error=f"cannot start {hook.command[0]!r}: {error}")
    async def feed() -> None:
        try:
            process.stdin.write(data)
            await process.stdin.drain()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            process.stdin.close()

    tasks = [asyncio.create_task(feed()),
             asyncio.create_task(_read_output(process.stdout)),
             asyncio.create_task(_read_output(process.stderr)),
             asyncio.create_task(process.wait())]
    try:
        _, stdout, stderr, _ = await asyncio.wait_for(asyncio.gather(*tasks), hook.timeout)
    except asyncio.TimeoutError:
        await _kill(process)
        return HookRun(error=f"timed out after {hook.timeout:g} s")
    except _OutputLimit:
        await _kill(process)
        return HookRun(error=f"output limit exceeded ({MAX_OUTPUT_BYTES} bytes per stream)")
    except BaseException:
        await _kill(process)
        raise
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    return HookRun(
        code=process.returncode,
        stdout=stdout.decode("utf-8", "replace"),
        stderr=stderr.decode("utf-8", "replace"),
    )


MAX_OUTPUT_BYTES = 65536


class _OutputLimit(Exception):
    pass


async def _read_output(reader: asyncio.StreamReader) -> bytes:
    output = bytearray()
    while chunk := await reader.read(4096):
        if len(output) + len(chunk) > MAX_OUTPUT_BYTES:
            raise _OutputLimit
        output.extend(chunk)
    return bytes(output)


async def _kill(process: asyncio.subprocess.Process) -> None:
    try:
        if os.name != "nt":
            os.killpg(process.pid, signal.SIGKILL)
        elif process.returncode is None:
            process.kill()
    except ProcessLookupError:
        pass
    try:
        await asyncio.wait_for(process.wait(), 0.5)
    except asyncio.TimeoutError:
        # An escaped descendant must not hold pipe cleanup open indefinitely.
        # asyncio has no public Process.close(); close its owned pipe transport.
        process._transport.close()


def _short(text: str, limit: int) -> str:
    text = redact_text(text.strip())
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _pre_tool_answer(hook: Hook, run: HookRun) -> tuple[str, str]:
    """A pre-tool hook's decision and reason.

    Exit 0 with no output allows; exit 0 with ``{"decision": ..., "reason": ...}``
    decides; exit 2 denies with stderr as the reason. Anything else is a failure,
    decided by ``on_error``.
    """

    if run.error is None and run.code == 2:
        return "deny", _short(run.stderr or run.stdout, MAX_REASON_CHARS) or "denied"
    if run.error is None and run.code == 0:
        text = run.stdout.strip()
        if not text:
            return "allow", ""
        try:
            answer = json.loads(text)
        except json.JSONDecodeError:
            answer = None
        if isinstance(answer, dict) and answer.get("decision") in _STRICTNESS:
            return answer["decision"], _short(str(answer.get("reason") or ""), MAX_REASON_CHARS)
        failure = "printed something that is not a decision"
    else:
        failure = run.error or f"exited with code {run.code}: {_short(run.stderr, 120)}"
    return hook.on_error, f"the hook failed ({failure})"


def _note(run: HookRun) -> str:
    text = run.stdout.strip()
    try:
        answer = json.loads(text) if text else None
    except json.JSONDecodeError:
        answer = None
    if isinstance(answer, dict) and "note" in answer:
        text = str(answer.get("note") or "")
    if not text and run.code:
        text = run.stderr
    return _short(text, MAX_NOTE_CHARS)


def default_hooks_file() -> Path:
    return Path.home() / ".config" / "odessy" / "hooks.toml"


def hooks_file(environ: Mapping[str, str] | None = None) -> Path:
    source = os.environ if environ is None else environ
    explicit = source.get(HOOKS_FILE_ENV, "").strip()
    return Path(explicit).expanduser() if explicit else default_hooks_file()


def _same_path(a: Path, b: Path) -> bool:
    return os.path.normcase(str(a.resolve())) == os.path.normcase(str(b.resolve()))


def load_hooks(
    workspace_root: Path,
    path: Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> HookSet | None:
    """The hooks for ``workspace_root``, or None when there are none."""

    target = path or hooks_file(environ)
    if not target.is_file():
        return None
    hooks = tuple(hook for hook, where in parse_hooks(target) if where is None or _same_path(where, workspace_root))
    if not hooks:
        return None
    return HookSet(hooks, workspace_root, target, os.environ if environ is None else environ)


def parse_hooks(target: Path) -> list[tuple[Hook, Path | None]]:
    """Every hook in the file with the workspace it is limited to, validated."""

    try:
        data = tomllib.loads(target.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise HookConfigError(f"{target}: cannot read hooks file: {error}") from error
    unknown = sorted(set(data) - set(EVENTS))
    if unknown:
        raise HookConfigError(f"{target}: unknown keys {unknown}; use [[pre_tool]], [[post_tool]] or [[turn_end]]")
    parsed: list[tuple[Hook, Path | None]] = []
    names: set[str] = set()
    for event in EVENTS:
        entries = data.get(event, [])
        if not isinstance(entries, list):
            raise HookConfigError(f"{target}: use [[{event}]] entries")
        if len(entries) > MAX_HOOKS_PER_EVENT:
            raise HookConfigError(f"{target}: at most {MAX_HOOKS_PER_EVENT} {event} hooks")
        for index, entry in enumerate(entries, start=1):
            hook, where = _parse_entry(f"{target}: {event} entry {index}", event, entry)
            if hook.name in names:
                raise HookConfigError(f"{target}: two hooks are named {hook.name!r}")
            names.add(hook.name)
            parsed.append((hook, where))
    return parsed


def _parse_entry(where: str, event: Event, entry: Any) -> tuple[Hook, Path | None]:
    allowed = {"name", "command", "timeout", "workspace"}
    if event != "turn_end":
        allowed |= {"match", "risk"}
    if event == "pre_tool":
        allowed |= {"on_error"}
    if not isinstance(entry, dict):
        raise HookConfigError(f"{where}: must be a table")
    unknown = sorted(set(entry) - allowed)
    if unknown:
        raise HookConfigError(f"{where}: unknown keys {unknown}")
    name = entry.get("name")
    if not isinstance(name, str) or not _NAME.match(name):
        raise HookConfigError(f"{where}: name must be 1-32 lowercase letters, digits, '-' or '_'")
    where = f"{where} ({name})"
    command = entry.get("command")
    if not isinstance(command, list) or not command or not all(isinstance(part, str) and part for part in command):
        raise HookConfigError(f"{where}: command must be a non-empty list of strings (the program, then its arguments)")
    match = entry.get("match", ["*"])
    if isinstance(match, str):
        match = [match]
    if not isinstance(match, list) or not match or not all(isinstance(item, str) and item for item in match):
        raise HookConfigError(f"{where}: match must be a tool-name pattern or a list of them")
    risk = entry.get("risk", [])
    if isinstance(risk, str):
        risk = [risk]
    if not isinstance(risk, list) or not set(risk) <= set(_RISKS):
        raise HookConfigError(f"{where}: risk must list some of {list(_RISKS)}")
    timeout = entry.get("timeout", _DEFAULT_TIMEOUT[event])
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0 < timeout <= _MAX_TIMEOUT:
        raise HookConfigError(f"{where}: timeout must be seconds, more than 0 and at most {_MAX_TIMEOUT:g}")
    on_error = entry.get("on_error", "deny")
    if on_error not in ("deny", "confirm"):
        raise HookConfigError(f"{where}: on_error must be 'deny' or 'confirm'; a failing hook never allows a call")
    workspace = entry.get("workspace")
    if workspace is not None and (not isinstance(workspace, str) or not workspace.strip()):
        raise HookConfigError(f"{where}: workspace must be a folder path")
    hook = Hook(
        event=event,
        name=name,
        command=tuple(command),
        match=tuple(match),
        risk=frozenset(risk),
        timeout=float(timeout),
        on_error=on_error,
    )
    return hook, Path(workspace).expanduser() if workspace else None


def public_event(entry: Any) -> tuple[str, dict[str, Any]] | None:
    """The session event for one turn-log entry, or None when it has none.

    Only names, call ids and outcomes: arguments, results and reasons stay in the
    turn log, because a reason can quote an argument.
    """

    event, detail = getattr(entry, "event", None), getattr(entry, "detail", None) or {}
    ids = {key: detail[key] for key in ("call_id", "batch_id") if key in detail}
    if event == "tool_dispatch":
        return "tool", {"phase": "started", "name": detail.get("name"), **ids}
    if event == "tool_observation":
        result = detail.get("result")
        status = result.get("status", "ok") if isinstance(result, dict) else "ok"
        return "tool", {"phase": "finished", "name": detail.get("name"), "status": status, **ids}
    outcomes = {
        "policy_allowed": "allowed",
        "policy_confirmed": "confirmed",
        "policy_preapproved": "preapproved",
        "policy_denied": "denied",
    }
    if event in outcomes:
        return "policy", {"name": detail.get("name"), "outcome": outcomes[event], "risk": detail.get("risk"), **ids}
    if event == "hook_decision":
        return "hook", {"event": "pre_tool", "name": detail.get("name"), "hook": detail.get("hook"),
                        "decision": detail.get("decision"), **ids}
    if event in ("hook_note", "hook_failed"):
        return "hook", {"event": detail.get("event"), "name": detail.get("name"), "hook": detail.get("hook"),
                        "failed": event == "hook_failed", **ids}
    if event == "final_call":
        return "final_call", {"model_calls": detail.get("model_calls"), "limit": detail.get("limit")}
    return None


__all__ = [
    "EVENTS",
    "HOOKS_FILE_ENV",
    "Hook",
    "HookConfigError",
    "HookRun",
    "HookSet",
    "HookVerdict",
    "hook_environment",
    "hooks_file",
    "load_hooks",
    "parse_hooks",
    "public_event",
    "run_hook",
    "tighten",
]
