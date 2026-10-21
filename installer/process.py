"""Installer-only subprocess and ownership helpers; no runtime changes."""
from __future__ import annotations
import contextlib
import os
import shutil
import subprocess
import uuid
from pathlib import Path
from installer.runtime import destination, load_release, DEFAULT_RESOURCES, probe_runtime

class StepError(ValueError):
    pass

def child_env(runtime: Path) -> dict[str,str]:
    env=dict(os.environ)
    for key in list(env):
        if key.upper().startswith(('PYTHON','PIP_','NODE_','NPM_CONFIG_')) or key.upper() in {'VIRTUAL_ENV','CONDA_PREFIX'}:
            env.pop(key)
    env['PATH']=os.pathsep.join((str(runtime/'node-runtime'),str(runtime/'python-runtime'),env.get('PATH','')))
    return env

def validate_runtimes(runtime: Path) -> dict:
    manifest,_=load_release(DEFAULT_RESOURCES)
    return {a.id:probe_runtime(runtime/a.id,a.id,a.version) for a in manifest.artifacts}

def run_step(command: list[str], *, cwd: Path, env: dict, label: str, timeout: float = 300) -> str:
    result=subprocess.run(command,cwd=cwd,env=env,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=timeout,check=False)
    if result.returncode:
        detail=(result.stderr or result.stdout)[-3000:]
        raise StepError(label+' failed (exit '+str(result.returncode)+'): '+detail)
    return result.stdout.strip()

@contextlib.contextmanager
def owned_directory(path: Path):
    """Never replace existing output. Delete only this attempt's owned directory."""
    target=destination(path)
    target.parent.mkdir(parents=True,exist_ok=True)
    target.mkdir()
    marker=target/'.odessy-preparing'
    token=uuid.uuid4().hex
    marker.write_text(token,encoding='ascii')
    try:
        yield target
    except BaseException:
        # Same-user hostile ancestor replacement is outside the supported boundary.
        try:
            owned=target.exists() and not target.is_symlink() and target.resolve()==target and marker.read_text(encoding='ascii')==token
        except OSError:
            owned=False
        if owned:
            shutil.rmtree(target)
        raise
    else:
        marker.unlink()
