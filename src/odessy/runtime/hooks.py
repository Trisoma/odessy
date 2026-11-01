"""Hooks: programs and rules you configure to screen a turn (``hooks.toml``).

Four events, each a list of programs, and declarative rules
(``odessy.runtime.hook_rules``)::

    [[pre_tool]]
    name = "no-force-push"
    match = ["terminal.run", "laptop.terminal.run"]
    command = ["python", "C:/Users/me/hooks/no_force_push.py"]

    [[post_tool]]
    name = "lint"
    match = "filesystem.write*"
    workspace = "D:/projects/odessy"
    command = ["ruff", "check", "--quiet", "."]

    [[pre_escalation]]
    name = "no-full-access"
    command = ["python", "C:/Users/me/hooks/no_full_access.py"]

    [[turn_end]]
    name = "notify"
    command = ["notify-send", "Odessy finished a turn"]

* ``pre_tool`` runs after the policy has decided and before anyone is asked. It can
  only make the decision stricter: deny the call, or ask for confirmation of a call
  the policy would have allowed. It cannot allow what the policy denies, skip a
  confirmation, or widen a command's sandbox mode. Rules run first, in-process.
* ``pre_escalation`` runs for a call that asks for a wider sandbox
  (``sandbox_permissions``), before the human is asked. It can only deny.
* ``post_tool`` runs after a call. What it prints is added to the tool result as a
  note for the model. It can also mark the turn (``taint``), end it (``"decision":
  "stop"``), or withhold the result from the model (``quarantine``); the tool's own
  result is kept in the turn log.
* ``turn_end`` runs after the turn has its answer, in the background. Its output is
  ignored.

The file is fingerprinted the first time a process reads it. If it, or a script a
hook names, changes or disappears afterwards, every tool call is denied until
Odessy restarts: an agent that rewrote its own hooks must not be screened by them.
The fingerprint is compared again before each hook starts, so a command that edits
a script cannot have its edit run, outside the sandbox, by the hook that follows it.

The file lives with the user's config (``~/.config/odessy/hooks.toml``, or
``ODESSY_HOOKS_FILE``), never in the workspace: a repository must not be able to run
programs of its own choosing. A hook runs as you, outside the sandbox, with the
workspace as its working directory and an environment without API keys. It reads one
JSON object on stdin; ``docs/reference/hooks.mdx`` describes the protocol.
"""

from __future__ import annotations

import asyncio
import fnmatch
import hashlib
import json
import os
import signal
import re
import shutil
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Literal

from odessy.authority.redaction import redact_text, redact_value
from odessy.runtime.hook_rules import (
    Rule,
    RuleConfigError,
    TurnState,
    call_target,
    evaluate_rules,
    parse_rules,
    taints_for,
)
from odessy.runtime.types import PolicyDecision, ToolRequest

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - exercised on 3.10 only
    import tomli as tomllib

HOOKS_FILE_ENV = "ODESSY_HOOKS_FILE"
#: Set to 1 to refuse to start when the hooks file is missing or has nothing for
#: the workspace, instead of running without hooks.
HOOKS_REQUIRED_ENV = "ODESSY_HOOKS_REQUIRED"

Event = Literal["pre_tool", "pre_escalation", "post_tool", "turn_end"]
EVENTS: tuple[Event, ...] = ("pre_tool", "pre_escalation", "post_tool", "turn_end")
_TOP_LEVEL = (*EVENTS, "rule", "presets")

_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
_RISKS = ("low", "medium", "high")
_DEFAULT_TIMEOUT = {"pre_tool": 5.0, "pre_escalation": 5.0, "post_tool": 10.0, "turn_end": 10.0}
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


#: The fingerprint of each hooks file the first time this process read it.
_BASELINES: dict[str, str] = {}
#: Files larger than this are fingerprinted by size and modification time.
_HASHED_BYTES = 1 << 20


