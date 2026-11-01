"""Real separate executor process, keyboard input, authenticated Node and agent policy."""
import asyncio
import csv
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


def test_real_terminal_approval_connects_tools_and_policy(tmp_path, tmp_path_factory):
    if os.name == 'nt':
        # Python 3.12.4+ creates pytest's private temp root with OWNER RIGHTS
        # instead of an explicit user ACE. The LUA restricted token cannot
        # read that fixture tree, so PowerShell silently starts in System32.
        # Give only the current user Modify access to the fixture tree so the
        # normal token access check succeeds. Writes still require the sandbox
        # capability in the second access check;
        # do not grant Everyone access or change the restricted token.
        identity = subprocess.check_output(
            ['whoami', '/user', '/fo', 'csv', '/nh'], text=True,
        )
        user_sid = next(csv.reader(identity.strip().splitlines()))[-1]
        base = tmp_path_factory.getbasetemp()
        directory = tmp_path
        while True:
            subprocess.run(
                ['icacls', str(directory), '/grant', f'*{user_sid}:(OI)(CI)(M)', '/Q'],
                check=True, capture_output=True,
            )
            if directory == base:
                break
            directory = directory.parent
    async def run():
        clear()
        process = subprocess.Popen([sys.executable, '-u', '-c', HOST_CODE, str(tmp_path)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            encoding='utf-8', env={**os.environ, 'PYTHONUTF8':'1'})
        controller = None
        try:
            line = await asyncio.wait_for(asyncio.to_thread(process.stdout.readline), timeout=40)
            assert line, 'Executor failed before readiness: ' + process.stderr.read()
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
                if os.name == 'nt':
                    # The user ACE above must not bypass the restricting-SID
                    # write check: neither an outside write nor a read-only
                    # workspace write may succeed.
                    blocked = await client.call_tool('laptop.terminal.run', {
                        'command': 'echo forbidden > ../sandbox-outside.txt',
                        'mode': 'workspace-write', 'isolation': 'preferred',
                    })
                    assert not blocked.is_error, str(blocked)
                    assert blocked.structured_content['sandbox']['provider_id'] == 'windows-acl'
                    assert blocked.structured_content['exit_code'] != 0
                    assert not (tmp_path / 'sandbox-outside.txt').exists()
                    readonly = await client.call_tool('laptop.terminal.run', {
                        'command': 'echo forbidden > sandbox-readonly.txt',
                        'mode': 'read-only', 'isolation': 'preferred',
                    })
                    assert not readonly.is_error, str(readonly)
                    assert readonly.structured_content['sandbox']['provider_id'] == 'windows-acl'
                    assert readonly.structured_content['exit_code'] != 0
                    assert not (workspace / 'sandbox-readonly.txt').exists()

            from odessy.application.bootstrap import create_controller
            from odessy.runtime.types import ToolRequest, FinalResponse
            class Model:
                def propose(self, text, **kwargs):
                    if 'on this computer' in text:
                        return ToolRequest('terminal.run', {'command':'echo odessy-local > local-approved.txt'})
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
            assert (workspace / 'approved.txt').exists(), turn.final_text
            # PowerShell's redirection writes UTF-16LE with a BOM on Windows, so the
            # assertion decodes by BOM rather than assuming the platform default.
            raw = (workspace / 'approved.txt').read_bytes()
            written = raw.decode('utf-16') if raw[:2] in (b'\xff\xfe', b'\xfe\xff') else raw.decode('utf-8')
            assert written.strip() == 'odessy-connected', turn.final_text
            # Stop the real remote, leaving it selected. A reviewed command
            # explicitly addressed to this coordinator must still run locally.
            process.stdin.write('\n')
            process.stdin.flush()
            await asyncio.to_thread(process.wait, timeout=10)
            local_turn = await controller.run_turn('Run the reviewed echo on this computer')
            local_raw = (tmp_path / 'local-approved.txt').read_bytes()
            local_text = local_raw.decode('utf-16') if local_raw[:2] in (b'\xff\xfe', b'\xfe\xff') else local_raw.decode('utf-8')
            assert local_text.strip() == 'odessy-local', local_turn.final_text
            assert not (workspace / 'local-approved.txt').exists()
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
