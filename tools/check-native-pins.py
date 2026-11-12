#!/usr/bin/env python3
"""The native build (native/) must use exactly the sources of the Flatpak manifest, the one place where the
versions are set: every git source of the manifest needs a wrap in native/subprojects with the same URL and
commit and the other way round, and native/tool-jars.txt the same jar URLs and sha256 sums.

  tools/check-native-pins.py [REPO]      exit 0: equal, 1: a difference (printed)
"""
import configparser
import re
import sys
from pathlib import Path

MANIFEST = "io.github.Jimbambuli.ApkStarter.yml"


def manifest_sources(text: str) -> list[dict[str, str]]:
    """The `sources:` entries of the manifest (type, url, commit, sha256), without a YAML library: each entry starts
    with `- type:`, its keys follow on their own lines."""
    out: list[dict[str, str]] = []
    for line in text.splitlines():
        m = re.match(r"\s*-\s+type:\s*(\S+)\s*$", line)
        if m:
            out.append({"type": m.group(1)})
            continue
        m = re.match(r"\s+(url|commit|sha256):\s*(\S+)\s*$", line)
        if m and out:
            out[-1].setdefault(m.group(1), m.group(2))
    return out


def check(repo: Path) -> list[str]:
    sources = manifest_sources((repo / MANIFEST).read_text(encoding="utf-8"))
    git = {(s["url"], s["commit"]) for s in sources if s["type"] == "git"}
    jars = {(s["url"], s["sha256"]) for s in sources if s["type"] == "file" and s.get("url", "").endswith(".jar")}

    wraps = set()
    for w in sorted((repo / "native/subprojects").glob("*.wrap")):
        cp = configparser.ConfigParser(interpolation=None)
        cp.read(w, encoding="utf-8")
        wraps.add((cp["wrap-git"]["url"], cp["wrap-git"]["revision"]))
    listed = set()
    for line in (repo / "native/tool-jars.txt").read_text(encoding="utf-8").splitlines():
        if line.strip() and not line.startswith("#"):
            _name, url, sha = line.split()
            listed.add((url, sha))

    problems = [f"wrap not in the manifest: {u} {c}" for u, c in sorted(wraps - git)]
    problems += [f"manifest source without a wrap: {u} {c}" for u, c in sorted(git - wraps)]
    problems += [f"tool-jars.txt not in the manifest: {u} {s}" for u, s in sorted(listed - jars)]
    problems += [f"manifest jar not in tool-jars.txt: {u} {s}" for u, s in sorted(jars - listed)]
    return problems


def main() -> int:
    repo = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent
    problems = check(repo)
    for p in problems:
        print(f"check-native-pins: {p}", file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
