"""Programs opened from OpenRAR must not inherit the bundled Qt runtime (PyInstaller build)."""
import os

from openrar.gui import launch


def test_clean_environment_strips_bundle(tmp_path, monkeypatch):
    bundle = str(tmp_path / "OpenRAR" / "_internal")
    monkeypatch.setattr(launch, "FROZEN", True)
    monkeypatch.setattr(launch, "_BUNDLE", os.path.normcase(os.path.abspath(bundle)))
    other = str(tmp_path / "other")
    env = {
        "QT_PLUGIN_PATH": os.path.join(bundle, "PySide6", "plugins"),
        "QML2_IMPORT_PATH": os.path.join(bundle, "PySide6", "qml"),
        "_PYI_APPLICATION_HOME_DIR": bundle,
        "PATH": os.pathsep.join([bundle, other, os.path.join(bundle, "sub")]),
        "HOME": "/home/x",
    }
    clean = launch.clean_environment(env)
    assert clean == {"PATH": other, "HOME": "/home/x"}

    # a QT_PLUGIN_PATH the user set themselves (pointing elsewhere) is kept
    env["QT_PLUGIN_PATH"] = other
    assert launch.clean_environment(env)["QT_PLUGIN_PATH"] == other


def test_not_frozen_leaves_environment_alone(monkeypatch):
    monkeypatch.setattr(launch, "FROZEN", False)
    env = {"QT_PLUGIN_PATH": "/x", "PATH": "/a"}
    assert launch.clean_environment(env) == env
