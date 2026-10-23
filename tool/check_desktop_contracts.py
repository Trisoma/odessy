"""Validate desktop runtime-setting coverage against the executable registry."""
import argparse
import json
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from odessy.config.schema import SETTINGS

def inventory():
    return {'version': 1, 'settings': [
        {'name': s.name, 'kind': s.kind, 'summary': s.summary,
         'source': 'runtime environment', 'screen': screen(s.name),
         'status': 'unavailable', 'reason': 'No desktop configuration management API exists.',
         'apply_timing': 'runtime startup', 'validation': {'choices': list(s.choices), 'minimum': s.minimum, 'maximum': s.maximum}}
        for s in sorted(SETTINGS, key=lambda x: x.name)]}

def screen(name):
    if any(x in name for x in ('VOICE', 'WHISPER', 'TTS', 'WAKE', 'AUDIO')): return 'Voice and audio'
    if any(x in name for x in ('MODEL', 'CLOUD', 'BACKEND', 'OLLAMA')): return 'Models and providers'
    if any(x in name for x in ('HOST', 'SSH', 'DEPLOYMENT')): return 'Devices and deployment'
    return 'Advanced runtime settings'

def validate_inventory(actual, expected):
    if actual != expected:
        raise ValueError('Settings inventory drift; run python tool/check_desktop_contracts.py --write')
    names = [x['name'] for x in actual['settings']]
    if len(set(names)) != len(names):
        raise ValueError('Duplicate settings inventory entries')

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--write', action='store_true')
    args = parser.parse_args()
    path = ROOT / 'docs/ui/settings-inventory.json'
    expected = inventory()
    if args.write: path.write_text(json.dumps(expected, indent=2) + '\n', encoding='utf-8')
    actual = json.loads(path.read_text(encoding='utf-8'))
    validate_inventory(actual, expected)

    print(f"Validated {len(SETTINGS)} runtime settings; unavailable controls do not count as implemented.")
if __name__ == '__main__': main()
