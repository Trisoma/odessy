#!/usr/bin/env bash
# Native Linux builder. Inputs are explicit, pre-fetched and independently verified.
set -euo pipefail
repo=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd -P)
usage() {
  echo 'Usage: build_portable.sh --python-runtime DIR --node-runtime DIR --wheelhouse DIR --notices DIR --flutter BIN --output FILE.tar.gz' >&2
  exit 2
}
python_root= node_root= wheelhouse= notices= flutter= output=
while (($#)); do
  (($# >= 2)) || usage
  case "$1" in
    --python-runtime) python_root=$2;; --node-runtime) node_root=$2;;
    --wheelhouse) wheelhouse=$2;; --notices) notices=$2;;
    --flutter) flutter=$2;; --output) output=$2;; *) usage;;
  esac
  shift 2
done
[[ $(uname -s) == Linux ]] || { echo 'Build on Linux, not Windows/macOS.' >&2; exit 2; }
[[ -n $python_root && -n $node_root && -n $wheelhouse && -n $notices && -n $flutter && -n $output ]] || usage
case "$(uname -m)" in x86_64) arch=x86_64; flutter_arch=x64;; aarch64) arch=arm64; flutter_arch=arm64;; *) echo 'Unsupported architecture' >&2; exit 2;; esac
for cmd in cargo go cmake ninja clang++ pkg-config tar; do
  command -v "$cmd" >/dev/null || { echo "Missing build prerequisite: $cmd" >&2; exit 2; }
done
pkg-config --exists gtk+-3.0 || {
  echo 'Missing GTK development files. Build machine needs libgtk-3-dev (and liblzma-dev, libstdc++-12-dev on Ubuntu). Testers only need the GTK desktop runtime.' >&2
  exit 2
}
[[ -x $python_root/bin/python3 && -x $node_root/bin/node && -x $node_root/bin/npm && -x $flutter ]] || { echo 'Provide native Linux Python, Node and Flutter inputs.' >&2; exit 2; }
[[ -d $wheelhouse && -f $notices/inventory.json ]] || { echo 'Offline wheelhouse and reviewed notices inventory required.' >&2; exit 2; }
python_root=$(realpath "$python_root"); node_root=$(realpath "$node_root"); wheelhouse=$(realpath "$wheelhouse"); notices=$(realpath "$notices"); flutter=$(realpath "$flutter")
output=$(realpath -m "$output")
[[ ! -e $output ]] || { echo 'Artifact already exists; choose a new output.' >&2; exit 2; }
mkdir -p -- "$(dirname -- "$output")"
# All generated files live in this invocation-owned temporary directory.
build_dir=$(mktemp -d -t odessy-linux-build-XXXXXXXX)
cleanup() { rm -rf -- "$build_dir"; }
trap cleanup EXIT
mkdir -p "$build_dir/runtime" "$build_dir/app" "$build_dir/packages" "$build_dir/link" "$build_dir/monitor" "$build_dir/wheels"
cp -a "$python_root/." "$build_dir/runtime/"
export PATH="$node_root/bin:$build_dir/runtime/bin:$PATH"
export PYTHONNOUSERSITE=1 PYTHONDONTWRITEBYTECODE=1
unset PYTHONHOME PYTHONPATH
python="$build_dir/runtime/bin/python3"
# An explicit offline wheelhouse supplies pip/build dependencies; runtime never downloads.
"$python" -m pip wheel --no-index --find-links "$wheelhouse" --no-build-isolation --no-deps --wheel-dir "$build_dir/wheels" "$repo"
"$python" -m pip install --no-index --find-links "$wheelhouse" --find-links "$build_dir/wheels" 'odessy[ui,cloud]'
"$python" -m pip check
(cd "$repo/apps/desktop"; tar --exclude=build --exclude=.dart_tool --exclude=.git --exclude=ephemeral -cf - .) | (cd "$build_dir/app"; tar -xf -)
# Avoid copying any generated Windows state into a Linux compile.
"$python" -c 'import pathlib,shutil,sys; p=pathlib.Path(sys.argv[1]); [shutil.rmtree(p/n,ignore_errors=True) for n in (".dart_tool","build","linux/flutter/ephemeral","windows/flutter/ephemeral")]' "$build_dir/app"
# Preserve relative path dependency ../../packages/odessy_client.
mkdir -p "$build_dir/apps"; mv "$build_dir/app" "$build_dir/apps/desktop"
cp -a "$repo/packages/odessy_client" "$build_dir/packages/odessy_client"
(cd "$build_dir/apps/desktop"; "$flutter" pub get; "$flutter" build linux --release --target-platform "linux-$flutter_arch")
cp -a "$repo/tools" "$build_dir/tools"
"$python" -c 'import pathlib,shutil,sys; p=pathlib.Path(sys.argv[1]); [shutil.rmtree(p/n,ignore_errors=True) for n in ("node_modules","dist")]' "$build_dir/tools"
(cd "$build_dir/tools"; npm ci --ignore-scripts; npm run build; npm prune --omit=dev --ignore-scripts)
# Existing runtime detection needs tsc only for source builds. Portable bundles use
# their verified compiled entry, not a development compiler.
CARGO_TARGET_DIR="$build_dir/cargo-target" cargo build --release --locked --manifest-path "$repo/native/odessy-link/Cargo.toml"
cp "$build_dir/cargo-target/release/odessy-link" "$build_dir/link/"
(cd "$repo/services/monitoring"; go build -trimpath -o "$build_dir/monitor/odessy-monitor" ./cmd/odessy-monitor)
"$python" -I -c 'import odessy.setup.portable,odessy.application.serve; print("Bundled Python imports OK")'
"$python" "$repo/tool/build_linux_portable.py" --runtime "$build_dir/runtime" \
  --desktop "$build_dir/apps/desktop/build/linux/$flutter_arch/release/bundle" \
  --node "$node_root" --tools "$build_dir/tools" --link "$build_dir/link" \
  --monitor "$build_dir/monitor" --notices "$notices" --architecture "$arch" --output "$output"
printf 'Created %s\nVerify checksum, extract outside the checkout, then run ./Odessy --verify before GUI acceptance.\n' "$output"