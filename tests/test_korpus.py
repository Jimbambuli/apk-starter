"""Cross-check against the local F-Droid corpus (only with APK_STARTER_KORPUS set)."""
import os
from pathlib import Path

import pytest

from apk_starter.apkinfo import read_apk_info
from apk_starter.errors import ApkError

KORPUS = os.environ.get("APK_STARTER_KORPUS")

# Known label differences: corpus name is the F-Droid name, the manifest label differs.
KNOWN_LABELS: dict[str, str] = {
    "chat.simplex.app": "SimpleX",
    "de.danoeh.antennapod": "AntennaPod",
    "org.fossify.musicplayer": "Music Player",
    "org.fossify.gallery": "Gallery",
    "org.fossify.notes": "Notes",
    "com.ichi2.anki": "AnkiDroid",
    "app.organicmaps": "Organic Maps",
    "org.fossify.calendar": "Calendar",
    "org.fossify.filemanager": "File Manager",
    "com.kunzisoft.keepass.libre": "KeePassDX",
    "com.beemdevelopment.aegis": "Aegis",
    "org.tasks": "Tasks",
    "org.secuso.privacyfriendlysudoku": "Sudoku",
    "org.sufficientlysecure.keychain": "OpenKeychain",
}


@pytest.mark.skipif(not KORPUS, reason="APK_STARTER_KORPUS not set")
def test_corpus_matches_apps_tsv():
    root = Path(KORPUS).expanduser()
    rows = [l.rstrip("\n").split("\t") for l in (root / "apps.tsv").read_text(encoding="utf-8").splitlines()[1:]]
    bad, label_diffs, seen = [], [], 0
    for r in rows:
        pkg, name, version, vcode, apk = r[0], r[1], r[3], r[4], r[8]
        f = root / apk
        if not f.is_file():
            continue
        seen += 1
        try:
            i = read_apk_info(f)
        except ApkError as e:
            bad.append(f"{pkg}: {e}")
            continue
        if (i.package, i.version_code, i.version_name) != (pkg, int(vcode), version):
            bad.append(f"{pkg}: {i.package} {i.version_code} {i.version_name} != {vcode} {version}")
        if i.label != name and KNOWN_LABELS.get(pkg) != i.label:
            label_diffs.append(f"{pkg}: label {i.label!r} != corpus {name!r}")
    assert seen > 0
    assert not bad
    assert not label_diffs
