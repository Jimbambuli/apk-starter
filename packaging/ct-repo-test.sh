#!/bin/sh
# ct-repo-test.sh PHASE BASE (as root, in a fresh container): PHASE install = set the repository up as a user would
# (key + .list/.repo/pacman.conf from BASE) and install apk-starter from it; PHASE upgrade = the distribution's own
# upgrade command (apt upgrade, dnf upgrade, zypper up, pacman -Syu); both print the installed version.
set -eu
phase=$1; base=$2; . /etc/os-release
key=/tmp/apk-starter.gpg
case $ID in
debian|ubuntu)
  export DEBIAN_FRONTEND=noninteractive; installed() { dpkg-query -W -f '${Version}\n' apk-starter; }
  if [ "$phase" = install ]; then
    apt-get update -qq >/dev/null; apt-get install -y -qq --no-install-recommends curl ca-certificates >/dev/null
    install -d /etc/apt/keyrings; curl -fsS "$base/apk-starter.gpg" -o /etc/apt/keyrings/apk-starter.gpg
    echo "deb [signed-by=/etc/apt/keyrings/apk-starter.gpg] $base/apt $VERSION_CODENAME main" > /etc/apt/sources.list.d/apk-starter.list
    apt-get update -qq >/dev/null; apt-get install -y -qq apk-starter >/dev/null
  else apt-get update -qq >/dev/null; apt-get upgrade -y -qq >/dev/null; fi ;;
fedora)
  installed() { rpm -q --qf '%{VERSION}-%{RELEASE}\n' apk-starter; }
  if [ "$phase" = install ]; then
    curl -fsS "$base/rpm/apk-starter-fedora.repo" -o /etc/yum.repos.d/apk-starter.repo
    dnf -q -y install apk-starter 2>&1 | tail -n 4
  else dnf -q -y upgrade --refresh 2>&1 | tail -n 4; fi ;;
opensuse*)
  installed() { rpm -q --qf '%{VERSION}-%{RELEASE}\n' apk-starter; }
  if [ "$phase" = install ]; then
    curl -fsS "$base/rpm/apk-starter-opensuse.repo" -o /etc/zypp/repos.d/apk-starter.repo
    zypper -q --non-interactive --gpg-auto-import-keys refresh >/dev/null 2>&1
    zypper -q --non-interactive install --no-recommends apk-starter >/dev/null
  else zypper -q --non-interactive refresh >/dev/null 2>&1; zypper -q --non-interactive up >/dev/null; fi ;;
arch)
  installed() { pacman -Q apk-starter; }
  if [ "$phase" = install ]; then
    curl -fsS "$base/apk-starter.gpg" -o $key
    pacman-key --init >/dev/null 2>&1; pacman-key --populate archlinux >/dev/null 2>&1
    pacman -Syu --noconfirm --disable-sandbox >/dev/null 2>&1
    pacman-key --add $key; pacman-key --lsign-key "$(gpg --show-keys --with-colons $key | awk -F: '/^fpr/{print $10; exit}')" 2>&1 | tail -n 1
    printf '\n[apk-starter]\nSigLevel = Required\nServer = %s/arch/$arch\n' "$base" >> /etc/pacman.conf
    pacman -Syu --noconfirm --disable-sandbox >/dev/null 2>&1; pacman -S --noconfirm --disable-sandbox apk-starter 2>&1 | tail -n 3
  else pacman -Syu --noconfirm --disable-sandbox 2>&1 | tail -n 3; fi ;;
esac
echo "$ID $phase: installed apk-starter $(installed); $(/usr/bin/apk-starter --version 2>&1)"
