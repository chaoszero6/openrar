# PyInstaller spec: builds dist\OpenRAR\ with OpenRAR.exe (GUI) and orar.exe (console CLI).
# Usage:  pyinstaller --noconfirm openrar.spec
import os

block_cipher = None
ROOT = os.path.abspath(SPECPATH)
datas = [(os.path.join(ROOT, "openrar", "resources"), os.path.join("openrar", "resources"))]
hidden = ["pyzipper", "py7zr", "rarfile", "zstandard", "pyppmd", "bcj", "inflate64", "brotli", "Cryptodome",
          "cryptography.hazmat.primitives.ciphers", "cryptography.hazmat.backends"]
excludes = ["tkinter", "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtQml",
            "PySide6.QtQuick", "PySide6.Qt3DCore", "PySide6.QtMultimedia", "PySide6.QtPdf", "PySide6.QtCharts",
            "PySide6.QtDataVisualization", "PySide6.QtBluetooth", "PySide6.QtPositioning", "PySide6.QtSql",
            "PySide6.QtTest", "PySide6.QtDesigner", "PySide6.QtOpenGL", "PySide6.QtOpenGLWidgets"]

gui = Analysis([os.path.join(ROOT, "packaging", "openrar_gui.py")], pathex=[ROOT], datas=datas,
               hiddenimports=hidden, excludes=excludes, cipher=block_cipher)
cli = Analysis([os.path.join(ROOT, "packaging", "orar_cli.py")], pathex=[ROOT], datas=datas,
               hiddenimports=hidden, excludes=excludes + ["PySide6"], cipher=block_cipher)

gui_pyz = PYZ(gui.pure, gui.zipped_data, cipher=block_cipher)
cli_pyz = PYZ(cli.pure, cli.zipped_data, cipher=block_cipher)

icon = os.path.join(ROOT, "openrar", "resources", "icon.ico")
version = os.path.join(ROOT, "packaging", "version_info.txt")
gui_exe = EXE(gui_pyz, gui.scripts, [], exclude_binaries=True, name="OpenRAR", console=False, icon=icon,
              version=version, upx=False)
cli_exe = EXE(cli_pyz, cli.scripts, [], exclude_binaries=True, name="orar", console=True, icon=icon,
              version=version, upx=False)

coll = COLLECT(gui_exe, gui.binaries, gui.datas, cli_exe, cli.binaries, cli.datas, name="OpenRAR", upx=False)
