#!/bin/sh
# collect-sources.sh OUTDIR: the offline source bundle OUTDIR/apk-starter-VERSION.tar.xz = the starter (git archive of
# HEAD) + every component at the commit of native/subprojects/*.wrap (with the wraps' patch directories applied) +
# the tool jars of native/tool-jars.txt. The only step that needs the network (or the wraps' file:// repositories);
# the package builds from the bundle need none. VERSION is read from src/apk_starter/cli.py, the one place.
set -eu
out=$(realpath "$1"); repo=$(cd "$(dirname "$0")/.." && pwd)
version=$(sed -n 's/^VERSION = "\(.*\)"$/\1/p' "$repo/src/apk_starter/cli.py")
[ -n "$version" ] || { echo "no VERSION in cli.py" >&2; exit 1; }
work=$(mktemp -d "${TMPDIR:-/tmp}/apk-starter-bundle.XXXXXX"); top="$work/apk-starter-$version"
mkdir -p "$top" "$out"
git -C "$repo" archive HEAD | tar -x -C "$top"
# the packaging of the working tree, so that uncommitted packaging files can be tried
cp -a "$repo/packaging/." "$top/packaging/"
meson subprojects download --sourcedir "$top/native"
for w in "$top"/native/subprojects/*.wrap; do
  d=$(sed -n 's/^directory *= *//p' "$w"); rev=$(sed -n 's/^revision *= *//p' "$w")
  [ "$(git -C "$top/native/subprojects/$d" rev-parse HEAD)" = "$rev" ] || { echo "$d is not at $rev" >&2; exit 1; }
  echo "$rev" > "$top/native/subprojects/$d/.pinned-revision"
  rm -rf "$top/native/subprojects/$d/.git"
done
python3 "$top/native/build.py" fetch-jars --source "$top/native" --dest "$top/native/jars"
git -C "$repo" log -1 --format=%ct > "$top/.source-date-epoch"
tar -C "$work" --sort=name --owner=0 --group=0 --numeric-owner -cJf "$out/apk-starter-$version.tar.xz" "apk-starter-$version"
rm -rf --one-file-system "$work"
ls -l "$out/apk-starter-$version.tar.xz"
