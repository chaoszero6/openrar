# OpenRAR

A free archive manager for **Windows**, **Linux** and **Android**. It is an independent app, not affiliated with
WinRAR or RARLAB. The goal is the feature set of a paid WinRAR license on open formats, with no trial period and no
nag screen.

| Folder | What it is |
|---|---|
| [`shared/`](shared/) | The archive engine, the `orar` command line and the Qt desktop interface, used by Windows and Linux |
| [`windows/`](windows/) | Windows: Explorer context menu and file associations, DPAPI password storage, `.exe` SFX module, installer |
| [`linux/`](linux/) | Linux: applications menu, MIME defaults, Dolphin / Nemo / GNOME Files menus, keyring, `.deb` package |
| [`android/`](android/) | Android: native Kotlin app (Jetpack Compose) with its own engine on zip4j, Commons Compress and 7-Zip |

Downloads for every platform are on the [Releases](../../releases) page.

## Features

| | Windows & Linux | Android |
|---|---|---|
| **Create** | ZIP, 7Z, TAR, TAR.GZ/BZ2/XZ/ZST, self-extracting `.exe` (Windows) and `.run` (Linux) | ZIP, 7Z, TAR, TAR.GZ/BZ2/XZ |
| **Open / extract** | all of the above, RAR 1.5 – 5 (solid, volumes, encrypted names), ISO, CAB, ARJ, LZH, WIM, … | all of the above, RAR 1.5 – 5, split `.001` volumes, OpenRAR SFX files |
| **Encryption** | AES-256 (ZIP and 7Z), encrypted file names (7Z), password organizer | AES-256 ZIP; opens encrypted ZIP, 7Z and RAR |
| **Split volumes** | presets for e-mail, CD, DVD, FAT32 or any size (`name.zip.001` …, 7-Zip compatible) | same format and presets |
| **Repair** | recovery records (`archive.orr`), salvage of damaged ZIPs | – |
| **Editing** | add, update, freshen, sync, delete, rename, edit files in place, lock, comments | delete, rename, add (ZIP) |
| **Tools** | find (inside archives too), convert, benchmark, virus scan, reports, wizard, test | test, info, view and share files from archives |
| **Integration** | Explorer / file-manager context menus, file associations, drag & drop | "Open with", share sheet ("Compress with OpenRAR") |
| **Command line** | `orar` with rar-style commands and switches | – |

Archives move freely between the three: the Android engine is tested against archives made by the desktop app
(including AES, encrypted names, volumes and both SFX types), and the desktop app against Android-made ones.

### The one thing it can't do: create `.rar` files
RAR is a proprietary format. Only RARLAB's software may create `.rar` archives; the UnRAR license forbids using
its code to build a RAR compressor. OpenRAR **opens and extracts** RAR everywhere. To create archives it uses 7Z
(equal or better compression) or ZIP (opens anywhere).

## Building

Each folder's README has the details.

```
shared/     python -m pytest                         engine, CLI and GUI tests (any OS)
windows/    build.bat                                PyInstaller .exe + NSIS installer   (on Windows)
linux/      ./build.sh   or   ./build_in_docker.sh   portable folder, .tar.gz and .deb   (on Linux)
android/    ./gradlew assembleRelease                APK                                 (JDK 17+, Android SDK)
```

## License
OpenRAR is released under the [MIT License](LICENSE).

The builds bundle third-party components under their own licenses: Qt / PySide6 (LGPL-3.0, shipped as separate
replaceable libraries), py7zr (LGPL-2.1+), pyzipper (MIT), rarfile (ISC), zstandard (BSD-3-Clause), cryptography
(Apache-2.0 or BSD-3-Clause), keyring (MIT), zlib (zlib license, statically linked into the Windows SFX module); on
Android zip4j (Apache-2.0), Apache Commons Compress / IO / Lang (Apache-2.0), XZ for Java (public domain), junrar
(UnRAR license) and 7-Zip-JBinding-4Android (LGPL-2.1 with the unRAR restriction; RAR decoding uses unRAR code by
Alexander Roshal). The RAR test files in `shared/tests/data` come from the rarfile test suite (ISC license).
RAR is a trademark of RARLAB; OpenRAR is not affiliated with RARLAB or WinRAR.
