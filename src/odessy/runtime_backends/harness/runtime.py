"""The Harness engine for one Odessy session.

Scope of this prompt (03): the process lifecycle and the narrow profile. The engine can
boot, open one ACP session, answer a settled (non-streaming) turn, report honest
capabilities and shut down — and it refuses everything it has not implemented yet with
:class:`~odessy.runtime_backends.contracts.RuntimeCapabilityUnavailable`.

Deliberately *not* here: live text deltas (05), per-session cancel and resume (06),
model/credential selection (04), approvals (08), MCP and tools (07-09). Nothing in this
module falls back to the legacy engine; a failure surfaces as a typed error.
"""

from __future__ import annotations

import asyncio
import threading
from typing import Any

from odessy.runtime_backends.contracts import (
    HARNESS_CAPABILITIES,
    RuntimeCapabilityUnavailable,
    RuntimeEvent,
    RuntimeUnavailable,
)
from odessy.runtime_backends.harness import pin
from odessy.runtime_backends.harness.config import HarnessConfig, from_environment
from odessy.runtime_backends.harness.process import (
    HarnessProcess,
    HarnessProcessError,
    HarnessProtocolError,
    HarnessTransportError,
)
from odessy.runtime.types import AgentTurnResult

ENGINE = "harness"
_CLIENT_INFO = {"name": "odessy", "version": "0.5.0"}


class HarnessRuntime:
    """Owns one supervised Harness child per Odessy session."""

    engine = ENGINE
    capabilities = HARNESS_CAPABILITIES

    def __init__(self, session: Any, config: HarnessConfig | None = None):
        # Resolving here, not at first turn, is what makes ``harness`` a decision taken
        # when the session is created: a misconfigured engine fails session creation.
        self._config = config if config is not None else from_environment()
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
        self._last_stop_reason: str | None = None
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

    def diagnostics(self) -> list[str]:
        return [] if self._process is None else self._process.diagnostics()

    # ------------------------------------------------------------------ lifecycle

    async def start(self) -> dict[str, object]:
        """Ready the runtime once. Safe to await repeatedly and from several tasks."""
        if self._closed:
            raise RuntimeUnavailable(ENGINE, "the session is closed")
        if self._ready:
            return dict(self._identity)
        try:
            await asyncio.to_thread(self._start_blocking)
        except asyncio.CancelledError:
            # Cancelling this await does not stop the worker thread. Mark the start as
            # abandoned so the worker closes the child it is building instead of
            # publishing it; otherwise a cancelled start would leak a running runtime.
            self._abandoned = True
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
            self._process = process
            self._acp_session = session_id
            self._identity = dict(identity)
            self._ready = True
            process.note(
                f"ready: version={identity.get('version')} "
                f"artifact={identity.get('artifact')} protocolVersion={negotiated} "
                f"acp_session={session_id}"
            )

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
            self._ready = False
            process = self._process
            if process is not None:
                process.close()

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
        """One settled turn with no tools.

        Streaming (05), cancellation (06) and consultation are refused, not emulated.
        """
        if kwargs.get("participant") is not None or args:
            raise RuntimeCapabilityUnavailable("collaboration", gate="10")
        await self.start()
        if self._closed:
            raise RuntimeUnavailable(ENGINE, "the session is closed")
        if not self._turn_lock.acquire(blocking=False):
            raise RuntimeError("a turn is already running")
        try:
            self._busy = True
            return await asyncio.to_thread(self._prompt_blocking, text)
        finally:
            self._busy = False
            self._turn_lock.release()

    def _prompt_blocking(self, text: str) -> AgentTurnResult:
        process = self._process
        if process is None:
            raise RuntimeUnavailable(ENGINE, "the runtime is not running")
        process.take_notifications()  # drop anything left by an earlier turn
        refusals_before = len(process.refused_requests())
        try:
            reply = process.call("session/prompt", {
                "sessionId": self._acp_session,
                "prompt": [{"type": "text", "text": text}],
            }, timeout=self._config.turn_timeout)
        except HarnessTransportError as exc:
            raise RuntimeUnavailable(ENGINE, "the runtime exited during the turn",
                                     detail=self._detail(exc)) from exc
        except HarnessProtocolError as exc:
            raise RuntimeUnavailable(ENGINE, "the turn failed", detail=str(exc)) from exc

        stop_reason = reply.get("stopReason")
        self._last_stop_reason = str(stop_reason)
        updates = process.take_notifications()
        self._refuse_unimplemented(updates, refusals_before)
        answer = self._assistant_text(updates)
        return AgentTurnResult(final_text=answer, log=[])

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

    async def stop(self) -> None:
        raise RuntimeCapabilityUnavailable("per_session_cancel", gate="06")
