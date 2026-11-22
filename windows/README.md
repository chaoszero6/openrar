# OpenRAR for Windows

The Windows app: the [shared engine and Qt interface](../shared/) plus the Windows plugin in `openrar_windows/`:

* **Explorer integration** (`shell.py`): cascaded context menu (Add to archive…, Add to .zip, Extract files…,
  Extract Here, Extract to subfolder, Test) and file associations, all under `HKEY_CURRENT_USER`, so no
  administrator rights are needed and *Remove all integration* undoes everything. Selecting several files in
  Explorer opens one window for all of them.
* **Saved passwords** are encrypted with your Windows account (DPAPI, `dpapi.py`).
* **Virus scan** uses Microsoft Defender (`MpCmdRun.exe`).
* **SFX module**: `sfx_stub/` is the native C source of the self-extracting `.exe` stub (Win32 UI, zlib). The built
  `sfx.exe` lives in `../shared/openrar/resources/`, so Windows SFX archives can be made on Linux too.

## Install
Download `OpenRAR-Setup-<version>.exe` (installer) or `OpenRAR-<version>-win64.zip` (portable) from the
[Releases](../../../releases) page. The files are not code-signed, so SmartScreen may warn on first run
(*More info → Run anyway*). The installer is per-user, adds Start-menu shortcuts, Explorer integration, an optional
PATH entry for `orar`, and an uninstaller in *Settings → Apps*.

For RAR, ISO, CAB and similar formats install the free [7-Zip](https://www.7-zip.org) (OpenRAR finds it
automatically). On Windows 11 the context-menu entries are under *Show more options* (Shift+Right-click).

## Build
1. Install [Python 3.12](https://www.python.org/downloads/windows/) (64-bit) with *Add python.exe to PATH*.
2. Double-click `build.bat`. It creates `.venv`, installs the dependencies, runs the shared and Windows tests and
   builds `dist\OpenRAR\` with `OpenRAR.exe` (the app) and `orar.exe` (command line).
3. Optional: install [NSIS 3](https://nsis.sourceforge.io) and run `build.bat` again to also get
   `dist\OpenRAR-Setup-<version>.exe` from `packaging\installer.nsi`. On Linux the same script builds with
   `makensis packaging/installer.nsi`.

Run from source without building: `run.bat` (uses `run_openrar.pyw`, which puts `..\shared` on the path).

Rebuild the SFX module with `sfx_stub/build.sh` (mingw-w64 + zlib, e.g. on Linux).

## Tests
```
python -m pytest ..\shared      the engine, CLI and GUI
python -m pytest                Windows plugin (DPAPI on Windows; the SFX module also runs under Wine on Linux)
```
