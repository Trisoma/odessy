"""Real two-process transport tests: odessy-link connect ⇄ mutual TLS ⇄ odessy-link serve.

The peer is a counting stub server, so every assertion about how many calls executed is
about what the host actually forwarded, not what the client believes. Two stubs also
start requests of their own, so the node-to-peer direction is exercised as well as the
peer-to-node one.

Opt-in, because it needs a built ``odessy-link``::

    ODESSY_LINK_E2E=1 python -m pytest tests/test_paired_transport.py -q
"""

from __future__ import annotations

import json
import os
import socket
import sqlite3
import subprocess
import threading
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
BINARY_NAME = "odessy-link.exe" if os.name == "nt" else "odessy-link"
BINARIES = (
    REPO / "native" / "odessy-link" / "target" / "release" / BINARY_NAME,
    REPO / "native" / "odessy-link" / "target" / "debug" / BINARY_NAME,
)

STUB = r"""
const fs = require("fs");
const counter = "__COUNTER__";
process.stdin.setEncoding("utf8");
let buffer = "";
process.stdin.on("data", (chunk) => {
  buffer += chunk;
  let index;
  while ((index = buffer.indexOf("\n")) >= 0) {
    const line = buffer.slice(0, index);
    buffer = buffer.slice(index + 1);
    if (!line.trim()) continue;
    const message = JSON.parse(line);
    if (message.id === undefined) continue;
    const seen = fs.existsSync(counter) ? Number(fs.readFileSync(counter, "utf8")) : 0;
    fs.writeFileSync(counter, String(seen + 1));
    process.stdout.write(JSON.stringify({jsonrpc: "2.0", id: message.id, result: {ok: true}}) + "\n");
  }
});
"""

STUB_WITH_CHILD = r"""
const fs = require("fs");
const { spawn } = require("child_process");
const counter = "__COUNTER__";
const childPidFile = "__CHILD_PID__";
// A long-lived grandchild: the host must terminate the tree, not just this process.
const child = spawn(process.execPath, ["-e", "setInterval(() => {}, 1000)"], { stdio: "ignore" });
fs.writeFileSync(childPidFile, String(child.pid));
process.stdin.setEncoding("utf8");
let buffer = "";
process.stdin.on("data", (chunk) => {
  buffer += chunk;
  let index;
  while ((index = buffer.indexOf("\n")) >= 0) {
    const line = buffer.slice(0, index);
    buffer = buffer.slice(index + 1);
    if (!line.trim()) continue;
    const message = JSON.parse(line);
    if (message.id === undefined) continue;
    fs.writeFileSync(counter, "1");
    process.stdout.write(JSON.stringify({jsonrpc: "2.0", id: message.id, result: {ok: true}}) + "\n");
  }
});
"""

# Answers every request *and* starts one of its own with the same numeric id, so the two
# directions carry equal ids at the same time. Answers it receives are counted, which is
# what proves the second direction's requests were released rather than leaked.
STUB_BIDIRECTIONAL = r"""
const fs = require("fs");
const counter = "__COUNTER__";
const answers = "__ANSWERS__";
process.stdin.setEncoding("utf8");
let buffer = "";
let answered = 0;
function send(message) {
  process.stdout.write(JSON.stringify(message) + "\n");
}
process.stdin.on("data", (chunk) => {
  buffer += chunk;
  let index;
  while ((index = buffer.indexOf("\n")) >= 0) {
    const line = buffer.slice(0, index);
    buffer = buffer.slice(index + 1);
    if (!line.trim()) continue;
    const message = JSON.parse(line);
    if (message.id === undefined) continue;
    if (message.method !== undefined) {
      const seen = fs.existsSync(counter) ? Number(fs.readFileSync(counter, "utf8")) : 0;
      fs.writeFileSync(counter, String(seen + 1));
      send({jsonrpc: "2.0", id: message.id, result: {ok: true}});
      // Same id, opposite direction: the namespaces have to stay independent.
      send({jsonrpc: "2.0", id: message.id, method: "node/ping", params: {}});
    } else {
      answered += 1;
      fs.writeFileSync(answers, String(answered));
    }
  }
});
"""

