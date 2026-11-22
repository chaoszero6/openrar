"""Linux plugin: desktop entry, MIME defaults, Dolphin / Nemo / Nautilus menus, keyring-protected passwords."""
import configparser
import os
import shutil
import subprocess
import sys

import pytest

from openrar import platform
from openrar.integration import DEFAULT_ASSOCIATIONS

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="Linux integration")

CFG = {"cascaded": True, "add": True, "add_quick": True, "extract": True, "extract_here": True,
       "extract_to": True, "test": False, "associate": list(DEFAULT_ASSOCIATIONS)}


@pytest.fixture
def xdg(tmp_path, monkeypatch):
    data, conf = tmp_path / "data", tmp_path / "config"
    monkeypatch.setenv("XDG_DATA_HOME", str(data))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(conf))
    conf.mkdir()
    (conf / "mimeapps.list").write_text("[Default Applications]\napplication/zip=org.gnome.FileRoller.desktop\n"
                                        "text/plain=gedit.desktop\n")
    return data, conf


def test_plugin_loads():
    p = platform.load("linux")
    assert p.name == "linux" and p.supports_integration
    assert p.app_command()[-1].endswith("run_openrar.py")


def test_install_and_uninstall(xdg, tmp_path):
    from openrar_linux import desktop
    data, conf = xdg
    app = [sys.executable, str(tmp_path / "odd dir $x" / "run_openrar.py")]
    icon = os.path.join(os.path.dirname(__file__), "..", "..", "shared", "openrar", "resources", "icon.png")
    desktop.install(CFG, app, icon)

    entry = data / "applications" / "openrar.desktop"
    if shutil.which("desktop-file-validate"):
        r = subprocess.run(["desktop-file-validate", str(entry)], capture_output=True, text=True)
        assert r.returncode == 0, r.stdout + r.stderr
    cp = configparser.ConfigParser(interpolation=None)
    cp.read(entry)
    assert cp["Desktop Entry"]["Exec"] == f'{sys.executable} "{tmp_path}/odd dir \\\\$x/run_openrar.py" %F'
    assert "application/vnd.rar" in cp["Desktop Entry"]["MimeType"]
    assert (data / "icons" / "hicolor" / "48x48" / "apps" / "openrar.png").exists()

    kf6 = data / "kio" / "servicemenus" / "openrar-extract.desktop"
    assert os.access(kf6, os.X_OK) and "X-KDE-Submenu=OpenRAR" in kf6.read_text()
    assert "--extract-subfolder %F" in kf6.read_text() and "a_add" not in kf6.read_text()
    assert "--add-dialog %F" in (data / "kio" / "servicemenus" / "openrar-add.desktop").read_text()
    assert (data / "kservices5" / "ServiceMenus" / "openrar-add.desktop").exists()
    nemo = sorted(os.listdir(data / "nemo" / "actions"))
    assert nemo == ["openrar-a_add.nemo_action", "openrar-a_quick.nemo_action", "openrar-b_extract.nemo_action",
                    "openrar-c_here.nemo_action", "openrar-d_to.nemo_action"]
    scripts = sorted(os.listdir(data / "nautilus" / "scripts" / "OpenRAR"))
    assert scripts == ["Add to .zip archive", "Add to archive", "Extract Here", "Extract files",
                       "Extract to subfolder"]

    m = configparser.ConfigParser(interpolation=None)
    m.optionxform = str
    m.read(conf / "mimeapps.list")
    assert m["Default Applications"]["application/zip"] == "openrar.desktop"
    assert m["Default Applications"]["text/plain"] == "gedit.desktop"
    assert m["Added Associations"]["application/x-7z-compressed"].startswith("openrar.desktop;")

    desktop.uninstall()
    assert not entry.exists() and not kf6.exists() and not (data / "nautilus" / "scripts" / "OpenRAR").exists()
    assert not os.listdir(data / "nemo" / "actions")
    m = configparser.ConfigParser(interpolation=None)
    m.optionxform = str
    m.read(conf / "mimeapps.list")
    assert m["Default Applications"]["application/zip"] == "org.gnome.FileRoller.desktop"   # restored
    assert "application/x-7z-compressed" not in m["Default Applications"]
    assert m["Default Applications"]["text/plain"] == "gedit.desktop"


def test_nautilus_script_passes_selection(xdg, tmp_path):
    from openrar.integration import VERBS
    from openrar_linux import desktop
    rec = tmp_path / "args.txt"
    fake = tmp_path / "fake.sh"
    fake.write_text(f'#!/bin/sh\nfor a in "$@"; do printf "%s\\n" "$a"; done > "{rec}"\n')
    fake.chmod(0o755)
    script = tmp_path / "s.sh"
    script.write_text(desktop.nautilus_script([str(fake)], [v for v in VERBS if v.key == "c_here"][0]))
    env = dict(os.environ, NAUTILUS_SCRIPT_SELECTED_FILE_PATHS="/a b/one.zip\n/c/it's.7z\n")
    subprocess.run(["sh", str(script), "ignored"], env=env, check=True, timeout=30)
    assert rec.read_text().splitlines() == ["--extract-here", "/a b/one.zip", "/c/it's.7z"]


def test_exec_quoting():
    from openrar_linux.desktop import exec_line
    assert exec_line(["/usr/bin/openrar"], ["--add-dialog"]) == "/usr/bin/openrar --add-dialog %F"
    assert exec_line(['/o "q"/100%'], []) == '"/o \\\\"q\\\\"/100%%" %F'


class _MemoryKeyring:
    priority = 1

    def __init__(self):
        self.store = {}

    def get_password(self, service, user):
        return self.store.get((service, user))

    def set_password(self, service, user, value):
        self.store[(service, user)] = value


def test_passwords_use_keyring(monkeypatch):
    from openrar_linux import secrets
    kr = _MemoryKeyring()
    monkeypatch.setattr(secrets, "_keyring", lambda: kr)
    p = platform.load("linux")
    blob = p.protect("pässword")
    assert blob.startswith("kr:") and "pässword" not in blob and p.unprotect(blob) == "pässword"
    assert "keyring" in p.protection_note
    monkeypatch.setattr(secrets, "_keyring", lambda: None)          # no keyring: obfuscation fallback
    blob2 = p.protect("x")
    assert blob2.startswith("b64:") and p.unprotect(blob2) == "x"
    assert "only obfuscated" in p.protection_note
    with pytest.raises(ValueError):
        p.unprotect(blob)


def test_settings_dialog_integration_tab(xdg, monkeypatch):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6")
    from openrar.gui import app as appmod
    from openrar.gui.tool_dialogs import SettingsDialog
    platform.set_current(platform.load("linux"))
    try:
        appmod._settings = None
        appmod._app()
        d = SettingsDialog(None, appmod.settings())
        assert d.theme.itemText(0) == "Follow system"
        assert ".run" in [d.assoc.item(i).text() for i in range(d.assoc.count())]
        assert d.sh["extract_here"].isEnabled()
    finally:
        platform.set_current(None)
