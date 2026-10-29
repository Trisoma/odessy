from __future__ import annotations

import asyncio
import os
import shutil
import socket
import subprocess
import sys
import tempfile
from odessy.credentials import tool_environment
from collections.abc import Mapping
from contextlib import AbstractAsyncContextManager
from pathlib import Path
from types import TracebackType
from typing import IO, Any

from mcp.client.stdio import StdioServerParameters, stdio_client

# TODO: Potentially a dead code: this class and the following function code:
class NodeToolServerTransport(AbstractAsyncContextManager):
    """MCP stdio transport for Odessy's laptop-side Node tool server."""

    def __init__(self, workspace_root: Path | str) -> None:
        public_repo_root = Path(__file__).resolve().parents[3]
        self._context = stdio_client(
            StdioServerParameters(
                command="npm",
                args=["run", "start", "--silent"],
                cwd=_node_tools_dir(public_repo_root),
                env={
                    **tool_environment(os.environ),
                    "ODESSY_WORKSPACE_ROOT": str(Path(workspace_root).resolve()),
                },
                encoding="utf-8",
                encoding_error_handler="replace",
            ),
            errlog=sys.stderr,
        )

    async def __aenter__(self) -> Any:
        return await self._context.__aenter__()

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None:
        return await self._context.__aexit__(exc_type, exc, traceback)

def build_node_tool_server_params(workspace_root: Path | str) -> NodeToolServerTransport:
    return NodeToolServerTransport(workspace_root)

