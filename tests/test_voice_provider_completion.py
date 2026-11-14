"""The smoke provider signals stream completion, including client disconnects."""
import importlib.util
from pathlib import Path
import urllib.request
import json


def test_provider_signals_finished_stream(tmp_path):
    path=Path(__file__).resolve().parents[1]/'experiments/restructure/harness_compatibility/mock_provider.py'
    spec=importlib.util.spec_from_file_location('voice_mock_provider',path)
    provider=importlib.util.module_from_spec(spec);spec.loader.exec_module(provider)
    server,port=provider.serve(tmp_path)
    try:
        prompt='completion regression'
        request=urllib.request.Request(f'http://127.0.0.1:{port}/v1/chat/completions',
            data=json.dumps({'stream':True,'messages':[{'role':'user','content':prompt}]}).encode(),
            headers={'Content-Type':'application/json'})
        with urllib.request.urlopen(request,timeout=5) as response:
            assert b'[DONE]' in response.read()
        event=server.stream_completions[prompt]
        assert event.wait(2)
    finally:
        server.shutdown();server.server_close()
