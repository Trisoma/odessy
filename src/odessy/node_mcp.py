from __future__ import annotations

import asyncio
import os
import shutil
import socket
import subprocess
import sys
from contextlib import AbstractAsyncContextManager
from pathlib import Path
from types import TracebackType
from typing import Any

from mcp.client.stdio import StdioServerParameters, stdio_client


class NodeToolServerTransport(AbstractAsyncContextManager):
    """MCP stdio transport for Odessy's laptop-side Node tool server."""

    def __init__(self, workspace_root: Path | str) -> None:
        public_repo_root = Path(__file__).resolve().parents[2]
        self._context = stdio_client(
            StdioServerParameters(
                command="npm",
                args=["run", "start", "--silent"],
                cwd=_node_tools_dir(public_repo_root),
                env={
                    **os.environ,
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
    ) -> None:
        self._workspace_root = Path(workspace_root).resolve()
        self._host = host
        self._port = port
        self._process: subprocess.Popen[bytes] | None = None
        self.url: str | None = None

    async def __aenter__(self) -> str:
        if not node_tool_server_dependencies_installed():
            raise RuntimeError(
                "Node tool server dependencies are not installed. Run npm install in tools/."
            )

        npm = shutil.which("npm")
        if npm is None:
            raise RuntimeError("npm is required to run the Odessy Node MCP tool server")

        port = self._port or _find_free_port(self._host)
        public_repo_root = _public_repo_root()
        env = {
            **os.environ,
            "ODESSY_WORKSPACE_ROOT": str(self._workspace_root),
            "ODESSY_MCP_HOST": self._host,
            "ODESSY_MCP_PORT": str(port),
            "ODESSY_MCP_TRANSPORT": "http",
        }
        self._process = subprocess.Popen(
            [npm, "run", "start:http", "--silent"],
            cwd=_node_tools_dir(public_repo_root),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.url = f"http://{self._host}:{port}/mcp"
        await _wait_for_port(self._host, port, self._process)
        return self.url

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None:
        if self._process is None:
            return None
        self._process.terminate()
        try:
            await asyncio.to_thread(self._process.wait, timeout=5)
        except subprocess.TimeoutExpired:
            self._process.kill()
            await asyncio.to_thread(self._process.wait)
        return None


def build_node_tool_server_http(workspace_root: Path | str) -> NodeToolServerHttp:
    return NodeToolServerHttp(workspace_root)


def node_tool_server_dependencies_installed() -> bool:
    tool_server = _node_tools_dir(_public_repo_root())
    bin_dir = tool_server / "node_modules" / ".bin"
    tsc = bin_dir / ("tsc.cmd" if os.name == "nt" else "tsc")
    return (
        shutil.which("npm") is not None
        and (tool_server / "node_modules" / "@modelcontextprotocol" / "sdk").exists()
        and tsc.exists()
    )


def _public_repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _node_tools_dir(public_repo_root: Path) -> Path:
    return public_repo_root / "tools"


def _find_free_port(host: str) -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind((host, 0))
        return int(probe.getsockname()[1])


async def _wait_for_port(
    host: str,
    port: int,
    process: subprocess.Popen[bytes],
    *,
    timeout: float = 15.0,
) -> None:
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
