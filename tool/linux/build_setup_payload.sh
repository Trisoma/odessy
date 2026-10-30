#!/usr/bin/env bash
# Build a Linux setup payload so the install -> manage -> uninstall lifecycle can be
# exercised on a real Linux host.
#
# Development use only, not a shippable artifact:
#   * the Python runtime is whatever prefix you pass in (a copied system CPython is
#     acceptable for lifecycle testing, a portable CPython is required for release);
#   * only the python, desktop and wheel components are assembled; the Node tool
#     server, the Rust link binary and the Go monitor are not part of this payload;
#   * wheels are resolved for CPython 3.10 / manylinux2014_x86_64 only.
set -euo pipefail
repo=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd -P)

usage() {
  echo 'Usage: build_setup_payload.sh --python-runtime DIR --desktop-bundle DIR --output payload.zip' >&2
  echo '       --python-runtime needs bin/python3 plus a LICENSE or LICENSE.txt at its root.' >&2
  exit 2
}

python_root= desktop= output=
while (($#)); do
  (($# >= 2)) || usage
  case "$1" in
    --python-runtime) python_root=$2 ;;
    --desktop-bundle) desktop=$2 ;;
    --output) output=$2 ;;
    *) usage ;;
  esac
  shift 2
done
[[ $(uname -s) == Linux ]] || { echo 'Build the Linux payload on Linux.' >&2; exit 2; }
[[ -n $python_root && -n $desktop && -n $output ]] || usage
[[ -x $python_root/bin/python3 ]] || { echo '--python-runtime must contain an executable bin/python3' >&2; exit 2; }
[[ -f $desktop/odessy_desktop ]] || { echo '--desktop-bundle must contain the release desktop binary' >&2; exit 2; }
[[ ! -e $output ]] || { echo 'Output already exists; choose a new artifact.' >&2; exit 2; }

work=$(mktemp -d -t odessy-setup-payload-XXXXXXXX)
trap 'rm -rf -- "$work"' EXIT
mkdir -p "$work/wheelhouse"

echo '== 1/4 build the Odessy wheel =='
# Building without isolation keeps a slow PyPI mirror out of the critical path, but
# the local setuptools then has to understand the PEP 621 [project] table.
if ! python3 -c 'import sys,setuptools; sys.exit(0 if tuple(int(p) for p in setuptools.__version__.split(".")[:2])>=(61,0) else 1)' 2>/dev/null; then
  echo 'setuptools>=61 is required. Install it once, then retry:' >&2
  echo '  python3 -m pip install --user -U setuptools wheel' >&2
  exit 2
fi
python3 -m pip wheel --no-deps --no-build-isolation --wheel-dir "$work/wheelhouse" "$repo"

echo '== 2/4 download manylinux dependencies =='
python3 -m pip download --dest "$work/wheelhouse" --only-binary=:all: --timeout 60 --retries 5 \
  --platform manylinux2014_x86_64 --python-version 3.10 --implementation cp --abi cp310 \
  'mcp>=2,<3' 'jsonschema>=4' 'httpx>=0.27,<1' 'fastapi>=0.115,<1' 'uvicorn>=0.30,<1' 'anthropic>=1.15' \
  'tomli>=2.0.1'

echo '== 3/4 read the licence of every wheel =='
python3 - "$work/wheelhouse" "$work/licenses.json" <<'PY'
import json,pathlib,sys,zipfile
wheels=pathlib.Path(sys.argv[1])
out=pathlib.Path(sys.argv[2])
licences={}
for wheel in sorted(wheels.glob('*.whl')):
    with zipfile.ZipFile(wheel) as archive:
        metadata=next((n for n in archive.namelist() if n.endswith('.dist-info/METADATA')),None)
        if metadata is None:raise SystemExit('wheel has no METADATA: '+wheel.name)
        text=archive.read(metadata).decode('utf-8','replace')
    expressed=[line.split(': ',1)[1].strip() for line in text.splitlines() if line.startswith('License-Expression: ')]
    declared=[line.split(': ',1)[1].strip() for line in text.splitlines() if line.startswith('License: ')]
    classified=[line.split(': ',1)[1].strip() for line in text.splitlines() if line.startswith('Classifier: License ::')]
    value=(next((item for item in expressed if item),None)
           or next((item for item in declared if item and item!='UNKNOWN'),None)
           or (classified[0] if classified else 'UNKNOWN'))
    licences[wheel.name]=value
out.write_text(json.dumps(licences,indent=2))
unknown=[name for name,value in licences.items() if value=='UNKNOWN']
print(f'{len(licences)} wheels; {len(unknown)} without a declared licence')
if unknown:print('  no licence metadata: '+', '.join(sorted(unknown)))
PY

echo '== 3b/4 check the wheelhouse covers every declared requirement =='
# The installer runs `pip install --no-deps`, so a requirement missing here only
# surfaces later as a `pip check` failure after the whole install succeeded.
python3 - "$work/wheelhouse" <<'PY'
import pathlib,sys,zipfile
wheels=pathlib.Path(sys.argv[1])
present={wheel.name.split('-')[0].lower().replace('-','_') for wheel in wheels.glob('*.whl')}
odessy=next(wheels.glob('odessy-*.whl'),None)
if odessy is None: raise SystemExit('the Odessy wheel is missing')
with zipfile.ZipFile(odessy) as archive:
    metadata=next((n for n in archive.namelist() if n.endswith('.dist-info/METADATA')),None)
    if metadata is None: raise SystemExit('the Odessy wheel has no METADATA')
    text=archive.read(metadata).decode('utf-8','replace')
def name_of(requirement):
    head=requirement.split(';')[0].split('[')[0].strip()
    for separator in (' ','<','>','=','!','~'):
        head=head.split(separator)[0]
    return head.strip().lower().replace('-','_')
requirements=[line[len('Requires-Dist: '):] for line in text.splitlines() if line.startswith('Requires-Dist: ')]
# Optional extras are only installed when the extra is requested, so they are not
# part of this payload's coverage.
mandatory=[requirement for requirement in requirements if 'extra ==' not in requirement]
missing=[requirement for requirement in mandatory if name_of(requirement) not in present]
if missing:
    raise SystemExit('wheelhouse does not satisfy the Odessy requirements: '+', '.join(missing))
print(f'{len(present)} wheels cover all {len(mandatory)} mandatory requirements')
PY

echo '== 4/4 assemble the payload =='
python3 "$repo/tool/build_setup_bundle.py" --wheelhouse "$work/wheelhouse" \
  --licenses "$work/licenses.json" --python-runtime "$python_root" \
  --desktop-bundle "$desktop" --output "$output"

echo "Created $output"
echo 'Extract it outside the checkout, then point the setup binary at it with --payload.'
