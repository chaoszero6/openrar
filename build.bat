@echo off
rem Builds dist\OpenRAR\OpenRAR.exe (and the orar.exe command line tool).
rem Requirements: Python 3.11 - 3.13 (64-bit) from python.org, with "Add python.exe to PATH".
setlocal
cd /d "%~dp0"
if not exist .venv (
    python -m venv .venv || goto :error
)
call .venv\Scripts\activate.bat || goto :error
python -m pip install --upgrade pip >nul
python -m pip install -r requirements-dev.txt || goto :error
python -m pytest -q || goto :error
pyinstaller --noconfirm --clean openrar.spec || goto :error
echo.
echo Build finished: %CD%\dist\OpenRAR\OpenRAR.exe
set "NSIS=%ProgramFiles(x86)%\NSIS\makensis.exe"
if exist "%NSIS%" (
    "%NSIS%" packaging\installer.nsi || goto :error
    echo Installer: %CD%\dist\OpenRAR-Setup-1.0.1.exe
) else (
    echo Optional installer: install NSIS 3 from https://nsis.sourceforge.io and run build.bat again.
)
exit /b 0
:error
echo.
echo Build FAILED.
exit /b 1
