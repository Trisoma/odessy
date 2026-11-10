"""The Harness engine for one Odessy session.

Scope of this prompt (03): the process lifecycle and the narrow profile — boot, open one
ACP session, answer a settled (non-streaming) turn, report honest capabilities, shut down.
Prompt 04 adds the **model route**: the session's selected Odessy profile is expressed as
one declared ``llm-pi-ai`` route (:mod:`…harness.routes`), the runtime is launched with it,
and a model change is applied through ``session/set_config_option`` between turns.

Deliberately *not* here: live text deltas (05), per-session cancel and resume (06),
approvals (08), MCP and tools (07-09). Nothing in this module falls back to the legacy
engine or to another provider; a failure surfaces as a typed error.
"""

from __future__ import annotations

from odessy.authority.tool_control import ToolControl

import asyncio
import re
import secrets
import threading
import time
import uuid
from dataclasses import replace
from pathlib import Path
from typing import Any

from odessy.runtime_backends.contracts import (
    HARNESS_CAPABILITIES,
    ModelRouteError,
    ModelRouteLocked,
    RuntimeCapabilityUnavailable,
    RuntimeEvent,
    RuntimeUnavailable,
    StopOutcome,
    TurnCancelled,
)
from odessy.runtime_backends.harness import pin
from odessy.runtime_backends.harness.approvals import (
    ApprovalBindError,
    CANCELLED_RESPONSE,
    HarnessApprovalBridge,
    PERMISSION_METHOD,
    unsupported_response,
)
from odessy.runtime_backends.harness.config import (
    TOOL_ROSTER_REQUIRED,
    HarnessConfig,
    from_environment,
)
from odessy.runtime_backends.harness.process import (
    HarnessProcess,
    HarnessProcessError,
    HarnessProtocolError,
    HarnessTransportError,
)
from odessy.runtime_backends.harness.bridge import (
    BridgeError,
    FEATURE_TOOLS,
    LiveBridge,
    free_port,
)
from odessy.runtime_backends.harness.questions import HarnessQuestionBridge
from odessy.runtime_backends.harness.routes import HarnessRoute, resolve_route
from odessy.runtime_backends.harness.stream import Delta, Settlement, TurnStream
from odessy.runtime.types import AgentTurnResult


ENGINE = "harness"
_CLIENT_INFO = {"name": "odessy", "version": "0.5.0"}
#: What a runtime session id may look like. The pin issues UUIDs; the shape is checked only so
#: that a corrupt record cannot smuggle a path, a newline or an empty string into a request.
_SESSION_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
#: The ACP ``stopReason`` that means the runtime itself cancelled the turn.
CANCEL_STOP_REASON = "cancelled"
#: The one model-facing tool this engine may execute: the runtime's own clarification tool.
#: It asks a human and returns the answer as a tool result; it has no filesystem, process or
#: network effect. Every other tool call still fails the turn closed.
CLARIFICATION_TOOL = "ask_user_question"
#: What this engine can do once the packaged bridge is attached: text arrives before the
#: answer is committed, and (when the plugin also announces the question feature) a
#: clarification round trip is available. Without the bridge the record stays as in 03.
_LIVE_CAPABILITIES = replace(HARNESS_CAPABILITIES, live_text_deltas=True)
_QUESTION_CAPABILITIES = replace(_LIVE_CAPABILITIES, questions=True)


def _sibling_models(session: Any, selected: Any) -> tuple[str, ...]:
    """Models of the session's other profiles that share the selected profile's provider.

    Declaring them up front is what lets ``select_route`` change the model without a
    relaunch: the runtime can only select a pair it was told about.
    """
    provider = (selected or {}).get("provider") if isinstance(selected, dict) else None
    if not provider:
        return ()
    profiles = getattr(session, "model_profiles", None)
    profiles = profiles() if callable(profiles) else profiles
    models = []
    for profile in profiles or []:
        if not isinstance(profile, dict) or profile.get("provider") != provider:
            continue
        if profile.get("adapter", "cloud") != "cloud":
            continue
        model = profile.get("model")
        if isinstance(model, str) and model.strip():
            models.append(model.strip())
    return tuple(models)


def _resume_hint(session: Any) -> dict[str, Any] | None:
    """The durable session this engine was asked to continue, if any.

    Read from the session object rather than passed to the backend builder, because the
    selection layer builds every engine from the session alone (``selection.resolve_and_build``).
    A hint that is not a mapping is not silently ignored: it is a caller bug that would
    otherwise look like "no restore requested" and start a fresh session.
    """
    hint = getattr(session, "runtime_resume", None)
    if hint is None:
        return None
    if not isinstance(hint, dict):
        raise RuntimeUnavailable(ENGINE, "a restore hint must be a mapping")
    return dict(hint)


def attach_route(config: HarnessConfig, route: HarnessRoute, *,
                 extra_env: dict[str, str] | None = None) -> HarnessConfig:
    """Return the config that launches this session with ``route``.

    The route overlay is materialised in the owned Harness home, and the child receives
    exactly one credential variable — the resolved value for this route — plus whatever the
    caller names in ``extra_env`` (the bridge port and token). Nothing else changes.
    """
    from odessy.runtime_backends.harness.routes import materialize_route_patch

    path = materialize_route_patch(config.home, route)
    merged = {**dict(config.extra_env), **route.environment(), **(extra_env or {})}
    return replace(config, route_patch=path, extra_env=merged)


