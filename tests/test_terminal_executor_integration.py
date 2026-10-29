"""Real separate executor process, keyboard input, authenticated Node and agent policy."""
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import urllib.error
import urllib.request

from odessy.application.lan_connections import Connections
from odessy.tools.lan_executor import clear, current

HOST_CODE = r'''
import asyncio, json, secrets, sys
from pathlib import Path
from odessy.application.executor import HostSettings, TerminalApproval, create_listener
from odessy.tools.node_mcp import NodeToolServerHttp
async def main():
    settings = HostSettings.load(Path(sys.argv[1])/'settings.json', workspace=Path(sys.argv[1])/'workspace')
    token=secrets.token_urlsafe(32)
    async with NodeToolServerHttp(settings.workspace, extra_env={'ODESSY_MCP_TOKEN':token}) as url:
        server=create_listener(settings, TerminalApproval(), token, url, host='127.0.0.1', networks=['127.0.0.0/8'])
        print(json.dumps({'id':settings.device_id,'name':'Test execution host','host':'127.0.0.1','port':server.server_address[1]}),flush=True)
        try:
            while not server.accepted: await asyncio.sleep(.05)
            await asyncio.to_thread(sys.stdin.readline)
        finally:
            await asyncio.to_thread(server.shutdown)
            server.server_close()
asyncio.run(main())
'''


def test_real_terminal_approval_connects_tools_and_policy(tmp_path):
    async def run():
        clear()
        process = subprocess.Popen([sys.executable, '-u', '-c', HOST_CODE, str(tmp_path)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            encoding='utf-8', env={**os.environ, 'PYTHONUTF8':'1'})
        controller = None
        try:
            line = await asyncio.wait_for(asyncio.to_thread(process.stdout.readline), timeout=40)
            assert line, 'Executor failed before readiness'
            host = json.loads(line)
            workspace = tmp_path / 'workspace'
            (workspace / 'hello.txt').write_text('executor-workspace-fixture', encoding='utf-8')
            manager = Connections(tmp_path / 'selected.json')
            manager.hosts[host['id']] = host
            connect = asyncio.create_task(manager.connect(host['id']))
            # Feed the one keyboard reader; this is not a mocked approve callback.
            await asyncio.sleep(.3)
            process.stdin.write('Y\n')
            process.stdin.flush()
            result = await asyncio.wait_for(connect, timeout=30)
            assert result['state'] == 'connected'
            assert result['tools'] > 0
            assert 'credential' not in json.dumps(result)
            assert current().credential not in (tmp_path / 'selected.json').read_text()
            # A caller without the execution credential is rejected by actual Node.
            def unauthorized():
                opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                try:
                    opener.open(current().url, timeout=5)
                except urllib.error.HTTPError as error:
                    assert error.code == 401
                    error.close()
                else:
                    raise AssertionError('Tool endpoint admitted an anonymous caller')
            await asyncio.to_thread(unauthorized)
            async with current().client() as client:
                read = await client.call_tool('filesystem.read', {'path':'hello.txt'})
                assert 'executor-workspace-fixture' in str(read)
                outside = await client.call_tool('filesystem.read', {'path':'../settings.json'})
                assert outside.is_error or outside.structured_content.get('status') in ('error', 'denied'), str(outside)

            from odessy.application.bootstrap import create_controller
            from odessy.runtime.types import ToolRequest, FinalResponse
            class Model:
                def propose(self, text, **kwargs):
                    return ToolRequest('laptop.terminal.run', {'command':'echo odessy-connected > approved.txt'})
                def respond_to_observation(self, text, observation, **kwargs):
                    return FinalResponse('observed: ' + str(observation))
            controller = await create_controller(workspace=tmp_path, model=Model())
            approvals = []
            async def approve(entry):
                approvals.append(entry)
                return True
            async def handle(entry):
                approvals.append(entry)
                # The approver this prototype defines is the execution host's
                # terminal, so that is the channel it answers on. The broker allows
                # only channels it advertised on the entry; anything else is refused
                # as a channel mismatch rather than approved.
                assert 'terminal' in entry['allowed_channels'], entry['allowed_channels']
                controller.interactions.resolve_approval(entry['id'], True, channel='terminal')
            controller.interactions.handler = handle
            turn = await controller.run_turn('Run the reviewed echo command on the execution host')
            assert approvals, 'The real agent must request command approval'
            # PowerShell's redirection writes UTF-16LE with a BOM on Windows, so the
            # assertion decodes by BOM rather than assuming the platform default.
            raw = (workspace / 'approved.txt').read_bytes()
            written = raw.decode('utf-16') if raw[:2] in (b'\xff\xfe', b'\xfe\xff') else raw.decode('utf-8')
            assert written.strip() == 'odessy-connected', turn.final_text
            clear()
        finally:
            if controller:
                await controller.close()
            clear()
            if process.stdin and not process.stdin.closed:
                process.stdin.close()
            try:
                process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
    asyncio.run(run())
