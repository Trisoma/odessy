#!/usr/bin/env bash
# Assemble a distributable Linux setup folder: the desktop binary, its runtime data
# and the payload beside it, so setup asks for no internal path.
set -euo pipefail

usage() {
  echo 'Usage: package_setup_app.sh --bundle DIR --payload ZIP|DIR --output DIR' >&2
  echo '       --bundle is apps/desktop/build/linux/<arch>/release/bundle' >&2
  exit 2
}

bundle= payload= output=
while (($#)); do
  (($# >= 2)) || usage
  case "$1" in
    --bundle) bundle=$2 ;;
    --payload) payload=$2 ;;
    --output) output=$2 ;;
    *) usage ;;
  esac
  shift 2
done
[[ -n $bundle && -n $payload && -n $output ]] || usage
[[ $(uname -s) == Linux ]] || { echo 'Package the Linux setup folder on Linux.' >&2; exit 2; }
[[ -x $bundle/odessy_desktop ]] || { echo '--bundle must be a Flutter release bundle' >&2; exit 2; }
[[ -e $payload ]] || { echo '--payload does not exist' >&2; exit 2; }
[[ ! -e $output ]] || { echo 'Output already exists; choose a new folder.' >&2; exit 2; }

mkdir -p "$output"
cp -a "$bundle/." "$output/"
mv "$output/odessy_desktop" "$output/OdessySetup"
chmod 755 "$output/OdessySetup"

if [[ -d $payload ]]; then
  cp -a "$payload" "$output/payload"
else
  mkdir -p "$output/payload"
  unzip -q "$payload" -d "$output/payload"
fi
[[ -f $output/payload/setup-context.json ]] || { echo 'payload lacks setup-context.json' >&2; exit 2; }

# The discoverable entry point: no arguments, no payload path, no elevation.
cat > "$output/install.sh" <<'SH'
#!/bin/sh
set -eu
here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec "$here/OdessySetup" "$@"
SH
chmod 755 "$output/install.sh"

cat > "$output/README.txt" <<'TXT'
Odessy setup (Linux x86_64, Ubuntu 22.04)

Start the installer with ./install.sh, or by running ./OdessySetup directly.
The payload beside the binary is found automatically; no other path is needed and
no administrator rights are requested.

Odessy installs into your own account and adds an application-menu entry with
Manage and Uninstall actions. Keep this folder until the installation completes:
management keeps working afterwards because the verified payload is cached outside
the installation.

If the desktop shows blank panels or missing text (virtual machines and remote
desktops without GPU acceleration), the setup app already falls back to software
rendering on its own. ODESSY_DISABLE_IMPELLER=1 forces that fallback if a driver
is not recognised:

  ODESSY_DISABLE_IMPELLER=1 ./install.sh
TXT

echo "Created $output"
echo 'Verify by running it, then archive the folder for hand-off.'