@dataclass(frozen=True)
class Hook:
    event: Event
    name: str
    command: tuple[str, ...]
    #: Tool-name patterns (``fnmatch``, case-sensitive); not for turn-end hooks.
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
    #: Not started, because the hooks changed since they were read. Unlike a failure
    #: this is never left to ``on_error``.
    tampered: bool = False


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
    rules: tuple[Rule, ...] = ()
    #: Why this set cannot be trusted (the file changed since the process read it).
    #: A tampered set denies every call.
    tampered: str | None = None
    #: Files a hook names (its scripts), protected from the agent like the file itself.
    scripts: tuple[Path, ...] = ()
    #: The fingerprint of the file and its absolutely named scripts when loaded, and
    #: those scripts; ``verify`` compares it again.
    fingerprint: str | None = None
    fingerprinted: tuple[Path, ...] = ()

    def for_event(self, event: Event) -> tuple[Hook, ...]:
        return tuple(hook for hook in self.hooks if hook.event == event)

    def verify(self) -> "HookSet":
        """This set, or a tampered copy if the file or a script changed since it was loaded.

        The agent calls this each turn and around each tool call, and ``run`` before
        each hook: a script runs fresh each time, so an edit to it would otherwise
        take effect unnoticed.
        """

        if self.tampered is not None or self.fingerprint is None or self.source is None:
            return self
        if _fingerprint(self.source, self.fingerprinted) == self.fingerprint:
            return self
        return replace(self, hooks=(), rules=(),
                       tampered="the hooks file or a hook script changed after Odessy read it; restart Odessy to load it again")

    def matching(self, event: Event, tool: str, risk: str) -> tuple[Hook, ...]:
        return tuple(hook for hook in self.for_event(event) if hook.applies(tool, risk))

    def screens(self, request: ToolRequest, risk: str) -> bool:
        """Whether a call must be screened before it runs (``_screen``)."""

        if self.tampered is not None or self.matching("pre_tool", request.name, risk):
            return True
        if "sandbox_permissions" in request.arguments and self.matching("pre_escalation", request.name, risk):
            return True
        return any(
            rule.decision != "taint" and any(fnmatch.fnmatchcase(request.name, pattern) for pattern in rule.match)
            for rule in self.rules
        )

    def protected_paths(self) -> tuple[Path, ...]:
        """The hooks file, its folder and the scripts its hooks name."""

        paths = [*self.scripts]
        if self.source is not None:
            paths[:0] = [self.source, self.source.parent]
        return tuple(paths)

    async def pre_tool(
        self,
        request: ToolRequest,
        decision: PolicyDecision,
        context: Mapping[str, Any] | None = None,
        state: TurnState | None = None,
    ) -> HookVerdict:
        """Run the rules, then the matching pre-tool hooks in order; the strictest answer wins.

        A deny stops the remaining checks: nothing they say could loosen it.
        ``context`` (execution, session, request) is added to the hook's JSON.
        """

        tampered = self.verify().tampered
        if tampered is not None:
            return HookVerdict("deny", tampered, "integrity", ({"hook": "integrity", "decision": "deny", "reason": tampered},))
        verdict = ALLOW
        results: list[dict[str, Any]] = []
        for rule, action in evaluate_rules(self.rules, request, decision.risk, self.workspace_root, state):
            results.append({"hook": rule.name, "kind": "rule", "decision": action, "reason": rule.describe()})
            if _STRICTNESS[action] > _STRICTNESS[verdict.action] or verdict.hook is None:
                verdict = HookVerdict(action, rule.describe(), rule.name)
            if action == "deny":
                return replace(verdict, results=tuple(results))
        hooks = self.matching("pre_tool", request.name, decision.risk)
        if not hooks:
            return replace(verdict, results=tuple(results))
        payload = {
            "event": "pre_tool",
            "tool": request.name,
            "arguments": redact_value(dict(request.arguments)),
            "policy": {"decision": decision.action, "risk": decision.risk},
            "workspace": str(self.workspace_root),
            **(context or {}),
            "taints": sorted(state.taints) if state is not None else [],
        }
        for hook in hooks:
            action, reason = _pre_tool_answer(hook, await self.run(hook, payload))
            results.append({"hook": hook.name, "decision": action, "reason": reason})
            if _STRICTNESS[action] > _STRICTNESS[verdict.action] or verdict.hook is None:
                verdict = HookVerdict(action, reason, hook.name)
            if action == "deny":
                break
        return replace(verdict, results=tuple(results))

    async def pre_escalation(
        self,
        request: ToolRequest,
        decision: PolicyDecision,
        context: Mapping[str, Any] | None = None,
    ) -> HookVerdict:
        """Run the pre-escalation hooks for a call that asks for a wider sandbox.

        They can only deny: a human is asked about every escalation anyway, so
        ``confirm`` (or an ``on_error = "confirm"`` failure) leaves it to the human.
        """

        requested = request.arguments.get("sandbox_permissions")
        hooks = self.matching("pre_escalation", request.name, decision.risk) if requested is not None else ()
        if not hooks:
            return ALLOW
        payload = {
            "event": "pre_escalation",
            "tool": request.name,
            "arguments": redact_value(dict(request.arguments)),
            "escalation": {
                "requested": requested,
                "justification": redact_text(str(request.arguments.get("justification") or "")),
            },
            "policy": {"decision": decision.action, "risk": decision.risk},
            "workspace": str(self.workspace_root),
            **(context or {}),
        }
        results: list[dict[str, Any]] = []
        for hook in hooks:
            action, reason = _pre_tool_answer(hook, await self.run(hook, payload))
            action = "deny" if action == "deny" else "allow"
            results.append({"hook": hook.name, "event": "pre_escalation", "decision": action, "reason": reason})
            if action == "deny":
                return HookVerdict("deny", f"sandbox escalation refused: {reason}", hook.name, tuple(results))
        return replace(ALLOW, results=tuple(results))

    async def post_tool(
        self,
        request: ToolRequest,
        result: Mapping[str, Any],
        risk: str,
        context: Mapping[str, Any] | None = None,
        state: TurnState | None = None,
    ) -> list[dict[str, Any]]:
        """Run the taint rules and the matching post-tool hooks.

        Each record is a failure (``error``) or what a hook or rule asked for:
        ``note``, ``taint`` (a list), ``stop`` and ``quarantine``, with ``reason``.
        """

        records: list[dict[str, Any]] = []
        if state is not None and result.get("status") not in ("denied", "skipped", "rejected"):
            for rule, taint in taints_for(self.rules, request, risk, self.workspace_root, state):
                records.append({"hook": rule.name, "kind": "rule", "taint": [taint]})
        hooks = self.matching("post_tool", request.name, risk)
        if not hooks:
            return records
        payload = {
            "event": "post_tool",
            "tool": request.name,
            "arguments": redact_value(dict(request.arguments)),
            "result": redact_value(dict(result)),
            "workspace": str(self.workspace_root),
            **(context or {}),
            "taints": sorted(state.taints) if state is not None else [],
        }
        for hook in hooks:
            run = await self.run(hook, payload)
            if run.error is not None:
                records.append({"hook": hook.name, "error": run.error})
                continue
            record = _post_tool_answer(run)
            if record:
                records.append({"hook": hook.name, "exit_code": run.code, **record})
        return records

    def review_changes(self, paths: list[str]) -> list[dict[str, Any]]:
        """The ``filesystem.write`` rules that fire for files a delegated agent changed.

        A child coding CLI runs its own tools, out of the hooks' reach, so its result
        is checked against the write rules as if Odessy had written each file.
        """

        if self.tampered is not None:
            return [{"path": None, "rule": "integrity", "decision": "deny", "reason": self.tampered}]
        findings = []
        for path in paths:
            request = ToolRequest("filesystem.write", {"path": path})
            for rule, action in evaluate_rules(self.rules, request, "medium", self.workspace_root, None):
                findings.append({"path": path, "rule": rule.name, "decision": action, "reason": rule.describe()})
        return findings

    async def turn_end(self, payload: Mapping[str, Any]) -> list[dict[str, Any]]:
        """Run every turn-end hook; returns the failures, for the log."""

        failures = []
        for hook in self.for_event("turn_end"):
            run = await self.run(hook, {"event": "turn_end", **payload, "workspace": str(self.workspace_root)})
            if run.error is not None:
                failures.append({"hook": hook.name, "error": run.error})
        return failures

    async def run(self, hook: Hook, payload: Mapping[str, Any]) -> HookRun:
        tampered = self.verify().tampered
        if tampered is not None:
            return HookRun(error=tampered, tampered=True)
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

    if run.tampered:
        return "deny", run.error or "the hooks changed after Odessy read them"
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
    return _post_tool_answer(run).get("note", "")


