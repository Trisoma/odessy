"""A paired coordinator reaches the laptop's tool server with its execution credential.

These run the real components: the built Node MCP server as a child process, driven
over TCP. The credential boundary is where the defect lived, so it is exercised at the
wire, not with a mock.
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import time
import urllib.error
import urllib.request

import pytest

from odessy.tools import node_mcp
from odessy.tools.external_tools import ServerConfig

pytestmark = pytest.mark.skipif(
    not node_mcp.node_tool_server_dependencies_installed(),
    reason='the Node tool server is not built on this machine')

TOKEN_VARIABLE = 'ODESSY_TEST_EXECUTION_TOKEN'
CREDENTIAL = 'live-execution-credential'
INITIALIZE = json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': 'initialize',
                         'params': {'protocolVersion': '2024-11-05', 'capabilities': {},
                                    'clientInfo': {'name': 'probe', 'version': '0'}}}).encode()


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        return probe.getsockname()[1]


def _start(workspace, token):
    tools = node_mcp._node_tools_dir(node_mcp._public_repo_root())
    entry = node_mcp._built_server_entry(tools)
    port = _free_port()
    env = {**os.environ, 'ODESSY_MCP_HOST': '127.0.0.1', 'ODESSY_MCP_PORT': str(port),
           'ODESSY_MCP_TRANSPORT': 'http', 'ODESSY_WORKSPACE_ROOT': str(workspace),
           'ODESSY_MCP_TOKEN': token}
    process = subprocess.Popen(['node', str(entry), '--http'], cwd=tools, env=env,
                               stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL)
    for _ in range(200):
        try:
            socket.create_connection(('127.0.0.1', port), timeout=0.2).close()
            return process, f'http://127.0.0.1:{port}/mcp'
        except OSError:
            if process.poll() is not None:
                break
            time.sleep(0.05)
    process.terminate()
    pytest.skip('the Node tool server did not start on this machine')


def _stop(process):
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()


def _call(url, authorization=None):
    request = urllib.request.Request(url, data=INITIALIZE, method='POST',
                                     headers={'Content-Type': 'application/json',
                                              'Accept': 'application/json, text/event-stream'})
    if authorization is not None:
        request.add_header('Authorization', authorization)
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return response.status, response.read().decode('utf-8', 'replace')
    except urllib.error.HTTPError as error:
        error.close()
        return error.code, ''


def test_only_the_execution_credential_reaches_the_tool_endpoint(tmp_path):
    """The endpoint answers the credential it was started with, and nothing else."""
    process, url = _start(tmp_path, CREDENTIAL)
    try:
        assert _call(url)[0] == 401
        assert _call(url, 'Bearer not-the-credential')[0] == 401
        status, body = _call(url, 'Bearer ' + CREDENTIAL)
    finally:
        _stop(process)
    assert status == 200
    assert 'odessy-tool-server' in body


def test_the_coordinator_attaches_the_credential_without_putting_it_in_the_url(monkeypatch):
    """The header is attached by the client; the secret never enters the URL."""
    monkeypatch.setenv(TOKEN_VARIABLE, CREDENTIAL)
    config = ServerConfig(name='paired-laptop', transport='http',
                          url='http://127.0.0.1:9/mcp',
                          headers={'Authorization': TOKEN_VARIABLE})
    assert CREDENTIAL not in config.url
    assert CREDENTIAL not in repr(config)
    connection = config.connection()
    assert connection is not config.url, 'an authenticated client must be used'
    assert CREDENTIAL not in repr(config.connection())


def test_an_unset_header_variable_falls_back_to_the_plain_url(monkeypatch):
    monkeypatch.delenv(TOKEN_VARIABLE, raising=False)
    config = ServerConfig(name='plain', transport='http', url='http://127.0.0.1:9/mcp',
                          headers={'Authorization': TOKEN_VARIABLE})
    assert config.connection() == 'http://127.0.0.1:9/mcp'
