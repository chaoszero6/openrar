import os
import random
import shutil

import pytest

from openrar.core import operations as ops
from openrar.core import recovery
from openrar.core.formats import open_archive
from openrar.core.model import (ArchiveLocked, BadPassword, CreateOptions, ExtractOptions, Interaction,
                                PasswordRequired)
from openrar.core.volumes import list_volumes

FORMATS = ["zip", "7z", "tar", "tar.gz", "tar.bz2", "tar.xz", "tar.zst"]


@pytest.fixture
def src(tmp_path):
    d = tmp_path / "src"
    (d / "sub" / "empty").mkdir(parents=True)
    rnd = random.Random(42)
    (d / "rand.bin").write_bytes(bytes(rnd.getrandbits(8) for _ in range(200_000)))
    (d / "sub" / "text.txt").write_text("hello world\n" * 20000)
    (d / "sub" / "naïve файл.txt").write_text("ünïcode")
    return d


def names(path, pw=None):
    h = open_archive(str(path), pw)
    try:
        return sorted(e.name for e in h.entries())
    finally:
        h.close()


def same_tree(a, b):
    for root, _dirs, files in os.walk(a):
        for f in files:
            p = os.path.join(root, f)
            q = os.path.join(b, os.path.relpath(p, a))
            assert open(p, "rb").read() == open(q, "rb").read(), q


@pytest.mark.parametrize("fmt", FORMATS)
def test_roundtrip(tmp_path, src, fmt):
    arc = tmp_path / f"a.{fmt}"
    ops.create(str(arc), [str(src)], CreateOptions(format=fmt))
    assert ops.test(str(arc)) == []
    out = tmp_path / "x"
    ops.extract(str(arc), ExtractOptions(dest=str(out)))
    same_tree(src, out / "src")
    assert (out / "src" / "sub" / "empty").is_dir()


@pytest.mark.parametrize("fmt", ["zip", "7z"])
def test_password(tmp_path, src, fmt):
    arc = tmp_path / f"p.{fmt}"
    ops.create(str(arc), [str(src)], CreateOptions(format=fmt, password="s3cret", encrypt_names=True))
    with pytest.raises((PasswordRequired, BadPassword)):
        ops.extract(str(arc), ExtractOptions(dest=str(tmp_path / "x1")))
    with pytest.raises(BadPassword):
        ops.extract(str(arc), ExtractOptions(dest=str(tmp_path / "x2"), password="wrong"))

    class Asker(Interaction):
        asked = 0

        def ask_password(self, archive, entry=""):
            Asker.asked += 1
            return "s3cret"

    ex, errs = ops.extract(str(arc), ExtractOptions(dest=str(tmp_path / "x3")), ui=Asker())
    assert errs == [] and Asker.asked == 1
    same_tree(src, tmp_path / "x3" / "src")


@pytest.mark.parametrize("fmt", ["zip", "7z", "tar.gz"])
def test_modify(tmp_path, src, fmt):
    arc = str(tmp_path / f"m.{fmt}")
    pw = "pw" if fmt != "tar.gz" else ""
    ops.create(arc, [str(src)], CreateOptions(format=fmt, password=pw))
    ops.delete(arc, ["src/rand.bin"], password=pw or None)
    assert "src/rand.bin" not in names(arc, pw or None)
    ops.rename(arc, "src/sub", "src/renamed", password=pw or None)
    n = names(arc, pw or None)
    assert "src/renamed/text.txt" in n and not any(x.startswith("src/sub") for x in n)
    extra = tmp_path / "new.txt"
    extra.write_text("added")
    ops.add_files(arc, [str(extra)], into="src/renamed", opts=CreateOptions(format=fmt, password=pw))
    assert "src/renamed/new.txt" in names(arc, pw or None)
    assert ops.test(arc, pw or None) == []
    out = tmp_path / "x"
    ops.extract(arc, ExtractOptions(dest=str(out), password=pw))
    assert (out / "src" / "renamed" / "new.txt").read_text() == "added"
    assert (out / "src" / "renamed" / "text.txt").read_text() == (src / "sub" / "text.txt").read_text()


