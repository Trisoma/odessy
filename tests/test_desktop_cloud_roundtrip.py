"""Real local HTTP composition with a mock cloud endpoint; no paid API traffic."""
import json
import os
import secrets
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import pytest
from odessy.setup.bridge import execute
from odessy.setup.configuration import export_selection
from odessy.setup.contracts import SetupSelection
from odessy.setup.planning import build_plan


def test_saved_model_reaches_cloud_adapter_and_errors_are_public(tmp_path):
    requests = []
    class Cloud(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_POST(self):
            payload=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            requests.append(payload['model'])
            if payload['model'] != 'deepseek-flash':
                self.send_response(400); self.end_headers()
                self.wfile.write(json.dumps({'error':{'message':'invalid model identifier'}}).encode()); return
            if 'tool_choice' in payload:
                self.send_response(400); self.end_headers()
                self.wfile.write(json.dumps({'error':{'message':'tool_choice unsupported in thinking mode'}}).encode()); return
            self.send_response(200); self.send_header('Content-Type','application/json'); self.end_headers()
            self.wfile.write(json.dumps({'choices':[{'message':{'role':'assistant','content':'Hello from the configured cloud adapter.'},'finish_reason':'stop'}], 'usage':{'prompt_tokens':20,'completion_tokens':8}}).encode())
    cloud=ThreadingHTTPServer(('127.0.0.1',0),Cloud)
    thread=threading.Thread(target=cloud.serve_forever,daemon=True);thread.start()
    try:
        for index, model in enumerate(('Deepseek V4.1 Flash','deepseek-flash')):
            selection={'role':'combined','model':{'provider':'deepseek','name':model,'endpoint':f'http://127.0.0.1:{cloud.server_port}/v1'}}
            path=tmp_path/f'selection-{index}.json'
            export_selection(build_plan(SetupSelection.from_dict(selection)),path)
            spec=execute(dict(operation='launch',selection=selection,output=str(path)))
            token=secrets.token_urlsafe(32)
            with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
            bootstrap='import sys,runpy;sys.path[:0]='+repr(sys.path)+';runpy.run_module("odessy.setup.configured",run_name="__main__")'
            env={**os.environ,'ODESSY_API_TOKEN':token,'DEEPSEEK_API_KEY':'fixture-secret','ODESSY_NODE_TOOLS_DIR':str(tmp_path/'missing-tools'),'ODESSY_CLOUD_RETRIES':'0','ODESSY_CLOUD_FAILOVER':'','ODESSY_CLOUD_API_KEY':'fixture-secret'}
            args=[sys.executable,'-c',bootstrap,'--config',str(path),'--format','selection','--selection-json',json.dumps(selection),'--sha256',spec['sha256'],'--workspace',str(tmp_path/'workspace'),'--port',str(port),'--exit-on-stdin-close']
            with (tmp_path/f'service-{index}.log').open('w+') as log:
                child=subprocess.Popen(args,env=env,stdin=subprocess.PIPE,stdout=subprocess.DEVNULL,stderr=log)
                def api(route,method='GET',data=None):
                    headers={'Authorization':'Bearer '+token}
                    if data is not None:headers['Content-Type']='application/json'
                    with urllib.request.urlopen(urllib.request.Request(f'http://127.0.0.1:{port}'+route,data=json.dumps(data).encode() if data is not None else None,headers=headers,method=method),timeout=8) as response:return json.load(response)
                try:
                    for _ in range(100):
                        try:profiles=api('/v1/profiles');break
                        except OSError:
                            if child.poll() is not None:log.seek(0);pytest.fail(log.read())
                            time.sleep(.1)
                    else:pytest.fail('coordinator not ready')
                    assert profiles['profiles']['current']['model']==model
                    sid=api('/v1/sessions','POST')['id']
                    api(f'/v1/sessions/{sid}/turns','POST',{'text':'Hi'})
                    for _ in range(150):
                        snapshot=api(f'/v1/sessions/{sid}')
                        if any(e['type'] in ('result','error') for e in snapshot['events']):break
                        time.sleep(.05)
                    else:pytest.fail('turn did not finish')
                    if index==0:
                        error=next(e for e in snapshot['events'] if e['type']=='error')
                        assert 'invalid model identifier' in error['data']['message']
                    else:
                        assert not any(e['type']=='error' for e in snapshot['events']), snapshot['events']
                        assert 'Hello from the configured cloud adapter.' in json.dumps(snapshot)
                    assert 'fixture-secret' not in json.dumps(snapshot)
                finally:
                    child.stdin.close()
                    try:child.wait(timeout=8)
                    except subprocess.TimeoutExpired:child.kill();child.wait()
        assert requests == ['Deepseek V4.1 Flash','deepseek-flash']
    finally:cloud.shutdown();cloud.server_close();thread.join(2)
