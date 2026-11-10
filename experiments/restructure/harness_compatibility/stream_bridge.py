"""Client for the spike stream bridge plugin (`plugin/odessy-stream-bridge`).

The plugin forwards the runtime's own `agent/assistant-stream` frames to a
token-authenticated loopback TCP socket. This client authenticates, collects frames
with receive timestamps, and exposes them per session. It is a fixture, not a
production transport: the product decision it informs is recorded in
docs/restructure/harness-integration-contract.md §8.
"""

from __future__ import annotations

import json
import queue
import socket
import threading
import time


class BridgeClient:
    def __init__(self, host: str, port: int, token: str, *, connect_timeout: float = 10.0):
        self.frames: list[dict] = []
        self._queue: queue.Queue = queue.Queue()
        deadline = time.monotonic() + connect_timeout
        last_error: Exception | None = None
        while time.monotonic() < deadline:
            try:
                self._sock = socket.create_connection((host, port), timeout=2.0)
                break
            except OSError as exc:  # the runtime may still be booting
                last_error = exc
                time.sleep(0.1)
        else:
            raise TimeoutError(f"bridge did not accept a connection: {last_error}")
        self._sock.settimeout(None)
        self._sock.sendall((json.dumps({"token": token}) + "\n").encode())
        self._buffer = b""
        self.hello = self._read_one(timeout=5.0)
        if self.hello.get("type") != "hello":
            raise RuntimeError(f"bridge rejected the token or protocol: {self.hello}")
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()

    def _read_one(self, *, timeout: float) -> dict:
        self._sock.settimeout(timeout)
        try:
            while b"\n" not in self._buffer:
                chunk = self._sock.recv(65536)
                if not chunk:
                    raise ConnectionError("bridge closed")
                self._buffer += chunk
        finally:
            self._sock.settimeout(None)
        line, self._buffer = self._buffer.split(b"\n", 1)
        return json.loads(line)

    def _read_loop(self) -> None:
        try:
            while True:
                message = self._read_one_blocking()
                if message is None:
                    return
                message["_received"] = time.perf_counter()
                self.frames.append(message)
                self._queue.put(message)
        except Exception:
            return

    def _read_one_blocking(self) -> dict | None:
        while b"\n" not in self._buffer:
            chunk = self._sock.recv(65536)
            if not chunk:
                return None
            self._buffer += chunk
        line, self._buffer = self._buffer.split(b"\n", 1)
        return json.loads(line)

    def text_deltas(self, session_id: str) -> list[dict]:
        return [
            frame for frame in self.frames
            if frame.get("type") == "frame"
            and frame.get("sessionId") == session_id
            and (frame.get("chunk") or {}).get("type") == "text-delta"
        ]

    def close(self) -> None:
        try:
            self._sock.close()
        except OSError:
            pass


def free_port(host: str = "127.0.0.1") -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind((host, 0))
        return probe.getsockname()[1]
