"""Client for the packaged live-stream bridge (prompt 05).

Prompt 01 proved that the pinned runtime carries assistant text before settlement only on
its process-local ``agent/assistant-stream`` event, and that a small bundle
(``@odessy/dsh-stream-bridge``, ``plugin/odessy-stream-bridge/`` in the compatibility
experiments) can forward those frames to one token-authenticated loopback client without
touching the agent loop. This module is that client, promoted for production:

- it knows nothing about models, turns or text semantics; :mod:`.stream` normalizes frames;
- the socket is loopback only and requires a per-session random token;
- every read is bounded (line length, and a bounded queue), so a wedged or hostile producer
  cannot grow this process without limit;
- frames are handed over a queue: the *pump* thread drains it and the turn thread never
  blocks on I/O. A slow consumer loses transient deltas (counted by the caller), never the
  committed answer, which arrives over ACP.

The plugin is optional. When it is not installed in the profile, nothing listens, the
handshake fails within a bounded wait, and the engine reports ``live_text_deltas=False``
instead of pretending.
"""

from __future__ import annotations

import json
import queue
import secrets
import socket
import threading
import time
from typing import Any

#: Longest accepted line. The plugin sends one small JSON object per frame; anything near
#: this bound is not a frame we produced.
MAX_LINE_BYTES = 64 * 1024
#: Frames queued for the pump. Bounded on purpose: see the module docstring.
MAX_QUEUED_FRAMES = 2048
#: Outbound messages queued for the writer. An answer or a cancel is small and rare; this
#: is far more than one turn can produce, so overflow means the plugin stopped reading.
MAX_QUEUED_SENDS = 64
#: Protocol version the plugin announces. A different one is refused, not guessed at.
PROTOCOL = 1
#: The greeting feature that means "this plugin can report the runtime's tool roster".
FEATURE_TOOLS = "tools"
#: Roster bounds, enforced on both ends: a hostile or wedged plugin cannot make this process
#: allocate an unbounded name list.
MAX_TOOL_NAMES = 512
MAX_TOOL_NAME = 200
#: Queued in place of a line that arrived but was not a JSON object. The translator counts
#: it as malformed input; the transport does not invent channel semantics, and no
#: unparsed content is passed along.
UNPARSEABLE = {"type": "malformed"}


class BridgeError(RuntimeError):
    """The bridge is absent, refused the token, or spoke a different protocol."""


def _checked_roster(message: dict[str, Any]) -> dict[str, Any]:
    """Validate one roster reply. Shapes are checked, not assumed."""

    available = message.get("available")
    if not isinstance(available, bool):
        raise BridgeError("the roster reply did not say whether it was available")
    if not available:
        reason = message.get("reason")
        return {"available": False,
                "reason": str(reason)[:300] if isinstance(reason, str) else "unavailable",
                "tools": [], "agents": 0, "global": 0}
    raw = message.get("tools")
    if not isinstance(raw, list):
        raise BridgeError("the roster reply carried no tool list")
    names: list[str] = []
    for item in raw:
        if not isinstance(item, str) or not item or len(item) > MAX_TOOL_NAME:
            raise BridgeError("the roster reply carried a malformed tool name")
        if item not in names:
            names.append(item)
        if len(names) > MAX_TOOL_NAMES:
            raise BridgeError("the roster reply was larger than this client accepts")
    counts = {}
    for field in ("agents", "global"):
        value = message.get(field)
        counts[field] = value if isinstance(value, int) and not isinstance(value, bool) else 0
    return {"available": True, "reason": "", "tools": names, **counts}