def _post_tool_answer(run: HookRun) -> dict[str, Any]:
    """What a post-tool hook asked for. Plain output is a note.

    JSON may carry ``note``, ``taint`` (a name or a list), ``"decision": "stop"``,
    ``quarantine`` (true) and ``reason``. Unknown fields are ignored; a malformed
    taint name is dropped rather than trusted.
    """

    text = run.stdout.strip()
    try:
        answer = json.loads(text) if text else None
    except json.JSONDecodeError:
        answer = None
    record: dict[str, Any] = {}
    if isinstance(answer, dict) and answer.keys() & {"note", "taint", "decision", "quarantine"}:
        text = str(answer.get("note") or "")
        taints = answer.get("taint") or []
        taints = [taints] if isinstance(taints, str) else taints if isinstance(taints, list) else []
        taints = [taint for taint in taints if isinstance(taint, str) and _NAME.match(taint)]
        if taints:
            record["taint"] = taints
        if answer.get("decision") == "stop":
            record["stop"] = True
        if answer.get("quarantine") is True:
            record["quarantine"] = True
        if record:
            record["reason"] = _short(str(answer.get("reason") or ""), MAX_REASON_CHARS)
    if not text and run.code:
        text = run.stderr
    note = _short(text, MAX_NOTE_CHARS)
    if note:
        record["note"] = note
    return record


