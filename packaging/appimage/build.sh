#!/bin/sh
# build.sh TARBALL OUTDIR (as root in a Debian 13 container, x86_64): the AppImage of APK Starter from the offline
# source bundle (packaging/collect-sources.sh):
#   1. the native build (native/meson.build, prefix /usr/lib/apk-starter as the .deb, with Digitalis) into an AppDir,
#   2. make-appdir.py: Python, GTK, WebKitGTK and the libraries of Debian 13 into the AppDir; libgtk-4 built again from
#      the same GTK release with gtk4-linux-dmabuf-version.patch (Debian's crashes with KWin 6.8),
#   3. appimagetool -> OUTDIR/APK_Starter-VERSION-x86_64.AppImage (+ .sha256, build.log, appdir-du.txt).
# Debian 13 is the base because the AppImage runs on systems with at least its glibc (2.41); Debian 12 and
# Ubuntu 24.04 are too old for the build anyway (GTK 4.8, libunwind < 1.8).
# Network: apt, the Digitalis bundle (sha256 from the manifest), the GTK release (sha256 below) and appimagetool. APPIMAGETOOL=FILE takes a local
# appimagetool, RUNTIME=FILE a local type2 runtime. WORK (default /build/aiwork) keeps the native build between runs
# (build.py rebuilds a component only when its commit changed).
set -eu
tar=$(realpath "$1"); out=$(realpath "$2")
work=${WORK:-/build/aiwork}; prefix=/usr/lib/apk-starter
[ "$(uname -m)" = x86_64 ] || { echo "build.sh: x86_64 only (Digitalis, WebKit path)" >&2; exit 1; }
mkdir -p "$work" "$out"
t0=$(date +%s); stamp() { echo "== $(($(date +%s) - t0)) s: $*"; }

stamp apt
export DEBIAN_FRONTEND=noninteractive LC_ALL=C.UTF-8 LANG=C.UTF-8
apt-get update -qq
apt-get install -y -qq --no-install-recommends xz-utils ca-certificates >/dev/null
rm -rf "$work/src" "$work/AppDir"; mkdir -p "$work/src"
tar -xf "$tar" -C "$work/src"
src=$(echo "$work"/src/apk-starter-*); version=${src##*/apk-starter-}
# shellcheck disable=SC2046
apt-get install -y -qq --no-install-recommends $(grep -v '^#' "$src/native/packages-debian.txt") \
  glib-networking libgdk-pixbuf2.0-bin librsvg2-common libglib2.0-bin adwaita-icon-theme squashfuse file curl >/dev/null

stamp "native build $version"
[ -f "$work/obj/build.ninja" ] || meson setup "$work/obj" "$src/native" --prefix=$prefix -Ddownload=false -Ddigitalis=true >/dev/null
meson configure "$work/obj" -Ddigitalis=true >/dev/null   # the source directory may have moved: setup keeps it
nice meson compile -C "$work/obj" > "$out/native-build.log" 2>&1 || { tail -40 "$out/native-build.log"; exit 1; }
meson install -C "$work/obj" --no-rebuild --destdir "$work/AppDir" >/dev/null

# libgtk-4 of Debian's GTK release with one change (gtk4-linux-dmabuf-version.patch); the rest of GTK (typelib, data)
# stays Debian's, which is why the release must be the same
gtkv=4.18.6; gtksha=e1817c650ddc3261f9a8345b3b22a26a5d80af154630dedc03cc7becefffd0fa
have=$(dpkg-query -W -f '${Version}' libgtk-4-1); case $have in $gtkv+*|$gtkv-*) ;; *)
  echo "build.sh: Debian's GTK is $have, the patch is for $gtkv: update gtkv/gtksha" >&2; exit 1;; esac
gtk=$work/gtk-$gtkv; m=${gtkv#4.}; gtkso=libgtk-4.so.1.${m%.*}00.${m#*.}
if [ ! -f "$gtk/stamp" ] || [ "$(cat "$gtk/stamp")" != "$(sha256sum "$src/packaging/appimage/gtk4-linux-dmabuf-version.patch")" ]; then
  stamp "GTK $gtkv"
  apt-get install -y -qq --no-install-recommends libpng-dev libjpeg-dev libtiff-dev glslc sassc >/dev/null
  mkdir -p "$gtk"; curl -fsSL -o "$gtk.tar.xz" "https://download.gnome.org/sources/gtk/${gtkv%.*}/gtk-$gtkv.tar.xz"
  echo "$gtksha  $gtk.tar.xz" | sha256sum -c --quiet
  tar -xJf "$gtk.tar.xz" -C "$gtk" --strip-components=1 && patch -d "$gtk" -p1 < "$src/packaging/appimage/gtk4-linux-dmabuf-version.patch"
  meson setup "$gtk/b" "$gtk" --prefix=/usr --libdir=lib/x86_64-linux-gnu --buildtype=release -Dintrospection=disabled \
    -Ddocumentation=false -Dman-pages=false -Dbuild-demos=false -Dbuild-examples=false -Dbuild-tests=false \
    -Dbuild-testsuite=false -Dmedia-gstreamer=disabled -Dprint-cups=disabled -Dprint-cpdb=disabled \
    -Dcloudproviders=disabled -Dsysprof=disabled -Dtracker=disabled -Dcolord=disabled -Daccesskit=disabled >/dev/null
  nice ninja -C "$gtk/b" gtk/$gtkso > "$out/gtk-build.log" 2>&1 || { tail -20 "$out/gtk-build.log"; exit 1; }
  sha256sum "$src/packaging/appimage/gtk4-linux-dmabuf-version.patch" > "$gtk/stamp"
fi

stamp AppDir
id=$(sha256sum "$tar" | cut -c1-16)
python3 -I "$src/packaging/appimage/make-appdir.py" "$work/AppDir" "$src" "$id"
cp "$gtk/b/gtk/$gtkso" "$work/AppDir/usr/lib/libgtk-4.so.1" && strip --strip-unneeded "$work/AppDir/usr/lib/libgtk-4.so.1"

stamp appimagetool
tool=${APPIMAGETOOL:-$work/appimagetool-x86_64.AppImage}
[ -f "$tool" ] || curl -fsSL -o "$tool" https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-x86_64.AppImage
chmod +x "$tool"; sha256sum "$tool" > "$out/tools.sha256"
rt=${RUNTIME:-$work/runtime-x86_64}
[ -f "$rt" ] || curl -fsSL -o "$rt" https://github.com/AppImage/type2-runtime/releases/download/continuous/runtime-x86_64
sha256sum "$rt" >> "$out/tools.sha256"
img="$out/APK_Starter-$version-x86_64.AppImage"
rm -f "$img"
APPIMAGE_EXTRACT_AND_RUN=1 ARCH=x86_64 "$tool" --no-appstream --runtime-file "$rt" "$work/AppDir" "$img" > "$out/appimagetool.log" 2>&1 \
  || { tail -20 "$out/appimagetool.log"; exit 1; }
(cd "$out" && sha256sum "${img##*/}" > "${img##*/}.sha256")
du -sh "$work/AppDir" "$work/AppDir$prefix" "$work/AppDir/usr/lib" > "$out/appdir-du.txt"
ls -l "$img"; cat "$out/appdir-du.txt"
stamp done
