#!/bin/sh
# Cross-compile the SFX module on Linux (mingw-w64 + libz-mingw-w64-dev).
# On Windows with MSYS2: pacman -S mingw-w64-x86_64-gcc mingw-w64-x86_64-zlib, then run this script.
set -e
cd "$(dirname "$0")"
CC=${CC:-x86_64-w64-mingw32-gcc}
WINDRES=${WINDRES:-x86_64-w64-mingw32-windres}
$WINDRES sfx.rc -O coff -o sfx.res.o
$CC -Os -s -municode -mwindows -Wall -Wextra -Wno-cast-function-type -static \
    -o ../openrar/resources/sfx.exe sfx.c sfx.res.o -lz -lcomctl32 -lshell32 -lole32 -luser32 -lgdi32
rm -f sfx.res.o
echo "built ../openrar/resources/sfx.exe"