class NodeToolServerHttp(AbstractAsyncContextManager):
    """Lifecycle helper for Odessy's Streamable HTTP Node MCP server."""

    def __init__(
        self,
        workspace_root: Path | str,
        *,
        host: str = "127.0.0.1",
        port: int = 0,
        extra_env: Mapping[str, str] | None = None,
    ) -> None:
        self._workspace_root = Path(workspace_root).resolve()
        self._host = host
        self._port = port
        self._extra_env = dict(extra_env or {})
        self._process: subprocess.Popen[bytes] | None = None
        self._stderr: IO[bytes] | None = None
        self.url: str | None = None

    async def __aenter__(self) -> str:
        if not node_tool_server_dependencies_installed():
            raise RuntimeError(
                "Node tool server dependencies are not installed. Run npm install in tools/."
            )

        npm = shutil.which("npm")
        if npm is None and not os.environ.get("ODESSY_PORTABLE_ROOT"):
            raise RuntimeError("npm is required to run the Odessy Node MCP tool server")

        port = self._port or _find_free_port(self._host)
        public_repo_root = _public_repo_root()
        tools_dir = _node_tools_dir(public_repo_root)
        node = shutil.which("node")
        env = {
            **tool_environment(os.environ),
            "ODESSY_WORKSPACE_ROOT": str(self._workspace_root),
            "ODESSY_MCP_HOST": self._host,
            "ODESSY_MCP_PORT": str(port),
            "ODESSY_MCP_TRANSPORT": "http",
            **self._extra_env,
        }
        if node is not None and (os.environ.get("ODESSY_PORTABLE_ROOT") or _build_is_current(tools_dir)):
            command = [node, str(_built_server_entry(tools_dir)), "--http"]
        else:
            command = [npm, "run", "start:http", "--silent"]
        # stderr goes to a file, not a pipe: nobody drains a pipe while the server runs,
        # and a full pipe would block it. The file is read only if startup fails.
        self._stderr = tempfile.TemporaryFile()
        self._process = subprocess.Popen(
            command,
            cwd=tools_dir,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=self._stderr,
        )
        self.url = f"http://{self._host}:{port}/mcp"
        try:
            await _wait_for_port(self._host, port, self._process)
        except BaseException as error:
            # `async with` does not call __aexit__ when __aenter__ raises, so a server
            # that is slow rather than dead would otherwise be left running.
            detail = self._stderr_tail()
            await self._stop()
            if detail and isinstance(error, (RuntimeError, TimeoutError)):
                raise type(error)(f"{error}. Server output:\n{detail}") from error
            raise
        return self.url

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None:
        await self._stop()
        return None

    async def _stop(self) -> None:
        process, self._process = self._process, None
        if process is not None:
            process.terminate()
            try:
                await asyncio.to_thread(process.wait, timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                await asyncio.to_thread(process.wait)
        if self._stderr is not None:
            self._stderr.close()
            self._stderr = None

    def _stderr_tail(self, limit: int = 2000) -> str:
        if self._stderr is None:
            return ""
        try:
            self._stderr.seek(0)
            text = self._stderr.read().decode("utf-8", "replace")
        except (OSError, ValueError):
            return ""
        return text.strip()[-limit:]

def build_node_tool_server_http(workspace_root: Path | str) -> NodeToolServerHttp:
    return NodeToolServerHttp(workspace_root)

def node_tool_server_dependencies_installed() -> bool:
    tool_server = _node_tools_dir(_public_repo_root())
    bin_dir = tool_server / "node_modules" / ".bin"
    tsc = bin_dir / ("tsc.cmd" if os.name == "nt" else "tsc")
    if os.environ.get("ODESSY_PORTABLE_ROOT"):
        return (shutil.which("node") is not None
                and (tool_server / "node_modules" / "@modelcontextprotocol" / "sdk").exists()
                and _built_server_entry(tool_server).is_file())
    return (
        shutil.which("npm") is not None
        and (tool_server / "node_modules" / "@modelcontextprotocol" / "sdk").exists()
        and tsc.exists()
    )

def _public_repo_root() -> Path:
    return Path(__file__).resolve().parents[3]

def _node_tools_dir(public_repo_root: Path) -> Path:
    portable = os.environ.get("ODESSY_NODE_TOOLS_DIR")
    if portable:
        path = Path(portable)
        if not path.is_absolute():
            raise ValueError("ODESSY_NODE_TOOLS_DIR must be absolute")
        return path
    return public_repo_root / "tools"

def _built_server_entry(tools_dir: Path) -> Path:
    return tools_dir / "dist" / "src" / "server.js"

def _build_is_current(tools_dir: Path) -> bool:
    """True when the built server exists and no build input is newer than it.

    ``npm run start:http`` is ``npm run build && node dist/src/server.js``, so every
    start pays a full ``tsc`` compile before the server even listens. That compile —
    not startup — dominates the wait: about 10 s on a warm machine, and longer on a
    loaded or shared one. When the build is already current we run the built entry
    directly and skip npm and tsc entirely; when it is not, we fall back to the npm
    script, so editing a source file still takes effect without anyone remembering to
    rebuild.

    The inputs are every TypeScript source tsc compiles, plus the two files that
    configure the compile itself. ``tsconfig.json`` and ``package.json`` change what is
    emitted without touching any ``.ts`` file, so leaving them out would let a
    config-only change sit behind a stale ``dist`` that still read as current.
    """
    entry = _built_server_entry(tools_dir)
    if not entry.exists():
        return False
    newest_input = 0.0
    for pattern in ("src/**/*.ts", "tests/**/*.ts", "vendor/**/*.ts"):
        for source in tools_dir.glob(pattern):
            newest_input = max(newest_input, source.stat().st_mtime)
    for name in ("tsconfig.json", "package.json"):
        config = tools_dir / name
        if config.exists():
            newest_input = max(newest_input, config.stat().st_mtime)
    return entry.stat().st_mtime >= newest_input

def _find_free_port(host: str) -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind((host, 0))
        return int(probe.getsockname()[1])

async def _wait_for_port(
    host: str,
    port: int,
    process: subprocess.Popen[bytes],
    *,
    timeout: float = 60.0,
) -> None:
    # The generous ceiling is for the fallback path only: when no current build exists,
    # the server's npm script runs a full TypeScript compile first. A server that dies
    # is still reported immediately by the poll() check below, so this is not a 60 s
    # wait on failure -- only on being genuinely slow.
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if process.poll() is not None:
            raise RuntimeError("Node MCP tool server exited before accepting connections")
        try:
            with socket.create_connection((host, port), timeout=0.25):
                return
        except OSError:
            await asyncio.sleep(0.1)
    raise TimeoutError(f"Timed out waiting for Node MCP tool server on {host}:{port}")
