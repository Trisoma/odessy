import io
import json

import pytest


class Vault:
    def __init__(self):
        self.values = {}
    def get_password(self, service, account):
        return self.values.get((service, account))
    def set_password(self, service, account, value):
        self.values[service, account] = value
    def delete_password(self, service, account):
        self.values.pop((service, account), None)


@pytest.fixture
def credentials(monkeypatch):
    from odessy import credentials
    credentials.clear_sessions()
    vault = Vault()
    monkeypatch.setattr(credentials, '_backend', lambda: vault)
    yield credentials
    credentials.clear_sessions()


def test_secure_save_resolve_metadata_and_endpoint_isolation(credentials):
    scope = credentials.Scope('deepseek', 'https://api.deepseek.com/v1')
    credentials.set_value(scope, 'sentinel-private-key', storage='keyring', environ={})
    assert credentials.resolve(scope, environ={}) == 'sentinel-private-key'
    status = credentials.describe(scope, environ={})
    assert (status['source'], status['persistence'], status['configured']) == ('keyring', 'persistent', True)
    assert 'sentinel-private-key' not in json.dumps(status)
    assert credentials.resolve(credentials.Scope('deepseek', 'https://other.example/v1'), environ={}) is None
    credentials.remove(scope, storage='keyring', environ={})
    assert credentials.describe(scope, environ={})['source'] == 'none'


def test_precedence_and_environment_shadowing_are_explicit(credentials):
    scope = credentials.Scope('deepseek', '')
    credentials.set_value(scope, 'stored', storage='keyring', environ={})
    credentials.set_value(scope, 'ephemeral', storage='session', environ={})
    assert credentials.resolve(scope, environ={}) == 'ephemeral'
    env = {'ODESSY_CLOUD_API_KEY': 'generic', 'DEEPSEEK_API_KEY': 'provider'}
    assert credentials.resolve(scope, environ=env) == 'generic'
    assert credentials.describe(scope, environ=env)['writable'] is False
    with pytest.raises(credentials.CredentialError):
        credentials.set_value(scope, 'shadowed', storage='keyring', environ=env)
    explicit = credentials.Scope('deepseek', '', 'CUSTOM_MODEL_CREDENTIAL')
    assert credentials.resolve(explicit, environ=env) is None
    credentials.clear_sessions()
    assert credentials.resolve(scope, environ={}) == 'stored'


def test_storage_failures_do_not_fall_back_to_plaintext(credentials, monkeypatch):
    def unavailable():
        raise credentials.CredentialError('Secure storage is unavailable.', code='unavailable')
    monkeypatch.setattr(credentials, '_backend', unavailable)
    scope = credentials.Scope('deepseek', '')
    assert not credentials.describe(scope, environ={})['secure_storage_available']
    with pytest.raises(credentials.CredentialError):
        credentials.set_value(scope, 'never-write-a-file', storage='keyring', environ={})
    credentials.set_value(scope, 'memory-only', storage='session', environ={})
    assert credentials.describe(scope, environ={})['source'] == 'session'


def test_backend_rejects_file_and_chained_keyrings():
    from odessy.credentials import approved_backend
    assert not approved_backend(Vault())
    pytest.importorskip('keyring')
    from keyring.backends.chainer import ChainerBackend
    from keyring.backends.null import Keyring
    assert not approved_backend(ChainerBackend())
    assert not approved_backend(Keyring())


def test_bridge_replies_never_echo_secret(credentials, monkeypatch):
    from odessy.setup.bridge import run
    monkeypatch.delenv('DEEPSEEK_API_KEY', raising=False)
    monkeypatch.delenv('ODESSY_CLOUD_API_KEY', raising=False)
    secret = 'bridge-secret-123456'
    request = {'id': 'credential-test', 'operation': 'model-credential', 'selection': {},
               'parameters': {'action': 'set', 'provider': 'deepseek', 'storage': 'keyring', 'value': secret}}
    output = io.BytesIO()
    assert run(io.BytesIO(json.dumps(request).encode() + b'\n'), output) == 0
    assert secret not in output.getvalue().decode()
    assert json.loads(output.getvalue())['result']['source'] == 'keyring'


