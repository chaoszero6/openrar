"""Start OpenRAR from the source tree: puts ../shared and this folder on sys.path, then runs the app.
Used by run.bat and by the Explorer entries OpenRAR writes when it is not installed as a package."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [HERE, os.path.join(os.path.dirname(HERE), "shared")]

from openrar.__main__ import main  # noqa: E402

sys.exit(main())