def default_hooks_file() -> Path:
    return Path.home() / ".config" / "odessy" / "hooks.toml"


def hooks_file(environ: Mapping[str, str] | None = None) -> Path:
    source = os.environ if environ is None else environ
    explicit = source.get(HOOKS_FILE_ENV, "").strip()
    return Path(explicit).expanduser() if explicit else default_hooks_file()


def _same_path(a: Path, b: Path) -> bool:
    return os.path.normcase(str(a.resolve())) == os.path.normcase(str(b.resolve()))


def protected_paths(environ: Mapping[str, str] | None = None, hooks: HookSet | None = None) -> tuple[Path, ...]:
    """Paths the agent's commands must not touch: the hooks file, its folder, and
    the scripts its hooks name.

    The file is protected whether or not it exists. A hook runs as the user, outside
    the sandbox, so an agent that could create one could run anything it liked.
    """

    target = hooks_file(environ)
    paths = [target, target.parent]
    if hooks is not None:
        paths.extend(path for path in hooks.protected_paths() if path not in paths)
    return tuple(paths)


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in ("1", "true", "yes", "on")


def _script_paths(hooks: tuple[Hook, ...], workspace_root: Path, where: Path) -> tuple[tuple[Path, ...], tuple[Path, ...]]:
    """The existing files a hook's command names, refusing any inside the workspace.

    A hook runs with the workspace as its working directory, so a relative name is
    resolved there. A script in the workspace could be rewritten by the repository
    or the agent, which would turn the user's hook into theirs.

    Returns every script, and those named by an absolute path: only those are part
    of the fingerprint, since a relative name means a different file per workspace.
    """

    root = workspace_root.resolve()
    scripts: list[Path] = []
    absolute: list[Path] = []
    for hook in hooks:
        for index, part in enumerate(hook.command):
            candidate = Path(part).expanduser()
            named_absolute = candidate.is_absolute()
            if not named_absolute:
                candidate = workspace_root / candidate
            try:
                if not candidate.is_file():
                    continue
                resolved = candidate.resolve()
            except OSError:
                continue
            if resolved == root or root in resolved.parents:
                raise HookConfigError(
                    f"{where}: the {hook.name} hook runs {part!r}, which is inside the workspace; "
                    "keep hook scripts outside it, where the repository cannot change them"
                )
            if resolved not in scripts and not (index == 0 and _is_interpreter(resolved)):
                scripts.append(resolved)
            if named_absolute and resolved not in absolute:
                absolute.append(resolved)
    return tuple(scripts), tuple(absolute)


