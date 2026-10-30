"""Desktop dependency resolution must include its advertised LAN discovery."""
from pathlib import Path
try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib


def test_desktop_and_headless_pairing_both_declare_discovery_dependencies():
    project = tomllib.loads((Path(__file__).parents[1] / 'pyproject.toml').read_text(encoding='utf-8'))
    extras = project['project']['optional-dependencies']
    for extra in ('ui', 'pairing'):
        assert 'zeroconf>=0.131,<1' in extras[extra]
        assert 'ifaddr>=0.2,<1' in extras[extra]