def test_node_server_does_not_receive_cloud_credentials(monkeypatch, tmp_path):
    import mcp.client.stdio as stdio
    from odessy.tools.node_mcp import NodeToolServerTransport
    captured = {}
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'must-stay-private')
    monkeypatch.setenv('ODESSY_CLOUD_API_KEY', 'must-stay-private-too')
    def transport(params, **kwargs):
        captured.update(params.env)
        return None
    # node_mcp imports stdio_client lazily inside the transport's __init__, so the
    # patch has to land on the module that lazy import reads, not on node_mcp.
    monkeypatch.setattr(stdio, 'stdio_client', transport)
    NodeToolServerTransport(tmp_path)
    assert captured, 'the Node server environment was never observed'
    assert 'DEEPSEEK_API_KEY' not in captured
    assert 'ODESSY_CLOUD_API_KEY' not in captured
    assert captured['ODESSY_WORKSPACE_ROOT'] == str(tmp_path.resolve())


def test_cloud_adapter_resolves_rotated_key_only_in_authorization(credentials, monkeypatch):
    from odessy.models.cloud_model import build_cloud_model
    scope = credentials.Scope('deepseek', 'https://api.deepseek.com')
    credentials.set_value(scope, 'first-sentinel', storage='keyring', environ={})
    model = build_cloud_model('deepseek', {})
    requests = []
    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): pass
    def open_(request, **kwargs):
        requests.append(request)
        return Response()
    monkeypatch.setattr('urllib.request.build_opener', lambda *args: __import__('types').SimpleNamespace(open=open_))
    model._send_chat({'model': 'test', 'messages': [{'role': 'user', 'content': 'hello'}]}, lambda _: {})
    credentials.set_value(scope, 'second-sentinel', storage='keyring', environ={})
    model._send_chat({'model': 'test'}, lambda _: {})
    assert [r.get_header('Authorization') for r in requests] == ['Bearer first-sentinel', 'Bearer second-sentinel']
    assert all(b'sentinel' not in r.data for r in requests)
    assert 'sentinel' not in repr(model)


def test_native_vault_persists_across_processes_without_revealing_key(monkeypatch):
    import os, subprocess, sys, uuid
    if os.environ.get('ODESSY_TEST_NATIVE_KEYRING') != '1':
        pytest.skip('Opt-in native OS credential storage test with a disposable dummy key.')
    from odessy import credentials
    scope = credentials.Scope('deepseek', 'https://' + uuid.uuid4().hex + '.odessy-qualification.invalid')
    monkeypatch.delenv('ODESSY_CLOUD_API_KEY', raising=False)
    monkeypatch.delenv('DEEPSEEK_API_KEY', raising=False)
    try:
        assert credentials.describe(scope)['secure_storage_available']
        credentials.set_value(scope, 'native-dummy-sentinel', storage='keyring')
        child = subprocess.run([sys.executable, '-c',
            'import sys,json;from odessy.credentials import Scope,resolve,describe;'
            's=Scope("deepseek",sys.argv[1]);assert resolve(s)=="native-dummy-sentinel";print(json.dumps(describe(s)))', scope.endpoint],
            env=dict(os.environ, PYTHONPATH=str(__import__('pathlib').Path(__file__).resolve().parents[1] / 'src')),
            capture_output=True, text=True, timeout=20)
        assert child.returncode == 0, 'Native credential restart check failed.'
        assert json.loads(child.stdout)['source'] == 'keyring'
        assert 'native-dummy-sentinel' not in child.stdout + child.stderr
    finally:
        credentials.remove(scope, storage='keyring')


def test_private_session_envelope_is_atomic_and_clearable(credentials):
    scope = credentials.Scope('deepseek', '')
    entry = {'provider': 'deepseek', 'value': 'ephemeral-test'}
    credentials.read_startup(io.BytesIO(json.dumps({'session_credentials': [entry]}).encode() + b'\n'))
    assert credentials.resolve(scope, environ={}) == 'ephemeral-test'
    with pytest.raises(credentials.CredentialError):
        credentials.read_startup(io.BytesIO(b'{"session_credentials":[{"provider":"deepseek","value":""}]}\n'))
    assert credentials.resolve(scope, environ={}) == 'ephemeral-test'
    credentials.load_sessions([])
    assert credentials.resolve(scope, environ={}) is None


def test_provider_never_forwards_authorization_to_another_origin(credentials):
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from odessy.models.cloud_model import build_cloud_model
    reached = []
    class Other(BaseHTTPRequestHandler):
        def do_GET(self):
            reached.append(self.headers.get('Authorization'))
            self.send_response(200); self.end_headers()
        def log_message(self, *args): pass
    other = ThreadingHTTPServer(('127.0.0.1', 0), Other)
    class Redirect(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers.get('Content-Length', '0')))
            self.send_response(302)
            self.send_header('Location', f'http://127.0.0.1:{other.server_port}/stolen')
            self.send_header('Content-Length', '0')
            self.end_headers()
        def log_message(self, *args): pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Redirect)
    threads = [threading.Thread(target=s.serve_forever, daemon=True) for s in (server, other)]
    for worker in threads: worker.start()
    try:
        model = build_cloud_model('custom', {'ODESSY_CLOUD_API_KEY': 'redirect-sentinel',
            'ODESSY_CLOUD_BASE_URL': f'http://127.0.0.1:{server.server_port}', 'ODESSY_CLOUD_MODEL': 'test'})
        with pytest.raises(RuntimeError, match='302'):
            model._send_chat({'model': 'test'}, lambda response: response.read())
        assert reached == []
    finally:
        for s in (server, other): s.shutdown(); s.server_close()
        for worker in threads: worker.join(2)