def _is_interpreter(program: Path) -> bool:
    """A general program (python, node, a shell): fingerprinted, but not protected,
    since protecting it would stop the agent running it for its own work."""

    if os.path.normcase(str(program)) == os.path.normcase(str(Path(sys.executable).resolve())):
        return True
    return shutil.which(program.name) is not None


def _fingerprint(target: Path, scripts: tuple[Path, ...]) -> str:
    digest = hashlib.sha256()
    for path in (target, *scripts):
        digest.update(os.path.normcase(str(path)).encode("utf-8") + b"\0")
        try:
            stat = path.stat()
            if stat.st_size <= _HASHED_BYTES:
                digest.update(path.read_bytes())
            else:
                digest.update(f"{stat.st_size}:{stat.st_mtime_ns}".encode())
        except OSError:
            digest.update(b"missing")
    return digest.hexdigest()


def reset_baselines() -> None:
    """Forget the fingerprints (tests; a restart does the same)."""

    _BASELINES.clear()


def load_hooks(
    workspace_root: Path,
    path: Path | None = None,
    environ: Mapping[str, str] | None = None,
    *,
    track: bool = True,
) -> HookSet | None:
    """The hooks and rules for ``workspace_root``, or None when there are none.

    With ``track`` (the agent; not the doctor) the file is compared with the
    fingerprint this process took the first time it read it. A file that changed
    or disappeared since gives a tampered set, which denies every call.
    """

    source = os.environ if environ is None else environ
    target = path or hooks_file(environ)
    key = os.path.normcase(str(target.expanduser().resolve()))
    required = _truthy(source.get(HOOKS_REQUIRED_ENV))
    if not target.is_file():
        if track and key in _BASELINES:
            return _tampered(workspace_root, target, source, "the hooks file was removed after Odessy read it")
        if required:
            raise HookConfigError(f"{target}: {HOOKS_REQUIRED_ENV} is set, but there is no hooks file")
        return None
    data = _read(target)
    hooks = tuple(hook for hook, where in parse_hooks(target, data) if where is None or _same_path(where, workspace_root))
    try:
        rules = tuple(rule for rule, where in parse_rules(target, data) if where is None or _same_path(where, workspace_root))
    except RuleConfigError as error:
        raise HookConfigError(str(error)) from error
    scripts, fingerprinted = _script_paths(hooks, workspace_root, target)
    if track:
        fingerprint = _fingerprint(target, fingerprinted)
        baseline = _BASELINES.setdefault(key, fingerprint)
        if baseline != fingerprint:
            return _tampered(workspace_root, target, source, "the hooks file or a hook script changed after Odessy read it")
    if not hooks and not rules:
        if required:
            raise HookConfigError(f"{target}: {HOOKS_REQUIRED_ENV} is set, but no hook or rule applies to {workspace_root}")
        return None
    # Unlike the process baseline, this set belongs to one workspace, so it can also
    # cover scripts named relative to it.
    watched = tuple(dict.fromkeys((*fingerprinted, *scripts)))
    return HookSet(hooks, workspace_root, target, source, rules=rules, scripts=scripts,
                   fingerprint=_fingerprint(target, watched), fingerprinted=watched)


