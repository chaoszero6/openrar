#!/bin/sh
# Run OpenRAR from source (shared/ + linux/) in a local virtual environment.
set -e
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
    python3 -m venv .venv
    .venv/bin/python -m pip install -q -r requirements.txt
fi
exec .venv/bin/python run_openrar.py "$@"
