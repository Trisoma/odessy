"""The supervised Harness child process: stdio framing, failures, and its own tree.

Everything that can block — spawning, reading a frame, waiting for a reply, shutting
down — happens on a worker thread. The application event loop only ever awaits
:func:`asyncio.to_thread` (see ``runtime.HarnessRuntime``), so a wedged or crashed
runtime can never freeze the session's loop.

Protocol recovery rules:
- a line that is not JSON, or JSON that is not an object, is *diagnosed* and skipped;
  it is not allowed to kill a session that is otherwise healthy;
- a reply with an unknown id is diagnosed, never matched to the wrong request;
- a reply with no result object, or an error, fails that call with
  :class:`HarnessProtocolError`;
- losing the stream (EOF, crash) fails every pending call with
  :class:`HarnessTransportError` carrying the exit code;
- a server-initiated request is handed to the owner's ``on_request`` callback, which
  must answer it exactly once through :meth:`HarnessProcess.respond`. Without a
  callback — or for a request the callback cannot own — it is **refused** with
  ``-32601`` and the refusal is visible in the diagnostics.

The reader thread must never wait for a person: a callback that needs an answer from
somewhere else schedules that work and returns, so protocol traffic keeps flowing.
"""

from __future__ import annotations

import collections
import json
import os
import queue
import signal
import subprocess
import sys
import threading
import time
from typing import Any, Callable, Mapping, Sequence

from odessy.authority.redaction import redact_text
from odessy.runtime_backends.harness.config import HarnessConfig
from odessy.runtime_backends.harness.job import JobObject

_CREATE_NEW_PROCESS_GROUP = 0x00000200
_CREATE_NO_WINDOW = 0x08000000
_MAX_FRAME_CHARS = 400
#: Internal marker: a pending call must fail with the transport error, not parse
#: this as a protocol reply. Never sent to or received from the child.
_TRANSPORT_FAILURE = "__odessy_transport_failure__"
#: The ACP contract's fail-closed permission answer. Used only when a caller's response
#: guard refuses or itself fails; it is the same literal ``approvals`` writes.
_FAIL_CLOSED_ANSWER: dict[str, Any] = {"result": {"outcome": {"outcome": "cancelled"}}}


class HarnessProcessError(RuntimeError):
    """Base class for child-process failures."""

    code = "runtime_process_error"


class HarnessTransportError(HarnessProcessError):
    """The stream ended or a write failed: the child is gone."""

    code = "runtime_transport_error"

    def __init__(self, message: str, *, exit_code: int | None = None,
                 diagnostics: Sequence[str] = ()):  # noqa: D401 - plain data
        """Initialize the owned state and synchronization used by this component."""
        self.exit_code = exit_code
        self.diagnostics = list(diagnostics)
        detail = f" (exit code {exit_code})" if exit_code is not None else ""
        super().__init__(f"{message}{detail}")


class HarnessProtocolError(HarnessProcessError):
    """A well-formed transport carried an unusable message.

    ``rpc_code`` and ``remote_detail`` carry what the runtime actually said when it *refused*
    a request, so a caller can classify the refusal (a missing session is not a corrupt one)
    without parsing prose.
    """

    code = "runtime_protocol_error"

    def __init__(self, message: str, *, rpc_code: int | None = None,
                 remote_detail: str = ""):
        self.rpc_code = rpc_code
        self.remote_detail = remote_detail
        super().__init__(message)


def _clip(text: str) -> str:
    """Redact and bound a diagnostic before retaining or exposing it."""
    text = redact_text(text)
    return text if len(text) <= _MAX_FRAME_CHARS else text[:_MAX_FRAME_CHARS] + "…"


