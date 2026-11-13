#!/bin/sh
# ct-test.sh PACKAGE STEPS APK HELPER (as root, in a FRESH container; the files under /build): install PACKAGE with the
# package manager (dependencies from the repositories), apk-starter --version, start an app with the starter in a
# headless KWin as an ordinary user (atl-doctor record, steps from STEPS; HELPER = atl-doctor-input), remove the
# package, show that nothing of it is left. Prints the evidence; exit 1 on the first failure.
set -eu
pkg=$(realpath "$1"); steps=$(realpath "$2"); apk=$(realpath "$3"); helper=$(realpath "$4")
P=/usr/lib/apk-starter
. /etc/os-release
case $pkg in
*.deb) export DEBIAN_FRONTEND=noninteractive; apt-get update -qq
  apt-get install -y -qq --no-install-recommends "$pkg" kwin-wayland dbus-daemon dbus-user-session mesa-vulkan-drivers \
    libgl1-mesa-dri fonts-dejavu-core xauth >/dev/null
  files() { dpkg -L apk-starter; }; remove() { apt-get remove -y -qq apk-starter >/dev/null; } ;;
*.rpm) if command -v zypper >/dev/null; then
    zypper -q --non-interactive --no-gpg-checks install --no-recommends "$pkg" kwin6 dbus-1-daemon Mesa-dri \
      dejavu-fonts >/dev/null; remove() { zypper -q --non-interactive remove apk-starter; }
  else dnf -q -y install "$pkg" kwin-wayland dbus-daemon mesa-dri-drivers dejavu-sans-fonts >/dev/null
    remove() { dnf -q -y remove apk-starter; }; fi
  files() { rpm -ql apk-starter; } ;;
*.pkg.tar.zst) pacman -Sy --noconfirm --disable-sandbox >/dev/null 2>&1
  pacman -U --noconfirm --disable-sandbox "$pkg" >/dev/null 2>&1
  pacman -S --noconfirm --needed --disable-sandbox kwin dbus mesa ttf-dejavu xorg-xauth >/dev/null 2>&1
  files() { pacman -Qlq apk-starter; }; remove() { pacman -R --noconfirm apk-starter >/dev/null; } ;;
esac
echo "system: $PRETTY_NAME"
files | sort > /tmp/files.list; echo "package files: $(grep -c . /tmp/files.list)"
id nutzer >/dev/null 2>&1 || useradd -m -s /bin/sh nutzer
w=/tmp/pkgtest; rm -rf $w; mkdir -p $w/xdg/data $w/xdg/cache $w/xdg/run ~nutzer/.local/lib/atl-doctor
install -m755 "$helper" ~nutzer/.local/lib/atl-doctor/atl-doctor-input; chown -R nutzer: ~nutzer/.local
cp "$steps" "$apk" $w/; chmod 700 $w/xdg/run; chown -R nutzer: $w
echo "version: $(runuser -u nutzer -- /usr/bin/apk-starter --version 2>&1)"
runuser -u nutzer -- env XDG_DATA_HOME=$w/xdg/data XDG_CACHE_HOME=$w/xdg/cache XDG_RUNTIME_DIR=$w/xdg/run \
  PULSE_SERVER=unix:/nonexistent PIPEWIRE_REMOTE=/nonexistent PYTHONPATH=$P/share/apk-starter \
  timeout 900 python3 -m atl_doctor record --headless --no-trace --launcher /usr/bin/apk-starter \
  --steps $w/$(basename "$steps") -o $w/rec $w/$(basename "$apk") > $w/record.log 2>&1 && echo "record rc=0" || echo "record rc=$?"
tail -6 $w/record.log; ls $w/xdg/data/applications 2>/dev/null
remove
left=$(while read -r f; do [ -e "$f" ] || [ -L "$f" ] && echo "$f"; done < /tmp/files.list | grep -v '^/usr/share/\(doc\|man\)' \
  | grep -vx -e / -e /usr -e /usr/bin -e /usr/lib -e /usr/share -e /usr/share/applications -e /usr/share/icons \
  -e /usr/share/icons/hicolor -e /usr/share/icons/hicolor/scalable -e /usr/share/icons/hicolor/scalable/apps \
  -e /usr/share/metainfo -e /usr/share/licenses || true)
echo "left after removal (files of the package): ${left:-none}"
[ -e $P ] && echo "$P still there: $(find $P | wc -l) entries" || echo "$P gone"
