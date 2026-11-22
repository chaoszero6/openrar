#!/bin/sh
# Build the Linux packages inside Debian 11 (glibc 2.31), so they run on older distributions too
# (Ubuntu 22.04+, Debian 11+, Mint 21+, Fedora 34+). Arguments are passed to build.sh.
# Debian 11 is out of support, so only its main repository is used (for build and test tools only).
set -e
cd "$(dirname "$0")/.."
IMAGE=${IMAGE:-python:3.12-bullseye}
docker run --rm -v "$PWD:/src" -w /src/linux -e VENV=/tmp/venv -e HOME=/tmp -e PIP_CACHE_DIR=/src/linux/.cache/pip \
    "$IMAGE" sh -c 'sed -i "/security/d" /etc/apt/sources.list /etc/apt/sources.list.d/* 2>/dev/null
        apt-get update -qq && apt-get install -y -qq --no-install-recommends fakeroot libgl1 libegl1 \
        libxkbcommon0 libfontconfig1 libdbus-1-3 libglib2.0-0 >/dev/null && ./build.sh "$@"' sh "$@"
