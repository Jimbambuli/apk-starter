#!/bin/sh
# private-sonames.sh DIR: a regex for rpm's %__requires_exclude: the dependencies (soname + "(") on the libraries that
# lie in the private tree DIR itself, which the package provides but is not to advertise (__provides_exclude_from).
printf '^('
find "$1" \( -type f -o -type l \) -name '*.so*' -printf '%f\n' | sort -u | sed 's/[.+]/\\&/g' | paste -sd'|' | tr -d '\n'
printf ')\\('
