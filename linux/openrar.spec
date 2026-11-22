# PyInstaller spec: builds linux/dist/OpenRAR/ with `openrar` (GUI) and `orar` (command line).
# Usage (from the linux folder):  pyinstaller --noconfirm openrar.spec     (build.sh does this)
import os

ROOT = os.path.abspath(SPECPATH)                      # linux/
SHARED = os.path.join(os.path.dirname(ROOT), "shared")  # shared engine + Qt GUI
datas = [(os.path.join(SHARED, "openrar", "resources"), os.path.join("openrar", "resources"))]
hidden = ["pyzipper", "py7zr", "rarfile", "zstandard", "pyppmd", "bcj", "inflate64", "brotli", "Cryptodome",
          "cryptography.hazmat.primitives.ciphers", "cryptography.hazmat.primitives.ciphers.aead",
          "cryptography.hazmat.backends", "keyring", "keyring.backends.SecretService", "keyring.backends.kwallet",
          "openrar_linux", "openrar_linux.desktop", "openrar_linux.secrets"]
excludes = ["tkinter", "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtQml",
            "PySide6.QtQuick", "PySide6.Qt3DCore", "PySide6.QtMultimedia", "PySide6.QtPdf", "PySide6.QtCharts",
            "PySide6.QtDataVisualization", "PySide6.QtBluetooth", "PySide6.QtPositioning", "PySide6.QtSql",
            "PySide6.QtTest", "PySide6.QtDesigner", "PySide6.QtOpenGL", "PySide6.QtOpenGLWidgets"]

gui = Analysis([os.path.join(ROOT, "packaging", "openrar_gui.py")], pathex=[SHARED, ROOT], datas=datas,
               hiddenimports=hidden, excludes=excludes)
cli = Analysis([os.path.join(ROOT, "packaging", "orar_cli.py")], pathex=[SHARED, ROOT], datas=datas,
               hiddenimports=hidden, excludes=excludes + ["PySide6"])
# Desktop libraries come from the user's system (as in the AppImage exclude list): bundling the build machine's
# older copies next to the system's newer ones crashes Qt (e.g. bundled libxkbcommon + system libxkbcommon-x11).
# Python's own old dependencies (libffi.so.7, OpenSSL 1.1, readline) stay bundled because new distros lack them.
import re  # noqa: E402

SYSTEM_LIBS = re.compile(
    r"^lib(X11|X11-xcb|Xau|Xdmcp|Xext|Xrender|Xi|Xfixes|xcb(-[\w-]+)?|xkbcommon(-x11)?|GL|EGL|GLX|GLdispatch|OpenGL|"
    r"gbm|drm|wayland-[\w-]+|fontconfig|freetype|harfbuzz|fribidi|thai|datrie|graphite2|pixman-1|png16|"
    r"cairo(-gobject)?|pango(cairo|ft2)?-1\.0|gdk_pixbuf-2\.0|glib-2\.0|gio-2\.0|gobject-2\.0|gmodule-2\.0|"
    r"gthread-2\.0|dbus-1|systemd|gcrypt|gpg-error|selinux|blkid|mount|uuid|expat|pcre|pcre2-8|stdc\+\+|gcc_s|"
    r"atomic|bsd|md|lz4|com_err|krb5|krb5support|k5crypto|gssapi_krb5|keyutils|z)\.so(\.\d+)*$")


def _without_system_libs(binaries):
    return [b for b in binaries if not SYSTEM_LIBS.match(os.path.basename(b[0]))]


gui.binaries = _without_system_libs(gui.binaries)
cli.binaries = _without_system_libs(cli.binaries)

gui_exe = EXE(PYZ(gui.pure), gui.scripts, [], exclude_binaries=True, name="openrar", console=False, strip=True)
cli_exe = EXE(PYZ(cli.pure), cli.scripts, [], exclude_binaries=True, name="orar", console=True, strip=True)
coll = COLLECT(gui_exe, gui.binaries, gui.datas, cli_exe, cli.binaries, cli.datas, name="OpenRAR", strip=True)
