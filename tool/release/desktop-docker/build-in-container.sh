#!/usr/bin/env bash
# Runs inside the arm64 build container. Source is a pristine export at /build.
# Writes artifact + checksum to /out. Never touches /work/odessy.
set -euo pipefail

export CI=true

ARCH="${ARCH:-arm64}"
OUT="${OUT:-/out}"
SRC="${SRC:-/build}"
ART="odessy-desktop-v0.5.0-unstable-linux-${ARCH}-ui-client"

# Flutter SDK is a private copy (the /work volume is mounted read-only).
export PATH="${FLUTTER_HOME:-/opt/flutter}/bin:/root/.cargo/bin:$PATH"
mkdir -p "$OUT"


mkdir -p "$OUT"

echo '== identity =='
uname -m
head -2 /etc/os-release
echo '== flutter =='
flutter --version | head -4
echo '== rust =='
cargo --version
rustc --version
echo '== gtk =='
pkg-config --modversion gtk+-3.0

git config --global --add safe.directory "${FLUTTER_HOME:-/opt/flutter}" 2>/dev/null || true

echo '== cargo: odessy-link =='
cd "$SRC"
cargo build --release --locked --manifest-path native/odessy-link/Cargo.toml 2>&1 | tail -5

echo '== flutter pub get =='
cd "$SRC/apps/desktop"
flutter pub get 2>&1 | tail -5

echo '== flutter analyze =='
flutter analyze 2>&1 | tail -15

if [ "${RUN_TESTS:-0}" = "1" ]; then
  echo '== flutter test =='
  flutter test 2>&1 | tail -20
else
  echo 'SKIPPED: flutter test (RUN_TESTS=0)'
fi

echo '== flutter build linux =='
flutter build linux --release 2>&1 | tail -15

echo '== assemble bundle =='
bundle=$(ls -d "$SRC"/apps/desktop/build/linux/*/release/bundle)
cp "$SRC"/native/odessy-link/target/release/odessy-link "$bundle/"
echo "BUNDLE=$bundle"
if command -v file >/dev/null 2>&1; then
  file "$bundle/odessy_desktop"
  file "$bundle/odessy-link"
fi
echo "--- contents ---"
find "$bundle" -type f | sed "s|$bundle|bundle|" | sort
echo "--- file count: $(find "$bundle" -type f | wc -l) ---"

tar -C "$SRC/apps/desktop/build/linux" -czf "$OUT/$ART.tar.gz" bundle
sha256sum "$OUT/$ART.tar.gz" > "$OUT/SHA256SUMS-linux-${ARCH}.txt"
cat "$OUT/SHA256SUMS-linux-${ARCH}.txt"
echo 'BUILD_OK'
