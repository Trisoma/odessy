"""End-to-end: Python's paired adapter → odessy-link → mutual TLS → real Node server.

Opt-in, because it needs a built ``odessy-link`` and a built ``tools/dist``. It is the
acceptance test the plan asks for at the Python boundary: the tool names the Python
side sees, and a real ``filesystem.list`` result, come from the repository's own Node
tool server rather than a stub.

Run with::

    ODESSY_LINK_E2E=1 python -m pytest tests/test_paired_integration.py -q
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

pytest.importorskip("mcp")

REPO = Path(__file__).resolve().parents[1]
NODE_SERVER = REPO / "tools" / "dist" / "src" / "server.js"
BINARIES = (
    REPO / "native" / "odessy-link" / "target" / "release" / ("odessy-link.exe" if os.name == "nt" else "odessy-link"),
    REPO / "native" / "odessy-link" / "target" / "debug" / ("odessy-link.exe" if os.name == "nt" else "odessy-link"),
)

pytestmark = pytest.mark.skipif(
    os.environ.get("ODESSY_LINK_E2E") != "1" or not NODE_SERVER.is_file()
    or not any(path.is_file() for path in BINARIES),
    reason="set ODESSY_LINK_E2E=1 with a built odessy-link and tools/dist",
)


def _run(binary: Path, state: Path, *arguments: str) -> str:
    environment = {**os.environ, "ODESSY_LINK_STATE_DIR": str(state)}
    completed = subprocess.run(
        [str(binary), *arguments], capture_output=True, text=True, env=environment, check=False
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout
    return completed.stdout


def _fingerprint(bundle: Path) -> str:
    return json.loads(bundle.read_text(encoding="utf-8"))["fingerprint_sha256"]


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def test_python_adapter_reaches_the_real_node_tool_server(tmp_path):
    binary = next(path for path in BINARIES if path.is_file())
    coordinator, host = tmp_path / "coordinator", tmp_path / "host"
    workspace = REPO

    # Enrol both machines, then grant the host's workspace to the coordinator.
    for state, bundle in ((coordinator, tmp_path / "coordinator.json"), (host, tmp_path / "host.json")):
        _run(binary, state, "init")
        _run(binary, state, "identity", "export", "--out", str(bundle))
    port = _free_port()
    _run(binary, host, "pair", "add", "--alias", "coordinator",
         "--certificate", str(tmp_path / "coordinator.json"),
         "--expect-sha256", _fingerprint(tmp_path / "coordinator.json"))
    _run(binary, host, "peers", "grant", "coordinator",
         "--capability", "mcp", "--workspace", str(workspace))
    _run(binary, coordinator, "pair", "add", "--alias", "host",
         "--certificate", str(tmp_path / "host.json"),
         "--expect-sha256", _fingerprint(tmp_path / "host.json"),
         "--address", f"127.0.0.1:{port}")

    environment = {
        **os.environ,
        "ODESSY_LINK_STATE_DIR": str(host),
        "ODESSY_LINK_NODE": shutil.which("node") or "node",
    }
    server = subprocess.Popen(
        [str(binary), "serve", "--listen", f"127.0.0.1:{port}", "--workspace", str(workspace),
         "--node-server", str(NODE_SERVER)],
        env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    try:
        _wait_for_port(port)
        os.environ["ODESSY_LINK_STATE_DIR"] = str(coordinator)
        from odessy.tools.paired import paired_host

        host_record = paired_host({"ODESSY_PAIRED_HOST": "host", "ODESSY_LINK_BIN": str(binary)})
        server_record = host_record.server()

        import asyncio

        async def exercise():
            tools = await server_record.list_tools()
            return [getattr(tool, "name", None) for tool in tools]

        names = asyncio.run(exercise())
        assert "filesystem.list" in names
        # The peer's Node server names its command tool itself; no alias prefix.
        assert "laptop.terminal.run" in names
        assert not any((name or "").startswith("host.") for name in names)

        async def call():
            return await server_record.call_tool("filesystem.list", {"path": "."})

        result = asyncio.run(call())
        text = str(result)
        assert "pyproject.toml" in text, "a real directory listing must come back"
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:  # pragma: no cover - hang guard
            server.kill()
        os.environ.pop("ODESSY_LINK_STATE_DIR", None)


def _wait_for_port(port: int, attempts: int = 100) -> None:
    for _ in range(attempts):
        with socket.socket() as probe:
            probe.settimeout(0.2)
            try:
                probe.connect(("127.0.0.1", port))
                return
            except OSError:
                time.sleep(0.05)
    raise AssertionError(f"serve did not listen on {port}")
