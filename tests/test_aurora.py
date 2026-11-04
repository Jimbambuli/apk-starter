"""«Set up Aurora Store»: download from F-Droid (faked), signature and version checks, install."""
import json
import os
from pathlib import Path

import pytest

from apk_starter import aurora
from apk_starter.cli import main
from apk_starter.errors import StarterError
from test_apksig import AURORA_SIGNER, SIGNED
from test_cli import box  # noqa: F401  (fixture)

needs_apk = pytest.mark.skipif(not os.path.isfile(SIGNED), reason="no Aurora Store APK to serve")


def fetcher(code=76, apk=SIGNED, served=None):
    def fetch(url, limit, sink):
        (served if served is not None else []).append(url)
        if url == aurora.API:
            sink(json.dumps({"packageName": aurora.PACKAGE, "suggestedVersionCode": code}).encode())
        elif url == aurora.APK_URL.format(package=aurora.PACKAGE, code=code):
            data = Path(apk).read_bytes()
            if len(data) > limit:
                raise StarterError("too large")
            sink(data)
        else:
            raise AssertionError(url)
    return fetch


def test_pinned_signer_is_auroras():
    assert aurora.SIGNER == AURORA_SIGNER


@needs_apk
def test_setup_downloads_checks_and_installs(box, monkeypatch):
    served = []
    monkeypatch.setattr(aurora, "_get", fetcher(served=served))
    assert main(["--setup-aurora"], env=box.env) == 0
    assert served == [aurora.API, "https://f-droid.org/repo/com.aurora.store_76.apk"]
    assert (box.data / "apps" / "com.aurora.store.apk").is_file()
    assert box.calls[-1][:3] == ("install", "com.aurora.store", "Aurora Store")


@needs_apk
def test_other_signer_is_refused(tmp_path):
    with pytest.raises(StarterError, match="not signed by Aurora OSS"):
        aurora.download(tmp_path / "a.apk", fetcher(), signer="00" * 32)


@needs_apk
def test_other_version_than_announced_is_refused(tmp_path):
    def fetch(url, limit, sink):
        if url == aurora.API:
            sink(b'{"suggestedVersionCode": 77}')
        else:
            sink(Path(SIGNED).read_bytes())
    with pytest.raises(StarterError, match="not Aurora Store 77"):
        aurora.download(tmp_path / "a.apk", fetch)


def test_changed_file_is_refused(tmp_path):
    bad = tmp_path / "bad.apk"
    bad.write_bytes(b"PK\x05\x06" + bytes(18))
    with pytest.raises(StarterError):
        aurora.download(tmp_path / "a.apk", fetcher(apk=bad))


@pytest.mark.parametrize("answer", [b"{}", b"no json", b'{"suggestedVersionCode": "76"}'])
def test_no_version_from_fdroid(answer):
    with pytest.raises(StarterError, match="no version"):
        aurora.latest_version(lambda url, limit, sink: sink(answer))


# -- updates Aurora knows (Spec 18 Phase 3): its own update list, against the installed versions -----------------
import json as _json  # noqa: E402
import sqlite3  # noqa: E402

from apk_starter.paths import Paths as _Paths  # noqa: E402


def _aurora_db(p: _Paths, updates, ignored=()):
    db = p.data(aurora.PACKAGE) / f"{aurora.PACKAGE}.apk_" / "databases" / "aurora_database"
    db.parent.mkdir(parents=True)
    c = sqlite3.connect(db)
    c.execute("CREATE TABLE `update` (`packageName` TEXT NOT NULL, `versionCode` INTEGER NOT NULL, "
              "`versionName` TEXT NOT NULL, `displayName` TEXT NOT NULL)")
    c.execute("CREATE TABLE `ignored_update` (`packageName` TEXT NOT NULL, `ignoredVersionCode` INTEGER)")
    c.executemany("INSERT INTO `update` VALUES (?, ?, ?, ?)", updates)
    c.executemany("INSERT INTO `ignored_update` VALUES (?, ?)", ignored)
    c.commit()
    c.close()


def _installed(p: _Paths, pkg, code, label):
    p.apps_dir().mkdir(parents=True, exist_ok=True)
    p.meta(pkg).write_text(_json.dumps({"package": pkg, "label": label, "version_code": code}))


def test_updates_are_what_aurora_found_newer_than_installed(tmp_path):
    p = _Paths(tmp_path / "d", tmp_path / "c", tmp_path / "p")
    for pkg, code, label in (("com.whatsapp", 10, "WhatsApp"), ("org.mozilla.firefox", 5, "Firefox"),
                             ("org.done", 9, "Done"), ("org.skip", 1, "Skip"), (aurora.PACKAGE, 76, "Aurora")):
        _installed(p, pkg, code, label)
    _aurora_db(p, [("com.whatsapp", 11, "2.26", "WhatsApp"), ("org.mozilla.firefox", 6, "150", "Firefox"),
                   ("org.done", 9, "1", "Done"), ("org.skip", 2, "2", "Skip"), ("org.gone", 3, "3", "Gone")],
               ignored=[("org.skip", 2)])
    assert aurora.pending_updates(p) == [("org.mozilla.firefox", "Firefox"), ("com.whatsapp", "WhatsApp")]


def test_no_aurora_data_no_updates(tmp_path):
    p = _Paths(tmp_path / "d", tmp_path / "c", tmp_path / "p")
    assert aurora.pending_updates(p) == []


def test_note_only_when_the_updates_changed(tmp_path):
    p = _Paths(tmp_path / "d", tmp_path / "c", tmp_path / "p")
    ups = [("org.mozilla.firefox", "Firefox"), ("com.whatsapp", "WhatsApp")]
    assert aurora.updates_note(p, ups) == ("Updates: 2", "Firefox, WhatsApp")
    assert aurora.updates_note(p, ups) is None                  # told already
    assert aurora.updates_note(p, ups[:1]) == ("Updates: 1", "Firefox")
    assert aurora.updates_note(p, []) is None
