# OpenRAR for Linux

The Linux desktop app: the [shared engine and Qt interface](../shared/) plus the Linux plugin in `openrar_linux/`:

* **Desktop integration** (`desktop.py`, *Options → Settings → Integration*):
  * applications-menu entry and icon (`~/.local/share/applications/openrar.desktop`)
  * OpenRAR as the default app for the archive types you tick (`~/.config/mimeapps.list`; the previous defaults
    are restored when you remove the integration)
  * right-click menus in **Dolphin** (KDE, as a submenu), **Nemo** (Cinnamon) and **GNOME Files** (*Scripts ›
    OpenRAR*): Add to archive…, Add to .zip, Extract files…, Extract Here, Extract to subfolder, Test
  * a MIME type for OpenRAR's self-extracting `.run` files, recognised by content (other `.run` installers are not
    affected)
* **Saved passwords** are AES-256-GCM encrypted with a key kept in your desktop keyring (GNOME Keyring, KWallet,
  KeePassXC through the Secret Service API). Without a keyring they are only obfuscated, and the password organizer
  says so.
* Opening files from archives uses `xdg-open`; *Show in folder* asks the file manager over D-Bus to highlight the file.
* Self-extracting archives for Linux (`.run`, a shell script + TAR.GZ) as well as Windows `.exe` ones.

## Install
From the [Releases](../../../releases) page:

* **Debian, Ubuntu, Mint, Pop!_OS…**: `sudo apt install ./openrar_<version>_amd64.deb`. Installs to `/opt/openrar`
  with `openrar` and `orar` on the PATH, the menu entry, file associations and the Dolphin / Nemo menus for every
  user. GNOME Files' *Scripts* menu is per user: *Options → Settings → Integration → Apply*.
* **Any other distribution**: unpack `OpenRAR-<version>-linux-x86_64.tar.gz` and run `OpenRAR/openrar`;
  *Options → Settings → Integration → Apply* adds the menu entry and file-manager commands for your user.

The builds need glibc 2.31 or newer (Ubuntu 20.04+, Debian 11+, Fedora 34+) and the usual Qt / X11 libraries
(installed automatically with the `.deb`). For RAR and ISO/CAB extraction install `7zip` (or `p7zip-full`) and
`unrar`; `zenity` gives `.run` files a graphical installer dialog.

## Build
```
./build.sh                 venv, tests, PyInstaller bundle, .tar.gz and .deb (needs python3-venv, fakeroot, dpkg-deb)
./build_in_docker.sh       the same inside Debian 11 (glibc 2.31), so the result runs on older distributions too
./build.sh --appimage      also an AppImage (needs appimagetool, see packaging/build_appimage.sh)
./run.sh                   run from source (shared/ + linux/) in a local venv
```
The PyInstaller spec (`openrar.spec`) leaves core desktop libraries (X11/xcb, xkbcommon, GL, fontconfig, GLib,
D-Bus, libstdc++) to the system: bundling older copies next to newer system ones crashes Qt on X11.

## Tests
```
python -m pytest ../shared     the engine, CLI, GUI and the Linux SFX (runs the .run scripts with sh)
python -m pytest               desktop entry, MIME defaults, Dolphin / Nemo / Nautilus menus, keyring passwords
```