def test_update_modes(tmp_path, src):
    arc = str(tmp_path / "u.zip")
    ops.create(arc, [str(src)], CreateOptions())
    before = names(arc)
    (src / "sub" / "brand_new.txt").write_text("x")
    ops.create(arc, [str(src)], CreateOptions(update_mode="freshen"))
    assert names(arc) == before                                   # freshen never adds
    ops.create(arc, [str(src)], CreateOptions(update_mode="update"))
    assert "src/sub/brand_new.txt" in names(arc)
    os.remove(src / "rand.bin")
    ops.create(arc, [str(src)], CreateOptions(update_mode="sync"))
    assert "src/rand.bin" not in names(arc)


def test_comment_and_lock(tmp_path, src):
    arc = str(tmp_path / "c.zip")
    ops.create(arc, [str(src)], CreateOptions(comment="first"))
    ops.set_comment(arc, "Line 1\nLine 2")
    h = open_archive(arc)
    assert h.comment().splitlines() == ["Line 1", "Line 2"] and not h.is_locked()
    h.close()
    ops.lock(arc)
    h = open_archive(arc)
    assert h.is_locked() and h.comment().splitlines() == ["Line 1", "Line 2"]
    h.close()
    with pytest.raises(ArchiveLocked):
        ops.delete(arc, ["src/rand.bin"])
    with pytest.raises(ArchiveLocked):
        ops.set_comment(arc, "nope")
    assert ops.test(arc) == []


@pytest.mark.parametrize("fmt", ["zip", "7z", "tar.xz"])
def test_volumes(tmp_path, src, fmt):
    arc = str(tmp_path / f"v.{fmt}")
    outs = ops.create(arc, [str(src)], CreateOptions(format=fmt, level=0, volume_size=64 * 1024))
    assert len(outs) > 3 and outs == list_volumes(outs[0])
    assert all(os.path.getsize(p) == 64 * 1024 for p in outs[:-1])
    assert ops.test(outs[1]) == []                     # opening any volume works
    out = tmp_path / "x"
    ops.extract(outs[0], ExtractOptions(dest=str(out)))
    same_tree(src, out / "src")


@pytest.mark.parametrize("damage", ["burst", "scattered", "truncate"])
def test_recovery_record(tmp_path, src, damage):
    arc = str(tmp_path / "r.7z")
    ops.create(arc, [str(src)], CreateOptions(format="7z", recovery_percent=5))
    assert os.path.exists(arc + ".orr")
    good = open(arc, "rb").read()
    data = bytearray(good)
    if damage == "burst":
        for i in range(5000, 9000):
            data[i] ^= 0xFF
    elif damage == "scattered":
        for i in (100, 40_000, 120_000, 190_000):
            data[i] ^= 0x55
    else:
        data = data[:-3000]
    open(arc, "wb").write(bytes(data))
    assert ops.test(arc) != []
    report = ops.repair(arc)
    fixed = os.path.join(os.path.dirname(arc), "fixed.r.7z")
    assert "Repaired" in report and open(fixed, "rb").read() == good


def test_recovery_too_much_damage(tmp_path, src):
    arc = str(tmp_path / "r.zip")
    ops.create(arc, [str(src)], CreateOptions(level=0, recovery_percent=1))
    data = bytearray(open(arc, "rb").read())
    for i in range(1000, 60000):
        data[i] = 0
    open(arc, "wb").write(bytes(data))
    with pytest.raises(Exception, match="too much damage"):
        recovery.repair(arc, arc + ".fixed")


def test_zip_salvage(tmp_path, src):
    arc = str(tmp_path / "s.zip")
    ops.create(arc, [str(src)], CreateOptions(level=3))
    data = bytearray(open(arc, "rb").read())
    del data[-200:]                                   # destroy the central directory
    open(arc, "wb").write(bytes(data))
    assert ops.test(arc) != []
    report = ops.repair(arc)
    fixed = os.path.join(os.path.dirname(arc), "fixed.s.zip")
    assert "Recovered" in report
    n = names(fixed)
    assert "src/sub/text.txt" in n and "src/rand.bin" in n
    assert ops.test(fixed) == []


def test_convert(tmp_path, src):
    a = str(tmp_path / "c.tar.gz")
    ops.create(a, [str(src)], CreateOptions(format="tar.gz"))
    b = str(tmp_path / "c.7z")
    ops.convert(a, b, CreateOptions(format="7z", password="x"))
    out = tmp_path / "x"
    ops.extract(b, ExtractOptions(dest=str(out), password="x"))
    same_tree(src, out / "src")


