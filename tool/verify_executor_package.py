"""Local packaged mTLS/Node acceptance with isolated administrative test identities.

This does NOT test or enable short-code enrollment. No models or API credentials.
Evidence/state is retained under the supplied new directory for inspection.
"""
import argparse
import json
import os
from pathlib import Path
import queue
import socket
import subprocess
import threading
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--package', type=Path, required=True)
    parser.add_argument('--evidence', type=Path, required=True)
    args = parser.parse_args()
    package = args.package.resolve()
    evidence = args.evidence.resolve()
    evidence.mkdir(parents=True, exist_ok=False)
    link = package / 'odessy-link.exe'
    executor = package / 'odessy-executor.exe'
    workspace = evidence / 'workspace'
    workspace.mkdir()
    (workspace / 'qualification.txt').write_text('packaged real Node fixture', encoding='utf8')
    environments = {side: dict(os.environ, ODESSY_LINK_STATE_DIR=str(evidence / side))
                    for side in ('host', 'client')}
    for env in environments.values():
        env.pop('ODESSY_LINK_TEST_PAIRING_ADAPTER', None)
    def run(side, *arguments):
        result = subprocess.run([str(link), *map(str, arguments)], env=environments[side],
                                capture_output=True, text=True, encoding='utf8', timeout=30)
        if result.returncode:
            raise RuntimeError(f'{arguments[0]} failed: {result.stderr[:1000]}')
        return result.stdout
    subprocess.run([str(executor), '--selftest'], check=True, timeout=30)
    for side in ('host', 'client'):
        run(side, 'init')
        run(side, 'identity', 'export', '--out', evidence / f'{side}.json')
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        port = probe.getsockname()[1]
    address = f'127.0.0.1:{port}'
    for side, other in [('host', 'client'), ('client', 'host')]:
        bundle = json.loads((evidence / f'{other}.json').read_text())
        run(side, 'pair', 'add', '--alias', other, '--certificate', evidence / f'{other}.json',
            '--expect-sha256', bundle['fingerprint_sha256'], '--address', address)
    run('host', 'peers', 'grant', 'client', '--capability', 'mcp', '--workspace', workspace)
    host = subprocess.Popen([str(executor), '--serve', '--workspace', str(workspace),
                             '--listen', address], env=environments['host'],
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    client = None
    logs = []
    def log_reader():
        for line in host.stderr:
            logs.append(line.decode('utf8', 'replace')[:4096])
            if len(logs) > 64:
                del logs[0]
    threading.Thread(target=log_reader, daemon=True).start()
    try:
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if host.poll() is not None:
                raise RuntimeError('packaged executor failed: ' + ''.join(logs))
            if any('listening on ' in line for line in logs):
                break
            time.sleep(.05)
        else:
            raise RuntimeError('packaged executor did not report listening')
        client = subprocess.Popen([str(link), 'connect', '--peer', 'host'], env=environments['client'],
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        messages = queue.Queue(maxsize=64)
        def reader():
            for line in client.stdout:
                try:
                    messages.put(json.loads(line), timeout=1)
                except (ValueError, queue.Full):
                    pass
        threading.Thread(target=reader, daemon=True).start()
        def request(identity, method, params):
            client.stdin.write((json.dumps({'jsonrpc':'2.0', 'id':identity, 'method':method,
                                            'params':params}) + '\n').encode())
            client.stdin.flush()
            end = time.monotonic() + 20
            while time.monotonic() < end:
                value = messages.get(timeout=max(.01, end-time.monotonic()))
                if value.get('id') == identity:
                    return value
            raise RuntimeError('MCP response timeout')
        initialized = request(1, 'initialize', {'protocolVersion':'2024-11-05','capabilities':{},
                                               'clientInfo':{'name':'odessy-package-acceptance','version':'1'}})
        assert 'result' in initialized, initialized
        client.stdin.write(b'{"jsonrpc":"2.0","method":"notifications/initialized"}\n')
        client.stdin.flush()
        tools = request(2, 'tools/list', {})
        assert any(tool['name'] == 'filesystem.list' for tool in tools['result']['tools'])
        call = request(3, 'tools/call', {'name':'filesystem.list','arguments':{'path':'.'}})
        assert 'qualification.txt' in json.dumps(call), call
        outside = request(4, 'tools/call', {'name':'filesystem.list','arguments':{'path':'..'}})
        assert outside.get('error') or outside.get('result', {}).get('isError') or 'error' in json.dumps(outside).lower(), outside
        run('host', 'peers', 'revoke', 'client')
        refused = subprocess.run([str(link), 'connect', '--peer', 'host'], env=environments['client'],
                                 input=b'{"jsonrpc":"2.0","id":1,"method":"tools/list"}\n',
                                 capture_output=True, timeout=15)
        assert refused.returncode != 0 and b'"tools"' not in refused.stdout
        report = dict(short_code_tested=False, platform='windows-x64', real_node=True,
                      packaged_executor=True, tools_list=True, workspace_read=True,
                      outside_workspace_refused=True, revoked_connection_refused=True,
                      tool_count=len(tools['result']['tools']))
        (evidence / 'result.json').write_text(json.dumps(report, indent=2))
        print(json.dumps(report, indent=2))
    finally:
        if client is not None:
            client.kill()
            client.wait(timeout=5)
        host.kill()  # Closing its Windows job kills only its owned relay/Node tree.
        host.wait(timeout=5)
        (evidence / 'host.log').write_text(''.join(logs), encoding='utf8')


if __name__ == '__main__':
    main()
