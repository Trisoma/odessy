from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor

import pytest

from odessy.collaboration.config import (
    CollaborationConfig, CollaborationSettings, ParticipantProfile, load_config, save_config,
)
from odessy.collaboration.participants import CollaborationService


def configured(**settings):
    return CollaborationConfig(CollaborationSettings(enabled=True, **settings), {
        'reviewer': ParticipantProfile('reviewer', 'ollama', model='test'),
    })


def test_missing_config_is_disabled(tmp_path):
    assert load_config(tmp_path / 'absent.toml').settings.enabled is False


def test_default_provider_credential_is_redacted_from_question_and_answer(monkeypatch):
    secret = 'opaque-provider-credential-without-standard-key-prefix'
    monkeypatch.setenv('OPENAI_API_KEY', secret)
    config = CollaborationConfig(CollaborationSettings(enabled=True), {
        'reviewer': ParticipantProfile('reviewer', 'cloud', provider='openai')})
    seen = []
    service = CollaborationService(config, adapters={'reviewer':
        lambda profile, question, context: seen.append(question) or secret})
    result = service.consult(participant='reviewer', question=secret, explicit=True)
    assert secret not in seen[0]
    assert result['answer'] == '<redacted>'


def test_toml_roundtrip_and_environment_path(tmp_path):
    path = tmp_path / 'profiles.toml'
    config = configured(default_participant='reviewer', rules=({'pattern': 'review', 'action': 'allow'},))
    save_config(config, environ={'ODESSY_COLLABORATION_FILE': str(path)})
    loaded = load_config(environ={'ODESSY_COLLABORATION_FILE': str(path)})
    assert loaded == config
    assert loaded.public_dict()['participants']['reviewer']['model'] == 'test'


@pytest.mark.parametrize('text', [
    '[settings]\nenabled="yes"',
    '[settings]\nmax_consultations=true',
    '[settings]\nmax_rounds=100000',
    '[participants.x]\nadapter="cloud"\napi_key="secret"',
    '[participants.x]\nadapter="unknown"',
    '[participants.x]\nadapter="cloud"\ncredential_env="literal-key-value!"',
    '[settings]\nrules=[{pattern="[",action="allow"}]',
    '[participants.x]\nadapter="cloud"\n[participants.x.pricing]\ninput=nan\noutput=1\ncached_input=0',
])
def test_invalid_config_rejected(tmp_path, text):
    path = tmp_path / 'bad.toml'
    path.write_text(text, encoding='utf-8')
    with pytest.raises(ValueError):
        load_config(path)


def test_invalid_save_preserves_previous_file(tmp_path):
    path = tmp_path / 'config.toml'
    save_config(configured(), path)
    before = path.read_bytes()
    with pytest.raises(ValueError):
        save_config({'settings': {'max_rounds': -1}}, path)
    assert path.read_bytes() == before


def test_explicit_consult_redacts_bounds_and_resets_budget():
    received = []
    service = CollaborationService(configured(default_participant='reviewer', max_context_chars=40),
        adapters={'ollama': lambda profile, question, context: received.append((question, context)) or 'advice'})
    assert service.consult(question='hello')['status'] == 'error'
    assert service.consult(question='API_KEY=verysecretvalue', context='API_KEY=verysecretvalue ' + 'x'*100, explicit=True)['answer'] == 'advice'
    assert len(received[0][1]) <= 40
    assert 'verysecretvalue' not in str(received)
    assert service.consult(explicit=True)['status'] == 'error'
    service.start_turn()
    assert service.consult(explicit=True)['status'] == 'ok'


def test_rule_first_match_and_missing_target_no_fallback():
    config = configured(automatic=True, default_participant='reviewer', rules=(
        {'pattern': 'private', 'action': 'deny'},
        {'pattern': 'review', 'participant': 'missing', 'action': 'allow'},
    ))
    service = CollaborationService(config, adapters={'ollama': lambda *args: 'ok'})
    service.start_turn('private review')
    assert service.consult(question='review')['status'] == 'error'
    service.start_turn('review')
    result = service.consult(question='review')
    assert result['status'] == 'error' and result['participant'] == 'missing'


def test_recursive_consult_and_tool_proposal_rejected():
    service = CollaborationService(configured(default_participant='reviewer', max_consultations=5, max_rounds=5))
    service.register_adapter('ollama', lambda *args: service.consult(explicit=True))
    assert service.consult(explicit=True)['status'] == 'error'
    service.register_adapter('ollama', lambda *args: {'answer': 'ok', 'tool_calls': [{'name': 'run'}]})
    assert service.consult(explicit=True)['status'] == 'error'


def test_concurrent_consultations_have_one_budget():
    service = CollaborationService(configured(default_participant='reviewer'), adapters={'ollama': lambda *args: 'ok'})
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(lambda _: service.consult(explicit=True), range(12)))
    assert sum(result['status'] == 'ok' for result in results) == 1


