# OpenRAR shared engine and desktop interface

Everything here runs on both Windows and Linux: the archive engine (`openrar/core`), the `orar` command line
(`openrar/cli.py`) and the Qt interface (`openrar/gui`). Operating-system specifics live in plugin packages next to
this folder, [`windows/openrar_windows`](../windows/) and [`linux/openrar_linux`](../linux/), and are reached
through `openrar/platform.py`. Without a plugin (for example when running the tests here) generic behaviour is used.

## Development
```
pip install -r requirements-dev.txt
python -m pytest              # engine, formats, encryption, volumes, recovery, SFX (both kinds), RAR, CLI, GUI
python -m openrar             # GUI without OS integration (use ../windows/run.bat or ../linux/run.sh for that)
python -m openrar help        # CLI
python tools/make_icon.py     # regenerate the icons in openrar/resources
```

## Command line

```
orar a  -m5 -p -hpSecret -v700m -rr5 -t backup.7z C:\Data      create: best, AES + hidden names, 700 MB volumes,
                                                                 5% recovery record, test afterwards
orar a  -sfx setup.exe C:\MyApp                                 self-extracting archive for Windows
orar a  -sfx setup.run ~/MyApp                                  self-extracting archive for Linux
orar x  archive.rar D:\Out\                                     extract with paths   (e = without paths)
orar x  -here archive.zip                                       smart "Extract Here"
orar l  archive.7z                                              list      orar i archive.7z   information
orar t  archive.zip                                             test      orar r archive.zip  repair
orar d  archive.zip docs/old.txt                                delete    orar rn archive.zip a.txt b.txt
orar c  archive.zip "comment text"                              comment   orar k archive.zip  lock
orar rr5 archive.7z                                             add a 5% recovery record
orar cv -fmt7z old.rar new.7z                                   convert   orar s archive.zip out.run  make SFX
```
Run `orar help` for every switch. Exit codes: 0 OK, 2 error, 3 damaged/CRC errors, 255 cancelled.

## Self-extracting archives
Both kinds take the same options (the GUI writes them for you): `Title=`, `Text=`, `Path=` (default destination),
`Setup=` (run after extraction), `Silent=1`, `Overwrite=0|1|2` (ask / all / skip), `TempMode=1` (extract to a
temporary folder, run Setup, then clean up) and `License=`.

* **Windows `.exe`**: a native 100 KB module (source in `../windows/sfx_stub`) followed by a ZIP; the options are
  kept in the ZIP comment. `Path=` accepts environment variables such as `%ProgramFiles%\App`. Run with
  `setup.exe /S` (silent) or `/D=C:\Target`.
* **Linux `.run`**: a POSIX shell script (`openrar/resources/sfx.sh`) followed by a TAR.GZ; it needs only `sh`,
  `tail`, `gzip` and `tar`, and uses `zenity` dialogs when double-clicked on a desktop. `Path=` accepts `$HOME/App`
  or `~/App`. Run with `sh setup.run [--target DIR] [--silent] [--list]`. Linux SFX archives can't be encrypted.

Either kind can be created, opened, tested and edited on both systems.

## How some features work
* **Recovery records** sit next to the archive as `archive.ext.orr`, so the archive itself stays readable by every
  tool. The archive is divided into sectors with CRC32s. With *N* parity sectors (N = record size), sector *i*
  belongs to parity group *i mod N*. Any damage that hits at most one sector per group can be rebuilt, which covers
  a contiguous damaged block up to the record size and truncated files. Results are verified with SHA-256.
* **Lock** stores a marker in the ZIP comment. OpenRAR (desktop and Android) then refuses changes. Other tools
  ignore it, as they do with WinRAR locks. ZIP only.
* **Solid** compression is always on for 7Z and TAR.* (best ratio). ZIP compresses each file on its own (fast
  random access).
* **RAR and other read-only formats**: listing RAR works on its own; unpacking uses 7-Zip or UnRAR when installed
  (on Windows, the built-in `tar.exe` is a fallback without password support). ISO, CAB, ARJ, LZH, WIM etc. use 7-Zip.
* **Programs started from OpenRAR** (opening a file from an archive) get a clean environment, without the bundled
  Qt and library paths of the packaged app (`openrar/gui/launch.py`).
* Settings live in `%APPDATA%\OpenRAR\settings.json` on Windows and `~/.config/OpenRAR/settings.json` on Linux.
