"""Linux desktop integration, per user (XDG directories) or system-wide (used by the .deb package):

* applications menu entry      <data>/applications/openrar.desktop
* icon                          <data>/icons/hicolor/<size>/apps/openrar.png
* MIME type for OpenRAR SFX     <data>/mime/packages/openrar.xml   (matched by content, not by *.run)
* default application           <config>/mimeapps.list             (per user only; previous defaults restored)
* Dolphin (KDE) service menus   <data>/kio/servicemenus/ (KF6) and <data>/kservices5/ServiceMenus/ (KF5)
* Nemo (Cinnamon) actions       <data>/nemo/actions/
* Nautilus / GNOME Files        <data>/nautilus/scripts/OpenRAR/   (right-click › Scripts › OpenRAR, per user)

Every file carries the marker "X-OpenRAR-Generated" (or a "# OpenRAR" line) so uninstall only removes ours.
"""
from __future__ import annotations

import configparser
import json
import os
import re
import shutil
import subprocess

from openrar.integration import ARCHIVE_TYPES, verbs

DESKTOP_ID = "openrar.desktop"
ICON_NAME = "openrar"
MARK = "X-OpenRAR-Generated=true"
SFX_MIME = "application/x-openrar-sfx"


def data_home() -> str:
    return os.environ.get("XDG_DATA_HOME") or os.path.join(os.path.expanduser("~"), ".local", "share")


def config_home() -> str:
    return os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")


# --------------------------------------------------------------------------- quoting
_SAFE = re.compile(r"^[A-Za-z0-9_./:=+,@-]+$")


def exec_arg(a: str) -> str:
    """Quote one argument for an Exec= line (Desktop Entry spec): '%' doubled, reserved characters
    escaped inside double quotes."""
    a = a.replace("%", "%%")
    if _SAFE.match(a):
        return a
    return '"' + re.sub(r'(["`$\\])', r"\\\1", a) + '"'


def exec_line(app: list[str], args, field: str = "%F") -> str:
    value = " ".join([exec_arg(x) for x in list(app) + list(args)] + ([field] if field else []))
    return value.replace("\\", "\\\\")      # desktop-file string escaping


def sh_quote(a: str) -> str:
    return "'" + a.replace("'", "'\\''") + "'"


def archive_mimes() -> list[str]:
    out = []
    for mimes in ARCHIVE_TYPES.values():
        for m in mimes:
            if m not in out:
                out.append(m)
    return out


# --------------------------------------------------------------------------- generated files
def desktop_entry(app: list[str], icon: str = ICON_NAME) -> str:
    return "\n".join([
        "[Desktop Entry]",
        "Type=Application",
        "Name=OpenRAR",
        "GenericName=Archive Manager",
        "Comment=Create, open and extract ZIP, 7Z, RAR, TAR and other archives",
        f"Exec={exec_line(app, [], '%F')}",
        f"Icon={icon}",
        "Terminal=false",
        "Categories=Utility;Archiving;Compression;Qt;",
        "Keywords=zip;7z;rar;tar;archive;extract;compress;unzip;",
        "MimeType=" + ";".join(archive_mimes()) + ";",
        "StartupWMClass=OpenRAR",
        MARK,
        "",
    ])


def mime_package() -> str:
    return """<?xml version="1.0" encoding="UTF-8"?>
<!-- X-OpenRAR-Generated -->
<mime-info xmlns="http://www.freedesktop.org/standards/shared-mime-info">
  <mime-type type="application/x-openrar-sfx">
    <comment>OpenRAR self-extracting archive</comment>
    <sub-class-of type="application/x-shellscript"/>
    <generic-icon name="package-x-generic"/>
    <magic priority="80">
      <match type="string" value="#!/bin/sh\\n# OpenRAR self-extracting archive" offset="0"/>
    </magic>
  </mime-type>
</mime-info>
"""


def dolphin_menu(app: list[str], cfg: dict, archives: bool) -> str | None:
    vs = verbs(cfg, archives)
    if archives:
        vs = [v for v in vs if v.archives_only]
    if not vs:
        return None
    lines = ["[Desktop Entry]", "Type=Service",
             "MimeType=" + (";".join(archive_mimes() + [SFX_MIME]) if archives else "all/all") + ";",
             "X-KDE-ServiceTypes=KonqPopupMenu/Plugin",
             "Actions=" + ";".join(v.key for v in vs) + ";", f"Icon={ICON_NAME}"]
    if cfg.get("cascaded"):
        lines.append("X-KDE-Submenu=OpenRAR")
    lines += [MARK, ""]
    for v in vs:
        lines += [f"[Desktop Action {v.key}]", f"Name={v.label}", f"Icon={ICON_NAME}",
                  f"Exec={exec_line(app, v.args, '%F')}", ""]
    return "\n".join(lines)


