#!/bin/sh
# install-extras.sh DESTDIR: what the packages add to the private tree /usr/lib/apk-starter (the native build puts
# everything under its prefix): the command in /usr/bin and the menu entry, icon and metainfo where the desktop
# looks for them. Used by the three package formats alike.
set -eu
d=$1; p=/usr/lib/apk-starter; app=io.github.Jimbambuli.ApkStarter
install -d "$d/usr/bin" "$d/usr/share/applications" "$d/usr/share/icons/hicolor/scalable/apps" "$d/usr/share/metainfo"
ln -sf ../lib/apk-starter/bin/apk-starter "$d/usr/bin/apk-starter"
sed -e 's|^Exec=.*|Exec=apk-starter %f|' -e "s|^Icon=.*|Icon=$app|" "$d$p/share/applications/$app.desktop" \
  > "$d/usr/share/applications/$app.desktop"
cp "$d$p/share/icons/hicolor/scalable/apps/$app.svg" "$d/usr/share/icons/hicolor/scalable/apps/"
cp "$d$p/share/metainfo/$app.metainfo.xml" "$d/usr/share/metainfo/"
# no second copy of the same files inside the private tree
rm -r "$d$p/share/applications" "$d$p/share/icons" "$d$p/share/metainfo"
