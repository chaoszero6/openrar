#!/bin/sh
# Build dist/OpenRAR-<version>-x86_64.AppImage from dist/OpenRAR (run build.sh first).
# Needs appimagetool (https://github.com/AppImage/appimagetool/releases) on PATH, or APPIMAGETOOL=/path/to/it.
set -e
cd "$(dirname "$0")/.."
PY=${PY:-.venv/bin/python}
TOOL=${APPIMAGETOOL:-$(command -v appimagetool || true)}
if [ -z "$TOOL" ]; then
    echo "appimagetool not found. Download it from https://github.com/AppImage/appimagetool/releases,"
    echo "make it executable, and run:  APPIMAGETOOL=/path/to/appimagetool-x86_64.AppImage $0"
    exit 1
fi
VERSION=$($PY -c "import sys; sys.path.insert(0, '../shared'); import openrar; print(openrar.__version__)")
APPDIR=build/AppDir
rm -rf "$APPDIR" && mkdir -p "$APPDIR/usr/lib"
cp -a dist/OpenRAR "$APPDIR/usr/lib/openrar"
cat > "$APPDIR/AppRun" <<'RUN'
#!/bin/sh
HERE=$(dirname "$(readlink -f "$0")")
# "OpenRAR.AppImage orar x file.zip" runs the command-line tool
if [ "$1" = "orar" ]; then shift; exec "$HERE/usr/lib/openrar/orar" "$@"; fi
exec "$HERE/usr/lib/openrar/openrar" "$@"
RUN
chmod 755 "$APPDIR/AppRun"
PYTHONPATH=.:../shared $PY - "$APPDIR" <<'PYEOF'
import sys
from openrar_linux import desktop
entry = desktop.desktop_entry(["openrar"]).replace("X-OpenRAR-Generated=true\n", "")
open(sys.argv[1] + "/openrar.desktop", "w").write(entry)
PYEOF
cp ../shared/openrar/resources/icon.png "$APPDIR/openrar.png"
ln -sf openrar.png "$APPDIR/.DirIcon"
OUT="dist/OpenRAR-${VERSION}-x86_64.AppImage"
ARCH=x86_64 "$TOOL" --no-appstream "$APPDIR" "$OUT"
echo "$OUT"
