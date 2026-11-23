"""Package the existing experimental LAN executor with the existing offline builder.

No desktop, model, voice or new installer. Inputs must be a reviewed platform-matched
wheelhouse, Node runtime and packaged tool-server closure, from the same candidate.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import shutil
import tempfile
from pathlib import Path
import zipfile
from build_setup_bundle import build

LAUNCHER = r'''"""Run an explicitly installed executor; opening the archive never starts sharing."""
import argparse
import json
import os
from pathlib import Path
import subprocess

parser = argparse.ArgumentParser(description='Start experimental trusted-LAN sharing')
parser.add_argument('--installation', required=True, type=Path)
parser.add_argument('--workspace', required=True, type=Path)
parser.add_argument('--host', help='Local LAN IPv4 interface')
args = parser.parse_args()
root = args.installation.resolve()
record = json.loads((root / 'installation.json').read_text(encoding='utf-8'))
active = record['active']
if not isinstance(active, str) or len(active) != 32 or any(c not in '0123456789abcdef' for c in active):
    raise SystemExit('Invalid installation identity')
base = root / 'versions' / active
python = base / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
checked = subprocess.run([str(python), '-I', '-m', 'odessy.setup.manage', 'verify', '--root', str(root)],
                         capture_output=True, text=True, encoding='utf-8', check=True)
if not json.loads(checked.stdout).get('valid'):
    raise SystemExit('Installation verification failed; repair the candidate before sharing')
node = base / 'node' / ('node.exe' if os.name == 'nt' else 'bin/node')
tools = base / 'tools'
if not node.is_file() or not (tools / 'dist/src/server.js').is_file():
    raise SystemExit('The matched Node runtime and tool server are required')
env = dict(os.environ, ODESSY_NODE_TOOLS_DIR=str(tools), ODESSY_PORTABLE_ROOT=str(base))
env['PATH'] = str(node.parent) + os.pathsep + env.get('PATH', '')
command = [str(python), '-I', '-m', 'odessy.application.executor', '--workspace', str(args.workspace.resolve())]
if args.host:
    command += ['--host', args.host]
raise SystemExit(subprocess.call(command, env=env, cwd=base))
'''

INSTALLER = r'''"""Headless front door to Odessy's existing reviewed offline installer."""
import argparse
import json
from pathlib import Path
import sys

payload = Path(__file__).resolve().parent / 'payload'
sys.path.insert(0, str(payload / 'bootstrap'))
from odessy.setup.maintenance import ManagedInstallation
from odessy.setup.contracts import SetupSelection

parser = argparse.ArgumentParser(description='Review/install an isolated experimental executor')
parser.add_argument('operation', choices=('plan', 'install', 'verify'))
parser.add_argument('--root', required=True)
parser.add_argument('--confirm')
args = parser.parse_args()
runtime = payload / 'runtime'
manager = ManagedInstallation(args.root, payload=payload,
    runtime_source=runtime if runtime.is_dir() else None)
selection = SetupSelection(role='execution')
if args.operation == 'plan':
    result = manager.plan(payload / 'wheelhouse', selection)
elif args.operation == 'install':
    result = manager.install(payload / 'wheelhouse', selection, args.confirm)
else:
    result = manager.verify()
