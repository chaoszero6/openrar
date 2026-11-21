"""Headless GUI workflow test (Qt offscreen platform, dialogs answered automatically)."""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
PySide6 = pytest.importorskip("PySide6")

from PySide6.QtCore import QItemSelectionModel  # noqa: E402
from PySide6.QtWidgets import QInputDialog, QMessageBox  # noqa: E402

from openrar.core import operations as ops  # noqa: E402
from openrar.core.model import CreateOptions, ExtractOptions  # noqa: E402


def _select(w, name):
    w.view.clearSelection()
    for i, r in enumerate(w.model.rows):
        if r.name == name:
            w.view.selectionModel().select(w.model.index(i, 0), QItemSelectionModel.Select | QItemSelectionModel.Rows)


def test_main_window_workflow(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path / "cfg"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    from openrar.gui import app as appmod
    from openrar.gui import dialogs
    from openrar.gui.main_window import MainWindow

    appmod._settings = None
    data = tmp_path / "data" / "sub"
    data.mkdir(parents=True)
    (tmp_path / "data" / "a.txt").write_text("alpha\n" * 1000)
    (data / "b.txt").write_text("beta")
    ops.create(str(tmp_path / "enc.7z"), [str(tmp_path / "data")],
               CreateOptions(format="7z", password="pw1", encrypt_names=True))

    asked = {"pw": 0, "ow": 0}

    def ask_pw(parent, archive, note=""):
        asked["pw"] += 1
        return "pw1"

    def ask_ow(parent, path, entry):
        asked["ow"] += 1
        return "yes_all"
    monkeypatch.setattr(dialogs.PasswordDialog, "ask", staticmethod(ask_pw))
    monkeypatch.setattr(dialogs.OverwriteDialog, "ask", staticmethod(ask_ow))
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Yes))
    infos = []
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: infos.append(a[2])))
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: infos.append("WARN " + str(a[2]))))

    appmod._app()
    w = MainWindow(appmod.settings(), start_path=str(tmp_path))
    w.show()

    # password prompt is raised from the worker thread and answered on the GUI thread
    assert w.open_archive_ui(str(tmp_path / "enc.7z"))
    assert asked["pw"] == 1 and [r.name for r in w.model.rows] == ["..", "data"]

    w.prefix = "data/sub"
    w._show_archive_folder()
    (tmp_path / "new.txt").write_text("new!")
    w._add_into_archive([str(tmp_path / "new.txt")])
    assert [r.name for r in w.model.rows] == ["..", "b.txt", "new.txt"]

    _select(w, "new.txt")
    monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: ("renamed.txt", True)))
    w.cmd_rename()
    assert [r.name for r in w.model.rows] == ["..", "b.txt", "renamed.txt"]

    _select(w, "renamed.txt")
    paths = w.extract_selection_to_temp()          # used for drag & drop out and View
    assert open(paths[0]).read() == "new!"

    for _ in range(2):                             # second run triggers the overwrite question
        w._run_extract([(str(tmp_path / "enc.7z"), ExtractOptions(dest=str(tmp_path / "out"),
                                                                  password="pw1"), None)])
    assert asked["ow"] == 1
    assert sorted(os.listdir(tmp_path / "out" / "data" / "sub")) == ["b.txt", "renamed.txt"]

    _select(w, "renamed.txt")
    w.cmd_delete()
    assert [r.name for r in w.model.rows] == ["..", "b.txt"]

    w.cmd_test()
    assert infos[-1].startswith("No errors")

    w.prefix = ""
    w._show_archive_folder()
    w.go_up()
    assert w.mode == "fs" and [r.name for r in w._selected_rows()] == ["enc.7z"]
    w.close()


def test_encrypt_names_checkbox(tmp_path, monkeypatch):
    """The checkbox is usable with any format and switches the archive to 7Z, which is the only
    format that can hide file names."""
    from openrar.gui import app as appmod
    from openrar.gui import dialogs
    from PySide6.QtWidgets import QDialog
    appmod._app()
    (tmp_path / "f.txt").write_text("x")
    d = dialogs.AddDialog(None, [str(tmp_path / "f.txt")], str(tmp_path / "out.zip"))
    assert d.fmt() == "zip"

    def fake_exec(pw_dialog):
        assert pw_dialog.names.isEnabled()
        pw_dialog.p1.setText("pw")
        pw_dialog.p2.setText("pw")
        pw_dialog.names.setChecked(True)
        assert "7Z" in pw_dialog.info.text()
        return QDialog.Accepted
    monkeypatch.setattr(dialogs.SetPasswordDialog, "exec", fake_exec)
    d._set_password()
    assert d.fmt() == "7z" and d.name.currentText().endswith(".7z")
    o = d.options()
    assert o.format == "7z" and o.password == "pw" and o.encrypt_names
    assert "file names encrypted" in d.pw_label.text()

    # switching back to ZIP: the label says names stay visible and the option is not passed on
    d._select_format("zip")
    assert "visible" in d.pw_label.text() and not d.options().encrypt_names


def test_every_menu_item_has_a_label(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path / "cfg"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    from openrar.gui import app as appmod
    from openrar.gui.main_window import MainWindow
    appmod._settings = None
    appmod._app()
    w = MainWindow(appmod.settings(), start_path=str(tmp_path))

    def walk(menu, path):
        for a in menu.actions():
            if a.isSeparator():
                continue
            assert a.text().strip(), f"unlabelled item in {path} (shortcut {a.shortcut().toString()})"
            if a.menu():
                walk(a.menu(), path + " > " + a.text())
    for top in w.menuBar().actions():
        walk(top.menu(), top.text())
    w.close()