class HarnessRuntime:
    """Owns one supervised Harness child per Odessy session."""

    engine = ENGINE

    @property
    def capabilities(self) -> Any:
        """What this *session* can do: live deltas only once the bridge is attached, and
        clarification only when that plugin also announced the question feature."""
        if not self._live:
            return HARNESS_CAPABILITIES
        return _QUESTION_CAPABILITIES if self._questions_ready else _LIVE_CAPABILITIES

    def __init__(self, session: Any, config: HarnessConfig | None = None,
                 route: HarnessRoute | None = None):
        # Resolving the route here, not at first turn, is what makes both the engine and
        # the model a decision taken when the session is created: a misconfigured engine or
        # an unroutable profile fails session creation instead of the first prompt.
        # Engine configuration first: "this build has no runtime" is a more fundamental
        # failure than "this profile has no route", and callers rely on that ordering.
        base_config = config if config is not None else from_environment()
        profile = getattr(session, "model_profile", None)
        selected = profile() if callable(profile) else profile
        self._route = route if route is not None else resolve_route(
            selected,
            # The route resolves in the same environment the engine was configured from, so
            # an explicit mapping (tests, integration smoke) also governs credentials.
            environ=dict(base_config.source_env) or None,
            extra_models=_sibling_models(session, selected),
        )
        # Live text needs a port and a token the packaged plugin reads from its
        # environment. Both are generated per session; the token never leaves memory or
        # the child's environment.
        self._bridge_enabled = (dict(base_config.source_env)
                                .get("ODESSY_HARNESS_BRIDGE", "on")
                                .strip().lower() not in {"0", "off", "false", "no"})
        self._bridge_port = free_port() if self._bridge_enabled else 0
        self._bridge_token = secrets.token_hex(16) if self._bridge_enabled else ""
        self._bridge: LiveBridge | None = None
        self._live = False
        #: What was observed about the session's tool surface (prompt 10): whether the roster
        #: was read, what the record expected, and what was missing. Never a claim that the
        #: servers were asked for — that is not the same thing as their tools existing.
        self._tool_surface: dict[str, Any] = {
            "state": "unchecked", "expected": None, "observed": None,
            "missing": [], "reason": "the session has not started"}
        #: The tool server's approval/cancel channel (prompt 09). Created at start() once the
        #: session loop exists; None until then, and for a session with no tool server.
        self._stream: TurnStream | None = None
        self._last_settlement: Settlement | None = None
        #: Highest bridge turn number already settled. A frame at or below it is a
        #: straggler from a finished turn, even if it arrives mid-turn (draining the queue
        #: at start cannot see a frame that has not arrived yet).
        self._turn_floor: int | None = None
        self._pump_thread: threading.Thread | None = None
        self._pump_stop = threading.Event()
        self._config = attach_route(base_config, self._route,
                                    extra_env=self._bridge_environment())
        # The environment path validates inside ``from_environment``; an injected config
        # (integration smoke, tests) must clear the same bar rather than bypass it.
        self._config.validate()
        #: The session's confinement (prompt 08). ``validate`` already refused a policy this
        #: host cannot honour, so this plan is launchable; it is kept here because the
        #: identity record, the approval bridge and the diagnostics all quote *this* plan
        #: rather than re-resolving one that could disagree.
        self._sandbox = self._config.sandbox_plan()
        self._session = session
        self.session_id = str(getattr(session, "id", "") or "")
        #: The durable session this engine was asked to continue (prompt 10), or ``None`` for a
        #: fresh one. Handed in by the controller before the engine is built, so a restore that
        #: cannot be honoured fails session creation instead of detaching the history.
        self._resume = _resume_hint(session)
        #: Which ACP method actually opened the session, for the identity record and notes.
        self._opened_with = "session/new"
        self._audit = getattr(session, "audit", None)
        #: One bridge per session: it owns the turn's tool-call identity, binds a
        #: permission request to the session's own approval broker, and answers the ACP
        #: request exactly once. It never decides anything itself.
        self._approvals = HarnessApprovalBridge(
            workspace=self._config.workspace,
            identity=self._approval_identity,
            approver=self._ask_human,
            epoch=lambda: int(getattr(session, "stop_epoch", 0) or 0),
            is_closed=lambda: self._closed,
            # A widening request is refused with this reason; the pinned runtime fixes the
            # sandbox mode per process, so an approved escalation could not be honoured.
            escalation=self._sandbox.escalation,
            sandbox_mode=self._sandbox.mode,
        )
        #: Approval tasks awaiting a person, so close() can stop waiting on them.
        self._approval_tasks: set[asyncio.Task[Any]] = set()
        #: Clarification round trips over the same bridge socket. The human wait runs on
        #: the session loop and answers the exact tool invocation that is waiting.
        self._questions = HarnessQuestionBridge(
            send=self._send_to_bridge,
            asker=self._ask_human_question,
            loop=lambda: self._loop,
            epoch=lambda: int(getattr(session, "stop_epoch", 0) or 0),
            is_closed=lambda: self._closed,
        )
        #: True only once the attached plugin announced the question feature.
        self._questions_ready = False
        #: Tool-call ids this turn allowed: exactly the clarification tool's own calls.
        self._allowed_calls: set[str] = set()
        #: The loop the session's turns run on; server requests are scheduled here.
        self._control = None
        self._loop: asyncio.AbstractEventLoop | None = None
        #: Turn number, starting at 1. A permission request is bound to the turn it
        #: arrived in, not to whichever turn happens to be running later.
        self._turn_number = 0
        self._process: HarnessProcess | None = None
        self._identity: dict[str, object] = {}
        #: The MCP servers this session attached, in launch order. The first is Odessy's own
        #: tool server; any others are upstream gateways (prompt 09a). Admission is checked
        #: against these names only, so nothing else can become reachable by title.
        self._server_names: tuple[str, ...] = ()
        self._acp_session: str | None = None
        self._ready = False
        self._closed = False
        self._busy = False
        #: A Stop request is in flight: new turns are refused for its duration.
        self._stopping = False
        # A timed-out await does not cancel a pipe writer. Keep admission blocked until
        # every actual cancellation write returns, even if its original turn ends first.
        self._cancel_write_lock = threading.Lock()
        self._pending_cancel_writes = 0
        #: Set by the turn's own thread when the prompt has finished, whatever the reason.
        self._turn_done: threading.Event | None = None
        #: Set when this turn's prompt has actually been written to the child. A cancel sent
        #: before that would reach a runtime with nothing to cancel, and be dropped.
        self._turn_prompt_sent: threading.Event | None = None
        #: A caller abandoned a turn that never ended; the session stays busy on purpose.
        self._turn_orphaned = False
        self._last_stop_reason: str | None = None
        #: Token context utilisation reported by ``usage_update`` (``used``/``size``).
        #: Not prompt/completion tokens: the ACP channel does not carry those, so Odessy's
        #: ledger keeps reporting an unknown price rather than an invented one.
        self._context_usage: dict[str, int] = {}
        self._model_option: str | None = None
        self._declared: list[str] = []
        #: Deltas are transient and must reach the session from a worker thread, so only
        #: the thread-safe emitter is used; without one this engine stays settlement-only.
        self._emit = getattr(session, "emit_from_worker", None)
        self._turn_lock = threading.Lock()
        #: One lock for *both* transitions. Startup and shutdown cannot interleave, so
        #: "close saw no process" and "startup published a process" cannot both be true.
        self._lifecycle = threading.Lock()
        #: Set when the caller's await is cancelled or the session is closing. A worker
        #: already inside ``_start_blocking`` checks it before publishing, and closes the
        #: child it built instead of adopting it.
        self._abandoned = False

    # ------------------------------------------------------------------ properties

    @property
    def config(self) -> HarnessConfig:
        return self._config

    @property
    def identity(self) -> dict[str, object]:
        """Version and artifact evidence captured at readiness."""
        return dict(self._identity)

    @property
    def acp_session_id(self) -> str | None:
        return self._acp_session

    @property
    def last_stop_reason(self) -> str | None:
        return self._last_stop_reason

    @property
    def route(self) -> HarnessRoute:
        """The model route this session was launched with (credential never exposed)."""
        return self._route

    @property
    def context_usage(self) -> dict[str, int]:
        """Last ``usage_update``: ``used``/``size`` tokens of context, if any."""
        return dict(self._context_usage)

    @property
    def stream_settlement(self) -> Settlement | None:
        """Counters and timings for the last turn (never the text)."""
        return self._last_settlement

    @property
    def tool_surface(self) -> dict[str, Any]:
        """What was established about the attached tool roster, and how."""
        return dict(self._tool_surface)

    @property
    def orphaned_turn(self) -> bool:
        """True when an abandoned turn never ended: the session is still busy."""
        return self._turn_orphaned

    @property
    def stopping(self) -> bool:
        """True while a Stop request has not finished."""
        with self._cancel_write_lock:
            return self._stopping or self._pending_cancel_writes > 0

    def diagnostics(self) -> list[str]:
        return [] if self._process is None else self._process.diagnostics()

    @property
    def approvals(self) -> HarnessApprovalBridge:
        """The session's approval bridge, for tests and diagnostics."""
        return self._approvals

    @property
    def sandbox(self) -> Any:
        """The confinement this session launched with, as resolved (prompt 08)."""
        return self._sandbox

    def _tool_server_name(self) -> str | None:
        """The first tool server this session attached, if any (prompt 09)."""

        return self._server_names[0] if self._server_names else None

    def _attached_server_names(self) -> tuple[str, ...]:
        """The servers this session attached; before ``start``, the ones it will attach.

        ``session/new`` attaches exactly what :meth:`HarnessConfig.mcp_servers` returns, so the
        planned list and the attached list are the same list. A plan that cannot be built
        admits nothing, which is the same answer a failed start gives.
        """

        if self._server_names:
            return self._server_names
        try:
            return tuple(str(entry["name"]) for entry in self._config.mcp_servers())
        except Exception:  # noqa: BLE001 - an unusable tool plan admits no tool
            return ()

    def _is_our_tool_call(self, title: Any) -> bool:
        """Whether a tool title names a tool of a server *this session* attached.

        The runtime prefixes an MCP tool as ``mcp__<server>__<tool>``. Only the prefixes of
        the servers this session attached are admitted: a built-in tool row (``bash``,
        ``pwsh``, ``fs`` …) has no such name and stays refused, and so does another
        session's server or a server that was refused activation — which is what keeps a
        disabled surface from becoming reachable through a name that merely looked close.
        """

        if not isinstance(title, str):
            return False
        for name in self._attached_server_names():
            prefix = f"mcp__{name}__"
            if title.startswith(prefix) and len(title) > len(prefix):
                return True
        return False

    def _approval_identity(self) -> dict[str, Any]:
        """Who is asking: the runtime identity captured at readiness, plus this session."""
        return {**self._identity, "odessy_session": self.session_id}

    def _bridge_environment(self) -> dict[str, str]:
        if not self._bridge_enabled:
            return {}
        return {"ODESSY_BRIDGE_PORT": str(self._bridge_port),
                "ODESSY_BRIDGE_TOKEN": self._bridge_token}

    # ------------------------------------------------------------------ lifecycle

    async def start(self) -> dict[str, object]:
        """Ready the runtime once. Safe to await repeatedly and from several tasks."""
        if self._closed:
            raise RuntimeUnavailable(ENGINE, "the session is closed")
        if self._ready:
            return dict(self._identity)
        # Captured before the worker starts: the child can raise a server request as
        # soon as it has a session, and the answer must be scheduled on this loop.
        self._loop = asyncio.get_running_loop()
        if self._config.tools_enabled and self._control is None:
            self._control = ToolControl(self._loop, self._ask_human)
        try:
            await asyncio.to_thread(self._start_blocking)
        except asyncio.CancelledError:
            # Cancelling this await does not stop the worker thread. Mark the start as
            # abandoned so the worker closes the child it is building instead of
            # publishing it; otherwise a cancelled start would leak a running runtime.
            self._abandoned = True
            if self._control is not None:
                self._control.cancel()
            raise
        except Exception:
            control, self._control = self._control, None
            if control is not None:
                await asyncio.to_thread(control.close)
            raise
        return dict(self._identity)

    def _start_blocking(self) -> None:
        # Thread context: no event loop, so every wait here is a plain blocking wait.
        with self._lifecycle:
            if self._ready:
                return
            if self._closed or self._abandoned:
                raise RuntimeUnavailable(
                    ENGINE, "the session was closed before the runtime became ready"
                )
            identity = pin.verify_version(
                self._config.command,
                self._config.expected_version,
                env=self._config.child_env(),
                expected_sha256=(pin.PINNED_EXECUTABLE_SHA256
                                 if self._config.verify_artifact else None),
                timeout=self._config.startup_timeout,
            )
            if self._resume is not None:
                # Before a child exists: a restore this build cannot honour costs a version
                # probe, not a spawned runtime that then has to be torn down.
                self._check_resume(identity)
            process = HarnessProcess(self._config,
                                     on_request=self._on_server_request,
                                     on_notification=self._approvals.observe)
            try:
                process.start()
                handshake = process.call("initialize", {
                    "protocolVersion": pin.ACP_PROTOCOL_VERSION,
                    "clientCapabilities": {
                        "fs": {"readTextFile": False, "writeTextFile": False},
                        "terminal": False,
                    },
                    "clientInfo": dict(_CLIENT_INFO),
                }, timeout=self._config.startup_timeout)
                negotiated = handshake.get("protocolVersion")
                if negotiated != pin.ACP_PROTOCOL_VERSION:
                    # The ACP contract is a negotiation, not an assertion: a runtime that
                    # answers a different (or no) version must not be adopted.
                    raise HarnessProtocolError(
                        "initialize reported protocolVersion="
                        f"{negotiated!r}; this build speaks ACP v{pin.ACP_PROTOCOL_VERSION}"
                    )
                servers = self._config.mcp_servers(
                    self._control.address if self._control else None)
                self._server_names = tuple(str(entry["name"]) for entry in servers)
                opened = self._open_session(process, servers)
                session_id = opened.get("sessionId")
                if not isinstance(session_id, str) or not session_id:
                    raise HarnessProtocolError(
                        f"{self._opened_with} returned no sessionId")
                self._read_model_options(opened.get("configOptions") or [])
                wanted = self._route.option_value()
                if self._declared and wanted not in self._declared:
                    raise HarnessProtocolError(
                        f"the runtime does not offer {self._route.route}"
                        f"/{self._route.model}; it offers: {', '.join(self._declared)}"
                    )
                if self._model_option != wanted:
                    updated = process.call("session/set_config_option", {
                        "sessionId": session_id, "configId": "model", "value": wanted,
                    }, timeout=self._config.startup_timeout)
                    self._read_model_options(updated.get("configOptions") or [])
            except HarnessProtocolError as exc:
                self._discard(process)
                raise RuntimeUnavailable(ENGINE, "the runtime did not complete the handshake",
                                         detail=str(exc)) from exc
            except HarnessTransportError as exc:
                self._discard(process)
                raise RuntimeUnavailable(
                    ENGINE, "the runtime exited during startup",
                    detail=self._detail(exc),
                ) from exc
            except HarnessProcessError as exc:
                self._discard(process)
                raise RuntimeUnavailable(ENGINE, "the runtime failed to start",
                                         detail=self._detail(exc)) from exc
            except RuntimeUnavailable:
                # A refused restore already carries the runtime's own words; the child that
                # was opened to attempt it must still be torn down.
                self._discard(process)
                raise
            except OSError as exc:
                self._discard(process)
                raise RuntimeUnavailable(ENGINE, "the runtime executable could not be launched",
                                         detail=str(exc)) from exc
            if self._abandoned or self._closed:
                # Publishing happens under the same lock as close(), so a close that ran
                # during this startup either waits here or has already set the flag.
                self._discard(process)
                raise RuntimeUnavailable(
                    ENGINE, "startup was abandoned before it completed"
                )
            try:
                self._connect_bridge(process)
                # Before the session is accepted: the runtime may have silently dropped a
                # server whose command no longer starts, and re-sending `mcpServers` proves
                # nothing. A refusal here is still inside startup, so the child it opened is
                # torn down instead of being left running behind a rejected restore.
                self._verify_tool_surface(process)
            except BaseException:
                self._discard(process)
                raise
            self._process = process
            self._acp_session = session_id
            self._identity = {**dict(identity), "sandbox": self._sandbox.describe(),
                              "tools": self._tool_server_name() or "none",
                              "servers": list(self._server_names),
                              "upstreams": self._config.upstream_activation(),
                              # The negotiated protocol and which ACP method opened the session;
                              # a restore compares the first and records the second.
                              "protocol": negotiated,
                              "resumed": self._resume is not None,
                              "opened_with": self._opened_with,
                              "tool_roster": self._tool_surface.get("tools"),
                              "tool_surface": self._tool_surface.get("state")}
            self._ready = True
            process.note(
                f"ready: version={identity.get('version')} "
                f"artifact={identity.get('artifact')} protocolVersion={negotiated} "
                f"route={self._route.route} model={self._route.model} "
                f"api={self._route.api} acp_session={session_id} "
                f"sandbox={self._sandbox.mode}/{self._sandbox.provider} "
                f"unenforced={list(self._sandbox.unenforced)}"
            )

    def _open_session(self, process: HarnessProcess,
                      servers: list[dict[str, Any]]) -> dict[str, Any]:
        """Create a new ACP session, or restore the durable one this engine was given.

        A restore is never silently downgraded to a new session. Every way the runtime can
        refuse one — history missing, history of a format this pin cannot read, a workspace that
        does not match, another process already holding it — is raised as
        :class:`RuntimeUnavailable` with what was actually observed, because a fresh session
        under the same application identity is indistinguishable from a lost conversation.

        The reviewed tool surface is re-passed: ``session/resume`` does not restore the servers
        the session was created with (measured, not assumed), and a server whose command no
        longer exists is dropped by the runtime without an error, so the same plan is attached
        again and the record keeps saying what was intended.
        """

        if self._resume is None:
            self._opened_with = "session/new"
            return process.call("session/new", {
                "cwd": str(self._config.workspace),
                # Odessy's reviewed tool server, spawned and supervised by the runtime as
                # this session's stdio MCP child (prompt 09), plus one guarded gateway per
                # upstream (prompt 09a). Empty unless the operator opted in, so a package
                # that has not qualified a tool surface still has none. Every entry's
                # confinement is fixed by argv from the resolved plan.
                "mcpServers": servers,
            }, timeout=self._config.session_timeout())
        # Validation happened in ``_check_resume`` before this child existed.
        self._opened_with = "session/resume"
        session = str(self._resume.get("runtime_session") or "")
        try:
            result = process.call("session/resume", {
                "sessionId": session,
                "cwd": str(self._config.workspace),
                "mcpServers": servers,
            }, timeout=self._config.session_timeout())
        except HarnessProtocolError as exc:
            raise self._resume_refusal(exc, session) from exc
        opened = dict(result or {})
        opened.setdefault("sessionId", session)
        process.note(f"restored durable session {session} "
                     f"({len(self._server_names)} tool server(s) re-attached)")
        return opened

    #: Identity fields that decide whether a persisted log can be continued. The record must
    #: carry every one of them, and each must equal what *this* launch observed: a field that
    #: cannot be checked is a refusal, because "probably the same runtime" is how a log written
    #: by another build gets read as if it were this one's.
    _COMPATIBILITY_FIELDS = ("version", "source_tag", "sha256", "protocol")

    def _check_resume(self, identity: dict[str, object]) -> None:
        """Refuse a restore this build cannot honour, before a child is even asked.

        Runs after the version probe and *before* a child process exists, so a refusal costs a
        probe rather than a spawned runtime that has to be torn down.
        """

        hint = self._resume or {}
        session = hint.get("runtime_session")
        if not isinstance(session, str) or not session.strip():
            raise RuntimeUnavailable(ENGINE, "a restore was requested without a runtime session")
        if len(session) > 128 or not _SESSION_ID_PATTERN.match(session):
            raise RuntimeUnavailable(
                ENGINE, "the durable record names a malformed runtime session",
                detail=f"session id {session!r} is not a runtime session id")
        workspace = hint.get("workspace")
        if not isinstance(workspace, str) or not workspace.strip():
            raise RuntimeUnavailable(
                ENGINE, "the durable record does not say which workspace it belongs to",
                detail="a restore needs the recorded workspace to compare with this launch")
        try:
            recorded_workspace = Path(workspace).resolve()
        except (OSError, ValueError) as exc:
            raise RuntimeUnavailable(
                ENGINE, "the durable record's workspace is malformed",
                detail=f"{workspace!r}: {exc}") from exc
        if not Path(workspace).is_absolute():
            raise RuntimeUnavailable(
                ENGINE, "the durable record's workspace is not an absolute path",
                detail=workspace)
        if recorded_workspace != Path(self._config.workspace).resolve():
            raise RuntimeUnavailable(
                ENGINE, "the durable session belongs to a different workspace",
                detail=f"recorded {workspace}, this launch is {self._config.workspace}")

        recorded = hint.get("identity")
        if not isinstance(recorded, dict) or not recorded:
            raise RuntimeUnavailable(
                ENGINE, "the durable record carries no runtime identity",
                detail="a record without identity evidence cannot be established compatible")
        for field_name in self._COMPATIBILITY_FIELDS:
            value = recorded.get(field_name)
            if value is None or (isinstance(value, str) and not value.strip()):
                raise RuntimeUnavailable(
                    ENGINE, "the durable record's runtime identity is incomplete",
                    detail=f"the record does not carry {field_name!r}")
            if not isinstance(value, (str, int)) or isinstance(value, bool):
                raise RuntimeUnavailable(
                    ENGINE, "the durable record's runtime identity is malformed",
                    detail=f"{field_name}={value!r} is not a string or integer")

        # Compare against what this launch actually observed, not against a constant: the
        # probe ran, so a value it did not produce (an unverified digest, for instance) is a
        # field the record cannot be checked on.
        for field_name in ("version", "source_tag", "sha256"):
            expected = identity.get(field_name)
            if expected is None or expected == "":
                raise RuntimeUnavailable(
                    ENGINE, "this launch cannot establish the runtime identity a restore needs",
                    detail=f"the version probe produced no {field_name!r}")
            if str(recorded[field_name]) != str(expected):
                raise RuntimeUnavailable(
                    ENGINE, "the durable session was written by a different runtime",
                    detail=f"recorded {field_name}={recorded[field_name]}, "
                           f"this launch reports {expected}")
        written = int(recorded["protocol"]) if str(recorded["protocol"]).isdigit() else None
        if written is None:
            raise RuntimeUnavailable(
                ENGINE, "the durable record's ACP protocol is malformed",
                detail=f"protocol={recorded['protocol']!r}")
        if written != int(pin.ACP_PROTOCOL_VERSION):
            raise RuntimeUnavailable(
                ENGINE, "the durable session was written under another ACP protocol",
                detail=f"recorded {written}, this build speaks {pin.ACP_PROTOCOL_VERSION}")

    def _resume_refusal(self, exc: HarnessProtocolError, session: str) -> RuntimeUnavailable:
        """Translate the runtime's refusal into what it means, keeping its own words."""

        detail = exc.remote_detail or str(exc)
        lowered = detail.lower()
        if exc.rpc_code == -32602 and "not resumable" in lowered:
            reason = "the runtime has no history for this session"
        elif exc.rpc_code == -32602 and "cwd" in lowered:
            reason = "the runtime holds this session for a different workspace"
        elif exc.rpc_code == -32603:
            # Opaque by design at this pin: a corrupt log, a log written by a newer runtime and
            # a session another process already holds all report this way.
            reason = ("the runtime could not open the persisted history (unreadable, from "
                      "another version, or already open elsewhere)")
        else:
            reason = "the runtime refused to restore this session"
        return RuntimeUnavailable(ENGINE, reason,
                                  detail=f"session/resume({session}) said: {detail}")

    def _verify_tool_surface(self, process: HarnessProcess) -> None:
        """Check the tools the runtime actually attached, and refuse an observed gap.

        Re-passing ``mcpServers`` only says what was *asked for*. The pin drops a server whose
        command no longer starts without reporting an error, so a restored session can look
        healthy while the tools it had are gone. The roster comes from the runtime's own
        read-only ``tools.schemas`` view through the packaged plugin — names only, no tool
        definition, no execution, and never the private session log.

        Policy (``ODESSY_HARNESS_TOOL_ROSTER``):

        * Restores always require a verified roster, regardless of this setting.
        * ``preferred`` permits an unverifiable roster only for new sessions.
        * ``required`` also refuses unverifiable new sessions.

        A refusal raises :class:`RuntimeUnavailable` from inside startup, so the child this
        restore opened is discarded and no replacement session is created.
        """

        expected: list[str] | None = None
        restoring = self._resume is not None
        if restoring:
            recorded = self._resume.get("tools")
            if isinstance(recorded, list) and recorded:
                expected = [str(name) for name in recorded if str(name)]
        reply: dict[str, Any] | None = None
        reason = ""
        if self._bridge is None:
            reason = ("the packaged bridge plugin is not attached, so the tool roster "
                      "cannot be read")
        elif FEATURE_TOOLS not in self._bridge.features:
            reason = "the installed bridge plugin does not report a tool roster"
        else:
            reply, reason = self._read_roster(process, expected)
        observed = reply["tools"] if reply and reply.get("available") else None
        if observed is None and not reason:
            reason = "the runtime reported no tool roster"
        missing = [name for name in (expected or []) if name not in (observed or [])]
        counts = {"agents": int(reply.get("agents") or 0) if reply else 0,
                  "global": int(reply.get("global") or 0) if reply else 0}
        if observed is None:
            state = {"state": "unverified", "expected": expected, "tools": None,
                     "missing": [], "reason": reason, **counts}
        elif missing:
            state = {"state": "incomplete", "expected": expected, "tools": list(observed),
                     "missing": missing, "reason": "the runtime attached fewer tools than this "
                                                   "session had", **counts}
        elif restoring and expected is None:
            # The runtime can be read, but the record has nothing to compare it with: the
            # session's own surface was never recorded, so "verified" would be a guess.
            state = {"state": "unverified", "expected": None, "tools": list(observed),
                     "missing": [],
                     "reason": "the record carries no tool roster to verify against", **counts}
        elif restoring:
            state = {"state": "verified", "expected": expected, "tools": list(observed),
                     "missing": [], "reason": "", **counts}
        else:
            # A new session records its surface so a later restore has something to check.
            state = {"state": "recorded", "expected": None, "tools": list(observed),
                     "missing": [], "reason": "", **counts}
        state["observed"] = None if observed is None else len(observed)
        self._tool_surface = state
        process.note(
            f"tool surface: {state['state']}"
            f" expected={'-' if expected is None else len(expected)}"
            f" observed={'-' if observed is None else len(observed)}"
            f" agents={state['agents']} global={state['global']}"
            + (f" missing={state['missing'][:5]}" if state["missing"] else "")
            + (f" reason={state['reason']}" if state["reason"] else ""))
        if state["state"] == "incomplete":
            raise RuntimeUnavailable(
                ENGINE, "the restored session's tool surface is incomplete",
                detail=f"{len(state['missing'])} tool(s) this session had are no longer "
                       f"attached: {', '.join(state['missing'][:8])}")
        if state["state"] == "unverified" and (
                restoring or self._config.tool_roster == TOOL_ROSTER_REQUIRED):
            raise RuntimeUnavailable(
                ENGINE, "the session's tool surface could not be verified",
                detail=reason or "no roster was reported")
    def _read_roster(self, process: HarnessProcess,
                     expected: list[str] | None) -> tuple[dict[str, Any] | None, str]:
        """Ask the plugin for the roster, re-reading once while slow servers finish starting.

        A second look only happens when the first answer was *available* but short: an
        unavailable registry is not a timing problem, and a gap that survives a second look is
        real.
        """

        timeout = min(10.0, max(2.0, self._config.startup_timeout))
        first: dict[str, Any] | None = None
        for attempt in range(2):
            try:
                reply = self._bridge.request_tools(timeout=timeout) if self._bridge else None
            except BridgeError as exc:
                return first, str(exc)
            if reply is None:
                return None, "the bridge is not attached"
            if not reply["available"]:
                return None, str(reply.get("reason") or "the roster is unavailable")
            first = reply
            if not expected or not [name for name in expected if name not in reply["tools"]]:
                return reply, ""
            if attempt == 0:
                process.note("tool surface: short roster, re-reading once while the "
                             "servers finish starting")
                time.sleep(1.0)
        return first, ""

    def _connect_bridge(self, process: HarnessProcess) -> None:
        """Attach to the packaged live-text plugin, or record why not.

        Absence is normal (the plugin is packaged by prompt 14 and may not be installed in
        a developer profile), so it degrades the capability instead of failing the engine.
        """
        if not self._bridge_enabled:
            process.note("live text: bridge disabled by ODESSY_HARNESS_BRIDGE")
            return
        try:
            self._bridge = LiveBridge(self._bridge_port, self._bridge_token,
                                      connect_timeout=min(5.0, self._config.startup_timeout))
            self._live = True
            # Truthful capability: only a plugin that announced the question feature can
            # answer `ask_user_question`, so only then is `questions` advertised.
            self._questions_ready = "questions" in self._bridge.features
            process.note("live text: bridge attached "
                         f"(protocol {self._bridge.hello.get('protocol')}, "
                         f"features {list(self._bridge.features) or 'none'}, "
                         f"port {self._bridge_port})")
        except BridgeError as exc:
            self._live = False
            self._questions_ready = False
            self._bridge = None
            missing = self._config.missing_for_network()
            # A child that cannot reach its own socket stack looks identical to a plugin
            # that was never installed, so the environment gap is named when it exists.
            process.note(f"live text unavailable: {exc}" + (
                f" (the child environment is missing {', '.join(missing)};"
                " the plugin cannot initialise its socket stack)" if missing else ""))

    def _read_model_options(self, options: list[dict[str, Any]]) -> None:
        """Record what this runtime actually offers and which pair is selected."""
        model_option = next((option for option in options if option.get("id") == "model"), None)
        if model_option is None:
            return
        self._model_option = model_option.get("currentValue")
        self._declared = [
            str(entry.get("value"))
            for group in model_option.get("options") or []
            for entry in group.get("options") or []
        ]

    def select_route(self, profile: Any, *, profile_name: str = "") -> dict[str, Any]:
        """Apply a model change for **future** turns; refuse anything else.

        A provider change would need another child (the credential is fixed at launch), so
        it is refused by name rather than half-applied. A turn in flight is never mutated.
        """
        if self._closed:
            raise RuntimeUnavailable(ENGINE, "the session is closed")
        if self._busy:
            raise ModelRouteError("the model cannot change while a turn is running")
        # Resolve in the environment this session was configured from, not whatever the
        # process environment happens to be at the moment of the change.
        environ = dict(self._config.source_env) or None
        # Identity first: "that is a different provider" does not depend on whether the
        # other provider happens to be configured.
        target = resolve_route(profile, environ=environ, profile_name=profile_name,
                               require_credential=False)
        if target.route != self._route.route:
            raise ModelRouteLocked(self._route.route, target.route)
        candidate = resolve_route(profile, environ=environ, profile_name=profile_name)
        if candidate.api_key != self._route.api_key:
            raise ModelRouteError(
                f"This session was launched with a different credential for "
                f"{candidate.route!r}; create a new session to use another one"
            )
        # Everything below the model is baked into the launch: the overlay declares the
        # endpoint, wire api, output ceiling and reasoning level for the whole process.
        # Accepting those into the recorded route without applying them would report
        # settings the provider never receives.
        fixed = [name for name in ("api", "base_url", "max_tokens", "reasoning")
                 if getattr(candidate, name) != getattr(self._route, name)]
        if fixed:
            raise ModelRouteError(
                f"Model profile {profile_name or candidate.provider!r} changes "
                f"{', '.join(fixed)}, which are fixed when the session's engine starts; "
                "create a new session to use them"
            )
        process = self._process
        if process is not None and self._ready:
            wanted = candidate.option_value()
            if self._declared and wanted not in self._declared:
                raise ModelRouteError(
                    f"Model {candidate.model!r} is not declared for this session; create a "
                    "new session to use it"
                )
            if wanted != self._model_option:
                updated = process.call("session/set_config_option", {
                    "sessionId": self._acp_session, "configId": "model", "value": wanted,
                }, timeout=self._config.startup_timeout)
                self._read_model_options(updated.get("configOptions") or [])
                process.note(f"model -> {candidate.model}")
        self._route = candidate
        return candidate.public()

    def _discard(self, process: HarnessProcess) -> None:
        try:
            process.close()
        except Exception:  # noqa: BLE001 - cleanup must not mask the original failure
            pass

    @staticmethod
    def _detail(exc: BaseException) -> str:
        text = str(exc)
        diagnostics = getattr(exc, "diagnostics", None)
        if diagnostics:
            text = f"{text} | {diagnostics[-1]}"
        return text[:400]

    async def close(self) -> None:
        """Stop the child and its tree. Idempotent and safe to race with startup."""
        self._abandoned = True  # a startup still in flight must not publish
        await asyncio.to_thread(self._close_blocking)

    def _close_blocking(self) -> None:
        # Blocks until any in-flight startup has left its critical section, so the two
        # transitions cannot interleave and no process can appear after this returns.
        with self._lifecycle:
            if self._closed:
                return
            self._closed = True
            if self._control is not None:
                self._control.cancel()
            self._ready = False
            # Nobody may keep waiting on a human for a session that is closing, and an
            # answer that arrives afterwards must not be written as consent.
            self._abandon_approvals()
            self._abandon_questions()
            self._questions_ready = False
            self._stop_pump()
            bridge, self._bridge = self._bridge, None
            if bridge is not None:
                bridge.close()
            self._live = False
            process = self._process
            if process is not None:
                process.close()
            if self._control is not None:
                self._control.close()
                self._control = None
            self._loop = None

    # ----------------------------------------------------------------- interface

    def events(self, after_id: int = 0) -> list[RuntimeEvent]:
        events = getattr(self._session, "events", ())
        if callable(events):  # a session may expose events as a method
            events = events()
        return [RuntimeEvent.from_public(event)
                for event in events
                if event["id"] > after_id]

    def snapshot(self) -> dict[str, Any]:
        return self._session.public_snapshot()

    async def run_turn(self, text: str, *args: Any, **kwargs: Any) -> AgentTurnResult:
        """One turn: transient deltas while it runs, one committed answer at the end.

        Tools (07-10), consultation (10) and approvals (08) are refused, not emulated.
        """
        if kwargs.get("participant") is not None or args:
            raise RuntimeCapabilityUnavailable("collaboration", gate="10")
        await self.start()
        if self._closed:
            raise RuntimeUnavailable(ENGINE, "the session is closed")
        if self.stopping:
            # A stop is in flight: admitting a turn now would race the cancellation on the
            # same ACP session.
            raise RuntimeError("a turn is being stopped")
        if not self._turn_lock.acquire(blocking=False):
            raise RuntimeError("a turn is already running")
        try:
            if self._control is not None:
                self._control.begin()
        except BaseException:
            self._turn_lock.release()
            raise
        self._turn_orphaned = False
        self._busy = True
        worker = asyncio.ensure_future(asyncio.to_thread(self._prompt_blocking, text))
        try:
            return await asyncio.shield(worker)
        except asyncio.CancelledError:
            # The caller stopped waiting. The prompt is still in flight in the child, so the
            # turn must not be released: the next one would collide with it on the same ACP
            # session. Join it within a bound and, if that fails, keep the session busy and
            # hand it back only when the engine itself finishes.
            if not await self._join_worker(worker):
                self._turn_orphaned = True
                self._note("the caller abandoned a turn that is still running")
                worker.add_done_callback(self._release_orphaned_turn)
            raise
        finally:
            if self._control is not None:
                self._control.cancel()
            self._busy = False
            if not self._turn_orphaned:
                self._turn_lock.release()

    def _release_orphaned_turn(self, _worker: "asyncio.Future[Any]") -> None:
        """The abandoned turn finally ended: the session can be used again."""
        if not self._turn_orphaned:
            return
        self._turn_orphaned = False
        try:
            self._turn_lock.release()
        except RuntimeError:  # already released; nothing to hand back
            pass
        self._note("the abandoned turn ended; the session is usable again")

    async def _join_worker(self, worker: "asyncio.Future[Any]") -> bool:
        """Wait for an in-flight turn's thread, bounded. True when it finished."""
        try:
            await asyncio.wait_for(asyncio.shield(worker), self._config.shutdown_timeout)
        except asyncio.TimeoutError:
            return False
        except asyncio.CancelledError:
            return worker.done()
        except Exception:  # noqa: BLE001 - the turn's own failure is not this path's
            return True
        return True

    def _note(self, line: str) -> None:
        process = self._process
        if process is not None:
            try:
                process.note(line)
            except Exception:  # noqa: BLE001 - a diagnostic must never fail a caller
                pass

    # ------------------------------------------------------------------ approvals

    def _on_server_request(self, request_id: Any, method: str,
                           params: dict[str, Any]) -> None:
        """Reader thread: classify, bind, and hand the human wait to the session loop.

        Only a structurally bindable ``session/request_permission`` is scheduled. Every
        other request — and any permission request whose session, tool call or options
        cannot be tied to one exact operation — is answered ``cancelled`` or refused
        right here, so the runtime never waits on an answer this build cannot own.
        """
        process = self._process
        bridge = self._approvals
        if method != PERMISSION_METHOD:
            bridge.note_unsupported(method)
            response = unsupported_response(method)
            if process is not None:
                process.respond(request_id, response)
            return
        try:
            bound = bridge.bind(request_id, params)
        except ApprovalBindError as exc:
            # No arguments in the diagnostic: the reason names the shape, not the data.
            self._note(f"approval refused: {exc.reason}")
            bridge.note_unbindable(exc.reason)
            if process is not None:
                process.respond(request_id, CANCELLED_RESPONSE)
            return
        loop = self._loop
        if loop is None or loop.is_closed():
            bridge.note_unbindable("the application loop is not available")
            if process is not None:
                process.respond(request_id, CANCELLED_RESPONSE)
            return
        try:
            loop.call_soon_threadsafe(self._start_approval_task, bound)
        except RuntimeError:
            bridge.note_unbindable("the application loop is closed")
            if process is not None:
                process.respond(request_id, CANCELLED_RESPONSE)

    def _start_approval_task(self, bound: Any) -> None:
        """Loop thread: own the wait for a person as one cancellable task."""
        task = asyncio.create_task(self._answer_approval(bound))
        self._approval_tasks.add(task)
        task.add_done_callback(self._approval_tasks.discard)

    async def _answer_approval(self, bound: Any) -> None:
        process = self._process
        response = await self._approvals.answer(bound)
        if process is None:
            return
        # The write can block on a full pipe; it must not block the loop the session runs on.
        # The guard is re-evaluated inside the writer, immediately before the bytes go out:
        # a Stop (or a turn that ended) while this call was waiting must not be overtaken by
        # the allow it already produced.
        await asyncio.to_thread(
            process.respond, bound.request_id, response,
            guard=lambda payload: self._approvals.guard_response(bound, payload))

    async def _ask_human(self, details: dict[str, Any]) -> tuple[bool, str]:
        """The session's own broker decides; this engine only carries the ask."""
        interactions = getattr(self._session, "interactions", None)
        if interactions is None:
            return False, "no_channel"
        return await interactions.request_approval(
            details, with_reason=True, audit=self._audit)

    def _cancel_approvals(self) -> None:
        """Loop thread: stop waiting for people once the session is closing."""
        for task in tuple(self._approval_tasks):
            task.cancel()

    def _abandon_approvals(self) -> None:
        self._approvals.close()
        loop = self._loop
        if loop is not None and not loop.is_closed():
            try:
                loop.call_soon_threadsafe(self._cancel_approvals)
            except RuntimeError:
                pass

    def _abandon_questions(self) -> None:
        """Withdraw pending clarifications before the socket goes away."""
        self._questions.close()
        loop = self._loop
        if loop is not None and not loop.is_closed():
            try:
                loop.call_soon_threadsafe(self._questions.cancel_all, "session closed")
            except RuntimeError:
                pass

    # ------------------------------------------------------------------ questions

    def _send_to_bridge(self, message: dict[str, Any]) -> bool:
        """Queue one client-to-plugin message (answer or cancel). Never blocks."""
        bridge = self._bridge
        if bridge is None:
            return False
        try:
            return bridge.send(message)
        except Exception:  # noqa: BLE001 - a lost socket must not fail the caller
            return False

    async def _ask_human_question(self, question: Any) -> dict[str, Any]:
        """Ask through the session's existing clarification broker and route.

        The answer is information, not authority: this path cannot approve anything. The
        asker's own header/detail travel with the question so the person sees them.
        """
        interactions = getattr(self._session, "interactions", None)
        if interactions is None:
            return {"status": "cancelled", "reason": "no_channel"}
        return await interactions.ask(
            question.text, list(question.choices) or None, True,
            detail=question.detail, header=question.header)

    def _prompt_blocking(self, text: str) -> AgentTurnResult:
        process = self._process
        if process is None:
            raise RuntimeUnavailable(ENGINE, "the runtime is not running")
        process.take_notifications()  # drop anything left by an earlier turn
        refusals_before = len(process.refused_requests())
        dropped_before = self._bridge.dropped if self._bridge is not None else 0
        #: Report from *this* turn only: a stop must not read a previous turn's reason.
        self._last_stop_reason = None
        #: A permission request belongs to the turn it arrived in: the identity it binds
        #: is this turn's observed tool call, and it cannot be reused by a later one.
        self._turn_number += 1
        self._approvals.take_failures()  # a late failure from the previous turn is not this one
        self._approvals.begin_turn(self._turn_number, session_id=self._acp_session or "")
        #: Only the clarification tool's own calls are executable this turn; the set is
        #: filled from the runtime's committed `tool_call` updates as they arrive.
        self._allowed_calls = set()
        self._questions.begin_turn(self._turn_number, session_id=self._acp_session or "",
                                   turn_floor=self._turn_floor or 0)
        # One translator per turn: it owns ordering, bounds and the single outcome.
        stream = TurnStream(self._acp_session or "",
                            stream_id=f"{self.session_id}:{uuid.uuid4().hex[:12]}",
                            previous_turn=self._turn_floor)
        self._stream = stream
        #: The stop path waits on this; it is set however the turn ends.
        done = threading.Event()
        self._turn_done = done
        #: ... and this one only once the prompt is really in the child's pipe.
        sent = threading.Event()
        self._turn_prompt_sent = sent
        # Frames that arrived after the previous turn settled cannot be rendered now (they
        # belong to a finished turn); discarding them here keeps them attributable.
        self._discard_stale(stream)
        self._start_pump(stream)
        stop_reason, answer, refusal = "error", "", None
        settlement: Settlement | None = None
        try:
            try:
                reply = process.call("session/prompt", {
                    "sessionId": self._acp_session,
                    "prompt": [{"type": "text", "text": text}],
                }, timeout=self._config.turn_timeout, on_sent=sent.set)
            except HarnessTransportError as exc:
                raise RuntimeUnavailable(ENGINE, "the runtime exited during the turn",
                                         detail=self._detail(exc)) from exc
            except HarnessProtocolError as exc:
                raise RuntimeUnavailable(ENGINE, "the turn failed", detail=str(exc)) from exc

            stop_reason = str(reply.get("stopReason"))
            self._last_stop_reason = stop_reason
            updates = process.take_notifications()
            self._stop_pump()
            # Tool activity is transient too: it is rendered, then the turn still fails
            # because no tool surface is implemented before prompts 07-10.
            for notification in updates:
                for event in stream.feed_notification(notification):
                    self._emit_event("tool_progress", event.public())
            try:
                self._refuse_unimplemented(updates, refusals_before)
            except RuntimeCapabilityUnavailable as exc:
                refusal = exc
            self._record_context_usage(updates)
            answer = self._assistant_text(updates)
        finally:
            self._stop_pump()
            if self._bridge is not None:
                stream.note_dropped(self._bridge.dropped - dropped_before)
            settlement = stream.settle(text=answer, stop_reason=stop_reason)
            self._last_settlement = settlement
            last_turn = stream.last_turn
            if last_turn is not None:
                self._turn_floor = (last_turn if self._turn_floor is None
                                    else max(self._turn_floor, last_turn))
            self._stream = None
            if settlement.first_delta_ms is None and self._live:
                process.note("live text: attached but no delta arrived for this turn")
            # The turn is over: no further permission request can bind to it, and an
            # answer that has not been written yet can no longer become an allow.
            self._approvals.end_turn()
            # A pending clarification belongs to this turn too: withdraw it so the tool
            # errors instead of resuming a turn that has ended.
            self._questions.end_turn()
            # The prompt is over, whatever the outcome: a Stop waiting on this may report.
            if self._turn_done is done:
                self._turn_done = None
                self._turn_prompt_sent = None
            self._stopping = False
            done.set()
        if stop_reason == CANCEL_STOP_REASON:
            # The runtime confirmed the cancellation. Its text is not the answer, so no
            # caller may receive it as a result.
            outcome = StopOutcome(True, "stopped", "complete", stop_reason,
                                  "the runtime cancelled the turn")
            raise TurnCancelled(outcome.public())
        if refusal is not None:
            raise refusal
        return AgentTurnResult(final_text=answer, log=[])

    # ------------------------------------------------------------------ live pump

    def _discard_stale(self, stream: TurnStream) -> None:
        bridge = self._bridge
        if bridge is None:
            return
        stale = 0
        while (message := bridge.next_frame(0)) is not None:
            if self._route_question(message):
                continue
            stale += 1
        if stale:
            stream.note_late(stale)

    def _route_question(self, message: dict[str, Any]) -> bool:
        """Hand a question frame to the question bridge; True if it was one."""
        if message.get("type") != "question":
            return False
        self._questions.observe(message)
        return True

    def _start_pump(self, stream: TurnStream) -> None:
        """Drain bridge frames on their own thread, so deltas never wait for the prompt."""
        bridge = self._bridge
        if bridge is None:
            return
        self._pump_stop = threading.Event()
        stop = self._pump_stop

        def pump() -> None:
            while not stop.is_set():
                message = bridge.next_frame(0.05)
                if message is None:
                    continue
                if self._route_question(message):
                    continue
                for event in stream.feed(message):
                    if isinstance(event, Delta):
                        self._emit_event("delta", event.public())

        self._pump_thread = threading.Thread(target=pump, name="harness-delta-pump",
                                             daemon=True)
        self._pump_thread.start()

    def _stop_pump(self) -> None:
        """Stop the pump and take whatever it had not consumed yet. Idempotent."""
        self._pump_stop.set()
        thread, self._pump_thread = self._pump_thread, None
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2.0)
        stream, bridge = self._stream, self._bridge
        if stream is None or bridge is None:
            return
        while (message := bridge.next_frame(0)) is not None:
            if self._route_question(message):
                continue
            for event in stream.feed(message):
                if isinstance(event, Delta):
                    self._emit_event("delta", event.public())

    def _emit_event(self, event_type: str, data: dict[str, Any]) -> None:
        """Best-effort public event from a worker thread; never fails a turn."""
        emit = self._emit
        if emit is None:
            return
        try:
            emit(event_type, data)
        except Exception:  # noqa: BLE001 - a consumer must not break the turn
            pass

    def _record_context_usage(self, updates: list[dict[str, Any]]) -> None:
        """Keep ``usage_update``'s context figures, and do not invent token counts.

        The ACP channel reports ``used``/``size`` for the context window only. Odessy's
        ledger needs prompt/completion tokens for pricing, so nothing is recorded there
        and the cost stays ``unknown`` rather than becoming a fabricated number.
        """
        for notification in updates:
            params = notification.get("params") or {}
            if notification.get("method") != "session/update":
                continue
            update = params.get("update") or {}
            if update.get("sessionUpdate") != "usage_update":
                continue
            used, size = update.get("used"), update.get("size")
            if isinstance(used, int) and isinstance(size, int):
                self._context_usage = {"used": used, "size": size}

    def _refuse_unimplemented(self, updates: list[dict[str, Any]],
                              refusals_before: int) -> None:
        """A turn may not quietly use a surface this prompt has not implemented."""
        process = self._process
        # What went wrong with *this turn's* consent traffic is more specific than "a tool
        # surface exists", so it is reported first: a question is named as a question, and
        # a permission ask that could not be bound is named as the refusal it was. Both
        # fail the turn closed; neither was ever answered allow.
        for kind, detail in self._approvals.take_failures():
            if kind == "question":
                raise RuntimeCapabilityUnavailable("questions", gate="07")
            if kind == "unbindable":
                raise RuntimeCapabilityUnavailable(
                    f"server request: {PERMISSION_METHOD} ({detail})", gate="07")
            raise RuntimeCapabilityUnavailable(f"server request: {detail}", gate="08")
        for notification in updates:
            params = notification.get("params") or {}
            if notification.get("method") != "session/update":
                continue
            update = params.get("update") or {}
            kind = update.get("sessionUpdate")
            if kind in {"tool_call", "tool_call_update"} or "toolCall" in update:
                call = update.get("toolCall") if isinstance(update.get("toolCall"), dict) else {}
                call_id = update.get("toolCallId") or call.get("toolCallId")
                title = update.get("title") or call.get("title") or call.get("name")
                if kind == "tool_call" and title == CLARIFICATION_TOOL \
                        and isinstance(call_id, str) and call_id:
                    # The one model-facing tool this engine may run: it asks a human and
                    # returns the answer as a tool result. Nothing else may execute.
                    self._allowed_calls.add(call_id)
                    continue
                if kind == "tool_call" and self._is_our_tool_call(title) \
                        and isinstance(call_id, str) and call_id:
                    # A call into Odessy's own tool server (prompt 09). It is admitted because
                    # that server is the boundary: it applies Odessy's authority policy and
                    # confines the command with Odessy's providers, so the depth of this call
                    # is decided there, not here. Any other tool title still fails the turn.
                    self._allowed_calls.add(call_id)
                    continue
                if kind != "tool_call" and isinstance(call_id, str) \
                        and call_id in self._allowed_calls:
                    continue
                raise RuntimeCapabilityUnavailable("tools", gate="07-10")
        refused = process.refused_requests()[refusals_before:] if process else []
        if refused:
            methods = ", ".join(sorted({str(item.get("method")) for item in refused}))
            raise RuntimeCapabilityUnavailable(f"server request: {methods}", gate="08")

    @staticmethod
    def _assistant_text(updates: list[dict[str, Any]]) -> str:
        parts: list[str] = []
        for notification in updates:
            params = notification.get("params") or {}
            if notification.get("method") != "session/update":
                continue
            update = params.get("update") or {}
            if update.get("sessionUpdate") != "agent_message_chunk":
                continue
            content = update.get("content") or {}
            if content.get("type") == "text" and isinstance(content.get("text"), str):
                parts.append(content["text"])
        return "".join(parts)

    def _write_cancel(self, process: HarnessProcess, session_id: str) -> None:
        """Own the write until it really returns, independently of the waiting task."""
        try:
            process.notify("session/cancel", {"sessionId": session_id})
        finally:
            with self._cancel_write_lock:
                if self._turn_done is None:
                    self._stopping = False
                self._pending_cancel_writes -= 1

    @staticmethod
    def _observe_cancel_writer(writer: "asyncio.Task[None]") -> None:
        """Retrieve late write failures after a Stop caller has already timed out."""
        if not writer.cancelled():
            writer.exception()

    async def stop(self, *, timeout: float | None = None) -> StopOutcome:
        """Cancel this session's turn through the runtime's own ACP cancellation.

        This is ``session/cancel`` on this session's ACP session id, not a local await being
        abandoned and not the child being killed: the runtime stops generating, and the
        prompt result is the evidence. The child (and every other session's child) keeps
        running, so the session stays usable afterwards.

        A join that exceeds ``timeout`` is reported as ``unconfirmed``: the turn may still be
        running, and nothing about it is rolled back.
        """
        control = self._control
        tool_interrupted = bool(control is not None and control.running)
        cleanup_complete = True
        def outcome(requested, execution, cleanup, reason, detail):
            if not cleanup_complete:
                return StopOutcome(requested, "unconfirmed", "pending", reason,
                                   "the tool process cleanup is not confirmed")
            return StopOutcome(requested, execution, cleanup, reason, detail)
        process = self._process
        acp_session = self._acp_session
        if self._closed:
            return outcome(False, "idle", "complete", None, "the session is closed")
        if process is None or not self._ready or not acp_session:
            return outcome(False, "idle", "complete", None, "no runtime process")
        # A clarification waiting for a person belongs to the turn being stopped: withdraw
        # it first so the tool errs rather than resuming after the Stop.
        if self._control is not None:
            self._control.cancel()
        self._questions.cancel_all("stopped")
        bound = self._config.shutdown_timeout if timeout is None else timeout
        if control is not None:
            cleanup_complete = await asyncio.to_thread(control.clean.wait, bound)
        done = self._turn_done
        if done is None:
            with self._cancel_write_lock:
                write_pending = self._pending_cancel_writes > 0
            if write_pending:
                return outcome(True, "unconfirmed", "pending", None,
                                   "a cancellation write is still pending")
            if not self._turn_lock.locked():
                return outcome(False, "idle", "complete", None, "no turn is running")
            # A turn has started but its prompting thread has not published its completion
            # handle yet. Reporting "idle" here would be a Stop that silently did nothing,
            # so wait (bounded) for the handle instead.
            deadline = time.monotonic() + bound
            while done is None and time.monotonic() < deadline:
                await asyncio.sleep(0.005)
                done = self._turn_done
            if done is None:
                return outcome(True, "unconfirmed", "pending", None,
                                   "a turn is in flight but its prompt has not started")
        if done.is_set():
            return outcome(False, "idle", "complete", None, "no turn is running")
        sent = self._turn_prompt_sent
        if sent is not None and not sent.is_set():
            # The prompt has not reached the child yet. A cancel written now would arrive
            # before there is anything to cancel and be dropped, so wait for the write
            # (bounded) instead of racing it.
            if not await asyncio.to_thread(sent.wait, bound):
                return outcome(True, "unconfirmed", "pending", None,
                                   f"the prompt was not sent within {bound:g}s")
        # From here on no new turn may start: the cancellation and the prompt share one ACP
        # session, so admitting work now would race it.
        self._stopping = True
        with self._cancel_write_lock:
            self._pending_cancel_writes += 1
        writer = asyncio.create_task(
            asyncio.to_thread(self._write_cancel, process, acp_session))
        writer.add_done_callback(self._observe_cancel_writer)
        try:
            # The write goes to the child's stdin, which can block if the child stops reading.
            # Waiting for a cancelled turn forever is worse than reporting that the request
            # could not be delivered, so the write is bounded too.
            await asyncio.wait_for(
                asyncio.shield(writer),
                timeout=bound,
            )
        except asyncio.TimeoutError:
            self._note(f"stop: the cancellation could not be written within {bound:g}s")
            return outcome(True, "unconfirmed", "pending", None,
                               f"the cancellation could not be written within {bound:g}s")
        except (HarnessProcessError, OSError) as exc:
            # The child cannot receive the cancellation. Its work may have ended or not: that
            # is unknown, so nothing here is called stopped.
            self._note(f"stop: the cancellation could not be sent ({type(exc).__name__})")
            return outcome(True, "unconfirmed", "pending", None,
                               "the runtime could not receive the cancellation")
        finished = await asyncio.to_thread(done.wait, bound)
        if not finished:
            self._note(f"stop: the runtime did not end the turn within {bound:g}s")
            return outcome(True, "unconfirmed", "pending", None,
                               f"the runtime did not end the turn within {bound:g}s")
        reason = self._last_stop_reason
        if reason is None:
            # The turn ended without a prompt result: the runtime died mid-turn. Nothing
            # confirms the cancellation, so this is unknown rather than stopped.
            self._note("stop: the turn ended without a prompt result")
            return outcome(True, "unconfirmed", "pending", None,
                               "the runtime ended the turn without reporting a reason")
        if reason != CANCEL_STOP_REASON and not (tool_interrupted and cleanup_complete):
            # The prompt came back with a normal stop reason: the runtime finished the turn
            # instead of cancelling it, so the Stop was not honoured. Saying "stopped" here
            # would claim an effect this call did not have.
            self._note(f"stop: the runtime completed the turn instead ({reason})")
            return outcome(True, "unconfirmed", "complete", reason,
                               f"the runtime completed the turn ({reason}) instead of "
                               "cancelling it")
        self._note(f"stop: the turn ended ({reason})")
        return outcome(True, "stopped", "complete", reason,
                           "the runtime ended the turn")
