# Odessy Linux desktop-UI-client bundle builder.
# (No `# syntax=` directive on purpose: BuildKit's built-in frontend is enough
# and this avoids a Docker Hub pull of docker/dockerfile:1.)
#
# Build context MUST be a pristine export of the candidate commit
# (e.g. `git archive <sha> | tar -x -C <dir>`), never a dirty working tree.
#
# Produces: /out/odessy-desktop-v0.5.0-unstable-linux-<ARCH>-ui-client.tar.gz
#           /out/SHA256SUMS-linux-<ARCH>.txt
#
# The archive contains exactly one top-level directory, `bundle/`, holding the
# Flutter Linux release bundle plus the native `odessy-link` pairing helper.
# It contains NO Python backend, NO Node runtime, NO model/voice assets.

# Base is a build arg so a locally present image is used when the registry is
# unreachable (Docker Hub anonymous-token auth is blocked on this host).
ARG BASE=ubuntu:24.04
FROM ${BASE}

ENV DEBIAN_FRONTEND=noninteractive \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8

# GTK 3 development files + the native toolchain Flutter Linux and
# native/odessy-link require. Runtime-only users need libgtk-3-0 instead.
RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates curl git unzip xz-utils zip file \
      clang cmake ninja-build pkg-config \
      libgtk-3-dev liblzma-dev libstdc++-12-dev \
      build-essential \
    && rm -rf /var/lib/apt/lists/*

# Rust pinned to the same toolchain the Windows build used.
ARG RUST_VERSION=1.92.0
RUN curl -sSf https://sh.rustup.rs | sh -s -- -y --profile minimal \
      --default-toolchain "${RUST_VERSION}" \
    && rm -rf /root/.rustup/downloads /root/.cargo/registry
ENV PATH=/root/.cargo/bin:$PATH

# Flutter pinned to the version the Windows artifact was built with.
ARG FLUTTER_VERSION=3.47.7
RUN curl -fL --retry 5 --retry-delay 3 --retry-all-errors -C - \
      -o /tmp/flutter.tar.xz \
      "https://storage.googleapis.com/flutter_infra_release/releases/stable/linux/flutter_linux_${FLUTTER_VERSION}-stable.tar.xz" \
    && tar -xf /tmp/flutter.tar.xz -C /opt \
    && rm -f /tmp/flutter.tar.xz
ENV PATH=/opt/flutter/bin:$PATH

RUN git config --global --add safe.directory /opt/flutter \
    && flutter config --no-analytics >/dev/null 2>&1 || true
RUN flutter --version && flutter precache --linux

WORKDIR /work/odessy
COPY . /work/odessy

# ARCH is only used for the artifact filename and labels.
ARG ARCH=x86_64
# Tests are platform-independent widget tests, but they are slow under qemu.
ARG RUN_TESTS=0

RUN set -eu; \
    echo '== container =='; uname -m; head -2 /etc/os-release; \
    echo '== flutter =='; flutter --version 2>&1 | head -4; \
    echo '== rust =='; cargo --version; rustc --version

RUN cd apps/desktop && flutter pub get 2>&1 | tail -5

RUN cd apps/desktop && flutter analyze

RUN if [ "${RUN_TESTS}" = "1" ]; then \
      cd apps/desktop && flutter test; \
    else \
      echo 'SKIPPED: flutter test (RUN_TESTS=0)'; \
    fi

RUN cargo build --release --locked --manifest-path native/odessy-link/Cargo.toml

RUN cd apps/desktop && flutter build linux --release

RUN set -eu; \
    mkdir -p /out; \
    bundle=$(ls -d /work/odessy/apps/desktop/build/linux/*/release/bundle); \
    cp /work/odessy/native/odessy-link/target/release/odessy-link "$bundle/"; \
    echo "BUNDLE=$bundle"; \
    echo '--- binary architectures ---'; \
    file "$bundle/odessy_desktop"; \
    file "$bundle/odessy-link"; \
    echo '--- bundle contents ---'; \
    find "$bundle" -type f | sed "s|$bundle|bundle|" | sort | head -40; \
    echo "--- file count: $(find "$bundle" -type f | wc -l) ---"; \
    cd "$(dirname "$bundle")"; \
    tar -czf "/out/odessy-desktop-v0.5.0-unstable-linux-${ARCH}-ui-client.tar.gz" bundle; \
    cd /out; \
    sha256sum "odessy-desktop-v0.5.0-unstable-linux-${ARCH}-ui-client.tar.gz" \
      > "SHA256SUMS-linux-${ARCH}.txt"; \
    cat "SHA256SUMS-linux-${ARCH}.txt"