class LiveBridge:
    """One connection to the session's bridge plugin."""

    def __init__(self, port: int, token: str, *, host: str = "127.0.0.1",
                 connect_timeout: float = 10.0, queue_size: int = MAX_QUEUED_FRAMES):
        """Initialize the owned state and synchronization used by this component."""
        if not token:
            raise BridgeError("the bridge requires a non-empty token")
        if not 0 < int(port) < 65536:
            raise BridgeError("the bridge requires a valid port")
        self.host, self.port = host, int(port)
        self._queue: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=max(1, queue_size))
        self.dropped = 0
        #: Lines received / queued, for diagnostics only. Never carries content.
        self.lines_read = 0
        self.lines_queued = 0
        self._closed = threading.Event()
        self._pending_bytes = b""
        #: Roster replies awaiting their requester, by request id. The reader thread routes
        #: them here instead of the frame queue, so a control reply can never be mistaken for
        #: a transient frame (and vice versa).
        self._replies: dict[str, queue.Queue[dict[str, Any]]] = {}
        self._replies_lock = threading.Lock()
        self._sock = self._connect(connect_timeout)
        try:
            self._sock.settimeout(None)
            self._sock.sendall((json.dumps({"token": token}) + "\n").encode("utf-8"))
            self.hello, self._pending_bytes = self._read_line_blocking(timeout=5.0)
            if self.hello.get("type") != "hello":
                raise BridgeError(f"the bridge did not greet: {self.hello!r}"[:200])
            if int(self.hello.get("protocol") or 0) != PROTOCOL:
                raise BridgeError(f"the bridge speaks protocol {self.hello.get('protocol')!r},"
                                  f" expected {PROTOCOL}")
        except Exception:
            self.close()
            raise
        self._reader = threading.Thread(target=self._read_loop, name="harness-bridge",
                                        daemon=True)
        self._reader.start()
        #: What the installed plugin says it implements. Capabilities gate on this, so an
        #: older plugin cannot make the engine claim a channel it does not have.
        self.features = tuple(str(item) for item in (self.hello.get("features") or ())
                              if isinstance(item, str))
        self.dropped_sends = 0
        self._send_queue: queue.Queue[dict[str, Any] | None] = queue.Queue(
            maxsize=MAX_QUEUED_SENDS)
        self._writer = threading.Thread(target=self._write_loop, name="harness-bridge-write",
                                        daemon=True)
        self._writer.start()

    # ------------------------------------------------------------------ connect/read

    def _connect(self, timeout: float) -> socket.socket:
        """Authenticate the local bridge and validate its protocol greeting."""
        deadline = time.monotonic() + timeout
        last: Exception | None = None
        while time.monotonic() < deadline and not self._closed.is_set():
            try:
                return socket.create_connection((self.host, self.port), timeout=2.0)
            except OSError as exc:  # the runtime may still be booting
                last = exc
                time.sleep(0.05)
        raise BridgeError(f"no bridge listening on {self.host}:{self.port} ({last})")

    def _read_line_blocking(self, *, timeout: float | None = None
                            ) -> tuple[dict[str, Any], bytes]:
        """Read the greeting and keep whatever was read after its newline.

        A greeting and the plugin's first real frame can arrive in one TCP read. Dropping
        the remainder would lose that frame, so it is returned and seeds the reader thread.
        """
        if timeout is not None:
            self._sock.settimeout(timeout)
        try:
            buffer = b""
            while b"\n" not in buffer:
                chunk = self._sock.recv(65536)
                if not chunk:
                    raise BridgeError("the bridge closed the connection")
                buffer += chunk
                if len(buffer) > MAX_LINE_BYTES:
                    raise BridgeError("the bridge sent an oversized greeting")
        finally:
            self._sock.settimeout(None)
        line, remainder = buffer.split(b"\n", 1)
        try:
            message = json.loads(line)
        except ValueError as exc:
            raise BridgeError("the bridge greeting is not JSON") from exc
        if not isinstance(message, dict):
            raise BridgeError("the bridge greeting is not an object")
        return message, remainder

    def _read_loop(self) -> None:
        # Bytes read together with the greeting are parsed before the first blocking read;
        # otherwise they would wait for a later frame (or be lost when the peer closes).
        buffer = self._pending_bytes
        self._pending_bytes = b""
        discarding = False
        while not self._closed.is_set():
            buffer, discarding = self._consume(buffer, discarding)
            try:
                chunk = self._sock.recv(65536)
            except OSError:
                return
            if not chunk:
                self._consume(buffer + b"\n", discarding)
                return
            buffer += chunk

    def _consume(self, buffer: bytes, discarding: bool) -> tuple[bytes, bool]:
        """Parse every complete line in ``buffer``; return what is left and the discard flag."""
        if discarding:
            # Still inside an oversized line: drop through its newline, once.
            newline = buffer.find(b"\n")
            if newline < 0:
                return b"", True
            buffer = buffer[newline + 1:]
            discarding = False
        while b"\n" in buffer:
            line, buffer = buffer.split(b"\n", 1)
            if not line.strip():
                continue
            self.lines_read += 1
            if len(line) > MAX_LINE_BYTES:
                # Over the bound we refuse to look at it at all, so it is a transport
                # loss rather than an unparseable frame.
                self.dropped += 1
                continue
            try:
                message = json.loads(line)
            except ValueError:
                self._note_unparseable()
                continue
            if not isinstance(message, dict):
                self._note_unparseable()
                continue
            if self._route_reply(message):
                continue
            try:
                self._queue.put_nowait(message)
                self.lines_queued += 1
            except queue.Full:
                # Keep accepted frames; exactly one incoming frame was rejected.
                self.dropped += 1
        if len(buffer) > MAX_LINE_BYTES:
            self.dropped += 1
            return b"", True
        return buffer, discarding

    def _note_unparseable(self) -> None:
        """Hand the consumer the fact of a bad line without the bad line itself."""
        try:
            self._queue.put_nowait(dict(UNPARSEABLE))
        except queue.Full:
            self.dropped += 1

    def _route_reply(self, message: dict[str, Any]) -> bool:
        """Deliver a control reply to its waiter. Returns whether the message was one."""

        if message.get("type") != "tools":
            return False
        request_id = message.get("requestId")
        if not isinstance(request_id, str):
            return True  # ours by type, but unusable: never a frame for the pump
        with self._replies_lock:
            waiter = self._replies.get(request_id)
        if waiter is not None:
            try:
                waiter.put_nowait(message)
            except queue.Full:
                pass
        return True

    # --------------------------------------------------------------------- request

    def request_tools(self, timeout: float = 10.0) -> dict[str, Any]:
        """Ask the plugin for the tool names the runtime actually attached.

        Names only: this is the read-only view (`ctx.tools.schemas`), not a tool definition and
        certainly not an execution path. An unavailable registry is reported as such — an empty
        list would read as "this session has no tools", a different and unverified claim.
        """

        if FEATURE_TOOLS not in self.features:
            raise BridgeError("the installed plugin does not report a tool roster")
        request_id = f"tools-{secrets.token_hex(8)}"
        waiter: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=1)
        with self._replies_lock:
            self._replies[request_id] = waiter
        try:
            if not self.send({"type": "tools", "requestId": request_id}):
                raise BridgeError("the roster request could not be queued")
            try:
                message = waiter.get(timeout=max(0.1, timeout))
            except queue.Empty:
                raise BridgeError("the plugin did not answer the roster request") from None
        finally:
            with self._replies_lock:
                self._replies.pop(request_id, None)
        return _checked_roster(message)

    # ------------------------------------------------------------------------ send

    def send(self, message: dict[str, Any]) -> bool:
        """Queue one client-to-plugin message. Never blocks the caller.

        The socket is also read by this client's reader thread, so writes go through one
        writer thread: a blocked plugin must not stall the session loop, and must not race
        a send timeout against the reader's blocking ``recv``.
        """
        if self._closed.is_set():
            return False
        try:
            self._send_queue.put_nowait(dict(message))
        except queue.Full:
            # The plugin is not draining. Losing an answer is a loss, not a grant: the
            # waiter settles by timeout or cancellation, and nothing is invented.
            self.dropped_sends += 1
            return False
        return True

    def _write_loop(self) -> None:
        while not self._closed.is_set():
            try:
                message = self._send_queue.get(timeout=0.1)
            except queue.Empty:
                continue
            if message is None:
                return
            line = json.dumps(message, ensure_ascii=False, separators=(",", ":")) + "\n"
            try:
                self._sock.sendall(line.encode("utf-8"))
            except OSError:
                return

    # ---------------------------------------------------------------------- consume

    def next_frame(self, timeout: float = 0.05) -> dict[str, Any] | None:
        """One frame, or ``None`` if none arrived within ``timeout``."""
        try:
            return self._queue.get(timeout=timeout)
        except queue.Empty:
            return None

    @property
    def closed(self) -> bool:
        """Report whether this transport has been explicitly closed."""
        return self._closed.is_set()

    def close(self) -> None:
        """Release this component and its owned resources; repeated closure is safe."""
        self._closed.set()
        writer = getattr(self, "_writer", None)
        if writer is not None:
            try:
                self._send_queue.put_nowait(None)
            except queue.Full:
                pass
        try:
            self._sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            self._sock.close()
        except OSError:
            pass
        reader = getattr(self, "_reader", None)
        if reader is not None and reader is not threading.current_thread():
            reader.join(timeout=2.0)
        if writer is not None and writer is not threading.current_thread():
            writer.join(timeout=2.0)


def free_port(host: str = "127.0.0.1") -> int:
    """A loopback port the runtime's bridge plugin can bind a moment later."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind((host, 0))
        return int(probe.getsockname()[1])
