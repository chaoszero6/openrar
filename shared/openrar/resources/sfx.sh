#!/bin/sh
# OpenRAR self-extracting archive (Linux / Unix). Needs only sh, tail, gzip and tar.
# Run:  sh ./@NAME@  [--target DIR] [--silent] [--list] [--help]
@OPTIONS@
OPENRAR_OFFSET=@OFFSET@
@SETTINGS@

usage() {
    echo "Usage: sh $0 [--target DIR] [--silent] [--list]"
    echo "  --target DIR, -d DIR   extract into DIR"
    echo "  --silent, -y, /S       no questions (accept license, overwrite as configured)"
    echo "  --list, -l             list the contents and exit"
}

TARGET=""; LIST=0
while [ $# -gt 0 ]; do
    case "$1" in
        --target|-d) TARGET="$2"; shift 2 ;;
        --target=*) TARGET="${1#--target=}"; shift ;;
        /D=*) TARGET="${1#/D=}"; shift ;;
        --silent|-y|-s|/S) SILENT=1; shift ;;
        --list|-l) LIST=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
    esac
done

payload() { tail -c +"$((OPENRAR_OFFSET + 1))" "$0" | gzip -dc; }

if [ "$LIST" = 1 ]; then
    payload | tar -tvf -
    exit $?
fi

# a desktop dialog when started without a terminal (double-clicked) and zenity is available
GUI=0
if [ "$SILENT" != 1 ] && [ ! -t 0 ] && [ -n "$DISPLAY$WAYLAND_DISPLAY" ] && command -v zenity >/dev/null 2>&1; then
    GUI=1
fi
[ -n "$TITLE" ] || TITLE="Self-extracting archive"

info() {
    if [ "$GUI" = 1 ]; then zenity --info --title="$TITLE" --text="$1" --width=440 2>/dev/null
    else printf '%s\n' "$1"; fi
}
fail() {
    if [ "$GUI" = 1 ]; then zenity --error --title="$TITLE" --text="$1" --width=440 2>/dev/null
    else printf '%s\n' "$1" >&2; fi
    exit 1
}
confirm() {
    [ "$SILENT" = 1 ] && return 0
    if [ "$GUI" = 1 ]; then zenity --question --title="$TITLE" --text="$1" --width=440 2>/dev/null; return $?; fi
    printf '%s [y/N] ' "$1"
    read -r ans || return 1
    case "$ans" in [yY]*) return 0 ;; *) return 1 ;; esac
}

if [ "$SILENT" != 1 ]; then
    [ "$GUI" = 1 ] || printf '=== %s ===\n\n' "$TITLE"
    [ -n "$TEXT" ] && info "$TEXT"
    if [ -n "$LICENSE" ]; then
        if [ "$GUI" = 1 ]; then
            lic=$(mktemp) || exit 1
            printf '%s\n' "$LICENSE" > "$lic"
            zenity --text-info --title="$TITLE - License" --filename="$lic" --checkbox="I accept the license" \
                --width=560 --height=440 2>/dev/null
            rc=$?; rm -f "$lic"
            [ $rc = 0 ] || exit 1
        else
            printf '%s\n\n' "$LICENSE"
            confirm "Do you accept the license?" || { echo "Cancelled."; exit 1; }
        fi
    fi
fi

# destination
here=$(cd "$(dirname "$0")" 2>/dev/null && pwd)
if [ "$TEMPMODE" = 1 ]; then
    DEST=$(mktemp -d "${TMPDIR:-/tmp}/openrar-sfx.XXXXXX") || exit 1
elif [ -n "$TARGET" ]; then
    DEST=$TARGET
else
    if [ -n "$DEFAULT_PATH" ]; then
        case "$DEFAULT_PATH" in "~"|"~/"*) DEFAULT_PATH="$HOME${DEFAULT_PATH#\~}" ;; esac
        DEST=$(eval "printf '%s' \"$DEFAULT_PATH\"")
        case "$DEST" in /*) ;; *) DEST="$here/$DEST" ;; esac
    else
        DEST=$here
    fi
    if [ "$SILENT" != 1 ]; then
        if [ "$GUI" = 1 ]; then
            DEST=$(zenity --file-selection --directory --title="$TITLE - Extract to" --filename="$DEST/" 2>/dev/null) || exit 1
        else
            printf 'Extract to [%s]: ' "$DEST"
            read -r ans && [ -n "$ans" ] && DEST=$ans
        fi
    fi
fi
mkdir -p "$DEST" || fail "Cannot create $DEST"

# existing files
KEEP=""
first=$(payload | tar -tf - | while IFS= read -r n; do
    if [ -e "$DEST/$n" ] && [ ! -d "$DEST/$n" ]; then printf '%s\n' "$n"; break; fi
done)
if [ -n "$first" ]; then
    case "$OVERWRITE" in
        1) ;;
        2) KEEP=1 ;;
        *) confirm "Some files already exist in $DEST (for example $first). Overwrite them?" || KEEP=1 ;;
    esac
fi
if [ -n "$KEEP" ]; then
    if tar --help 2>&1 | grep -q -- --skip-old-files; then KEEP=--skip-old-files; else KEEP=-k; fi
fi

payload | tar -xf - -C "$DEST" $KEEP || fail "Extraction failed. The file may be damaged or incomplete."

RC=0
if [ -n "$SETUP" ]; then
    (cd "$DEST" && sh -c "$SETUP")
    RC=$?
elif [ "$SILENT" != 1 ] && [ "$TEMPMODE" != 1 ]; then
    info "Files extracted to $DEST"
fi
[ "$TEMPMODE" = 1 ] && rm -rf "$DEST"
exit $RC
