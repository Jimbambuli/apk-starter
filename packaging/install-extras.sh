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
# what lintian and rpmlint reject: executable jars/headers, libtool's .la file, bytecode written while building
find "$d$p" -type f \( -name '*.jar' -o -name '*.h' \) -exec chmod 644 {} +
find "$d$p" -name '*.la' -delete
# development files of the bundled libraries (wolfSSL headers etc.): nothing at run time uses them
rm -rf "$d$p/include" "$d$p/lib/pkgconfig"
find "$d$p" -name .keep -size 0 -delete
find "$d$p" -name __pycache__ -type d -prune -exec rm -r {} +
# ART's Makefiles write the rpath as ${ORIGIN}; rpm's check-rpaths only accepts $ORIGIN (the same thing)
find "$d$p" -type f | while read -r f; do
  r=$(patchelf --print-rpath "$f" 2>/dev/null || true)
  case $r in *'${ORIGIN}'*) patchelf --set-rpath "$(echo "$r" | sed 's/\${ORIGIN}/$ORIGIN/g')" "$f";; esac
done
# no second copy of the same files inside the private tree
rm -r "$d$p/share/applications" "$d$p/share/icons" "$d$p/share/metainfo"