def _tampered(workspace_root: Path, target: Path, environ: Mapping[str, str], why: str) -> HookSet:
    return HookSet((), workspace_root, target, environ, tampered=f"{why}; restart Odessy to load it again")


def _read(target: Path) -> dict[str, Any]:
    try:
        return tomllib.loads(target.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise HookConfigError(f"{target}: cannot read hooks file: {error}") from error


def parse_hooks(target: Path, data: dict[str, Any] | None = None) -> list[tuple[Hook, Path | None]]:
    """Every hook in the file with the workspace it is limited to, validated."""

    if data is None:
        data = _read(target)
    unknown = sorted(set(data) - set(_TOP_LEVEL))
    if unknown:
        raise HookConfigError(
            f"{target}: unknown keys {unknown}; use [[pre_tool]], [[pre_escalation]], [[post_tool]], "
            "[[turn_end]], [[rule]] or presets"
        )
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
    if event in ("pre_tool", "pre_escalation"):
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
    match = entry.get("match", ["terminal.run", "laptop.terminal.run"] if event == "pre_escalation" else ["*"])
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
        finished = {"phase": "finished", "name": detail.get("name"), "status": status, **ids}
        sandbox = result.get("sandbox") if isinstance(result, dict) else None
        if isinstance(sandbox, dict):
            # Which backend ran the command and how completely: a client shows an
            # unconfined run differently from a confined one.
            finished["sandbox"] = {key: sandbox[key] for key in ("provider_id", "enforcement", "network") if key in sandbox}
        return "tool", finished
    outcomes = {
        "policy_allowed": "allowed",
        "policy_confirmed": "confirmed",
        "policy_preapproved": "preapproved",
        "policy_denied": "denied",
    }
    if event in outcomes:
        return "policy", {"name": detail.get("name"), "outcome": outcomes[event], "risk": detail.get("risk"), **ids}
    if event == "hook_decision":
        return "hook", {"event": detail.get("event", "pre_tool"), "name": detail.get("name"), "hook": detail.get("hook"),
                        "decision": detail.get("decision"), **ids}
    if event in ("hook_note", "hook_failed"):
        return "hook", {"event": detail.get("event"), "name": detail.get("name"), "hook": detail.get("hook"),
                        "failed": event == "hook_failed", **ids}
    if event in ("hook_taint", "hook_stop", "hook_quarantine"):
        return "hook", {"event": "post_tool", "name": detail.get("name"), "hook": detail.get("hook"),
                        "action": event.removeprefix("hook_"), **ids}
    if event == "final_call":
        return "final_call", {"model_calls": detail.get("model_calls"), "limit": detail.get("limit")}
    return None


async def screen_request(hooks: HookSet | None, request: ToolRequest, decision: PolicyDecision) -> PolicyDecision:
    """Screen a call that does not come from the agent loop (a desktop ``/jobs`` request).

    Same rules and pre-tool hooks, same tightening; there is no turn, so no taints.
    """

    if hooks is None or decision.action == "deny":
        return decision
    verdict = await hooks.pre_tool(request, decision, {"execution": {"target": call_target(request.name), "origin": "api"}})
    return tighten(decision, verdict)


__all__ = [
    "EVENTS",
    "HOOKS_FILE_ENV",
    "HOOKS_REQUIRED_ENV",
    "TurnState",
    "Hook",
    "HookConfigError",
    "HookRun",
    "HookSet",
    "HookVerdict",
    "hook_environment",
    "hooks_file",
    "load_hooks",
    "parse_hooks",
    "protected_paths",
    "public_event",
    "reset_baselines",
    "run_hook",
    "screen_request",
    "tighten",
]