class HarnessProcess:
    """One supervised child. Not reusable: construct a new instance per session."""

    def __init__(self, config: HarnessConfig, *,
                 on_request: Callable[[Any, str, Mapping[str, Any]], None] | None = None,
                 on_notification: Callable[[Mapping[str, Any]], None] | None = None):
        self.config = config
        self._on_request = on_request
        self._on_notification = on_notification
        self._process: subprocess.Popen[str] | None = None
        self._reader: threading.Thread | None = None
        self._stderr_reader: threading.Thread | None = None
        self._pending: dict[int, queue.Queue[dict[str, Any]]] = {}
        self._notifications: list[dict[str, Any]] = []
        self._refused: list[dict[str, Any]] = []
        #: Server-request ids already answered or refused. A second answer is a bug in the
        #: caller, and writing it would be a second decision for one ask.
        self._answered: collections.OrderedDict[Any, None] = collections.OrderedDict()
        self._malformed = 0
        self._diagnostics: collections.deque[str] = collections.deque(
            maxlen=config.diagnostic_lines
        )
        self._state_lock = threading.RLock()
        self._write_lock = threading.Lock()
        self._next_id = 0
        self._closing = False
        self._closed = threading.Event()
        self._dead: HarnessTransportError | None = None
        self._exit_code: int | None = None
        self._job: JobObject | None = None
        self._pgid: int | None = None

    # ------------------------------------------------------------------ properties

    @property
    def pid(self) -> int | None:
        """Return the owned child PID, if a child was started."""
        return None if self._process is None else self._process.pid

    @property
    def exit_code(self) -> int | None:
        """Return the most recently observed child exit status."""
        return self._exit_code

    @property
    def alive(self) -> bool:
        """Report whether the direct child is still running."""
        return self._process is not None and self._process.poll() is None

    @property
    def malformed_frames(self) -> int:
        """Return the number of rejected malformed transport frames."""
        return self._malformed

    def diagnostics(self) -> list[str]:
        """Return a bounded snapshot of content-safe runtime diagnostics."""
        with self._state_lock:
            return list(self._diagnostics)

    def note(self, line: str) -> None:
        """Record a bounded, redacted diagnostic line owned by the caller."""
        self._add_diagnostic(line)

    def take_notifications(self) -> list[dict[str, Any]]:
        with self._state_lock:
            taken, self._notifications = self._notifications, []
            return taken

    def refused_requests(self) -> list[dict[str, Any]]:
        with self._state_lock:
            return list(self._refused)

    def answered_requests(self) -> list[Any]:
        """Ids this process answered, in order. Evidence, never authority."""
        with self._state_lock:
            return list(self._answered)

    # ------------------------------------------------------------ server requests

    def respond(self, request_id: Any, payload: Mapping[str, Any], *,
                guard: Callable[[Mapping[str, Any]], Mapping[str, Any] | None] | None = None
                ) -> bool:
        """Write the one response for a server request. False if it was already answered.

        Thread-safe and non-raising on a closed child: a response that can no longer be
        delivered is reported by a diagnostic, not by failing the turn that raised it.

        ``guard`` runs after acquiring the write lock, before serialization, and may replace the
        payload, or return ``None`` to fail closed. It exists because the caller's own
        check can be arbitrarily stale by then (a blocked pipe, a paused thread): an
        answer that grants authority must be re-validated at the wire, not only when it
        was decided.
        """
        with self._state_lock:
            if self._closing or self._dead is not None:
                return False
            if request_id in self._answered:
                self._add_diagnostic(f"ignored a second response for request id={request_id}")
                return False
            self._answered[request_id] = None
            while len(self._answered) > 128:
                self._answered.popitem(last=False)
        message = {"jsonrpc": "2.0", "id": request_id}
        message.update(dict(payload))

        def prepare_response() -> Mapping[str, Any]:
            # This executes only after _write acquired its lock. An allow may have
            # become stale while another writer occupied that lock.
            try:
                checked = guard(dict(payload))
            except Exception as exc:  # noqa: BLE001 - a failed guard must never authorize
                self._add_diagnostic(f"response guard failed: {type(exc).__name__}")
                checked = None
            guarded = {"jsonrpc": "2.0", "id": request_id}
            guarded.update(dict(_FAIL_CLOSED_ANSWER if checked is None else checked))
            return guarded

        try:
            if guard is None:
                self._write(message)
            else:
                self._write(message, prepare=prepare_response)
        except HarnessProcessError as exc:
            self._add_diagnostic(
                f"could not answer server request id={request_id}: {type(exc).__name__}")
            return False
        return True

    def refuse(self, request_id: Any, method: str) -> bool:
        """Refuse a server request this build does not implement (never a silent ignore)."""
        with self._state_lock:
            self._refused.append({"method": method, "id": request_id})
        self._add_diagnostic(f"refused server request {method}")
        return self.respond(request_id, {
            "error": {
                "code": -32601,
                "message": f"Odessy does not handle {method} at this stage",
            }
        })

    # ---------------------------------------------------------------------- start

    def start(self) -> None:
        """Start the explicitly configured child and establish process-tree ownership."""
        if self._process is not None:
            raise RuntimeError("the Harness process was already started")
        argv = self.config.argv()
        env = self.config.child_env()
        creationflags = 0
        start_new_session = False
        if sys.platform == "win32":
            creationflags = _CREATE_NEW_PROCESS_GROUP | _CREATE_NO_WINDOW
        else:
            start_new_session = True
        self._process = subprocess.Popen(
            argv,
            cwd=str(self.config.workspace),
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            creationflags=creationflags,
            start_new_session=start_new_session,
        )
        self._own_process_tree()
        self._add_diagnostic(f"spawned pid={self._process.pid} profile={self.config.profile}")
        self._reader = threading.Thread(target=self._read_stdout, name="harness-stdout",
                                        daemon=True)
        self._stderr_reader = threading.Thread(target=self._read_stderr, name="harness-stderr",
                                               daemon=True)
        self._reader.start()
        self._stderr_reader.start()

    # ----------------------------------------------------------------------- calls

    def call(self, method: str, params: Mapping[str, Any] | None = None, *,
             timeout: float, on_sent: Callable[[], None] | None = None) -> dict[str, Any]:
        """Send one request and wait for its reply. Blocking: call from a worker.

        ``on_sent`` runs once the request has been written to the child, before the reply is
        awaited. A caller that must not act before the child has the request (a cancellation
        sent before its prompt would be dropped) can use it instead of guessing.
        """
        dead = self._dead
        if dead is not None:
            raise dead
        if self._process is None:
            raise RuntimeError("the Harness process is not running")
        with self._state_lock:
            self._next_id += 1
            message_id = self._next_id
            waiter: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=1)
            self._pending[message_id] = waiter
        payload = {"jsonrpc": "2.0", "id": message_id, "method": method}
        if params is not None:
            payload["params"] = dict(params)
        try:
            self._write(payload)
        except HarnessProcessError:
            with self._state_lock:
                self._pending.pop(message_id, None)
            raise
        if on_sent is not None:
            # The request is in the child's pipe now. A failure here is the caller's problem
            # to report, not a reason to abandon a request that is already in flight.
            try:
                on_sent()
            except Exception:  # noqa: BLE001 - never fails a sent request
                pass
        try:
            reply = waiter.get(timeout=timeout)
        except queue.Empty:
            with self._state_lock:
                self._pending.pop(message_id, None)
            dead = self._dead
            if dead is not None:
                raise dead from None
            raise HarnessProtocolError(
                f"{method} got no reply within {timeout:g}s"
            ) from None
        if _TRANSPORT_FAILURE in reply:
            raise (self._dead or HarnessTransportError(str(reply[_TRANSPORT_FAILURE])))
        if "error" in reply and reply.get("error") is not None:
            error = reply.get("error")
            detail = error.get("message") if isinstance(error, Mapping) else str(error)
            code = error.get("code") if isinstance(error, Mapping) else None
            raise HarnessProtocolError(f"{method} was refused ({code}): {detail}",
                                       rpc_code=code if isinstance(code, int) else None,
                                       remote_detail=detail)
        result = reply.get("result")
        if not isinstance(result, dict):
            raise HarnessProtocolError(
                f"{method} returned {type(result).__name__}, expected an object"
            )
        return result

    def notify(self, method: str, params: Mapping[str, Any] | None = None) -> None:
        """Write one JSON-RPC notification without registering a reply waiter."""
        payload = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            payload["params"] = dict(params)
        self._write(payload)

    def _write(self, payload: Mapping[str, Any], *,
               prepare: Callable[[], Mapping[str, Any]] | None = None) -> None:
        process = self._process
        if process is None or process.stdin is None:
            raise HarnessTransportError("the Harness process is not running")
        with self._write_lock:
            if prepare is not None:
                payload = prepare()
            line = json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"
            try:
                process.stdin.write(line)
                process.stdin.flush()
            except (BrokenPipeError, OSError, ValueError) as exc:
                raise HarnessTransportError(
                    f"writing to the Harness process failed: {type(exc).__name__}",
                    exit_code=process.poll(), diagnostics=self.diagnostics(),
                ) from exc

    # ---------------------------------------------------------------------- reads

    def _read_stdout(self) -> None:
        """Read bounded protocol lines and dispatch valid frames to their owners."""
        stream = self._process.stdout  # type: ignore[union-attr]
        try:
            for line in stream:
                text = line.strip()
                if not text:
                    continue
                try:
                    message = json.loads(text)
                except ValueError:
                    self._malformed += 1
                    self._add_diagnostic(f"unparseable frame: {_clip(text)}")
                    continue
                if not isinstance(message, dict):
                    self._malformed += 1
                    self._add_diagnostic(f"non-object frame: {_clip(text)}")
                    continue
                self._dispatch(message)
        except (ValueError, OSError) as exc:
            self._add_diagnostic(f"stdout reader stopped: {type(exc).__name__}: {_clip(str(exc))}")
        finally:
            self._on_stream_end("stdout")

    def _read_stderr(self) -> None:
        """Retain bounded redacted diagnostics without blocking the protocol reader."""
        stream = self._process.stderr  # type: ignore[union-attr]
        try:
            for line in stream:
                text = line.strip()
                if text:
                    self._add_diagnostic(f"stderr: {_clip(text)}")
        except (ValueError, OSError):
            pass

    def _dispatch(self, message: dict[str, Any]) -> None:
        """Route a validated JSON-RPC message to its waiter, observer or refusal handler."""
        if "method" in message:
            if "id" in message:
                self._on_server_request(message.get("id"), str(message.get("method")),
                                        message.get("params") or {})
                return
            else:
                with self._state_lock:
                    self._notifications.append(message)
                self._observe(message)
                return
        if "id" not in message:
            return
        reply_id = message.get("id")
        if not isinstance(reply_id, int):
            self._malformed += 1
            self._add_diagnostic(f"reply with unusable id: {_clip(json.dumps(message))}")
            return
        if "result" not in message and "error" not in message:
            self._malformed += 1
            self._add_diagnostic(f"reply without result: {_clip(json.dumps(message))}")
            return
        with self._state_lock:
            waiter = self._pending.pop(reply_id, None)
        if waiter is None:
            self._add_diagnostic(f"reply for unknown request id={reply_id}")
            return
        waiter.put(message)

    def _on_server_request(self, request_id: Any, method: str,
                           params: Mapping[str, Any]) -> None:
        """Hand a server request to the owner, or refuse it. Never blocks the reader."""
        handler = self._on_request
        if handler is None or self._closing:
            self.refuse(request_id, method)
            return
        try:
            handler(request_id, method, params)
        except Exception as exc:  # noqa: BLE001 - a broken handler is a refusal, not a crash
            self._add_diagnostic(
                f"server request handler failed for {method}: {type(exc).__name__}")
            self.refuse(request_id, method)

    def _observe(self, message: Mapping[str, Any]) -> None:
        """Feed a notification to the owner live; the queued copy still serves the turn."""
        handler = self._on_notification
        if handler is None:
            return
        try:
            handler(message)
        except Exception as exc:  # noqa: BLE001 - an observer must never kill the reader
            self._add_diagnostic(f"notification observer failed: {type(exc).__name__}")

    def _on_stream_end(self, which: str) -> None:
        """Wake pending waiters when a transport ends, including during closure."""
        process = self._process
        exit_code: int | None = None
        if process is not None:
            try:
                exit_code = process.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                exit_code = None
        with self._state_lock:
            self._exit_code = exit_code
            if self._dead is None and not self._closing:
                self._dead = HarnessTransportError(
                    f"the Harness process {which} ended unexpectedly",
                    exit_code=exit_code, diagnostics=self.diagnostics(),
                )
            failure = self._dead or HarnessTransportError("the Harness process closed", exit_code=exit_code)
            pending, self._pending = self._pending, {}
        self._add_diagnostic(f"{which} ended (exit={exit_code})")
        for waiter in pending.values():
            waiter.put({_TRANSPORT_FAILURE: str(failure)})

    def _add_diagnostic(self, line: str) -> None:
        """Append one already bounded diagnostic to the retained history."""
        with self._state_lock:
            self._diagnostics.append(line)

    # ----------------------------------------------------------------- close/kill

    def close(self) -> None:
        """Graceful EOF, then terminate, then kill the whole tree. Idempotent."""
        with self._state_lock:
            first = not self._closing
            self._closing = True
        if not first:
            self._closed.wait(self.config.shutdown_timeout + 5.0)
            return
        deadline = time.monotonic() + self.config.shutdown_timeout
        try:
            process = self._process
            if process is not None and process.poll() is None:
                if self._job is None and (sys.platform == "win32" or self._pgid is None):
                    # Without a job object a graceful exit can orphan descendants: the
                    # runtime ends on EOF and its children keep running, out of reach of
                    # `taskkill /T` because the parent pid is gone. Killing while the
                    # parent is still alive is the only order that guarantees cleanup, so
                    # the graceful path is skipped and the loss is recorded.
                    self._add_diagnostic(
                        "no job object: killing the tree before EOF "
                        "(a graceful exit could orphan descendants)"
                    )
                    self._kill_tree()
                else:
                    self._add_diagnostic("closing stdin (graceful shutdown)")
                    acquired = self._write_lock.acquire(timeout=max(0, deadline - time.monotonic()))
                    if not acquired:
                        self._add_diagnostic("shutdown writer is blocked; killing the owned tree")
                        self._kill_tree()
                    else:
                        try:
                            if process.stdin is not None:
                                process.stdin.close()
                        except OSError:
                            pass
                        finally:
                            self._write_lock.release()
                    try:
                        process.wait(timeout=max(0.1, deadline - time.monotonic()))
                    except subprocess.TimeoutExpired:
                        self._add_diagnostic("graceful shutdown timed out; terminating")
                        self._terminate()
        finally:
            self._kill_tree()
            for thread in (self._reader, self._stderr_reader):
                if thread is not None:
                    thread.join(timeout=5.0)
            process = self._process
            if process is not None:
                with self._state_lock:
                    self._exit_code = process.poll()
            self._add_diagnostic(f"closed (exit={self._exit_code})")
            self._closed.set()

    def _terminate(self) -> None:
        """Request direct-child termination when graceful shutdown cannot finish."""
        process = self._process
        if process is None or process.poll() is not None:
            return
        try:
            process.terminate()
            process.wait(timeout=2.0)
        except (OSError, subprocess.TimeoutExpired):
            pass

    def _own_process_tree(self) -> None:
        """Make later cleanup cover descendants the child may have created."""
        process = self._process
        if process is None:
            return
        if sys.platform == "win32":
            job = JobObject()
            if job.available and job.adopt(process.pid):
                self._job = job
                self._add_diagnostic("process tree owned by a job object")
            else:
                if job.available:
                    job.close()
                self._add_diagnostic(f"no job object ({job.reason}); taskkill will be used")
        else:
            try:
                self._pgid = os.getpgid(process.pid)
            except OSError:
                self._pgid = None

    def _kill_tree(self) -> None:
        """Kill the child *and anything it spawned*, not just the direct process."""
        job, self._job = self._job, None
        if job is not None:
            self._add_diagnostic("closing the job object (tree kill)")
            job.close()
        process = self._process
        if process is None:
            return
        if sys.platform != "win32" and self._pgid is not None:
            # The group outlives the direct child, so this also reaches orphans.
            try:
                os.killpg(self._pgid, signal.SIGKILL)
            except OSError:
                pass
        if process.poll() is not None:
            return
        self._add_diagnostic("killing the process tree")
        try:
            if sys.platform == "win32":
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(process.pid)],
                               capture_output=True, check=False, timeout=10)
        except (OSError, subprocess.SubprocessError):
            pass
        try:
            process.kill()
        except OSError:
            pass
        try:
            process.wait(timeout=5.0)
        except subprocess.TimeoutExpired:
            pass

    def wait_for_exit(self, timeout: float) -> int | None:
        """Wait up to the caller bound and return the observed child exit code."""
        process = self._process
        if process is None:
            return None
        try:
            return process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            return None
