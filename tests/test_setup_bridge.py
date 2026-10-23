import io
import json

from odessy.setup.bridge import run


def call(operation='review', **values):
    request = dict(id='request-1', operation=operation, selection={'role': 'combined'}) | values
    output = io.BytesIO()
    run(io.BytesIO(json.dumps(request).encode()+b'\n'), output)
    return json.loads(output.getvalue())


def test_review_requires_no_model_or_downloads():
    result = call()
    assert result['id'] == 'request-1' and result['status'] == 'ok'
    assert result['result']['downloads'] == []
    assert result['result']['selection']['model'] is None


def test_save_and_recovery_do_not_overwrite(tmp_path):
    target = str(tmp_path / 'selection.json')
    reviewed = call(output=target)['result']
    assert call('save', output=target, confirm_plan='wrong')['status'] == 'error'
    assert not (tmp_path / 'selection.json').exists()
    assert call('save', output=target, confirm_plan=reviewed['plan_id'])['result']['saved']
    assert call('recover', output=target)['result']['recovery'] == 'matches'
    assert call('save', output=target, confirm_plan=reviewed['plan_id'])['status'] == 'error'
    (tmp_path / 'selection.json').write_text('unrelated existing contents')
    assert call('recover', output=target)['result']['recovery'] == 'conflict'


def test_missing_recovery_and_verification_are_truthful(tmp_path):
    assert call('recover', output=str(tmp_path/'missing'))['result']['recovery'] == 'missing'
    assert call('verify')['result']['verification']['ready'] is False


def test_unknown_and_oversized_messages_fail_closed():
    assert call('execute-shell')['status'] == 'error'
    output = io.BytesIO()
    run(io.BytesIO(b'x'*65538), output)
    assert json.loads(output.getvalue())['status'] == 'error'


def test_secret_bearing_endpoint_not_echoed():
    result = call(selection={'role':'combined','model':{'provider':'ollama','name':'x','endpoint':'http://user:secret@localhost'}})
    assert result['status'] == 'error' and 'secret' not in json.dumps(result)
