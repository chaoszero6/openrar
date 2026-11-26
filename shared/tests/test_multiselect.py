"""Explorer starts one process per selected file; they must all end up in one window."""
import json
import os
import subprocess
import sys

import openrar

SHARED = os.path.dirname(os.path.dirname(os.path.abspath(openrar.__file__)))
CHILD = """
import json, random, sys, time
sys.path.insert(0, sys.argv[1])
from PySide6.QtCore import QCoreApplication
app = QCoreApplication([])
from openrar.gui.app import _collect
time.sleep(random.uniform(0, 0.8))      # uneven process start-up
# a long quiet period: this tests the handover, not how fast a loaded build machine starts processes
got = _collect(sys.argv[2], [sys.argv[3]], quiet_ms=10000, give_up_ms=60000)
if got is not None:
    print(json.dumps(got))
"""


def test_every_selected_file_reaches_one_collector(tmp_path):
    kind = f"test-{os.getpid()}"
    files = [str(tmp_path / f"file {i} é.txt") for i in range(40)]
    procs = [subprocess.Popen([sys.executable, "-c", CHILD, SHARED, kind, f], stdout=subprocess.PIPE,
                              encoding="utf-8") for f in files]
    outputs = [p.communicate(timeout=120)[0] for p in procs]
    assert all(p.returncode == 0 for p in procs)
    groups = [json.loads(o) for o in outputs if o.strip()]
    assert len(groups) == 1
    assert sorted(groups[0]) == sorted(files)
