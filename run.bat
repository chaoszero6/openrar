@echo off
rem Run OpenRAR from source without building an .exe
cd /d "%~dp0"
if not exist .venv (
    python -m venv .venv && .venv\Scripts\python -m pip install -r requirements.txt
)
start "" .venv\Scripts\pythonw.exe -m openrar %*