def test_real_tool_child_cannot_read_ambient_model_keys(credentials):
    import os, subprocess, sys
    env = credentials.tool_environment(dict(os.environ, DEEPSEEK_API_KEY='private-model-sentinel',
        ODESSY_CLOUD_API_KEY='private-generic-sentinel', ODESSY_API_TOKEN='private-session-sentinel'))
    child = subprocess.run([sys.executable, '-c',
        'import os,json;print(json.dumps([os.getenv(n) is None for n in '
        '["DEEPSEEK_API_KEY","ODESSY_CLOUD_API_KEY","ODESSY_API_TOKEN"]]))'],
        env=env, capture_output=True, text=True, timeout=10)
    assert child.returncode == 0
    assert json.loads(child.stdout) == [True, True, True]


def test_provider_echoed_credentials_are_removed_from_errors(credentials, monkeypatch):
    import urllib.error
    from odessy.models.cloud_model import build_cloud_model
    model = build_cloud_model('deepseek', {'DEEPSEEK_API_KEY': 'private-opaque-sentinel'})
    def fail(*args, **kwargs):
        raise urllib.error.HTTPError('https://provider.invalid', 401, 'error', {}, io.BytesIO(b'{"error":"private-opaque-sentinel rejected"}'))
    monkeypatch.setattr('urllib.request.build_opener', lambda *args: __import__('types').SimpleNamespace(open=fail))
    with pytest.raises(RuntimeError) as error:
        model._send_chat({}, lambda response: response.read())
    assert 'private-opaque-sentinel' not in str(error.value)
    assert '<redacted>' in str(error.value)


def test_invalid_cloud_endpoint_remains_a_configuration_error(credentials):
    from odessy.models.cloud_model import build_cloud_model, CloudModelConfigError
    with pytest.raises(CloudModelConfigError) as error:
        build_cloud_model('custom', {'ODESSY_CLOUD_MODEL': 'test', 'ODESSY_CLOUD_API_KEY': 'sentinel',
            'ODESSY_CLOUD_BASE_URL': 'https://provider.invalid/v1?api_key=sentinel'})
    assert 'sentinel' not in str(error.value)


def test_claude_errors_and_session_success_redact_rotated_values(credentials):
    from types import SimpleNamespace
    from odessy.models.cloud_model import build_cloud_model, _claude_error
    from odessy.application.session import SessionController
    scope = credentials.Scope('claude', '')
    credentials.set_value(scope, 'old-private-fixture', storage='session', environ={})
    model = build_cloud_model('claude', {})
    credentials.set_value(scope, 'rotated-private-fixture', storage='session', environ={})
    model._resolved_api_key()
    session = SessionController(model_factory=lambda name: model, agent_factory=lambda *a, **k: None, profiles={'current': {}})
    assert 'rotated-private-fixture' not in str(session._public({'text': 'Response: rotated-private-fixture'}))
    class Error(Exception):
        status_code = 401
        message = 'Rejected rotated-private-fixture'
    error = _claude_error(SimpleNamespace(APIConnectionError=ConnectionError), Error(), model.api_key)
    assert 'rotated-private-fixture' not in str(error)


def test_diagnostic_success_does_not_echo_managed_key(credentials, monkeypatch):
    from odessy.setup.contracts import SetupSelection, ModelSelection
    from odessy.setup.diagnostics import test_model
    from odessy.models.cloud_model import CloudModel
    scope = credentials.Scope('deepseek', '')
    credentials.set_value(scope, 'diagnostic-managed-fixture', storage='session', environ={})
    monkeypatch.delenv('DEEPSEEK_API_KEY', raising=False)
    monkeypatch.delenv('ODESSY_CLOUD_API_KEY', raising=False)
    monkeypatch.setattr(CloudModel, 'test_connection', lambda self: 'Echo diagnostic-managed-fixture')
    result = test_model(SetupSelection(role='combined', model=ModelSelection('deepseek', 'test')))
    assert 'diagnostic-managed-fixture' not in json.dumps(result)
