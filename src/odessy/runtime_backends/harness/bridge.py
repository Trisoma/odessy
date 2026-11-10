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
import socket
import threading
import time
from typing import Any

#: Longest accepted line. The plugin sends one small JSON object per frame; anything near
#: this bound is not a frame we produced.
MAX_LINE_BYTES = 64 * 1024
#: Frames queued for the pump. Bounded on purpose: see the module docstring.
MAX_QUEUED_FRAMES = 2048
#: Protocol version the plugin announces. A different one is refused, not guessed at.
PROTOCOL = 1
#: Queued in place of a line that arrived but was not a JSON object. The translator counts
#: it as malformed input; the transport does not invent channel semantics, and no
#: unparsed content is passed along.
UNPARSEABLE = {"type": "malformed"}


class BridgeError(RuntimeError):
    """The bridge is absent, refused the token, or spoke a different protocol."""


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
        self._closed = threading.Event()
        self._sock = self._connect(connect_timeout)
        try:
            self._sock.settimeout(None)
            self._sock.sendall((json.dumps({"token": token}) + "\n").encode("utf-8"))
            self.hello = self._read_line_blocking(timeout=5.0)
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

    def _read_line_blocking(self, *, timeout: float | None = None) -> dict[str, Any]:
        """Read one size-limited handshake line within the socket deadline."""
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
        line = buffer.split(b"\n", 1)[0]
        try:
            message = json.loads(line)
        except ValueError as exc:
            raise BridgeError("the bridge greeting is not JSON") from exc
        if not isinstance(message, dict):
            raise BridgeError("the bridge greeting is not an object")
        return message

    def _read_loop(self) -> None:
        """Parse bounded transient frames and count rejected frames without leaking payloads."""
        buffer = b""
        discarding = False
        while not self._closed.is_set():
            try:
                chunk = self._sock.recv(65536)
            except OSError:
                return
            if not chunk:
                return
            buffer += chunk
            if discarding:
                # Still inside an oversized line: drop through its newline, once.
                newline = buffer.find(b"\n")
                if newline < 0:
                    buffer = b""
                    continue
                buffer = buffer[newline + 1:]
                discarding = False
            while b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                if not line.strip():
                    continue
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
                try:
                    self._queue.put_nowait(message)
                except queue.Full:
                    # Keep queued frames; exactly one incoming frame was rejected.
                    self.dropped += 1
            if len(buffer) > MAX_LINE_BYTES:
                discarding = True
                self.dropped += 1
                buffer = b""

    def _note_unparseable(self) -> None:
        """Hand the consumer the fact of a bad line without the bad line itself."""
        try:
            self._queue.put_nowait(dict(UNPARSEABLE))
        except queue.Full:
            self.dropped += 1

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


def free_port(host: str = "127.0.0.1") -> int:
    """A loopback port the runtime's bridge plugin can bind a moment later."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind((host, 0))
        return int(probe.getsockname()[1])
