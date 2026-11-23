#!/bin/sh
# ct-test.sh (as root in a FRESH container of the distribution under test; files in /build/t): the AppImage
# (/build/t/*.AppImage) on that system. Installs only what a desktop has anyway (KWin for the headless session, Mesa,
# fonts, FUSE, PulseAudio with ALSA's pulse plugin, PipeWire's client library) and nothing of ATL/GTK/WebKit, then
# as user «nutzer»: which bundled library finds no host library (ldd on the extracted AppImage), --version, --install
# of every /build/t/*.apk, and test-apps.py (each app in a headless KWin session, then a menu entry beside the
# starter's window). Sound: an own PulseAudio with a null sink; per app a WAV and its level (pegel.py).
# Needs /build/t/atl_doctor.tar (atl-doctor's Python package, for the headless session). APPS: PKG[:SECONDS][@URI] list.
# With /build/t/vlc.apk, vlc-prefs.xml and clip.mp4 also VLC playing the clip (the sound check).
# Results in /build/t/res (test.log, screenshots, logs). Exit code 0 also on failures (they are the result).
# PKG_ONLY=1 installs the packages only.
set -u
T=/build/t; R=$T/res; mkdir -p $R
exec > $R/test.log 2>&1
export LC_ALL=C.UTF-8 LANG=C.UTF-8
. /etc/os-release; echo "system: $PRETTY_NAME"
deb="kwin-wayland kde-spectacle dbus-daemon dbus-user-session mesa-vulkan-drivers libgl1-mesa-dri libegl1 libgles2
  libvulkan1 fonts-dejavu-core xauth libglib2.0-bin python3 fuse3 pulseaudio pulseaudio-utils libasound2-plugins
  libpipewire-0.3-0t64 libva2 libva-drm2 libva-x11-2 libva-wayland2 libvdpau1 libelf1t64 libharfbuzz-subset0 procps file"
case $ID in
debian|ubuntu) export DEBIAN_FRONTEND=noninteractive; apt-get update -qq
  apt-get install -y -qq --no-install-recommends $deb >/dev/null 2>&1 \
    || for p in $deb; do apt-get install -y -qq --no-install-recommends $p >/dev/null 2>&1 || echo "package missing: $p"; done;;
fedora) dnf -q -y install kwin-wayland spectacle dbus-daemon mesa-dri-drivers mesa-libEGL mesa-libGLES \
    mesa-vulkan-drivers vulkan-loader dejavu-sans-fonts python3 fuse3 pulseaudio pulseaudio-utils alsa-plugins-pulseaudio \
    pipewire-libs libva libvdpau elfutils-libelf harfbuzz procps-ng file glib2 >/dev/null;;
arch) pacman-key --init >/dev/null 2>&1; pacman-key --populate archlinux >/dev/null 2>&1
  pacman -Syu --noconfirm --disable-sandbox >/dev/null 2>&1
  pacman -S --noconfirm --needed --disable-sandbox kwin spectacle dbus mesa vulkan-icd-loader ttf-dejavu xorg-xauth \
    glib2 python fuse3 pulseaudio pulseaudio-alsa alsa-plugins libpipewire libva libvdpau libelf harfbuzz procps-ng file \
    >/dev/null 2>&1;;
esac
echo "packages rc=$?"
[ -z "${PKG_ONLY:-}" ] || exit 0                  # PKG_ONLY=1: only the packages (before the AppImage is there)
echo "glibc: $(ldd --version | head -1)"
id nutzer >/dev/null 2>&1 || useradd -m -s /bin/sh nutzer
H=/home/nutzer; W=$H/w; AI=$H/Apps/APK_Starter.AppImage
mkdir -p $W/xdg/data $W/xdg/cache $W/xdg/run $H/Apps
chmod 700 $W/xdg/run
cp $T/*.AppImage $AI; chmod +x $AI
tar -xf $T/atl_doctor.tar -C $W
cp $T/*.apk $T/pegel.py $W/
chown -R nutzer: $H
U="runuser -u nutzer -- env HOME=$H XDG_DATA_HOME=$W/xdg/data XDG_CACHE_HOME=$W/xdg/cache XDG_RUNTIME_DIR=$W/xdg/run"
echo "--- bundled libraries without a host library (ldd, extracted AppImage)"
(cd $W && $U $AI --appimage-extract >/dev/null 2>&1)
x=$W/squashfs-root; p=$x/usr/lib/apk-starter
python3 -c 'import os, sys
for d, _, fs in os.walk(sys.argv[1]):
    for f in fs:
        p = os.path.join(d, f)
        if "/lib/digitalis/" not in p and not os.path.islink(p) and not p.endswith((".oat", ".art", ".vdex", ".odex")):
            with open(p, "rb") as h:
                if h.read(4) == b"\x7fELF": print(p)' $x > $W/elf.list
LD_LIBRARY_PATH=$p/lib:$p/lib/art:$x/usr/lib:$p/lib/java/dex/android_translation_layer/natives \
  xargs -a $W/elf.list -n 50 ldd 2>/dev/null | sed -n 's/^\s*\(\S*\) => not found.*/\1/p' | sort | uniq -c
