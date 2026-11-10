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

import asyncio
import secrets
import threading
import time
import uuid
from dataclasses import replace
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
from odessy.runtime_backends.harness.config import HarnessConfig, from_environment
from odessy.runtime_backends.harness.process import (
    HarnessProcess,
    HarnessProcessError,
    HarnessProtocolError,
    HarnessTransportError,
)
from odessy.runtime_backends.harness.bridge import BridgeError, LiveBridge, free_port
from odessy.runtime_backends.harness.routes import HarnessRoute, resolve_route
from odessy.runtime_backends.harness.stream import Delta, Settlement, TurnStream
from odessy.runtime.types import AgentTurnResult

ENGINE = "harness"
_CLIENT_INFO = {"name": "odessy", "version": "0.5.0"}
#: The ACP ``stopReason`` that means the runtime itself cancelled the turn.
CANCEL_STOP_REASON = "cancelled"
#: What this engine can do once the packaged bridge is attached: text arrives before the
#: answer is committed. Without the bridge the record stays as it was in prompt 03.
_LIVE_CAPABILITIES = replace(HARNESS_CAPABILITIES, live_text_deltas=True)


def _sibling_models(session: Any, selected: Any, environ=None) -> tuple[str, ...]:
    """Models of the session's other profiles that share the selected profile's provider.

    Declaring them up front is what lets ``select_route`` change the model without a
    relaunch: the runtime can only select a pair it was told about.
    """
    provider = (selected or {}).get("provider") if isinstance(selected, dict) else None
    if not provider:
        try:
            provider = resolve_route(selected, environ=environ, require_credential=False).provider
        except (ModelRouteError, RuntimeUnavailable):
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
        """What this *session* can do: live deltas only once the bridge is attached."""
        return _LIVE_CAPABILITIES if self._live else HARNESS_CAPABILITIES

    def __init__(self, session: Any, config: HarnessConfig | None = None,
                 route: HarnessRoute | None = None):
        # Resolving the route here, not at first turn, is what makes both the engine and
        # the model a decision taken when the session is created: a misconfigured engine or
        # an unroutable profile fails session creation instead of the first prompt.
        # Engine configuration first: "this build has no runtime" is a more fundamental
        # failure than "this profile has no route", and callers rely on that ordering.
        """Initialize the owned state and synchronization used by this component."""
        base_config = config if config is not None else from_environment()
        profile = getattr(session, "model_profile", None)
        selected = profile() if callable(profile) else profile
        self._route = route if route is not None else resolve_route(
            selected,
            # The route resolves in the same environment the engine was configured from, so
            # an explicit mapping (tests, integration smoke) also governs credentials.
            environ=dict(base_config.source_env) or None,
            extra_models=_sibling_models(session, selected, dict(base_config.source_env) or None),
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
        self._session = session
        self.session_id = str(getattr(session, "id", "") or "")
        self._process: HarnessProcess | None = None
        self._identity: dict[str, object] = {}
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
        """Return the immutable launch configuration for this session."""
        return self._config

    @property
    def identity(self) -> dict[str, object]:
        """Version and artifact evidence captured at readiness."""
        return dict(self._identity)

    @property
    def acp_session_id(self) -> str | None:
        """Return the opened runtime session identity, if available."""
        return self._acp_session

    @property
    def last_stop_reason(self) -> str | None:
        """Return the latest runtime-reported terminal reason."""
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
    def orphaned_turn(self) -> bool:
        """True when an abandoned turn never ended: the session is still busy."""
        return self._turn_orphaned

    @property
    def stopping(self) -> bool:
        """True while a Stop request has not finished."""
        with self._cancel_write_lock:
            return self._stopping or self._pending_cancel_writes > 0

    def diagnostics(self) -> list[str]:
        """Return a bounded snapshot of content-safe runtime diagnostics."""
        return [] if self._process is None else self._process.diagnostics()

    def _bridge_environment(self) -> dict[str, str]:
        """Return the session-specific bridge coordinates without other credentials."""
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
        worker = asyncio.create_task(asyncio.to_thread(self._start_blocking))
        try:
            await asyncio.shield(worker)
        except asyncio.CancelledError:
            # Cancelling this await does not stop the worker thread. Mark the start as
            # abandoned so the worker closes the child it is building instead of
            # publishing it; otherwise a cancelled start would leak a running runtime.
            self._abandoned = True
            try:
                await asyncio.shield(worker)
            except Exception:
                pass
            await asyncio.to_thread(self._reset_cancelled_start)
            raise
        return dict(self._identity)

    def _reset_cancelled_start(self) -> None:
        """Dispose only the interrupted startup attempt, preserving a reusable session."""
        with self._lifecycle:
            if self._closed:
                return
            self._stop_pump()
            if self._bridge is not None:
                self._bridge.close()
                self._bridge = None
            if self._process is not None:
                self._process.close()
            self._process = None
            self._acp_session = None
            self._ready = self._live = False
            self._abandoned = False

    def _start_blocking(self) -> None:
        # Thread context: no event loop, so every wait here is a plain blocking wait.
        """Verify, launch and negotiate the child while holding lifecycle ownership."""
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
            process = HarnessProcess(self._config)
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
                opened = process.call("session/new", {
                    "cwd": str(self._config.workspace),
                    "mcpServers": [],
                }, timeout=self._config.startup_timeout)
                session_id = opened.get("sessionId")
                if not isinstance(session_id, str) or not session_id:
                    raise HarnessProtocolError("session/new returned no sessionId")
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
            self._connect_bridge(process)
            self._process = process
            self._acp_session = session_id
            self._identity = dict(identity)
            self._ready = True
            process.note(
                f"ready: version={identity.get('version')} "
                f"artifact={identity.get('artifact')} protocolVersion={negotiated} "
                f"route={self._route.route} model={self._route.model} "
                f"api={self._route.api} acp_session={session_id}"
            )

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
            process.note("live text: bridge attached "
                         f"(protocol {self._bridge.hello.get('protocol')}, "
                         f"port {self._bridge_port})")
        except BridgeError as exc:
            self._live = False
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
        """Serialize a blocking route change with turn admission; async callers offload it."""
        if self.stopping or not self._turn_lock.acquire(blocking=False):
            raise ModelRouteError("the model cannot change while a turn is running")
        try:
            return self._select_route_locked(profile, profile_name=profile_name)
        finally:
            self._turn_lock.release()

    def _select_route_locked(self, profile: Any, *, profile_name: str = "") -> dict[str, Any]:
        """Apply a model change for **future** turns; refuse anything else.

        A provider change would need another child (the credential is fixed at launch), so
        it is refused by name rather than half-applied. A turn in flight is never mutated.
        """
        if self._closed:
            raise RuntimeUnavailable(ENGINE, "the session is closed")
        if self._busy or self.stopping:
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
        """Reap a failed startup child without masking its original error."""
        try:
            process.close()
        except Exception:  # noqa: BLE001 - cleanup must not mask the original failure
            pass

    @staticmethod
    def _detail(exc: BaseException) -> str:
        """Return bounded diagnostic context suitable for a runtime failure."""
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
        """Dispose the owned runtime and bridge under lifecycle synchronization."""
        with self._lifecycle:
            if self._closed:
                return
            self._closed = True
            self._ready = False
            self._stop_pump()
            bridge, self._bridge = self._bridge, None
            if bridge is not None:
                bridge.close()
            self._live = False
            process = self._process
            if process is not None:
                process.close()

    # ----------------------------------------------------------------- interface

    def events(self, after_id: int = 0) -> list[RuntimeEvent]:
        """Return normalized events after the requested cursor."""
        events = getattr(self._session, "events", ())
        if callable(events):  # a session may expose events as a method
            events = events()
        return [RuntimeEvent.from_public(event)
                for event in events
                if event["id"] > after_id]

    def snapshot(self) -> dict[str, Any]:
        """Return the application projection without creating a second runtime history."""
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
        """Record a safe diagnostic when a runtime process is available."""
        process = self._process
        if process is not None:
            try:
                process.note(line)
            except Exception:  # noqa: BLE001 - a diagnostic must never fail a caller
                pass

    def _prompt_blocking(self, text: str) -> AgentTurnResult:
        """Execute and settle one ACP prompt without releasing live timed-out work."""
        process = self._process
        if process is None:
            raise RuntimeUnavailable(ENGINE, "the runtime is not running")
        process.take_notifications()  # drop anything left by an earlier turn
        refusals_before = len(process.refused_requests())
        dropped_before = self._bridge.dropped if self._bridge is not None else 0
        #: Report from *this* turn only: a stop must not read a previous turn's reason.
        self._last_stop_reason = None
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
                if "got no reply within" in str(exc):
                    # Losing the reply waiter does not stop remote work. Reap the owned
                    # runtime before releasing admission, never send another prompt to it.
                    self._close_blocking()
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
        """Drain pre-turn transient frames so they cannot enter the next turn."""
        bridge = self._bridge
        if bridge is None:
            return
        stale = 0
        while bridge.next_frame(0) is not None:
            stale += 1
        if stale:
            stream.note_late(stale)

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
        for notification in updates:
            params = notification.get("params") or {}
            if notification.get("method") != "session/update":
                continue
            update = params.get("update") or {}
            kind = update.get("sessionUpdate")
            if kind in {"tool_call", "tool_call_update"} or "toolCall" in update:
                raise RuntimeCapabilityUnavailable("tools", gate="07-10")
        process = self._process
        refused = process.refused_requests()[refusals_before:] if process else []
        if refused:
            methods = ", ".join(sorted({str(item.get("method")) for item in refused}))
            raise RuntimeCapabilityUnavailable(f"server request: {methods}", gate="08")

    @staticmethod
    def _assistant_text(updates: list[dict[str, Any]]) -> str:
        """Assemble visible assistant text from committed runtime notifications."""
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
        process = self._process
        acp_session = self._acp_session
        if self._closed:
            return StopOutcome(False, "idle", "complete", None, "the session is closed")
        if process is None or not self._ready or not acp_session:
            return StopOutcome(False, "idle", "complete", None, "no runtime process")
        bound = self._config.shutdown_timeout if timeout is None else timeout
        done = self._turn_done
        if done is None:
            with self._cancel_write_lock:
                write_pending = self._pending_cancel_writes > 0
            if write_pending:
                return StopOutcome(True, "unconfirmed", "pending", None,
                                   "a cancellation write is still pending")
            if not self._turn_lock.locked():
                return StopOutcome(False, "idle", "complete", None, "no turn is running")
            # A turn has started but its prompting thread has not published its completion
            # handle yet. Reporting "idle" here would be a Stop that silently did nothing,
            # so wait (bounded) for the handle instead.
            deadline = time.monotonic() + bound
            while done is None and time.monotonic() < deadline:
                await asyncio.sleep(0.005)
                done = self._turn_done
            if done is None:
                return StopOutcome(True, "unconfirmed", "pending", None,
                                   "a turn is in flight but its prompt has not started")
        if done.is_set():
            return StopOutcome(False, "idle", "complete", None, "no turn is running")
        sent = self._turn_prompt_sent
        if sent is not None and not sent.is_set():
            # The prompt has not reached the child yet. A cancel written now would arrive
            # before there is anything to cancel and be dropped, so wait for the write
            # (bounded) instead of racing it.
            if not await asyncio.to_thread(sent.wait, bound):
                return StopOutcome(True, "unconfirmed", "pending", None,
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
            return StopOutcome(True, "unconfirmed", "pending", None,
                               f"the cancellation could not be written within {bound:g}s")
        except (HarnessProcessError, OSError) as exc:
            # The child cannot receive the cancellation. Its work may have ended or not: that
            # is unknown, so nothing here is called stopped.
            self._note(f"stop: the cancellation could not be sent ({type(exc).__name__})")
            return StopOutcome(True, "unconfirmed", "pending", None,
                               "the runtime could not receive the cancellation")
        finished = await asyncio.to_thread(done.wait, bound)
        if not finished:
            self._note(f"stop: the runtime did not end the turn within {bound:g}s")
            return StopOutcome(True, "unconfirmed", "pending", None,
                               f"the runtime did not end the turn within {bound:g}s")
        reason = self._last_stop_reason
        if reason is None:
            # The turn ended without a prompt result: the runtime died mid-turn. Nothing
            # confirms the cancellation, so this is unknown rather than stopped.
            self._note("stop: the turn ended without a prompt result")
            return StopOutcome(True, "unconfirmed", "pending", None,
                               "the runtime ended the turn without reporting a reason")
        if reason != CANCEL_STOP_REASON:
            # The prompt came back with a normal stop reason: the runtime finished the turn
            # instead of cancelling it, so the Stop was not honoured. Saying "stopped" here
            # would claim an effect this call did not have.
            self._note(f"stop: the runtime completed the turn instead ({reason})")
            return StopOutcome(True, "unconfirmed", "complete", reason,
                               f"the runtime completed the turn ({reason}) instead of "
                               "cancelling it")
        self._note(f"stop: the turn ended ({reason})")
        return StopOutcome(True, "stopped", "complete", reason,
                           "the runtime ended the turn")