def nemo_action(app: list[str], v, cascaded: bool) -> str:
    sel = ["Mimetypes=" + ";".join(archive_mimes() + [SFX_MIME]) + ";"] if v.archives_only else ["Extensions=any;"]
    return "\n".join(["[Nemo Action]", "Active=true", ("Name=OpenRAR: " if cascaded else "Name=") + v.label,
                      f"Comment=OpenRAR - {v.label}", f"Exec={exec_line(app, v.args, '%F')}",
                      f"Icon-Name={ICON_NAME}", "Selection=notnone", *sel, "Quote=double", MARK, ""])


def nautilus_script(app: list[str], v) -> str:
    cmd = " ".join(sh_quote(x) for x in list(app) + list(v.args))
    return f"""#!/bin/sh
# OpenRAR - {v.label} (generated; removed by OpenRAR's "Remove all integration")
if [ -n "$NAUTILUS_SCRIPT_SELECTED_FILE_PATHS" ]; then
    set --
    while IFS= read -r f || [ -n "$f" ]; do
        [ -n "$f" ] && set -- "$@" "$f"
    done <<EOF
$NAUTILUS_SCRIPT_SELECTED_FILE_PATHS
EOF
fi
exec {cmd} "$@"
"""


# --------------------------------------------------------------------------- install / uninstall
def _paths(data: str):
    return {
        "desktop": os.path.join(data, "applications", DESKTOP_ID),
        "mime": os.path.join(data, "mime", "packages", "openrar.xml"),
        "kf6": os.path.join(data, "kio", "servicemenus"),
        "kf5": os.path.join(data, "kservices5", "ServiceMenus"),
        "nemo": os.path.join(data, "nemo", "actions"),
        "nautilus": os.path.join(data, "nautilus", "scripts", "OpenRAR"),
        "backup": os.path.join(data, "openrar", "mimeapps-backup.json"),
    }


def _write(path: str, text: str, executable: bool = False):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    os.chmod(path, 0o755 if executable else 0o644)


def _is_ours(path: str) -> bool:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            head = f.read(4096)
    except OSError:
        return False
    return "X-OpenRAR-Generated" in head or "\n# OpenRAR - " in head


def install(cfg: dict, app: list[str], icon_png: str | None, data: str | None = None,
            config: str | None = None, per_user: bool = True):
    """Write all integration files. `per_user=False` (the .deb build) skips mimeapps.list and Nautilus."""
    data = data or data_home()
    p = _paths(data)
    uninstall(data, config if per_user else None, update_caches=False)

    icon = ICON_NAME
    if icon_png and os.path.isfile(icon_png):
        for size in (256, 48):
            dst = os.path.join(data, "icons", "hicolor", f"{size}x{size}", "apps", ICON_NAME + ".png")
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            _scaled_icon(icon_png, dst, size)
    _write(p["desktop"], desktop_entry(app, icon))
    _write(p["mime"], mime_package())

    for archives, name in ((False, "openrar-add.desktop"), (True, "openrar-extract.desktop")):
        text = dolphin_menu(app, cfg, archives)
        if text:
            _write(os.path.join(p["kf6"], name), text, executable=True)   # KF6 only runs executable menus
            _write(os.path.join(p["kf5"], name), text)
    for v in verbs(cfg, True):
        _write(os.path.join(p["nemo"], f"openrar-{v.key}.nemo_action"), nemo_action(app, v, cfg.get("cascaded")))
    if per_user:
        for v in verbs(cfg, True):
            _write(os.path.join(p["nautilus"], v.label.rstrip(".")), nautilus_script(app, v), executable=True)
        _set_defaults(cfg.get("associate", []), config or config_home(), p["backup"])
    refresh(data)


def _scaled_icon(src: str, dst: str, size: int):
    try:
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QImage
        img = QImage(src)
        if not img.isNull():
            img.scaled(size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation).save(dst, "PNG")
            return
    except Exception:
        pass
    shutil.copyfile(src, dst)


