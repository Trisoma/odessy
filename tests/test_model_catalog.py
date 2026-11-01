import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from odessy.setup.bridge import execute


def test_catalog_defaults_without_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('default suggestions must not contact a provider')
    monkeypatch.setattr('urllib.request.OpenerDirector.open', forbidden)
    result = execute({'operation':'model-catalog','parameters':{'provider':'deepseek'}})
    assert result['status'] == 'suggestions'
    assert result['models'][0]['name'] == 'deepseek-flash'
    assert 'default' in result['models'][0]['label'].lower()


def test_catalog_fetch_uses_ids_and_never_returns_credentials(monkeypatch):
    seen=[]
    class Endpoint(BaseHTTPRequestHandler):
        def log_message(self,*args): pass
        def do_GET(self):
            seen.append((self.path,self.headers.get('Authorization')))
            self.send_response(200);self.end_headers()
            self.wfile.write(json.dumps({'data':[{'id':'deepseek-flash','name':'DeepSeek Flash'},{'id':'another-model'}]}).encode())
    server=ThreadingHTTPServer(('127.0.0.1',0),Endpoint)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    monkeypatch.setenv('CATALOG_TEST_KEY','secret-fixture')
    try:
        reply=execute({'operation':'model-catalog','parameters':{'provider':'deepseek','endpoint':f'http://127.0.0.1:{server.server_port}/v1','credential_env':'CATALOG_TEST_KEY','refresh':True}})
        assert reply['status']=='ok'
        assert {m['name'] for m in reply['models']}=={'deepseek-flash','another-model'}
        assert next(m for m in reply['models'] if m['name']=='deepseek-flash')['label']=='DeepSeek Flash (deepseek-flash)'
        assert seen==[('/v1/models','Bearer secret-fixture')]
        assert 'secret-fixture' not in json.dumps(reply)
    finally:server.shutdown();server.server_close();thread.join()


def test_catalog_missing_key_is_actionable_and_preserves_suggestions(monkeypatch):
    monkeypatch.delenv('DEEPSEEK_API_KEY',raising=False)
    monkeypatch.delenv('ODESSY_CLOUD_API_KEY',raising=False)
    reply=execute({'operation':'model-catalog','parameters':{'provider':'deepseek','refresh':True}})
    assert reply['status']=='credentials_missing'
    assert reply['models']
