"""Windows plugin: Explorer integration commands, platform loading, SFX module (run under Wine on Linux)."""
import os
import shutil
import subprocess
import sys

import pytest

from openrar import platform
from openrar.core import operations as ops
from openrar.core.model import CreateOptions


@pytest.fixture
def src(tmp_path):
    d = tmp_path / "src"
    (d / "sub").mkdir(parents=True)
    (d / "a.txt").write_text("alpha " * 5000)
    (d / "sub" / "b.bin").write_bytes(os.urandom(50_000))
    return d


def test_plugin_loads_for_windows():
    p = platform.load("win32")
    assert p.name == "windows" and p.supports_integration and p.file_manager == "Explorer"
    cmd = p.app_command()
    assert cmd[-1].endswith("run_openrar.pyw") and os.path.exists(cmd[-1])


def test_explorer_verbs_quote_every_argument():
    from openrar_windows import shell
    app = [r"C:\Program Files\OpenRAR\OpenRAR.exe"]
    cfg = {"add": True, "add_quick": False, "extract": True, "extract_here": True, "extract_to": False,
           "test": True}
    files = shell._verbs(cfg, False, app)
    archives = shell._verbs(cfg, True, app)
    assert [k for k, *_ in files] == ["a_add"]
    assert [k for k, *_ in archives] == ["a_add", "b_extract", "c_here", "e_test"]
    assert archives[2][3] == '"C:\\Program Files\\OpenRAR\\OpenRAR.exe" --extract-here "%1"'
    assert ".run" not in shell.ARCHIVE_TYPES


@pytest.mark.skipif(sys.platform != "win32", reason="DPAPI needs Windows")
def test_dpapi_roundtrip():
    p = platform.load("win32")
    blob = p.protect("sécret")
    assert blob.startswith("dpapi:") and p.unprotect(blob) == "sécret"


@pytest.mark.skipif(not shutil.which("wine"), reason="wine not installed")
def test_sfx_runs_under_wine(tmp_path, src):
    exe = str(tmp_path / "run.exe")
    ops.create(exe, [str(src)], CreateOptions(sfx=True, sfx_options={"Silent": True, "Overwrite": 1}))
    dest = tmp_path / "wine_out"
    env = dict(os.environ, WINEDEBUG="-all")
    r = subprocess.run(["wine", exe, "/S", f"/D=Z:{dest}"], env=env, timeout=180, capture_output=True)
    assert r.returncode == 0
    assert (dest / "src" / "sub" / "b.bin").read_bytes() == (src / "sub" / "b.bin").read_bytes()
