#!/bin/sh
# test-repos.sh [DISTRO...]: the repositories as a user sees them, with a throwaway signing key (the release key is
# never used here). Builds the trees with make-repos.sh in a Debian 13 container from OUT/release (v1; v2 = the same
# plus a repacked copy at version +1), serves them over http from this machine, then in a FRESH container per
# distribution: set the repository up, install, swap the served tree to v2, run the upgrade command.
# Environment as build-all.sh (NODE, JUMP, OUT, CTBASE) plus SERVE (this machine's address, default: first one).
set -eu
here=$(cd "$(dirname "$0")" && pwd)
NODE=${NODE:-root@pve2}; JUMP=${JUMP:-root@pve1}; OUT=${OUT:-$here/out}; CTBASE=${CTBASE:-190}
SERVE=${SERVE:-$(hostname -I | cut -d' ' -f1)}; PORT=8099; BASE=http://$SERVE:$PORT
sshn() { ssh -o BatchMode=yes -J "$JUMP" "$NODE" "$@"; }
scpn() { scp -q -o BatchMode=yes -J "$JUMP" "$@"; }
want=${*:-debian13 ubuntu2404 fedora opensuse arch}
t=$OUT/repo-test; rm -rf "$t"; mkdir -p "$t/cur"
mkct() {  # id template ostype dir
  sshn "pct create $1 local:vztmpl/$2 --hostname pk-$1 --unprivileged 1 --features nesting=1 --cores 4 --memory 8192 \
    --swap 0 --rootfs local-lvm-thin:8 --mp0 $4,mp=/build --net0 name=eth0,bridge=LAN,ip=dhcp --nameserver 172.22.22.1 \
    --timezone UTC --onboot 0 --ostype $3 --description 'TEMPORAER: Paketquellen-Test (packaging/test-repos.sh)' >/dev/null \
    && pct start $1 && for i in \$(seq 30); do pct exec $1 -- getent hosts deb.debian.org >/dev/null 2>&1 && break; sleep 2; done"
}
# 1. the trees (a Debian 13 container with the tools)
dir=/var/lib/vz/pakete-repo; id=$CTBASE
sshn "rm -rf $dir; mkdir -p $dir/assets && chown -R 100000:100000 $dir"
scpn "$OUT"/release/* "$here/make-repos.sh" "$NODE:$dir/assets/"
mkct $id debian-13-standard_13.6-1_amd64.tar.zst debian $dir
sshn "pct exec $id -- sh -c 'export DEBIAN_FRONTEND=noninteractive; apt-get update -qq; apt-get install -y -qq apt-utils createrepo-c rpm cpio gnupg pacman-package-manager libarchive-tools zstd >/dev/null 2>&1
  export GNUPGHOME=/build/gnupg; mkdir -m 700 \$GNUPGHOME; gpg --batch --passphrase \"\" --quick-generate-key \"APK Starter test key\" rsa3072 sign never 2>/dev/null
  export GPG_KEY=\$(gpg --list-keys --with-colons | awk -F: \"/^fpr/{print \\\$10; exit}\") BASE=$BASE
  gpg --export \$GPG_KEY > /build/apk-starter.gpg
  sh /build/assets/make-repos.sh /build/assets /build/site1 && cp -a /build/site1 /build/site2
  BUMP=1 sh /build/assets/make-repos.sh /build/assets /build/site2'" 2>&1 | tail -n 6
mkdir -p "$t/v1" "$t/v2"; scpn -r "$NODE:$dir/site1/." "$t/v1/"; scpn -r "$NODE:$dir/site2/." "$t/v2/"; scpn "$NODE:$dir/apk-starter.gpg" "$t/"
cp "$t/apk-starter.gpg" "$t/v1/"; cp "$t/apk-starter.gpg" "$t/v2/"
sshn "pct stop $id; pct destroy $id --purge >/dev/null; rm -rf --one-file-system $dir"
# 2. serve v1, then per distribution: install, swap to v2, upgrade
cp -a "$t/v1/." "$t/cur/"
python3 -m http.server $PORT --directory "$t/cur" > "$t/http.log" 2>&1 & srv=$!
trap 'kill $srv 2>/dev/null' EXIT
n=$((CTBASE + 1))
for d in $want; do
  case $d in
  debian13) tpl="debian-13-standard_13.6-1_amd64.tar.zst debian";;
  ubuntu2404) tpl="ubuntu-24.04-standard_24.04-2_amd64.tar.zst ubuntu";;
  fedora) tpl="fedora-44-default_20260713_amd64.tar.xz fedora";;
  opensuse) tpl="opensuse-tumbleweed-20261113_amd64.tar.xz opensuse";;
  arch) tpl="archlinux-base_20260420-1_amd64.tar.zst archlinux";;
  esac
  id=$n; n=$((n + 1)); dir=/var/lib/vz/pakete-repo-$d
  sshn "rm -rf $dir; mkdir -p $dir && chown 100000:100000 $dir"; scpn "$here/ct-repo-test.sh" "$NODE:$dir/"
  rm -rf "$t/cur"/* "$t/cur"/.[!.]* 2>/dev/null; cp -a "$t/v1/." "$t/cur/"
  echo "== $d (fresh container $id)"
  mkct $id $tpl $dir
  sshn "pct exec $id -- sh /build/ct-repo-test.sh install $BASE" 2>&1 | tail -n 6
  rm -rf "$t/cur"/*; cp -a "$t/v2/." "$t/cur/"
  sshn "pct exec $id -- sh /build/ct-repo-test.sh upgrade $BASE" 2>&1 | tail -n 6
  sshn "pct stop $id; pct destroy $id --purge >/dev/null; rm -rf --one-file-system $dir"
done