# Answers the first request, then starts 33 requests of its own without waiting: the
# node-to-peer direction must allow 32 outstanding and refuse the 33rd.
STUB_NODE_FLOOD = r"""
const fs = require("fs");
const counter = "__COUNTER__";
const answers = "__ANSWERS__";
process.stdin.setEncoding("utf8");
let buffer = "";
let answered = 0;
let flooded = false;
function send(message) {
  process.stdout.write(JSON.stringify(message) + "\n");
}
process.stdin.on("data", (chunk) => {
  buffer += chunk;
  let index;
  while ((index = buffer.indexOf("\n")) >= 0) {
    const line = buffer.slice(0, index);
    buffer = buffer.slice(index + 1);
    if (!line.trim()) continue;
    const message = JSON.parse(line);
    if (message.id === undefined) continue;
    if (message.method !== undefined) {
      const seen = fs.existsSync(counter) ? Number(fs.readFileSync(counter, "utf8")) : 0;
      fs.writeFileSync(counter, String(seen + 1));
      send({jsonrpc: "2.0", id: message.id, result: {ok: true}});
      if (!flooded) {
        flooded = true;
        for (let id = 500; id < 533; id += 1) {
          send({jsonrpc: "2.0", id, method: "node/ping", params: {}});
        }
      }
    } else {
      answered += 1;
      fs.writeFileSync(answers, String(answered));
    }
  }
});
"""

pytestmark = pytest.mark.skipif(
    os.environ.get("ODESSY_LINK_E2E") != "1" or not any(path.is_file() for path in BINARIES),
    reason="set ODESSY_LINK_E2E=1 with a built odessy-link",
)


def _binary() -> Path:
    return next(path for path in BINARIES if path.is_file())


