"""Fingerprint the inputs used to resolve the private runtime dependency lock."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path


def fingerprint(path: Path) -> str:
    try:
        import tomllib
    except ModuleNotFoundError:  # Supported development/test Python 3.10.
        import tomli as tomllib
    project=tomllib.loads(path.read_text(encoding='utf-8'))['project']
    dependencies=project.get('dependencies',[])
    requires_python=project.get('requires-python')
    if (not isinstance(dependencies,list) or any(not isinstance(value,str) for value in dependencies)
        or (requires_python is not None and not isinstance(requires_python,str))
        or 'dependencies' in project.get('dynamic',[])):
        raise ValueError('Expected static runtime dependencies and a Python version requirement.')
    # Extras (test, cloud, UI, voice) are not installed by this dependency subset.
    value={'requires-python':requires_python,'dependencies':sorted(dependencies)}
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True).encode()).hexdigest()