def uninstall(data: str | None = None, config: str | None = "", update_caches: bool = True):
    """Remove our files. `config=None` leaves mimeapps.list alone."""
    data = data or data_home()
    p = _paths(data)
    for f in (p["desktop"], p["mime"]):
        if _is_ours(f):
            os.remove(f)
    for d in (p["kf6"], p["kf5"], p["nemo"]):
        if os.path.isdir(d):
            for n in os.listdir(d):
                if n.startswith("openrar") and _is_ours(os.path.join(d, n)):
                    os.remove(os.path.join(d, n))
    if os.path.isdir(p["nautilus"]):
        for n in os.listdir(p["nautilus"]):
            if _is_ours(os.path.join(p["nautilus"], n)):
                os.remove(os.path.join(p["nautilus"], n))
        try:
            os.rmdir(p["nautilus"])
        except OSError:
            pass
    for size in (256, 48):
        ic = os.path.join(data, "icons", "hicolor", f"{size}x{size}", "apps", ICON_NAME + ".png")
        if os.path.exists(ic):
            os.remove(ic)
    if config is not None:
        _restore_defaults(config or config_home(), p["backup"])
    if update_caches:
        refresh(data)


# --------------------------------------------------------------------------- default applications
def _read_mimeapps(path: str) -> configparser.ConfigParser:
    cp = configparser.ConfigParser(interpolation=None, delimiters=("=",), strict=False)
    cp.optionxform = str
    try:
        cp.read(path, encoding="utf-8")
    except configparser.Error:
        pass
    return cp


def _write_mimeapps(cp, path: str):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for sec in cp.sections():
            f.write(f"[{sec}]\n")
            for k, v in cp.items(sec):
                f.write(f"{k}={v}\n")
            f.write("\n")


def _set_defaults(exts: list[str], config: str, backup_path: str):
    mimes = []
    for e in exts:
        for m in ARCHIVE_TYPES.get(e, []):
            if m not in mimes:
                mimes.append(m)
    if not mimes:
        return
    path = os.path.join(config, "mimeapps.list")
    cp = _read_mimeapps(path)
    for sec in ("Default Applications", "Added Associations"):
        if not cp.has_section(sec):
            cp.add_section(sec)
    try:
        with open(backup_path, encoding="utf-8") as f:
            backup = json.load(f)
    except (OSError, ValueError):
        backup = {}
    for m in mimes:
        cur = cp.get("Default Applications", m, fallback="")
        if m not in backup and DESKTOP_ID not in cur:
            backup[m] = cur
        cp.set("Default Applications", m, DESKTOP_ID)
        added = [x for x in cp.get("Added Associations", m, fallback="").split(";") if x and x != DESKTOP_ID]
        cp.set("Added Associations", m, ";".join([DESKTOP_ID] + added) + ";")
    _write_mimeapps(cp, path)
    os.makedirs(os.path.dirname(backup_path), exist_ok=True)
    with open(backup_path, "w", encoding="utf-8") as f:
        json.dump(backup, f, indent=1)


def _restore_defaults(config: str, backup_path: str):
    path = os.path.join(config, "mimeapps.list")
    if not os.path.exists(path):
        return
    try:
        with open(backup_path, encoding="utf-8") as f:
            backup = json.load(f)
    except (OSError, ValueError):
        backup = {}
    cp = _read_mimeapps(path)
    changed = False
    for sec in ("Default Applications", "Added Associations"):
        if not cp.has_section(sec):
            continue
        for m, v in list(cp.items(sec)):
            if DESKTOP_ID not in v.split(";"):
                continue
            rest = [x for x in v.split(";") if x and x != DESKTOP_ID]
            if sec == "Default Applications" and backup.get(m):
                cp.set(sec, m, backup[m])
            elif rest:
                cp.set(sec, m, ";".join(rest) + (";" if sec == "Added Associations" else ""))
            else:
                cp.remove_option(sec, m)
            changed = True
    if changed:
        _write_mimeapps(cp, path)
    try:
        os.remove(backup_path)
    except OSError:
        pass


def refresh(data: str):
    """Update the desktop caches when the tools exist (all optional)."""
    for cmd in (["update-desktop-database", "-q", os.path.join(data, "applications")],
                ["update-mime-database", os.path.join(data, "mime")],
                ["kbuildsycoca6", "--noincremental"], ["kbuildsycoca5", "--noincremental"]):
        exe = shutil.which(cmd[0])
        if not exe or (cmd[0] == "update-mime-database" and not os.path.isdir(cmd[1])):
            continue
        try:
            subprocess.run([exe, *cmd[1:]], capture_output=True, timeout=60)
        except (OSError, subprocess.SubprocessError):
            pass

