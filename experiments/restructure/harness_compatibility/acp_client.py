"""Minimal Agent Client Protocol (ACP) v1 stdio client for the Harness spike.

Experiment only. It speaks newline-delimited JSON-RPC 2.0 to
`deepseek-harness-sdk-runtime-<platform>-<arch>.exe --profile acp` and is deliberately
small: one reader thread, one writer lock, blocking calls with a timeout, one queue per
in-flight turn, and a hook for server-to-client requests (`session/request_permission`).

Nothing here is a second event loop or a cancellation engine: `cancel()` sends the
protocol's own `session/cancel` notification and waits for the prompt response the
server already owes. `TurnHandle` exists so two turns can run at the same time and
each still owns its own `session/update` stream.
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Callable


class AcpError(RuntimeError):
    def __init__(self, code: Any, message: str, data: Any = None):
        super().__init__(f"{message} (code={code})")
        self.code = code
        self.data = data


class TurnHandle:
    """One in-flight `session/prompt`; owns that session's updates until it settles."""

    def __init__(self, session_id: str, started: float):
        self.session_id = session_id
        self.started = started
        self.updates: list[dict] = []
        self.result: dict | None = None
        self.error: BaseException | None = None
        self._done = threading.Event()

    def accept(self, update: dict) -> None:
        self.updates.append({"t": time.perf_counter(), "update": update})

    def finish(self, result: dict | None, error: BaseException | None) -> None:
        self.result = result
        self.error = error
        self._done.set()

    def wait(self, timeout: float = 180.0) -> bool:
        return self._done.wait(timeout)

    @property
    def done(self) -> bool:
        return self._done.is_set()

    @property
    def elapsed(self) -> float:
        return time.perf_counter() - self.started

    @property
    def stop_reason(self) -> str | None:
        return None if not isinstance(self.result, dict) else self.result.get("stopReason")


