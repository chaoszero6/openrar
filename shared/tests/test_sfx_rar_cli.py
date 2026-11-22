import os
import shutil
import subprocess
import sys

import pytest

from openrar import cli
from openrar.core import operations as ops
from openrar.core import tools
from openrar.core.formats import open_archive
from openrar.core.model import CreateOptions, ExtractOptions, Interaction

DATA = os.path.join(os.path.dirname(__file__), "data")


@pytest.fixture
def src(tmp_path):
    d = tmp_path / "src"
    (d / "sub").mkdir(parents=True)
    (d / "a.txt").write_text("alpha " * 5000)
    (d / "sub" / "b.bin").write_bytes(os.urandom(50_000))
    return d


# ------------------------------------------------------------------ SFX
def test_sfx_create_and_read(tmp_path, src):
    exe = str(tmp_path / "setup.exe")
    ops.create(exe, [str(src)], CreateOptions(sfx=True, comment="hello",
                                               sfx_options={"Title": "My setup", "Silent": True,
                                                            "Text": "line1\nline2"}))
    with open(exe, "rb") as f:
        assert f.read(2) == b"MZ"
    h = open_archive(exe)
    assert h.format == "ZIP (SFX)" and h.comment() == "hello"
    assert h.summary().sfx
    h.close()
    opts = ops.get_sfx_options(exe)
    assert opts["Title"] == "My setup" and opts["Silent"] == "1" and opts["Text"] == "line1\\nline2"
    assert ops.test(exe) == []


def test_sfx_modify_keeps_stub(tmp_path, src):
    exe = str(tmp_path / "s.exe")
    ops.create(exe, [str(src)], CreateOptions(sfx=True))
    stub = open(exe, "rb").read(4096)
    ops.delete(exe, ["src/a.txt"])
    ops.set_comment(exe, "new comment")
    assert open(exe, "rb").read(4096) == stub
    h = open_archive(exe)
    assert sorted(e.name for e in h.entries()) == ["src", "src/sub", "src/sub/b.bin"]
    assert h.comment() == "new comment"
    h.close()
    assert ops.get_sfx_options(exe) == {}          # empty script still parses
    assert ops.test(exe) == []


def test_make_sfx_from_zip_and_7z(tmp_path, src):
    z = str(tmp_path / "a.zip")
    ops.create(z, [str(src)], CreateOptions())
    exe = ops.make_sfx(z, sfx_options={"Path": r"%TEMP%\x"})
    assert ops.get_sfx_options(exe)["Path"] == r"%TEMP%\x"
    assert ops.test(exe) == []
    s = str(tmp_path / "b.7z")
    ops.create(s, [str(src)], CreateOptions(format="7z"))
    exe2 = ops.make_sfx(s, str(tmp_path / "b.exe"))
    out = tmp_path / "x"
    ops.extract(exe2, ExtractOptions(dest=str(out)))
    assert (out / "src" / "a.txt").read_text() == (src / "a.txt").read_text()


# ------------------------------------------------------------------ RAR
def _can_unpack_rar():
    return bool(tools.find_unrar())


def test_rar_listing_needs_no_tools():
    h = open_archive(os.path.join(DATA, "rar5-solid.rar"))
    assert h.format == "RAR" and [e.name for e in h.entries()] == ["stest1.txt", "stest2.txt"]
    assert h.summary().solid
    h.close()


def test_rar_volumes_from_any_part():
    h = open_archive(os.path.join(DATA, "rar5-vols.part2.rar"))
    assert h.summary().volumes == 3
    h.close()


def test_rar_is_read_only(tmp_path):
    p = tmp_path / "x.rar"
    shutil.copy(os.path.join(DATA, "rar5-solid.rar"), p)
    with pytest.raises(Exception, match="read-only"):
        ops.delete(str(p), ["stest1.txt"])


@pytest.mark.skipif(not _can_unpack_rar(), reason="UnRAR not installed")
def test_rar_extract_encrypted_headers(tmp_path):
    class UI(Interaction):
        def ask_password(self, archive, entry=""):
            return "password"
    done, errs = ops.extract(os.path.join(DATA, "rar5-hpsw.rar"), ExtractOptions(dest=str(tmp_path)), ui=UI())
    assert errs == [] and len(done) == 2


@pytest.mark.skipif(not _can_unpack_rar(), reason="UnRAR not installed")
def test_rar_convert_to_zip(tmp_path):
    out = str(tmp_path / "c.zip")
    ops.convert(os.path.join(DATA, "rar5-solid.rar"), out, CreateOptions())
    h = open_archive(out)
    assert [e.name for e in h.entries()] == ["stest1.txt", "stest2.txt"]
    h.close()


# ------------------------------------------------------------------ CLI
def test_cli_parse():
    cmd, rr, sw, args = cli.parse(["a", "-p secret", "-hpx", "-m5", "-v700m", "-rr5", "-x*.tmp", "-x*.bak",
                                   "-ep", "-t", "-df", "-sfx", "-k", "out.zip", "a", "b"])
    assert cmd == "a" and args == ["out.zip", "a", "b"]
    assert sw["password"] == "x" and sw["encrypt_names"] and sw["level"] == 5
    assert sw["volume_size"] == 700 * 1024 ** 2 and sw["recovery"] == 5
    assert sw["exclude"] == ["*.tmp", "*.bak"] and sw["store_paths"] == "none"
    assert sw["test_after"] and sw["delete_after"] and sw["sfx"] and sw["lock"]
    assert cli.parse(["rr10", "x.zip"])[1] == 10.0


def test_cli_end_to_end(tmp_path, src):
    env = dict(os.environ, PYTHONPATH=os.path.dirname(os.path.dirname(__file__)))
    arc = str(tmp_path / "c.7z")

    def run(*a):
        return subprocess.run([sys.executable, "-m", "openrar", *a], capture_output=True, text=True, env=env,
                              cwd=str(tmp_path), timeout=120, stdin=subprocess.DEVNULL)
    assert run("a", "-pzz", arc, str(src)).returncode == 0
    r = run("l", "-pzz", arc)
    assert "src/sub/b.bin" in r.stdout
    assert run("t", "-pbad", arc).returncode != 0
    assert run("x", "-pzz", "-y", arc, str(tmp_path / "out") + os.sep).returncode == 0
    assert (tmp_path / "out" / "src" / "a.txt").exists()


def test_rar_encrypted_names_any_extension(tmp_path):
    """Listing a RAR with encrypted file names needs an AES backend for rarfile (bundled: cryptography).
    Detection goes by content, so the extension does not matter."""
    import rarfile
    assert rarfile._have_crypto, "install 'cryptography': RAR archives with encrypted names cannot be opened"
    p = tmp_path / "renamed.dat"
    shutil.copy(os.path.join(DATA, "rar5-hpsw.rar"), p)

    class UI(Interaction):
        def ask_password(self, archive, entry=""):
            return "password"
    h = open_archive(str(p), ui=UI())
    assert h.format == "RAR" and len([e for e in h.entries() if not e.is_dir]) == 2
    h.close()
