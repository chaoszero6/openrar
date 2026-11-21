# OpenRAR

A free archive manager for Windows. It is an independent app, not affiliated with WinRAR or RARLAB. The goal was
to match the feature set of a paid WinRAR license with open formats, with no trial period and no nag screen.

## Features

| | |
|---|---|
| **Create archives** | ZIP, 7Z, TAR, TAR.GZ, TAR.BZ2, TAR.XZ, TAR.ZST, self-extracting `.exe` |
| **Open / extract** | everything above plus RAR (1.5 – 5, solid, multi-volume, encrypted names), ISO, CAB, ARJ, LZH, WIM, DMG, … |
| **Compression** | 6 levels (Store → Best), methods Deflate / BZip2 / LZMA / LZMA2 / PPMd / Zstandard |
| **Encryption** | AES-256 for ZIP and 7Z, optional file-name encryption (7Z), password organizer (DPAPI-protected) |
| **Split volumes** | presets for e-mail, CD, FAT32, DVD, Blu-ray or any size (`name.zip.001`, `.002` …, 7-Zip compatible) |
| **Recovery records** | parity data (`archive.orr`) that repairs damaged or truncated archives, for any format |
| **Repair** | from recovery records, or salvage of damaged ZIPs with no recovery record |
| **SFX** | native 100 KB Windows module: title, text, license, default path (env vars), run setup, silent, temp mode |
| **Archive editing** | add, update, freshen, synchronize, delete, rename, edit files in place. ZIP edits copy the existing compressed data as-is, so they are fast and don't need the password |
| **Lock / comment** | lock archives against changes, edit ZIP comments |
| **Tools** | Find (inside archives too, including text search), Convert (batch), Benchmark, Virus scan (Microsoft Defender), HTML/TXT reports, Wizard |
| **Explorer integration** | context menu (Add to archive…, Add to .zip, Extract files…, Extract Here, Extract to subfolder, Test) and file associations, per user, no admin needed |
| **Interface** | WinRAR-style file manager, folder tree, drag & drop in and out, open archives inside archives, light/dark/follow-Windows themes |
| **Command line** | `orar.exe` with rar-style commands and switches |

### The one thing it can't do: create `.rar` files
RAR is a proprietary format. Only RARLAB's own software can create `.rar` archives, and the UnRAR source license
forbids using it to build a RAR compressor. OpenRAR **opens and extracts** RAR archives. To create archives it uses
7Z (equal or better compression) or ZIP (opens anywhere).

Listing RAR contents works on its own. Unpacking compressed RAR data uses the free
[7-Zip](https://www.7-zip.org) or UnRAR if either is installed. Windows 10/11's built-in `tar.exe` is used as a
fallback (it can't handle passwords). The same applies to ISO, CAB, ARJ, LZH and similar read-only formats.

## Getting it running on Windows

**Download**: grab `OpenRAR-Setup-1.0.0.exe` (installer) or `OpenRAR-1.0.0-win64.zip` (portable) from the
[Releases](../../releases) page. The files are not code-signed, so SmartScreen may warn on first run
(*More info → Run anyway*).


**Option A: build the .exe (recommended)**
1. Install [Python 3.12](https://www.python.org/downloads/windows/) (64-bit) and tick *Add python.exe to PATH*.
2. Double-click `build.bat`. It creates a virtual environment, installs the dependencies, runs the tests and builds
   `dist\OpenRAR\` containing `OpenRAR.exe` (the app) and `orar.exe` (command line).
3. Optional: install [NSIS 3](https://nsis.sourceforge.io) and run `build.bat` again. It then also builds
   `dist\OpenRAR-Setup-1.0.0.exe` from `packaging\installer.nsi`, a per-user installer (no admin rights) with
   Start-menu shortcuts, Explorer integration, optional PATH entry and an uninstaller.
   On Linux the same script builds with `makensis packaging/installer.nsi`.

**Option B: run from source**: double-click `run.bat`.

Then open **Options → Settings → Integration** and click *Apply integration* to get the Explorer context menu.
On Windows 11 the entries are under *Show more options* (Shift+Right-click).

## Command line

```
orar a  -m5 -p -hpSecret -v700m -rr5 -t backup.7z C:\Data      create: best, AES + hidden names, 700 MB volumes,
                                                                 5% recovery record, test afterwards
orar a  -sfx setup.exe C:\MyApp                                 self-extracting archive
orar x  archive.rar D:\Out\                                     extract with paths   (e = without paths)
orar x  -here archive.zip                                       smart "Extract Here"
orar l  archive.7z                                              list      orar i archive.7z   information
orar t  archive.zip                                             test      orar r archive.zip  repair
orar d  archive.zip docs/old.txt                                delete    orar rn archive.zip a.txt b.txt
orar c  archive.zip "comment text"                              comment   orar k archive.zip  lock
orar rr5 archive.7z                                             add a 5% recovery record
orar cv -fmt7z old.rar new.7z                                   convert   orar s archive.zip  make SFX
```
Run `orar help` for every switch. Exit codes: 0 OK, 2 error, 3 damaged/CRC errors, 255 cancelled.

## SFX options
The SFX module reads its settings from the ZIP comment (the GUI writes them for you):
`Title=`, `Text=`, `Path=` (environment variables allowed, e.g. `%ProgramFiles%\App`), `Setup=` (run after extraction),
`Silent=1`, `Overwrite=0|1|2` (ask / all / skip), `TempMode=1` (extract to temp, run Setup, wait, clean up), `License=`.
At run time: `setup.exe /S` (silent) and `/D=C:\Target`.

The module source is in `sfx_stub/sfx.c`. Rebuild it with `sfx_stub/build.sh` (mingw-w64 + zlib).

## How some features work
* **Recovery records** sit next to the archive as `archive.ext.orr`, so the archive itself stays readable by every
  tool. The archive is divided into sectors with CRC32s. With *N* parity sectors (N = record size), sector *i*
  belongs to parity group *i mod N*. Any damage that hits at most one sector per group can be rebuilt, which covers
  a contiguous damaged block up to the record size and truncated files. Results are verified with SHA-256.
* **Lock** stores a marker in the ZIP comment. OpenRAR then refuses changes. Other tools ignore it, as they do with
  WinRAR locks. ZIP only.
* **Solid** compression is always on for 7Z and TAR.* (best ratio). ZIP compresses each file on its own (fast
  random access).
* Settings live in `%APPDATA%\OpenRAR\settings.json`.

## Development
```
pip install -r requirements-dev.txt
python -m pytest            # 45 tests: all formats, encryption, volumes, recovery, repair, SFX, RAR, CLI
python -m openrar           # GUI
python -m openrar help      # CLI
python packaging/make_icon.py   # regenerate icons
```