def test_disabled_and_unavailable_are_explicit_errors():
    service = CollaborationService(CollaborationConfig())
    assert service.consult(explicit=True)['status'] == 'error'
    service = CollaborationService(configured(default_participant='absent'))
    assert service.consult(explicit=True)['participant'] == 'absent'

def test_cloud_consult_has_no_tools_and_records_profile_usage(monkeypatch):
    from odessy.models.cloud_model import CloudModel
    from odessy.models.usage import UsageLedger, reset_process_ledger
    ledger = UsageLedger()
    reset_process_ledger(ledger)
    monkeypatch.setenv('REVIEW_CREDENTIAL', 'opaque-credential-value')
    profile = ParticipantProfile('cloudreview', 'cloud', model='test', provider='openai', credential_env='REVIEW_CREDENTIAL')
    config = CollaborationConfig(CollaborationSettings(enabled=True, default_participant='cloudreview'), {'cloudreview': profile})
    payloads = []
    def post(self, payload):
        payloads.append(payload)
        return {'choices': [{'message': {'content': 'reviewed'}}], 'usage': {'prompt_tokens': 12, 'completion_tokens': 4}}
    monkeypatch.setattr(CloudModel, '_post_chat', post)
    result = CollaborationService(config).consult(question='review', explicit=True)
    assert result['answer'] == 'reviewed'
    assert 'tools' not in payloads[0] and 'tool_choice' not in payloads[0]
    assert ledger.total.tokens == 16
    reset_process_ledger()


def test_cloud_consult_rejects_unsolicited_tool_calls(monkeypatch):
    from odessy.models.cloud_model import CloudModel
    monkeypatch.setenv('REVIEW_CREDENTIAL', 'opaque-credential-value')
    profile = ParticipantProfile('cloudreview', 'cloud', model='test', provider='openai', credential_env='REVIEW_CREDENTIAL')
    config = CollaborationConfig(CollaborationSettings(enabled=True, default_participant='cloudreview'), {'cloudreview': profile})
    monkeypatch.setattr(CloudModel, '_post_chat', lambda *args: {'choices': [{'message': {'tool_calls': [{'name': 'run'}]}}]})
    assert CollaborationService(config).consult(explicit=True)['status'] == 'error'


def test_referenced_credential_value_redacted_even_without_token_shape(monkeypatch):
    monkeypatch.setenv('REVIEW_CREDENTIAL', 'opaque-credential-value')
    profile = ParticipantProfile('reviewer', 'ollama', credential_env='REVIEW_CREDENTIAL')
    config = CollaborationConfig(CollaborationSettings(enabled=True, default_participant='reviewer'), {'reviewer': profile})
    received = []
    service = CollaborationService(config, adapters={'ollama': lambda p, q, c: received.append((q,c)) or q})
    result = service.consult(question='opaque-credential-value', context='opaque-credential-value', explicit=True)
    assert 'opaque-credential-value' not in str(received)
    assert 'opaque-credential-value' not in str(result)


def test_missing_rule_action_is_rejected_without_writing(tmp_path):
    with pytest.raises(ValueError):
        save_config({'settings': {'rules': [{'pattern': 'x'}]}}, tmp_path / 'missing.toml')

def test_custom_capabilities_and_modes_roundtrip(tmp_path):
    profile = ParticipantProfile('custom', 'custom_agent', capabilities=('consult', 'delegate', 'summarize_custom'), allowed_modes=('advice', 'patch', 'custom-mode'))
    config = CollaborationConfig(participants={'custom': profile})
    path = tmp_path / 'custom.toml'
    save_config(config, path)
    assert load_config(path) == config


@pytest.mark.parametrize('endpoint', ['https://user:password@example.com/v1', 'https://example.com/v1?api_key=secretvalue', 'file:///etc/passwords', 'not-a-url'])
def test_endpoint_rejects_embedded_credentials_and_non_http_urls(endpoint):
    with pytest.raises(ValueError):
        ParticipantProfile('custom', 'cloud', endpoint=endpoint)


@pytest.mark.parametrize('field,value', [('capabilities', ('bad name',)), ('allowed_modes', ('bad/name',))])
def test_custom_capability_and_mode_names_are_validated(field, value):
    with pytest.raises(ValueError):
        ParticipantProfile('custom', 'custom_agent', **{field: value})


def test_event_callback_failure_does_not_discard_successful_advice():
    def sink(event):
        raise RuntimeError('display disconnected')
    service = CollaborationService(configured(default_participant='reviewer'), adapters={'ollama': lambda *args: 'advice'}, event_sink=sink)
    assert service.consult(explicit=True)['answer'] == 'advice'

def test_explicit_named_participant_is_not_redirected_by_rule():
    service = CollaborationService(configured(default_participant='reviewer', rules=(
        {'pattern': 'review', 'action': 'allow', 'participant': 'other'},)), adapters={'ollama': lambda *args: 'ok'})
    result = service.consult(participant='reviewer', question='review', explicit=True)
    assert result['status'] == 'error'
    assert result['participant'] == 'reviewer'
