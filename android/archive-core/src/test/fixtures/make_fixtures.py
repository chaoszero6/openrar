"""Regenerate the desktop-made test archives used by archive-core's unit tests.

    python3 android/archive-core/src/test/fixtures/make_fixtures.py

Uses the shared OpenRAR engine (../../../../../shared) so the Android reader is tested against real desktop output.
"""
import hashlib
import json
import os
import random
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", "..", "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "shared"))

from openrar.core import operations as ops  # noqa: E402
from openrar.core.model import CreateOptions  # noqa: E402

OUT = os.path.join(HERE, "..", "resources", "desktop")


def tree(base):
    d = os.path.join(base, "data")
    os.makedirs(os.path.join(d, "docs"))
    os.makedirs(os.path.join(d, "bin"))
    os.makedirs(os.path.join(d, "empty"))
    with open(os.path.join(d, "docs", "readme.txt"), "w") as f:
        f.write("OpenRAR test file\n" * 3000)
    with open(os.path.join(d, "docs", "ünïcødé ñame.txt"), "w", encoding="utf-8") as f:
        f.write("non-ASCII name\n")
    rnd = random.Random(42)
    with open(os.path.join(d, "bin", "data.bin"), "wb") as f:
        f.write(bytes(rnd.getrandbits(8) for _ in range(150_000)))
    return d


def main():
    shutil.rmtree(OUT, ignore_errors=True)
    os.makedirs(OUT)
    tmp = tempfile.mkdtemp()
    try:
        src = tree(tmp)
        manifest = {}
        for root, _dirs, files in os.walk(src):
            for n in files:
                p = os.path.join(root, n)
                rel = os.path.relpath(p, tmp).replace(os.sep, "/")
                manifest[rel] = hashlib.sha256(open(p, "rb").read()).hexdigest()
        o = lambda n: os.path.join(OUT, n)  # noqa: E731
        ops.create(o("plain.zip"), [src], CreateOptions(comment="Hello from the desktop"))
        ops.create(o("aes.zip"), [src], CreateOptions(password="secret"))
        ops.create(o("plain.7z"), [src], CreateOptions(format="7z"))
        ops.create(o("aes.7z"), [src], CreateOptions(format="7z", password="secret"))
        ops.create(o("names.7z"), [src], CreateOptions(format="7z", password="secret", encrypt_names=True))
        for fmt in ("tar", "tar.gz", "tar.bz2", "tar.xz"):
            ops.create(o("data." + fmt), [src], CreateOptions(format=fmt))
        ops.create(o("setup.run"), [src], CreateOptions(sfx=True, sfx_options={"Silent": True}))
        ops.create(o("setup.exe"), [src], CreateOptions(sfx=True))
        ops.create(o("split.7z"), [src], CreateOptions(format="7z", level=0, volume_size=64 * 1024))
        ops.create(o("split.zip"), [src], CreateOptions(level=0, volume_size=64 * 1024))
        ops.create(o("locked.zip"), [src], CreateOptions(lock=True))
        with open(o("single.txt"), "w") as f:
            f.write("single file\n" * 100)
        ops.create(o("single.txt.gz"), [o("single.txt")], CreateOptions(format="tar.gz"))  # tar inside
        import gzip
        with open(o("single.txt"), "rb") as f, gzip.open(o("plain.txt.gz"), "wb") as g:
            g.write(f.read())
        os.remove(o("single.txt"))
        with open(o("manifest.json"), "w") as f:
            json.dump(manifest, f, indent=1, sort_keys=True)
    finally:
        shutil.rmtree(tmp)
    print("\n".join(sorted(os.listdir(OUT))))


if __name__ == "__main__":
    main()