def test_extract_selection_and_flat(tmp_path, src):
    arc = str(tmp_path / "e.zip")
    ops.create(arc, [str(src)], CreateOptions())
    out = tmp_path / "x"
    ops.extract(arc, ExtractOptions(dest=str(out), strip_prefix="src/sub"), names=["src/sub"])
    assert (out / "text.txt").exists() and not (out / "rand.bin").exists()
    out2 = tmp_path / "y"
    ops.extract(arc, ExtractOptions(dest=str(out2), paths="none"))
    assert sorted(os.listdir(out2)) == sorted(["rand.bin", "text.txt", "naïve файл.txt"])


def test_overwrite_policies(tmp_path, src):
    arc = str(tmp_path / "o.zip")
    ops.create(arc, [str(src / "sub" / "text.txt")], CreateOptions())
    out = tmp_path / "x"
    out.mkdir()
    (out / "text.txt").write_text("mine")
    ops.extract(arc, ExtractOptions(dest=str(out), overwrite="never"))
    assert (out / "text.txt").read_text() == "mine"
    ops.extract(arc, ExtractOptions(dest=str(out), overwrite="rename"))
    assert (out / "text (1).txt").exists()
    ops.extract(arc, ExtractOptions(dest=str(out), overwrite="always"))
    assert (out / "text.txt").read_text() != "mine"


def test_path_traversal_blocked(tmp_path):
    import zipfile

    arc = tmp_path / "evil.zip"
    with zipfile.ZipFile(arc, "w") as z:
        z.writestr("../../evil.txt", "x")
        z.writestr("/abs/evil2.txt", "y")
    out = tmp_path / "deep" / "x"
    ops.extract(str(arc), ExtractOptions(dest=str(out)))
    assert (out / "evil.txt").exists() and (out / "abs" / "evil2.txt").exists()
    assert not (tmp_path / "evil.txt").exists()


def test_delete_after_and_test_after(tmp_path, src):
    copy = tmp_path / "copy"
    shutil.copytree(src, copy)
    arc = str(tmp_path / "d.7z")
    ops.create(arc, [str(copy)], CreateOptions(format="7z", delete_after=True, test_after=True))
    assert not copy.exists()
    assert "copy/rand.bin" in names(arc)


def test_exclude_and_paths(tmp_path, src):
    arc = str(tmp_path / "x.zip")
    ops.create(arc, [str(src)], CreateOptions(exclude=["*.bin", "empty"]))
    n = names(arc)
    assert "src/rand.bin" not in n and "src/sub/empty" not in n and "src/sub/text.txt" in n
    arc2 = str(tmp_path / "flat.zip")
    ops.create(arc2, [str(src)], CreateOptions(store_paths="none"))
    assert "text.txt" in names(arc2)


def test_folder_times_preserved(tmp_path, src):
    old = 1_500_000_000
    os.utime(src / "sub", (old, old))
    for fmt in ("zip", "7z", "tar.gz"):
        arc = str(tmp_path / f"t.{fmt}")
        ops.create(arc, [str(src)], CreateOptions(format=fmt))
        ops.delete(arc, ["src/rand.bin"])            # rewrite must keep folder times too
        out = tmp_path / f"x_{fmt}"
        ops.extract(arc, ExtractOptions(dest=str(out)))
        assert abs(os.path.getmtime(out / "src" / "sub") - old) <= 2, fmt


def test_encrypt_names_when_adding_to_existing_7z(tmp_path):
    import py7zr
    a = tmp_path / "a.txt"
    a.write_text("a")
    b = tmp_path / "b.txt"
    b.write_text("b")
    arc = str(tmp_path / "x.7z")
    ops.create(arc, [str(a)], CreateOptions(format="7z", password="pw"))
    with py7zr.SevenZipFile(arc) as z:              # names readable without a password
        assert z.getnames() == ["a.txt"]
    ops.create(arc, [str(b)], CreateOptions(format="7z", password="pw", encrypt_names=True))
    with pytest.raises(Exception):
        py7zr.SevenZipFile(arc).getnames()
    h = open_archive(arc, "pw")
    assert sorted(e.name for e in h.entries()) == ["a.txt", "b.txt"] and h.summary().header_encrypted
    h.close()
    ops.delete(arc, ["a.txt"], password="pw")       # later edits keep the names hidden
    h = open_archive(arc, "pw")
    assert h.summary().header_encrypted
    h.close()