class AcpClient:
    def __init__(self, exe: str, *, env: dict[str, str], cwd: str,
                 on_request: Callable[["AcpClient", str, Any, Any], Any] | None = None,
                 extra_args: list[str] | None = None, stderr_path: Path | None = None):
        self._exe = exe
        self._cwd = cwd
        self._on_request = on_request
        self._stderr_path = stderr_path
        self._proc = subprocess.Popen(
            [exe, "--profile", "acp", *(extra_args or [])],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", bufsize=1, cwd=cwd, env=env,
        )
        self._lock = threading.Lock()
        self._responses: dict[str, queue.Queue] = {}
        self._notifications: queue.Queue = queue.Queue()
        self._turns: dict[str, TurnHandle] = {}
        self._stderr: list[str] = []
        self._next_id = 0
        self._id_lock = threading.Lock()
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()
        self._err_reader = threading.Thread(target=self._err_loop, daemon=True)
        self._err_reader.start()

    # -- transport -------------------------------------------------------
    def _read_loop(self) -> None:
        assert self._proc.stdout is not None
        for line in self._proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                self._stderr.append("stdout non-json: " + line[:200])
                continue
            if "id" in message and ("result" in message or "error" in message):
                waiter = self._responses.get(str(message["id"]))
                if waiter is not None:
                    waiter.put(message)
                continue
            if "method" in message and "id" in message:
                threading.Thread(target=self._answer_request, args=(message,), daemon=True).start()
                continue
            if message.get("method") == "session/update":
                params = message.get("params") or {}
                handle = self._turns.get(str(params.get("sessionId")))
                if handle is not None:
                    handle.accept(params.get("update") or {})
                    continue
            self._notifications.put(message)

    def _err_loop(self) -> None:
        assert self._proc.stderr is not None
        lines: list[str] = []
        for line in self._proc.stderr:
            lines.append(line.rstrip())
            if self._stderr_path is not None:
                with self._stderr_path.open("a", encoding="utf-8") as handle:
                    handle.write(line)
        self._stderr = lines

    def _answer_request(self, message: dict) -> None:
        method, params, request_id = message.get("method"), message.get("params") or {}, message.get("id")
        try:
            result = self._on_request(self, method, params, message) if self._on_request else None
            if result is None:
                self._write({"jsonrpc": "2.0", "id": request_id,
                             "error": {"code": -32601, "message": f"unsupported by spike client: {method}"}})
            else:
                self._write({"jsonrpc": "2.0", "id": request_id, "result": result})
        except Exception as exc:  # reported, never swallowed silently
            self._stderr.append(f"request handler failed: {exc!r}")
            self._write({"jsonrpc": "2.0", "id": request_id,
                         "error": {"code": -32603, "message": str(exc)}})

    def _write(self, message: dict) -> None:
        with self._lock:
            assert self._proc.stdin is not None
            self._proc.stdin.write(json.dumps(message, separators=(",", ":")) + "\n")
            self._proc.stdin.flush()

    def call(self, method: str, params: Any, timeout: float = 120.0) -> Any:
        with self._id_lock:
            self._next_id += 1
            request_id = f"spike-{self._next_id}"
        waiter: queue.Queue = queue.Queue(maxsize=1)
        self._responses[request_id] = waiter
        self._write({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
        try:
            message = waiter.get(timeout=timeout)
        except queue.Empty:
            raise TimeoutError(f"{method} timed out after {timeout}s") from None
        finally:
            self._responses.pop(request_id, None)
        if "error" in message:
            error = message["error"] or {}
            raise AcpError(error.get("code"), str(error.get("message")), error.get("data"))
        return message.get("result")

    def notify(self, method: str, params: Any) -> None:
        self._write({"jsonrpc": "2.0", "method": method, "params": params})

    def stderr_tail(self, limit: int = 20) -> list[str]:
        return self._stderr[-limit:]

    def close(self) -> None:
        try:
            if self._proc.stdin:
                self._proc.stdin.close()
        except Exception:
            pass
        try:
            self._proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self._proc.kill()
            self._proc.wait(timeout=5)

    # -- ACP surface -----------------------------------------------------
    def initialize(self) -> dict:
        return self.call("initialize", {
            "protocolVersion": 1,
            "clientCapabilities": {"fs": {"readTextFile": False, "writeTextFile": False}, "terminal": False},
            "clientInfo": {"name": "odessy-spike", "version": "0.1"},
        })

    def new_session(self, cwd: str | None = None, mcp_servers: list | None = None) -> dict:
        return self.call("session/new", {"cwd": cwd or self._cwd, "mcpServers": mcp_servers or []})

    def resume_session(self, session_id: str, cwd: str | None = None) -> dict:
        return self.call("session/resume", {"sessionId": session_id, "cwd": cwd or self._cwd, "mcpServers": []})

    def close_session(self, session_id: str) -> Any:
        return self.call("session/close", {"sessionId": session_id})

    def set_config_option(self, session_id: str, config_id: str, value: str) -> dict:
        return self.call("session/set_config_option", {"sessionId": session_id, "configId": config_id, "value": value})

    def cancel(self, session_id: str) -> None:
        """The ACP cancellation path: a notification, not a request."""
        self.notify("session/cancel", {"sessionId": session_id})

    def prompt_async(self, session_id: str, text: str, *, timeout: float = 180.0) -> TurnHandle:
        handle = TurnHandle(session_id, time.perf_counter())
        self._turns[session_id] = handle

        def run() -> None:
            try:
                result = self.call("session/prompt", {
                    "sessionId": session_id,
                    "prompt": [{"type": "text", "text": text}],
                }, timeout=timeout)
                error = None
            except BaseException as exc:  # surfaced to the caller
                result, error = None, exc
            finally:
                if self._turns.get(session_id) is handle:
                    self._turns.pop(session_id, None)
            handle.finish(result or {} if error is None else None, error)

        threading.Thread(target=run, daemon=True).start()
        return handle

    def prompt(self, session_id: str, text: str, *, timeout: float = 180.0) -> TurnHandle:
        handle = self.prompt_async(session_id, text, timeout=timeout)
        handle.wait(timeout + 5)
        return handle


def base_env(home: Path, base_url: str, *, permission_mode: str | None = None,
             bridge: tuple[int, str] | None = None,
             extra: dict[str, str] | None = None) -> dict[str, str]:
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)          # no checkout imports
    env.pop("PYTHONHOME", None)
    env["DSH_HOME"] = str(home)
    env["DEEPSEEK_BASE_URL"] = base_url
    env["DEEPSEEK_API_KEY"] = "spike-not-a-real-key"
    env["NO_COLOR"] = "1"
    if permission_mode:
        env["DSH_PERMISSION_MODE"] = permission_mode
    if bridge is not None:
        env["ODESSY_BRIDGE_PORT"] = str(bridge[0])
        env["ODESSY_BRIDGE_TOKEN"] = bridge[1]
    if extra:
        env.update(extra)
    return env
