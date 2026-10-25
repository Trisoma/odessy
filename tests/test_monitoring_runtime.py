import asyncio
import json

import pytest

from odessy.monitoring.runtime import RuntimeObservationSink
from odessy.monitoring.settings import MonitoringSettings


def test_monitoring_is_explicitly_opt_in_and_unknown_hosts_cannot_enable_it():
    assert not MonitoringSettings.from_env({}).enabled
    value = MonitoringSettings.from_env({'ODESSY_MONITORING': 'on', 'ODESSY_MONITOR_HOSTS': 'host_a,host_b'})
    assert value.host_ids == ('host_a', 'host_b')
    with pytest.raises(ValueError):
        MonitoringSettings.from_env({'ODESSY_MONITORING': 'maybe'})


def test_runtime_sink_allowlists_fields_and_never_invents_ttft_or_loaded_model():
    async def run():
        sent = []
        async def send(value): sent.append(value)
        sink = RuntimeObservationSink(send, host_id='host_local', generation='1:local')
        sink.publish({'model': 'local-model', 'prompt_tokens': 10,
                      'prompt': 'secret', 'password': 'secret', 'answer': 'secret',
                      'request_duration_ms': 100, 'ttft_ms': True,
                      'loaded_model': 'unverified'})
        await sink.close()
        assert len(sent) == 1
        ai = sent[0]['ai']
        assert ai['model'] == 'local-model'
        assert ai.get('ttft_ms') is None
        assert 'loaded_model' not in ai
        assert 'secret' not in json.dumps(sent)
    asyncio.run(run())


def test_full_or_failed_monitoring_sink_does_not_block_turns():
    async def run():
        async def send(value): raise RuntimeError('unavailable')
        sink = RuntimeObservationSink(send, host_id='host_local', generation='1:local', capacity=2)
        for _ in range(100): sink.publish({'prompt_tokens': 1})
        await sink.close()
        assert sink.dropped >= 98
    asyncio.run(run())


def test_session_emits_usage_to_sink_without_transcripts_or_actions():
    from odessy.application.session import SessionController
    seen = []
    class Sink:
        def publish(self, value): seen.append(value)
    controller = SessionController(model_factory=lambda _: object(), agent_factory=lambda *_: None,
                                   profiles={'local': {}})
    controller.monitoring_sink = Sink()
    controller.emit('transcript', {'text': 'private'})
    controller.emit('usage', {'prompt_tokens': 4, 'completion_tokens': 2,
                              'generation_tokens_per_sec': 10, 'prompt': 'private'})
    assert len(seen) == 1
    assert seen[0]['prompt_tokens'] == 4
    assert seen[0]['generation_tokens_per_second'] == 10
    assert 'private' not in json.dumps(seen)
