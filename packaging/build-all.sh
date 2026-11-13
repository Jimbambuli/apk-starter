#!/bin/sh
# build-all.sh [DISTRO...]: the release command. Builds the packages of APK Starter, one container per
# distribution on a Proxmox node, and collects them in OUT (default packaging/out):
#   debian13 (.deb)  fedora (.rpm)  opensuse (.rpm, Tumbleweed)  arch (.pkg.tar.zst)
# Per distribution: new container, ct-build.sh (dependencies from the distribution, build without network, lint),
# the package into OUT, container destroyed; with TEST=1 also a second, fresh container that installs the package,
# starts an app headless and removes it again (ct-test.sh; needs APK=, a test .apk).
# Environment: NODE (default root@pve2), JUMP (ssh jump host, default root@pve1), OUT, BUNDLE (a ready bundle),
# FLATPAK_HOST/FLATPAK_REPO: where the Flatpak build's repository is (adds the .flatpak bundles); no upload here.
# ASSEMBLE_ONLY=1: no builds, only the release folder from what is in OUT/<distro>. KEEP=1 keeps the containers, CTBASE (first container id, default 180).
set -eu
here=$(cd "$(dirname "$0")" && pwd)
NODE=${NODE:-root@pve2}; JUMP=${JUMP:-root@pve1}; OUT=${OUT:-$here/out}; CTBASE=${CTBASE:-180}
mkdir -p "$OUT"
sshn() { ssh -o BatchMode=yes -o ServerAliveInterval=20 -o ServerAliveCountMax=45 -J "$JUMP" "$NODE" "$@"; }
scpn() { scp -q -o BatchMode=yes -J "$JUMP" "$@"; }
all="debian13 fedora opensuse arch"; want=${*:-$all}
info() {  # distro -> template ostype format
  case $1 in
  debian13) echo "debian-13-standard_13.6-1_amd64.tar.zst debian deb";;
  ubuntu2404) echo "ubuntu-24.04-standard_24.04-2_amd64.tar.zst ubuntu deb";;
  fedora) echo "fedora-44-default_20260713_amd64.tar.xz fedora rpm";;
  opensuse) echo "opensuse-tumbleweed-20261113_amd64.tar.xz opensuse rpm";;
  arch) echo "archlinux-base_20260420-1_amd64.tar.zst archlinux arch";;
  *) echo "unknown distribution $1" >&2; exit 1;;
  esac
}
bundle=${BUNDLE:-}
[ -n "$bundle" ] || [ -n "${ASSEMBLE_ONLY:-}" ] || { "$here/collect-sources.sh" "$OUT/bundle" >&2; bundle=$(ls "$OUT"/bundle/apk-starter-*.tar.xz); }
n=$CTBASE
for d in $([ -n "${ASSEMBLE_ONLY:-}" ] || echo $want); do
  set -- $(info "$d"); tpl=$1; ostype=$2; fmt=$3
  id=$n; n=$((n + 1)); dir=/var/lib/vz/pakete/$d
  echo "== $d: container $id"
  sshn "rm -rf $dir; mkdir -p $dir/in $dir/out && chown -R 100000:100000 $dir"
  scpn "$bundle" "$NODE:$dir/in/"
  scpn "$here/ct-build.sh" "$NODE:$dir/in/"
  ct() { sshn "pct create $1 local:vztmpl/$tpl --hostname pk-$d --unprivileged 1 --features nesting=1 --cores 6 \
      --memory 12288 --swap 0 --rootfs local-lvm-thin:8 --mp0 $dir,mp=/build --net0 name=eth0,bridge=LAN,ip=dhcp \
      --nameserver 172.22.22.1 --dev0 /dev/dri/renderD128,mode=0666 --timezone UTC --onboot 0 --ostype $ostype \
      --description 'TEMPORAER: Paketbau $d (packaging/build-all.sh)' >/dev/null && pct start $1 \
      && for i in \$(seq 30); do pct exec $1 -- getent hosts deb.debian.org >/dev/null 2>&1 && break; sleep 2; done"; }
  ct $id
  sshn "pct exec $id -- sh /build/in/ct-build.sh $fmt /build/in/$(basename "$bundle") /build/out" 2>&1 | tail -n 40
  [ "${KEEP:-}" = 1 ] || sshn "pct stop $id; pct destroy $id --purge >/dev/null"
  mkdir -p "$OUT/$d"; scpn "$NODE:$dir/out/*" "$OUT/$d/"
  if [ "${TEST:-}" = 1 ]; then
    tid=$n; n=$((n + 1)); echo "== $d: test in fresh container $tid"
    pk=$(ls "$OUT/$d"/*.deb "$OUT/$d"/*.rpm "$OUT/$d"/*.pkg.tar.zst 2>/dev/null | head -1)
    sshn "mkdir -p $dir/t && chown 100000:100000 $dir/t"
    scpn "$pk" "$here/ct-test.sh" "$here/test/files.steps" "$APK" "$OUT/$d/atl-doctor-input" "$NODE:$dir/t/"
    ct $tid
    sshn "pct exec $tid -- sh /build/t/ct-test.sh /build/t/$(basename "$pk") /build/t/files.steps /build/t/$(basename "$APK") \
      /build/t/atl-doctor-input" 2>&1 | tee "$OUT/$d/test.log" | tail -n 30
    [ "${KEEP:-}" = 1 ] || sshn "pct stop $tid; pct destroy $tid --purge >/dev/null"
  fi
  sshn "rm -rf --one-file-system $dir"
done

# The assets of the GitHub release: one folder, names that tell the distributions apart, SHA256SUMS.
version=$(sed -n 's/^VERSION = "\(.*\)"$/\1/p' "$here/../src/apk_starter/cli.py")
rel=$OUT/release; rm -rf "$rel"; mkdir -p "$rel"
for d in $want; do
  for f in "$OUT/$d"/*.deb "$OUT/$d"/*.rpm "$OUT/$d"/*.pkg.tar.zst; do
    [ -f "$f" ] || continue
    b=$(basename "$f")
    case $d in
    debian13|ubuntu2404) b=apk-starter_${version}_${d}_amd64.deb;;           # same file name from both builds
    opensuse) b=$(echo "$b" | sed 's/\(-[0-9]*\)\.x86_64/\1.opensuse-tumbleweed.x86_64/');;
    esac                                                                      # fedora: .fc44 from %{?dist}; arch: unique
    cp "$f" "$rel/$b"
  done
done
# the source bundle (what the AUR PKGBUILD downloads, same sha256) and the finished AUR files
cp "$OUT/bundle/apk-starter-$version.tar.xz" "$rel/" 2>/dev/null || true
for f in PKGBUILD .SRCINFO; do cp "$OUT/arch/$f" "$rel/$f" 2>/dev/null || true; done
# Flatpak bundle (the existing way: flatpak build-bundle from the repository of the Flatpak build, runtime from
# Flathub): FLATPAK_REPO=PATH on FLATPAK_HOST (ssh), e.g. claude@172.22.22.61 and ~/bau/r088/repo.
if [ -n "${FLATPAK_REPO:-}" ]; then
  app=io.github.Jimbambuli.ApkStarter; fr=https://dl.flathub.org/repo/flathub.flatpakrepo
  ssh -o BatchMode=yes "$FLATPAK_HOST" "cd /tmp && rm -f ApkStarter-$version.flatpak ApkStarter-$version.Locale.flatpak &&
    flatpak build-bundle $FLATPAK_REPO ApkStarter-$version.flatpak $app stable --runtime-repo=$fr &&
    flatpak build-bundle --runtime $FLATPAK_REPO ApkStarter-$version.Locale.flatpak $app.Locale stable &&
    tar -C /tmp -cf - ApkStarter-$version.flatpak ApkStarter-$version.Locale.flatpak &&
    rm -f /tmp/ApkStarter-$version.flatpak /tmp/ApkStarter-$version.Locale.flatpak" | tar -x -C "$rel"
fi
(cd "$rel" && rm -f SHA256SUMS && sha256sum -- * > SHA256SUMS && cat SHA256SUMS)
echo "release assets in $rel; upload (not done here): gh release create v$version --repo Jimbambuli/apk-starter $rel/*"
