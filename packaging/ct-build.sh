#!/bin/sh
# ct-build.sh FORMAT TARBALL OUTDIR (as root, in a container of the target distribution; FORMAT deb|rpm|arch):
# unpack the offline source bundle (collect-sources.sh), install the build dependencies of the packaging with
# the package manager, build the package with the distribution's own tool (dpkg-buildpackage, rpmbuild,
# makepkg), lint it and leave package + lint log in OUTDIR. The build itself needs no network.
set -eu
fmt=$1; tar=$(realpath "$2"); out=$(realpath "$3")
work=/build/work; rm -rf "$work"; mkdir -p "$work" "$out"
tar -xJf "$tar" -C "$work"
src=$(echo "$work"/apk-starter-*); version=${src##*/apk-starter-}
sde=$(cat "$src/.source-date-epoch"); who='Jimbambuli <30672094+Jimbambuli@users.noreply.github.com>'
export APK_STARTER_VERSION=$version SOURCE_DATE_EPOCH=$sde
# $NETWORK_OFF: command that cuts the network once the dependencies are installed (proof of the offline build)
off() { [ -z "${NETWORK_OFF:-}" ] || sh -c "$NETWORK_OFF"; }
on() { [ -z "${NETWORK_ON:-}" ] || sh -c "$NETWORK_ON"; }
case $fmt in
deb)
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq && apt-get install -y -qq --no-install-recommends devscripts equivs lintian fakeroot >/dev/null
  printf 'apk-starter (%s) unstable; urgency=low\n\n  * Release %s.\n\n -- %s  %s\n' "$version" "$version" "$who" \
    "$(LC_ALL=C date -u -R -d @"$sde")" > "$src/packaging/debian/changelog"
  cp -a "$src/packaging/debian" "$src/debian"
  (cd "$src" && mk-build-deps -i -r -t 'apt-get -y -qq --no-install-recommends' debian/control >/dev/null)
  off; (cd "$src" && dpkg-buildpackage -b -uc -us); on
  cp "$work"/*.deb "$out"/
  lintian --no-tag-display-limit "$work"/*.changes > "$out/lint-deb.txt" 2>&1 || true ;;
rpm)
  if command -v zypper >/dev/null; then
    zypper -q --non-interactive install rpm-build rpmlint tar xz >/dev/null
    pm='zypper -q --non-interactive install --no-recommends'
  else dnf -q -y install rpm-build rpmlint tar xz >/dev/null; pm='dnf -q -y install'; fi
  top=$work/rpmbuild; mkdir -p "$top/SOURCES" "$top/SPECS"; cp "$tar" "$top/SOURCES/"
  spec=$top/SPECS/apk-starter.spec; cp "$src/packaging/apk-starter.spec" "$spec"
  printf '\n%%changelog\n* %s %s - %s-1\n- Release %s\n' "$(LC_ALL=C date -u -d @"$sde" '+%a %b %d %Y')" "$who" \
    "$version" "$version" >> "$spec"
  rpmspec -q --define "apkver $version" --buildrequires "$spec" | sed 's/ .*//' | sort -u | xargs $pm >/dev/null
  off; rpmbuild -bb --define "_topdir $top" --define "apkver $version" "$spec"; on
  cp "$top"/RPMS/*/*.rpm "$out"/
  rpmlint "$top"/RPMS/*/*.rpm > "$out/lint-rpm.txt" 2>&1 || true ;;
arch)
  pacman -Sy --noconfirm --needed --disable-sandbox base-devel namcap >/dev/null 2>&1
  id builder >/dev/null 2>&1 || useradd -m builder
  b=/build/pkg; rm -rf $b; mkdir -p $b; cp "$tar" "$b/apk-starter-$version.tar.xz"; tar -xJOf "$tar" \
    "apk-starter-$version/packaging/PKGBUILD" > $b/PKGBUILD; chown -R builder $b /build/work
  (. $b/PKGBUILD; pacman -S --noconfirm --needed --disable-sandbox $(echo "${makedepends[@]}" "${depends[@]}") >/dev/null)
  off; (cd $b && runuser -u builder -- env APK_STARTER_VERSION="$version" SOURCE_DATE_EPOCH="$sde" makepkg -f); on
  cp $b/*.pkg.tar.zst "$out"/
  namcap $b/PKGBUILD $b/*.pkg.tar.zst > "$out/lint-arch.txt" 2>&1 || true ;;
esac
# test helper (not part of the package): atl-doctor's fake-input client for the headless run in ct-test.sh
case $fmt in deb) h='libxkbcommon-dev';; rpm) h='pkgconfig(xkbcommon)';; arch) h=libxkbcommon;; esac
case $fmt in deb) apt-get install -y -qq $h >/dev/null;; rpm) $pm $h >/dev/null;;
  arch) pacman -S --noconfirm --needed --disable-sandbox $h >/dev/null 2>&1;; esac
jdk=$(dirname "$(dirname "$(readlink -f "$(command -v javac)")")")
meson setup /build/helper "$src/native/subprojects/atl-doctor/agent" -Djdk="$jdk" >/dev/null
ninja -C /build/helper atl-doctor-input >/dev/null && cp /build/helper/atl-doctor-input "$out"/
ls -l "$out"
