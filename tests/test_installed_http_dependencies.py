import json
from pathlib import Path


def test_installer_closure_contains_the_http_application_dependencies():
    root = Path(__file__).resolve().parents[1]
    manifest = json.loads((root / 'installer/releases/windows-x64-py312/python-wheels-windows-x64.json').read_text())
    names = {item['id'] for item in manifest['artifacts']}
    assert {'fastapi', 'annotated-doc', 'uvicorn'} <= names
