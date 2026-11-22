#!/bin/sh
# Build OpenRAR for Linux: dist/OpenRAR/ (portable folder + .tar.gz) and a .deb package.
# Needs python3 (3.11 - 3.13) with venv; fakeroot + dpkg-deb for the .deb.
#   ./build.sh               portable build + .deb
#   ./build.sh --appimage    also an AppImage (needs appimagetool, see packaging/build_appimage.sh)
# The result needs the glibc of the build machine or newer; build_in_docker.sh builds on Debian 11 (glibc 2.31)
# so the packages also run on Ubuntu 22.04, Debian 12, Mint 21 and similar.
set -e
cd "$(dirname "$0")"
APPIMAGE=0
for a in "$@"; do
    [ "$a" = "--appimage" ] && APPIMAGE=1
done
VENV=${VENV:-.venv}
export PY="$VENV/bin/python"
[ -x "$PY" ] || python3 -m venv "$VENV"
$PY -m pip install -q --upgrade pip
$PY -m pip install -q -r requirements-dev.txt
$PY -m pytest -q -p no:cacheprovider ../shared
$PY -m pytest -q -p no:cacheprovider
$PY -m PyInstaller --noconfirm --clean --log-level WARN openrar.spec
VER=$($PY -c "import sys; sys.path.insert(0, '../shared'); import openrar; print(openrar.__version__)")
(cd dist && tar -czf "OpenRAR-$VER-linux-x86_64.tar.gz" OpenRAR)
echo "dist/OpenRAR-$VER-linux-x86_64.tar.gz"
if command -v dpkg-deb >/dev/null 2>&1 && command -v fakeroot >/dev/null 2>&1; then
    packaging/build_deb.sh
else
    echo "Skipping .deb (install dpkg-deb and fakeroot)"
fi
[ "$APPIMAGE" = 1 ] && packaging/build_appimage.sh
exit 0