print(json.dumps(result, indent=2))
'''

GUIDE = """# OdessyExecutor — experimental trusted-LAN preview

This archive uses Odessy's existing executor and offline installation pipeline.
It needs an existing supported Python with venv/pip. It contains no Flutter app,
model or speech assets. Node and the tool-server closure remain required; this
is not a tiny self-contained executable. Match OS, CPU and Python wheel ABI.

1. Verify this archive's publisher-provided SHA-256, then extract payload.zip into payload/.
   On POSIX use `unzip payload.zip -d payload` to preserve executable permissions
   on the bundled node/bin/node binary.
2. Review the exact isolated installation plan (choose a NEW dedicated root):
   python install-executor.py plan --root <executor-root>
3. Apply only the reviewed plan_id; nothing starts or registers a service automatically:
   python install-executor.py install --root <executor-root> --confirm <plan_id>
4. Start explicitly in a terminal (the directory is the ONLY shared workspace):
   python run-executor.py --installation <executor-root> --workspace <shared-directory>
   Add --host <local-LAN-IPv4> when selecting a specific network interface.
5. In the coordinator open Devices, choose this host, then approve Y/N in the
   executor terminal. Review the requester and directory before accepting.
   Stop with Ctrl+C; credentials expire with that process. Existing action,
   confinement and approval policies remain enforced.

Experimental LAN transport is unencrypted. Use a trusted private network, never
public exposure. SSH monitoring is separate and does not grant execution.
No curl-to-shell bootstrap, trust edits, model download or background service.
Release publication and live LAN qualification are separate gates.
"""


def package(wheels, licenses, output, *, node, toolserver, runtime=None):
    output = Path(output)
    # Refuse incomplete entrypoints before producing a release-shaped artifact.
    candidates = list(Path(wheels).glob('odessy-*.whl'))
    if len(candidates) != 1:
        raise ValueError('Exactly one source-matched Odessy wheel is required')
    with zipfile.ZipFile(candidates[0]) as archive:
        if 'odessy/application/executor.py' not in archive.namelist():
            raise ValueError('Odessy wheel lacks executor')
        entry = [n for n in archive.namelist() if n.endswith('.dist-info/entry_points.txt')]
        if len(entry) != 1 or 'odessy-executor' not in archive.read(entry[0]).decode():
            raise ValueError('Odessy wheel lacks executor console entry point')
    names = [p.name.lower() for p in Path(wheels).glob('*.whl')]
    if not all(any(n.startswith(prefix) for n in names) for prefix in ('zeroconf-', 'ifaddr-')):
        raise ValueError('Executor wheelhouse needs the pinned pairing dependency closure')
    if not (Path(toolserver) / 'dist/src/server.js').is_file():
        raise ValueError('Packaged built tool server is required')
    output.mkdir(parents=True, exist_ok=False)
    payload = build(wheels, licenses, output/'payload.zip', node=node, toolserver=toolserver, runtime=runtime)
    if runtime is None:
        # The generic builder declares Python for its wheel/bootstrap component.
        # ManagedInstallation's component inventory means an actual runtime/
        # directory. This Python-required executor has none: retain the existing
        # interpreter prerequisite instead of claiming an empty bundled runtime.
        with tempfile.TemporaryDirectory(dir=output) as temporary:
            corrected = Path(temporary) / 'payload.zip'
            with zipfile.ZipFile(payload) as source, zipfile.ZipFile(
                    corrected, 'w', compression=zipfile.ZIP_DEFLATED) as target:
                for info in source.infolist():
                    if info.filename == 'setup-context.json':
                        context = json.loads(source.read(info))
                        context['components'] = [c for c in context['components'] if c != 'python']
                        target.writestr(info.filename, json.dumps(context, indent=2))
                    else:
                        with source.open(info) as before, target.open(info, 'w') as after:
                            shutil.copyfileobj(before, after, length=1024 * 1024)
            corrected.replace(payload)
    (output/'install-executor.py').write_text(INSTALLER, encoding='utf-8')
    (output/'run-executor.py').write_text(LAUNCHER, encoding='utf-8')
    (output/'README.md').write_text(GUIDE, encoding='utf-8')
    files = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(output.iterdir()) if p.is_file()}
    (output/'manifest.json').write_text(json.dumps({'schema_version': 1, 'experimental': True,
        'entry_point': 'odessy.application.executor', 'files': files}, indent=2)+'\n', encoding='utf-8')
    return payload


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for arg in ('wheelhouse', 'licenses', 'output', 'node', 'toolserver'):
        parser.add_argument('--'+arg, required=True)
    parser.add_argument('--python-runtime')
    args = parser.parse_args()
    print(package(args.wheelhouse, args.licenses, args.output, node=args.node,
                  toolserver=args.toolserver, runtime=args.python_runtime))
