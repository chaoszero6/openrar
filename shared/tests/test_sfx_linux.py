"""Linux self-extracting archives (.run): sh script + tar.gz payload."""
import os
import shutil
import stat
import subprocess
import sys

import pytest

from openrar.core import operations as ops
from openrar.core.formats import open_archive
from openrar.core.model import CreateOptions, ExtractOptions

pytestmark = pytest.mark.skipif(sys.platform == "win32" or not shutil.which("sh"), reason="needs a POSIX shell")


@pytest.fixture
def src(tmp_path):
    d = tmp_path / "app"
    (d / "bin").mkdir(parents=True)
    (d / "readme.txt").write_text("hello " * 2000)
    (d / "bin" / "tool.sh").write_text("#!/bin/sh\necho tool\n")
    os.chmod(d / "bin" / "tool.sh", 0o755)
    (d / "space name ü.bin").write_bytes(os.urandom(70_000))
    return d


def _run(exe, *args, stdin=b"", cwd=None):
    env = {k: v for k, v in os.environ.items() if k not in ("DISPLAY", "WAYLAND_DISPLAY")}
    return subprocess.run(["sh", exe, *args], input=stdin, capture_output=True, timeout=60, env=env, cwd=cwd)


def _same_tree(a, b):
    for root, _dirs, files in os.walk(a):
        for f in files:
            p = os.path.join(root, f)
            q = os.path.join(b, os.path.relpath(p, a))
            assert open(p, "rb").read() == open(q, "rb").read(), q
            assert os.stat(p).st_mode & 0o111 == os.stat(q).st_mode & 0o111, q


def test_create_open_and_run(tmp_path, src):
    exe = str(tmp_path / "setup.run")
    ops.create(exe, [str(src)], CreateOptions(sfx=True, sfx_options={"Title": "My app", "Text": "Hi\nthere",
                                                                      "Silent": True}))
    assert os.stat(exe).st_mode & stat.S_IXUSR
    h = open_archive(exe)
    assert h.format == "TAR.GZ (SFX)" and h.summary().sfx
    assert "app/space name ü.bin" in [e.name for e in h.entries()]
    h.close()
    assert ops.test(exe) == []
    o = ops.get_sfx_options(exe)
    assert o["Title"] == "My app" and o["Text"] == "Hi\\nthere" and o["Silent"] == "1"

    out = tmp_path / "out"
    r = _run(exe, "--target", str(out))
    assert r.returncode == 0, r.stderr
    _same_tree(src, out / "app")
    # OpenRAR extracts it like any archive
    ops.extract(exe, ExtractOptions(dest=str(tmp_path / "x")))
    _same_tree(src, tmp_path / "x" / "app")


def test_interactive_license_and_overwrite(tmp_path, src):
    exe = str(tmp_path / "lic.run")
    ops.create(exe, [str(src)], CreateOptions(sfx=True, sfx_options={"License": "Be nice.", "Path": "$HOME/x"}))
    out = tmp_path / "o"
    r = _run(exe, "--target", str(out), stdin=b"n\n")              # decline the license
    assert r.returncode != 0 and b"Be nice." in r.stdout and not out.exists()
    r = _run(exe, "--target", str(out), stdin=b"y\n")
    assert r.returncode == 0 and (out / "app" / "readme.txt").exists()
    (out / "app" / "readme.txt").write_text("mine")
    r = _run(exe, "--target", str(out), stdin=b"y\nn\n")           # accept license, keep existing files
    assert r.returncode == 0, r.stderr
    assert (out / "app" / "readme.txt").read_text() == "mine"
    r = _run(exe, "--target", str(out), stdin=b"y\ny\n")           # overwrite
    assert (out / "app" / "readme.txt").read_text() == (src / "readme.txt").read_text()


def test_default_path_setup_and_tempmode(tmp_path, src):
    marker = tmp_path / "ran.txt"
    exe = str(tmp_path / "t.run")
    ops.create(exe, [str(src)], CreateOptions(sfx=True, sfx_options={
        "Silent": True, "TempMode": True, "Setup": f"sh app/bin/tool.sh > '{marker}' && pwd >> '{marker}'"}))
    r = _run(exe)
    assert r.returncode == 0, r.stderr
    lines = marker.read_text().splitlines()
    assert lines[0] == "tool" and not os.path.exists(lines[1])      # temp folder was removed
    exe2 = str(tmp_path / "d.run")
    ops.create(exe2, [str(src)], CreateOptions(sfx=True, sfx_options={"Silent": True, "Path": "sub dir"}))
    assert _run(exe2).returncode == 0
    assert (tmp_path / "sub dir" / "app" / "readme.txt").exists()     # relative to the .run file
    r = _run(exe2, "--list")
    assert b"app/readme.txt" in r.stdout


def test_modify_keeps_script_and_mode(tmp_path, src):
    exe = str(tmp_path / "m.run")
    ops.create(exe, [str(src)], CreateOptions(sfx=True, sfx_options={"Silent": True}))
    ops.delete(exe, ["app/readme.txt"])
    ops.rename(exe, "app/bin/tool.sh", "app/bin/run.sh")
    assert os.stat(exe).st_mode & stat.S_IXUSR
    out = tmp_path / "o"
    assert _run(exe, "--target", str(out)).returncode == 0
    assert sorted(os.listdir(out / "app" / "bin")) == ["run.sh"] and not (out / "app" / "readme.txt").exists()


def test_make_sfx_both_targets(tmp_path, src):
    z = str(tmp_path / "a.zip")
    ops.create(z, [str(src)], CreateOptions())
    run = ops.make_sfx(z, str(tmp_path / "a.run"), {"Silent": True})
    assert _run(run, "--target", str(tmp_path / "r")).returncode == 0
    _same_tree(src, tmp_path / "r" / "app")
    tgz = str(tmp_path / "b.tar.gz")
    ops.create(tgz, [str(src)], CreateOptions(format="tar.gz"))
    run2 = ops.make_sfx(tgz, target="linux")
    assert run2.endswith("b.run") and ops.test(run2) == []
    exe = ops.make_sfx(run2, str(tmp_path / "b.exe"))                 # Linux SFX -> Windows SFX
    assert open(exe, "rb").read(2) == b"MZ" and ops.test(exe) == []


def test_linux_sfx_cannot_be_encrypted(tmp_path, src):
    with pytest.raises(Exception, match="cannot be encrypted"):
        ops.create(str(tmp_path / "e.run"), [str(src)], CreateOptions(sfx=True, password="x"))
