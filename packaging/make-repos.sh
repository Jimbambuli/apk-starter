#!/bin/sh
# make-repos.sh ASSETS SITE: signed package repositories for apt, dnf/zypper and pacman in SITE (the GitHub Pages tree,
# next to the Flatpak repository), from the release assets in ASSETS (names as build-all.sh writes them).
#   SITE/apt/   dists/{trixie,noble}/ (Release, InRelease, Release.gpg) + pool/{trixie,noble}/
#   SITE/rpm/   fedora/NN/ and tumbleweed/ (repodata/repomd.xml.asc, packages signed), apk-starter-{fedora,opensuse}.repo
#   SITE/arch/  x86_64/ (apk-starter.db + .sig, packages + .sig)
# Needs: apt-ftparchive (apt-utils), createrepo_c, rpmsign (rpm), repo-add (pacman-package-manager), gpg, zstd.
# Environment: GNUPGHOME and GPG_KEY (id of the signing key; it is only used, never read out or copied), KEEP (versions
# per distribution that stay in the tree, default 2; GitHub Pages: 1 GB per site, 100 MB per file), BASE (URL of
# SITE, default https://jimbambuli.github.io/apk-starter), BUMP=1 (only for tests: also add a repacked copy of each
# package with the version +1).
set -eu
assets=$(realpath "$1"); site=$2; mkdir -p "$site"; site=$(realpath "$site")
: "${GPG_KEY:?}" "${GNUPGHOME:?}"; export GNUPGHOME
keep=${KEEP:-2}; base=${BASE:-https://jimbambuli.github.io/apk-starter}
gpgs() { gpg --batch --yes --pinentry-mode loopback --passphrase '' -u "$GPG_KEY" "$@"; }
newest() { ls -1 "$@" 2>/dev/null | sort -V | tail -n "$keep"; }   # the KEEP newest of the file names given
prune() { ls -1 "$1"/$2 2>/dev/null | sort -V | head -n -"$keep" | xargs -r rm -f --; }
bump() { echo "$1" | awk -F. '{$NF=$NF+1} 1' OFS=.; }
w=$(mktemp -d); trap 'rm -rf --one-file-system "$w"' EXIT

# ---- apt: one suite per distribution, nothing shared
for pair in debian13:trixie ubuntu2404:noble; do
  d=${pair%%:*}; suite=${pair##*:}
  set -- "$assets"/*_"$d"_amd64.deb; [ -f "$1" ] || continue
  mkdir -p "$site/apt/pool/$suite" "$site/apt/dists/$suite/main/binary-amd64"; cp "$@" "$site/apt/pool/$suite/"
  if [ "${BUMP:-}" = 1 ]; then
    v=$(dpkg-deb -f "$1" Version); n=$(bump "$v"); dpkg-deb -R "$1" "$w/deb"
    sed -i "s/^Version: .*/Version: $n/" "$w/deb/DEBIAN/control"
    dpkg-deb -b "$w/deb" "$site/apt/pool/$suite/apk-starter_${n}_${d}_amd64.deb" >/dev/null; rm -rf "$w/deb"
  fi
  prune "$site/apt/pool/$suite" '*.deb'
  (cd "$site/apt"
   apt-ftparchive packages "pool/$suite" > "dists/$suite/main/binary-amd64/Packages"
   gzip -9nkf "dists/$suite/main/binary-amd64/Packages"
   apt-ftparchive -o "APT::FTPArchive::Release::Origin=APK Starter" -o "APT::FTPArchive::Release::Label=APK Starter" \
     -o "APT::FTPArchive::Release::Suite=$suite" -o "APT::FTPArchive::Release::Codename=$suite" \
     -o "APT::FTPArchive::Release::Architectures=amd64" -o "APT::FTPArchive::Release::Components=main" \
     release "dists/$suite" > "$w/Release"
   mv "$w/Release" "dists/$suite/Release"
   gpgs --clearsign -o "dists/$suite/InRelease" "dists/$suite/Release"
   gpgs -abs -o "dists/$suite/Release.gpg" "dists/$suite/Release")
done

# ---- rpm: Fedora (one directory per release; the package is built against that release's libraries) and Tumbleweed
for f in "$assets"/*.fc[0-9]*.x86_64.rpm "$assets"/*.opensuse-tumbleweed.x86_64.rpm; do
  [ -f "$f" ] || continue
  case $f in *.fc[0-9]*) n=${f##*.fc}; dir=fedora/${n%%.*};; *) dir=tumbleweed;; esac
  mkdir -p "$site/rpm/$dir"; cp "$f" "$site/rpm/$dir/"
  if [ "${BUMP:-}" = 1 ]; then
    v=$(rpm -qp --qf '%{VERSION}' "$f"); r=$(rpm -qp --qf '%{RELEASE}' "$f"); nv=$(bump "$v")
    mkdir -p "$w/rpm/tree" "$w/rpm/SPECS"; (cd "$w/rpm/tree" && rpm2cpio "$f" | cpio -idm 2>/dev/null)
    printf 'Name: apk-starter\nVersion: %s\nRelease: %s\nSummary: test repack\nLicense: GPL-3.0-or-later\nAutoReqProv: no\n%%description\nrepack\n%%install\ncp -a %s/. %%{buildroot}\n%%files\n/usr/lib/apk-starter\n/usr/bin/apk-starter\n/usr/share/applications/*\n/usr/share/icons/hicolor/scalable/apps/*\n/usr/share/metainfo/*\n/usr/share/licenses/apk-starter\n' \
      "$nv" "$r" "$w/rpm/tree" > "$w/rpm/SPECS/a.spec"
    rpmbuild -bb --define "_topdir $w/rpm" --define '_binary_payload w19.zstdio' "$w/rpm/SPECS/a.spec" >/dev/null
    cp "$w"/rpm/RPMS/*/*.rpm "$site/rpm/$dir/"; rm -rf "$w/rpm"
  fi
  prune "$site/rpm/$dir" '*.rpm'
  rpmsign --define "_gpg_name $GPG_KEY" --addsign "$site/rpm/$dir"/*.rpm >/dev/null
  createrepo_c -q --general-compress-type=gz "$site/rpm/$dir"
  gpgs --detach-sign --armor -o "$site/rpm/$dir/repodata/repomd.xml.asc" "$site/rpm/$dir/repodata/repomd.xml"
done
if [ -d "$site/rpm/fedora" ]; then
  cat > "$site/rpm/apk-starter-fedora.repo" <<EOM
[apk-starter]
name=APK Starter
baseurl=$base/rpm/fedora/\$releasever/
enabled=1
gpgcheck=1
repo_gpgcheck=1
gpgkey=$base/apk-starter.gpg
EOM
fi
if [ -d "$site/rpm/tumbleweed" ]; then
  cat > "$site/rpm/apk-starter-opensuse.repo" <<EOM
[apk-starter]
name=APK Starter
baseurl=$base/rpm/tumbleweed/
enabled=1
autorefresh=1
gpgcheck=1
repo_gpgcheck=1
gpgkey=$base/apk-starter.gpg
EOM
fi

# ---- Arch: a signed database and signed packages
set -- "$assets"/apk-starter-*-x86_64.pkg.tar.zst
if [ -f "$1" ]; then
  a=$site/arch/x86_64; mkdir -p "$a"; cp "$1" "$a/"
  if [ "${BUMP:-}" = 1 ]; then
    mkdir "$w/arch"; (cd "$w/arch" && tar -I zstd -xf "$1")
    pv=$(sed -n 's/^pkgver = //p' "$w/arch/.PKGINFO"); nv=$(bump "${pv%-*}")-${pv##*-}
    sed -i "s/^pkgver = .*/pkgver = $nv/" "$w/arch/.PKGINFO"
    (cd "$w/arch" && tar -I zstd -cf "$a/apk-starter-$nv-x86_64.pkg.tar.zst" .PKGINFO $(ls -A | grep -v '^.PKGINFO$')); rm -rf "$w/arch"
  fi
  prune "$a" '*.pkg.tar.zst'; rm -f "$a"/*.sig "$a"/apk-starter.db* "$a"/apk-starter.files*
  for p in "$a"/*.pkg.tar.zst; do gpgs --detach-sign -o "$p.sig" "$p"; done
  (cd "$a" && repo-add --sign --key "$GPG_KEY" apk-starter.db.tar.zst *.pkg.tar.zst >/dev/null 2>&1
   # Pages does not serve links: the names pacman asks for are real copies
   for x in apk-starter.db apk-starter.db.sig apk-starter.files apk-starter.files.sig; do
     [ -L "$x" ] && cp --remove-destination "$(readlink "$x")" "$x"; done)
fi
du -sh "$site"; find "$site" -type f -size +90M
