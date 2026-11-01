#!/bin/sh
# Probe org.gnome.Sdk//VERSION for everything the APK Starter manifest needs.
# Usage: sh tools/sdk-probe.sh VERSION
# Prints "OK <name>" / "MISSING <name>" per line, last line "BASE <freedesktop-version>".
set -u
V=${1:?usage: sdk-probe.sh GNOME_VERSION}

PKGS="gtk4 webkitgtk-6.0 libportal libportal-gtk4 sqlite3 icu-i18n icu-uc libavcodec libavformat
libavutil libswscale libdrm gudev-1.0 libsecret-1 fontconfig pangoft2 libwebp vulkan openxr
wayland-protocols wayland-client alsa gl egl libbsd libunwind libelf valgrind openssl expat liblz4
liblzma libcap zlib"
PROGS="zip python3 meson ninja autoreconf appstreamcli desktop-file-validate"

flatpak run --user --command=sh --env=PATH=/usr/bin:/usr/lib/sdk/openjdk21/bin \
    "org.gnome.Sdk//$V" -c '
for p in '"$(echo $PKGS)"'; do
    if pkg-config --exists "$p"; then echo "OK $p"; else echo "MISSING $p"; fi
done
if test -x /usr/lib/sdk/openjdk21/bin/javac; then echo "OK javac"; else echo "MISSING javac"; fi
for p in '"$PROGS"'; do
    if command -v "$p" >/dev/null 2>&1; then echo "OK $p"; else echo "MISSING $p"; fi
done
' 2>/dev/null

if flatpak run --user --command=python3 "org.gnome.Platform//$V" -c \
    "import gi; gi.require_version('Gio','2.0'); from gi.repository import Gio" 2>/dev/null; then
    echo "OK platform-gi-gio"
else
    echo "MISSING platform-gi-gio"
fi

base=$(flatpak info --user --show-metadata "org.gnome.Platform//$V" |
    awk '/^\[Extension org.freedesktop.Platform.GL\]/{s=1; next} /^\[/{s=0}
         s && /^versions *=/{sub(/^versions *= */, ""); split($0, a, ";"); print a[1]; exit}')
echo "BASE ${base:-unknown}"
