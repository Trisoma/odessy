#!/usr/bin/env bash
# Read-only archive smoke: no model request, automatic pairing or trust mutation.
set -euo pipefail
[[ $# == 1 && $(uname -s) == Linux ]] || { echo 'Usage: check_portable.sh <archive.tar.gz> (Linux)' >&2; exit 2; }
archive=$(realpath "$1")
[[ -f $archive.sha256 ]] || { echo 'Missing external checksum' >&2; exit 2; }
(cd "$(dirname -- "$archive")"; sha256sum -c "$(basename -- "$archive").sha256")
# Extraction is for locally built/reviewed release artifacts, never arbitrary inputs.
if tar -tzf "$archive" | grep -E '(^/|(^|/)\.\.(/|$))'; then echo 'Unsafe archive path' >&2; exit 2; fi
work=$(mktemp -d -t odessy-linux-acceptance-XXXXXXXX)
trap 'rm -rf -- "$work"' EXIT
tar --no-same-owner -xzf "$archive" -C "$work"
mapfile -t roots < <(find "$work" -mindepth 1 -maxdepth 1 -type d)
[[ ${#roots[@]} == 1 ]] || { echo 'Expected one bundle directory' >&2; exit 2; }
bundle=${roots[0]}
cd "$bundle"
./Odessy --verify
./Odessy --diagnose
for component in desktop/odessy_desktop runtime/bin/python3 node/bin/node link/odessy-link monitor/odessy-monitor; do
  if ldd "$component" 2>&1 | grep 'not found'; then echo "Missing runtime library: $component" >&2; exit 2; fi
done
./runtime/bin/python3 -I -c 'import odessy.setup.portable,odessy.application.serve; print("Native portable imports OK")'
echo 'Archive smoke passed. GUI/model/tool/permission/cleanup acceptance still required.'