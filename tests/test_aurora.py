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