def _run(state: Path, *arguments: str) -> str:
    completed = subprocess.run(
        [str(_binary()), *arguments],
        capture_output=True,
        text=True,
        env={**os.environ, "ODESSY_LINK_STATE_DIR": str(state)},
        check=False,
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout
    return completed.stdout


def _fingerprint(bundle: Path) -> str:
    return json.loads(bundle.read_text(encoding="utf-8"))["fingerprint_sha256"]


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


class ClientSession:
    """A live `connect` process; replies are line-delimited JSON."""

    def __init__(self, pairing: "Pairing", timeout: float = 30.0):
        self.timeout = timeout
        #: Requests the peer started that this session has answered.
        self.answered_peer = 0
        self.process = subprocess.Popen(
            [str(pairing.binary), "connect", "--peer", "host",
             "--address", f"127.0.0.1:{pairing.port}"],
            env={**os.environ, "ODESSY_LINK_STATE_DIR": str(pairing.coordinator)},
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )

    def __enter__(self) -> "ClientSession":
        return self

    def __exit__(self, *_) -> None:
        self.close()

    def send(self, *lines: str) -> None:
        assert self.process.stdin is not None
        for line in lines:
            self.process.stdin.write(line + "\n")
        self.process.stdin.flush()

    def read(self) -> dict:
        """The next reply, answering any request the peer starts on the way."""
        assert self.process.stdout is not None
        while True:
            line = self.process.stdout.readline()
            if not line:
                raise AssertionError("connection closed before a reply arrived")
            message = json.loads(line)
            if "method" in message:
                self.answer(message["id"])
                continue
            return message

    def answer(self, identifier: object) -> None:
        self.send(json.dumps({"jsonrpc": "2.0", "id": identifier, "result": {"ok": True}}))
        self.answered_peer += 1

    def answer_remaining(self, expected: int) -> None:
        """Answer peer requests until `expected` of them have been answered.

        The last request the peer starts arrives after the last reply, so the loop that
        reads replies always stops one short.
        """
        deadline = time.monotonic() + self.timeout
        assert self.process.stdout is not None
        while self.answered_peer < expected and time.monotonic() < deadline:
            line = self.process.stdout.readline()
            if not line:
                break
            message = json.loads(line)
            if "method" in message:
                self.answer(message["id"])
        assert self.answered_peer == expected, (
            f"the peer started {expected} requests, answered {self.answered_peer}"
        )

    def read_frames(self) -> list[dict]:
        """Every frame until the host closes, without answering anything.

        Used to keep requests outstanding on purpose: answering one frees its slot, so
        a cap cannot be observed otherwise.
        """
        frames: list[dict] = []
        assert self.process.stdout is not None
        while True:
            line = self.process.stdout.readline()
            if not line:
                return frames
            frames.append(json.loads(line))

    def read_until_closed(self) -> list[dict]:
        """Replies until the peer closes, answering its requests so nothing stalls."""
        replies: list[dict] = []
        deadline = time.monotonic() + self.timeout
        assert self.process.stdout is not None
        while time.monotonic() < deadline:
            line = self.process.stdout.readline()
            if not line:
                return replies
            message = json.loads(line)
            if "method" in message:
                self.answer(message["id"])
                continue
            replies.append(message)
        raise AssertionError("the peer never closed the connection")

    def close(self, timeout: float | None = None) -> tuple[int, str]:
        if self.process.stdin and not self.process.stdin.closed:
            self.process.stdin.close()
        _, error = self.process.communicate(timeout=timeout or self.timeout)
        return self.process.returncode or 0, error or ""


class Pairing:
    """Two enrolled hosts, one serve process, and a counter of executed calls."""

    def __init__(
        self,
        root: Path,
        *,
        session_limit: int | None = None,
        grant_workspace: Path | None = None,
        stub: str = STUB,
    ):
        self.root = root
        self.binary = _binary()
        self.host = root / "host"
        self.coordinator = root / "coordinator"
        self.workspace = root / "workspace"
        self.grant_workspace = grant_workspace
        self.counter = root / "calls.txt"
        self.answers_file = root / "answers.txt"
        self.port = _free_port()
        self.session_limit = session_limit
        self.stub = stub
        self.server: subprocess.Popen[str] | None = None
        #: Everything serve has written to stderr, drained by a thread so a full pipe
        #: can never stall it and its reports can be asserted on.
        self.log: list[str] = []
        self._drain_thread: threading.Thread | None = None

    def __enter__(self) -> "Pairing":
        self.workspace.mkdir(parents=True)
        self.script = self.root / "stub-server.js"
        self.child_pid_file = self.root / "child.pid"
        self.script.write_text(
            self.stub.replace("__COUNTER__", str(self.counter).replace("\\", "\\\\"))
            .replace("__CHILD_PID__", str(self.child_pid_file).replace("\\", "\\\\"))
            .replace("__ANSWERS__", str(self.answers_file).replace("\\", "\\\\")),
            encoding="utf-8",
        )
        for state, bundle in (
            (self.host, self.root / "host.json"),
            (self.coordinator, self.root / "coordinator.json"),
        ):
            _run(state, "init")
            _run(state, "identity", "export", "--out", str(bundle))
        _run(self.host, "pair", "add", "--alias", "coordinator",
             "--certificate", str(self.root / "coordinator.json"),
             "--expect-sha256", _fingerprint(self.root / "coordinator.json"))
        _run(self.host, "peers", "grant", "coordinator",
             "--capability", "mcp", "--workspace", str(self.grant_workspace or self.workspace))
        _run(self.coordinator, "pair", "add", "--alias", "host",
             "--certificate", str(self.root / "host.json"),
             "--expect-sha256", _fingerprint(self.root / "host.json"),
             "--address", f"127.0.0.1:{self.port}")
        environment = {
            **os.environ,
            "ODESSY_LINK_STATE_DIR": str(self.host),
            "ODESSY_LINK_NODE": os.environ.get("ODESSY_LINK_NODE") or "node",
        }
        if self.session_limit is not None:
            environment["ODESSY_LINK_MAX_SESSION_ITEMS"] = str(self.session_limit)
        self.server = subprocess.Popen(
            [str(self.binary), "serve", "--listen", f"127.0.0.1:{self.port}",
             "--workspace", str(self.workspace), "--node-server", str(self.script)],
            env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        self._drain_thread = threading.Thread(target=self._drain, args=(self.server,), daemon=True)
        self._drain_thread.start()
        try:
            self._wait_for_port()
        except AssertionError:
            self.server.terminate()
            _, error = self.server.communicate(timeout=10)
            raise AssertionError(f"serve did not listen: {error}") from None
        return self

    def __exit__(self, *_) -> None:
        self.stop()

    def _drain(self, server: subprocess.Popen[str]) -> None:
        assert server.stderr is not None
        for line in server.stderr:
            self.log.append(line)

    def wait_for_error(self, needle: str, timeout: float = 15.0) -> str:
        """Wait until serve reports `needle` on stderr, then stop it and return the log."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and needle not in "".join(self.log):
            if self.server is not None and self.server.poll() is not None:
                break
            time.sleep(0.05)
        return self.stop()

    def stop(self) -> str:
        """Terminate serve and return what it reported on stderr."""
        if self.server is not None:
            self.server.terminate()
            try:
                self.server.wait(timeout=10)
            except subprocess.TimeoutExpired:  # pragma: no cover - hang guard
                self.server.kill()
                self.server.wait()
            self.server = None
        if self._drain_thread is not None:
            self._drain_thread.join(timeout=5)
            self._drain_thread = None
        return "".join(self.log)

    def _wait_for_port(self, attempts: int = 150) -> None:
        for _ in range(attempts):
            with socket.socket() as probe:
                probe.settimeout(0.2)
                try:
                    probe.connect(("127.0.0.1", self.port))
                    return
                except OSError:
                    time.sleep(0.05)
        raise AssertionError("serve did not listen")

    def calls(self) -> int:
        if not self.counter.exists():
            return 0
        return int(self.counter.read_text(encoding="utf-8").strip() or 0)

    def node_answers(self) -> int:
        """Requests the node server started that the peer answered."""
        if not self.answers_file.exists():
            return 0
        return int(self.answers_file.read_text(encoding="utf-8").strip() or 0)

    def request(self, identifier: int) -> str:
        return json.dumps(
            {"jsonrpc": "2.0", "id": identifier, "method": "tools/call",
             "params": {"name": "filesystem.list", "arguments": {"path": "."}}}
        )

    def session(self) -> ClientSession:
        return ClientSession(self)

    def journal_states(self) -> dict[str, int]:
        database = sqlite3.connect(self.host / "journal.sqlite3")
        try:
            return dict(database.execute("SELECT state, COUNT(*) FROM journal GROUP BY state"))
        finally:
            database.close()


def test_ninety_six_sequential_requests_are_each_answered(tmp_path):
    with Pairing(tmp_path) as pairing, pairing.session() as session:
        for identifier in range(96):
            session.send(pairing.request(identifier))
            reply = session.read()
            assert reply["id"] == identifier, f"out of order at {identifier}"
            assert reply["result"]["ok"] is True
        status, error = session.close()
    assert status == 0, error
    assert pairing.calls() == 96, "each request must reach the server exactly once"


def test_ninety_six_answered_requests_in_each_direction(tmp_path):
    """Both directions must sustain a long session, with equal ids in flight at once.

    The peer answers every request and immediately asks its own with the same numeric
    id. If a response only released slots in one direction, the other direction's 32
    slots would fill and the session would die well before 96.
    """

    with Pairing(tmp_path, stub=STUB_BIDIRECTIONAL) as pairing, pairing.session() as session:
        for identifier in range(96):
            session.send(pairing.request(identifier))
            reply = session.read()
            assert reply["id"] == identifier, f"out of order at {identifier}"
            assert reply["result"]["ok"] is True
        session.answer_remaining(96)
        status, error = session.close()
    assert status == 0, error
    assert pairing.calls() == 96, "each request in this direction executed exactly once"
    assert pairing.node_answers() == 96, "each request the node started was answered once"


def test_the_node_side_outstanding_limit_is_independent(tmp_path):
    """The node's own direction allows 32 unanswered requests and refuses the 33rd.

    The peer deliberately answers nothing: answering a request frees its slot, so the
    cap is only observable while the burst is still outstanding. The 33rd request must
    never be delivered, and the host must say why the session ended.
    """

    with Pairing(tmp_path, stub=STUB_NODE_FLOOD) as pairing:
        with pairing.session() as session:
            session.send(pairing.request(1))
            frames = session.read_frames()
            session.close()
        errors = pairing.wait_for_error("TooManyOutstanding")
    requests = [frame for frame in frames if "method" in frame]
    assert [frame["id"] for frame in requests] == list(range(500, 532)), (
        f"32 unanswered requests may be outstanding, saw {len(requests)}"
    )
    assert [frame["id"] for frame in frames if "method" not in frame] == [1]
    assert pairing.calls() == 1
    assert pairing.node_answers() == 0, "the burst was deliberately left unanswered"
    assert "TooManyOutstanding" in errors, f"the host must refuse the 33rd: {errors}"
    # Exactly the 32 admitted requests are left unresolved; the refused one was never
    # recorded as work the session accepted.
    assert "32 unresolved request" in errors, f"the host must count accurately: {errors}"


def test_thirty_two_outstanding_are_allowed_and_the_thirty_third_is_refused(tmp_path):
    """The cap is enforced by shared accounting, and the refused request never runs.

    Either end may notice first: the coordinator counts what it has in flight, and the
    host applies the same limit to what it has forwarded. Whichever refuses, the
    refusal is reported and the request is not executed.
    """

    with Pairing(tmp_path) as pairing:
        with pairing.session() as session:
            for identifier in range(32):
                session.send(pairing.request(identifier))
            replies = [session.read() for _ in range(32)]
            assert sorted(reply["id"] for reply in replies) == list(range(32))
            # Answers freed their slots, so the session continues.
            session.send(pairing.request(100))
            assert session.read()["id"] == 100
            session.close()
        assert pairing.calls() == 33

        before = pairing.calls()
        with pairing.session() as session:
            for identifier in range(33):
                session.send(pairing.request(200 + identifier))
            replies = session.read_until_closed()
            status, error = session.close()
        assert len(replies) == 32, "every admitted request is answered"
        assert sorted(reply["id"] for reply in replies) == list(range(200, 232))
        assert status != 0, "the refusal must be reported, not silently dropped"
        assert "TooManyOutstanding" in error
    assert pairing.calls() - before == 32, "the 33rd request must never execute"


def test_a_duplicate_id_is_refused_without_executing_twice(tmp_path):
    with Pairing(tmp_path) as pairing:
        with pairing.session() as session:
            session.send(pairing.request(7))
            assert session.read()["id"] == 7
            session.send(pairing.request(7))
            replies = session.read_until_closed()
            session.close()
        assert replies == [], "a duplicate closes the session without a second reply"
    assert pairing.calls() == 1, "a duplicate id must never run the call twice"


def test_a_used_session_that_hits_its_allowance_requires_a_fresh_session(tmp_path):
    with Pairing(tmp_path, session_limit=3) as pairing:
        with pairing.session() as session:
            for identifier in range(3):
                session.send(pairing.request(identifier))
                assert session.read()["id"] == identifier
            session.close()
        # A new session starts with its own allowance.
        with pairing.session() as session:
            session.send(pairing.request(0))
            assert session.read()["id"] == 0
            session.close()
        assert pairing.calls() == 4
        # The first session's ids stay recorded; nothing was evicted to make room.
        assert pairing.journal_states()["settled"] >= 4


def test_a_grant_for_another_directory_is_refused(tmp_path):
    """Granting one directory while serving another is the host escalating itself."""

    other = tmp_path / "elsewhere"
    other.mkdir()
    with Pairing(tmp_path, grant_workspace=other) as pairing:
        with pairing.session() as session:
            session.send(pairing.request(1))
            replies = session.read_until_closed()
            session.close()
        assert replies == [], "no reply may be produced for a refused peer"
        assert pairing.calls() == 0, "nothing may execute in a workspace the grant does not name"


def test_revoking_the_grant_ends_a_live_session(tmp_path):
    with Pairing(tmp_path) as pairing, pairing.session() as session:
        session.send(pairing.request(1))
        assert session.read()["id"] == 1
        assert pairing.calls() == 1
        _run(pairing.host, "peers", "revoke", "coordinator")
        # The watcher polls every second; the session must end without another request.
        session.read_until_closed()
        session.close()
    assert pairing.calls() == 1, "a revoked session executes nothing more"


def test_owned_descendants_are_terminated_with_the_session(tmp_path):
    """The host owns the process tree, not just the direct child."""

    with Pairing(tmp_path, stub=STUB_WITH_CHILD) as pairing, pairing.session() as session:
        session.send(pairing.request(1))
        assert session.read()["result"]["ok"] is True
        session.close()
        child_pid = int((tmp_path / "child.pid").read_text(encoding="utf-8").strip())
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and _process_alive(child_pid):
            time.sleep(0.1)
        assert not _process_alive(child_pid), (
            f"descendant {child_pid} survived the session"
        )


def _process_alive(pid: int) -> bool:
    if os.name == "nt":
        completed = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
            capture_output=True, text=True, check=False,
        )
        return str(pid) in completed.stdout
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True
