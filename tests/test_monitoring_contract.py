import copy
import json
from pathlib import Path

import jsonschema
import pytest

ROOT = Path(__file__).resolve().parents[1] / 'contracts' / 'monitoring'


def validate(value):
    schema = json.loads((ROOT / 'observation.schema.json').read_text())
    jsonschema.Draft202012Validator(schema, format_checker=jsonschema.FormatChecker()).validate(value)


def test_sample_fixture_is_valid():
    validate(json.loads((ROOT / 'fixtures' / 'sample.json').read_text()))


@pytest.mark.parametrize('value', [True, -1, 1.5, '1024', float('inf')])
def test_invalid_byte_values_are_rejected(value):
    sample = json.loads((ROOT / 'fixtures' / 'sample.json').read_text())
    sample['memory']['total_bytes'] = value
    with pytest.raises(jsonschema.ValidationError):
        validate(sample)


def test_unsupported_null_is_distinct_from_zero_and_secret_fields_rejected():
    sample = json.loads((ROOT / 'fixtures' / 'sample.json').read_text())
    sample['gpu'] = {'status': 'unsupported', 'reason': 'unavailable', 'devices': []}
    validate(sample)
    bad = copy.deepcopy(sample)
    bad['password'] = 'secret'
    with pytest.raises(jsonschema.ValidationError):
        validate(bad)
    bad = copy.deepcopy(sample)
    bad['network']['devices'] = [{'id': str(i)} for i in range(65)]
    with pytest.raises(jsonschema.ValidationError):
        validate(bad)
    sample['schema_version'] = 2
    with pytest.raises(jsonschema.ValidationError):
        validate(sample)


def test_worker_rejects_unknown_commands_and_fields():
    schema = json.loads((ROOT / 'worker.schema.json').read_text())
    validator = jsonschema.Draft202012Validator(schema)
    validator.validate({'schema_version': 1, 'id': 'a', 'op': 'inventory'})
    for value in [
        {'schema_version': 1, 'id': 'a', 'op': 'shell'},
        {'schema_version': 1, 'id': 'a', 'op': 'inventory', 'command': 'whoami'},
    ]:
        with pytest.raises(jsonschema.ValidationError):
            validator.validate(value)
