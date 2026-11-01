# Desktop UI-client release builds (v0.5.0-unstable)

Docker recipes that produced the Linux desktop archives for `v0.5.0-unstable`.
They are kept here for reproducibility and provenance. Nothing here is needed to
install or run Odessy.

Built from `169f5db`. The `v0.5.0-unstable` tag (`71062b2`) differs from that commit
by `docs/releases/v0.5.0-unstable-notes.md` only — no code files — so these recipes
correspond to the tagged source.

## What each file is

| File | Purpose |
|---|---|
| `Dockerfile` | Builds the **linux/x86_64** desktop bundle. Produces `/out/odessy-desktop-v0.5.0-unstable-linux-<ARCH>-ui-client.tar.gz` plus a SHA-256 file. |
| `build-in-container.sh` | Drives the **linux/arm64** build inside an emulated container, assumes an existing Flutter SDK and Rust toolchain. |
| `lock-probe.dart` | Standalone evidence for the `setup_usability_test.dart` finding: a second `FileLock.exclusive` acquire in the same process **succeeds** on POSIX. Run with `dart lock-probe.dart`. |

## x86_64

Build context must be a pristine export of the commit, not a working tree:

```bash
git archive <sha> | tar -x -C /tmp/odessy-src
docker build --platform linux/amd64 \
  --build-arg BASE=ubuntu:latest \
  --build-arg ARCH=x86_64 \
  --build-arg RUN_TESTS=0 \
  -f Dockerfile -t odessy-linux-build:amd64 /tmp/odessy-src
docker create --name odessy-artifact odessy-linux-build:amd64
docker cp odessy-artifact:/out/ ./
docker rm odessy-artifact
```

`BASE` defaults to `ubuntu:24.04`. Override it when the Docker Hub registry is
unreachable, which is why the arg exists.

## arm64

`build-in-container.sh` was written for a specific emulated container and is kept
verbatim rather than generalised. Its assumptions:

- the source tree is at `/build` (a pristine export)
- a Flutter SDK is at `/opt/flutter`, already precached for `linux-arm64`
- Rust is at `/root/.cargo/bin`
- `ARCH=arm64`, `RUN_TESTS=0`

```bash
docker run -d --name build --platform linux/arm64 <image> sleep infinity
docker cp /tmp/odessy-src build:/build
docker cp build-in-container.sh build:/build-in-container.sh
docker exec build bash -lc 'ARCH=arm64 RUN_TESTS=0 bash /build-in-container.sh'
```

## Known limits of these recipes

- **The arm64 build is not fully reproducible from this directory alone.** It reused
  a pre-existing container with Flutter 3.47.8 / Dart 3.13.7 / Rust 1.99.1, while the
  x86_64 build installs Flutter 3.47.7 / Dart 3.13.5 / Rust 1.92.0. `desktop-preview.yml`
  pins 3.47.7.
- **`RUN_TESTS=0` is deliberate**, not an oversight. `flutter test` cannot pass on
  Linux at this commit: `setup_usability_test.dart` asserts per-handle lock semantics
  that only hold on Windows. See `docs/releases/v0.5.0-unstable-notes.md`.
- The archives contain the Flutter desktop bundle and the native `odessy-link` helper
  only. They are **not** the portable bundle from `tool/linux/build_portable.sh`: no
  Python or Node runtime and no notices inventory are included.
- Built under qemu emulation for arm64. That proves the build, not that it runs on a
  device. No archive has been executed end to end.