echo "(ok if only libraries ART/bionic load from their own directories)"
echo "--- AppImage --version (FUSE)"
$U $AI --version; echo "rc=$?"
if ! $U $AI --version >/dev/null 2>&1; then echo "FUSE start failed: from here APPIMAGE_EXTRACT_AND_RUN=1"
  U="$U APPIMAGE_EXTRACT_AND_RUN=1"; $U $AI --version; echo "rc=$?"; fi
echo "--- install"
pkgs=""
for a in $W/*.apk; do
  s=$(date +%s); $U $AI --install "$a" > $R/install-$(basename "$a").log 2>&1; rc=$?
  echo "install $(basename "$a") rc=$rc $(( $(date +%s) - s )) s"; tail -2 $R/install-$(basename "$a").log
done
$U $AI --list 2>&1 | head -12
grep -h '^Exec=' $W/xdg/data/applications/*.desktop 2>/dev/null | head -5
vlc=""
if [ -f $T/vlc.apk ] && [ -f $T/clip.mp4 ]; then          # VLC without its first-run tips, the clip as a file
  d=$W/xdg/data/io.github.Jimbambuli.ApkStarter/data/org.videolan.vlc/org.videolan.vlc.apk_/shared_prefs
  mkdir -p $d && cp $T/vlc-prefs.xml $d/org.videolan.vlc_preferences.xml && cp $T/clip.mp4 $W/clip.mp4
  chown -R nutzer: $W/xdg/data $W/clip.mp4; vlc="org.videolan.vlc:25@file://$W/clip.mp4"; fi
# sound: own PulseAudio (no session bus, null sink); ATL's AudioTrack (ALSA) through the ALSA pulse plugin
P=$W/pulse; mkdir -p $P/run $P/state; chown -R nutzer: $P
printf 'pcm.!default { type pulse server "unix:%s" }\nctl.!default { type pulse server "unix:%s" }\n' \
  $P/run/native $P/run/native > $W/asound.conf
runuser -u nutzer -- env DBUS_SESSION_BUS_ADDRESS=disabled: PULSE_RUNTIME_PATH=$P/run PULSE_STATE_PATH=$P/state \
  XDG_RUNTIME_DIR=$P/run pulseaudio -n --daemonize=no --exit-idle-time=-1 --use-pid-file=no --system=no -F /dev/null \
  -L "module-null-sink sink_name=aip rate=48000" -L "module-native-protocol-unix socket=$P/run/native auth-anonymous=1" \
  --log-target=file:$P/pulse.log & PP=$!
sleep 3
echo "--- apps"
$U PYTHONPATH=$W PULSE_SERVER=unix:$P/run/native ALSA_CONFIG_PATH=$W/asound.conf \
  timeout 3600 python3 $T/test-apps.py $AI $W/res ${APPS:-de.baumann.browser:25 com.dozingcatsoftware.bouncy:20 com.artifex.mupdf.viewer.app:25 io.anuke.mindustry:40 $vlc}
echo "test-apps rc=$?"
kill $PP
for w in $W/res/*.wav; do [ -f "$w" ] && echo "sound $(basename $w): $(python3 $W/pegel.py $w)"; done
cp -r $W/res/. $R/
echo "--- ATL logs (errors)"
for f in $(find $W/xdg/data -name '*.log' 2>/dev/null | head -12); do echo "## $f"
  grep -i -E 'error|fail|not found|cannot|native bridge|fatal' "$f" | sort | uniq -c | sort -rn | head -6 | cut -c1-220; done
echo "--- mounts left"; grep -c -E '\.mount_|apk-starter-appimage-' /proc/self/mountinfo
ls -la $W/xdg/run
