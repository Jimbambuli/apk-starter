"""microG for every app, «microG settings», and profile folders of a test build beside stable."""
import hashlib
import json
import os

import pytest

from apk_starter import apksig, microg
from apk_starter.cli import main
from apk_starter.errors import StarterError
from apk_starter.paths import Paths, apply_profile
from apkgen import axml_manifest, make_apk
from test_cli import PKG, box  # noqa: F401  (fixture)

GMS, GSF = "com.google.android.gms", "com.google.android.gsf"


def test_profile_folders_beside_stable_once():
    env = {"HOME": "/h", "XDG_DATA_HOME": "/h/.var/app/X/data", "APK_STARTER_PROFILE": "microg"}
    apply_profile(env)
    assert (env["XDG_DATA_HOME"], env["XDG_CACHE_HOME"]) == ("/h/.var/app/X/microg/data", "/h/.var/app/X/microg/cache")
    apply_profile(env)                              # a child started by the window: unchanged
    assert env["XDG_DATA_HOME"] == "/h/.var/app/X/microg/data"
    plain = {"HOME": "/h", "APK_STARTER_PROFILE": "../x"}
    apply_profile(plain)
    assert "XDG_DATA_HOME" not in plain


def repo(tmp_path, monkeypatch, gms_hash=None, served=None, signer=None):
    """A fake microG repository: index and two APKs; the v2 check is faked (the real one: test_apksig)."""
    files = {}
    for pkg, code in ((GMS, 252432034), (GSF, 8)):
        f = tmp_path / f"{pkg}-{code}.apk"
        make_apk(f, manifest=axml_manifest(package=pkg, label="microG Services", version_code=code))
        files[pkg] = (f, code)
    index = {"packages": {pkg: [{"apkName": f.name, "hash": gms_hash or hashlib.sha256(f.read_bytes()).hexdigest()
                                 if pkg == GMS else hashlib.sha256(f.read_bytes()).hexdigest(),
                                 "hashType": "sha256", "versionCode": code},
                                {"apkName": "old.apk", "hash": "0" * 64, "hashType": "sha256", "versionCode": 1}]
                         for pkg, (f, code) in files.items()}}
    monkeypatch.setitem(microg.PINNED_SHA256, GSF, hashlib.sha256(files[GSF][0].read_bytes()).hexdigest())
    monkeypatch.setattr(microg.apksig, "verify", lambda _p: apksig.Signer(signer or microg.SIGNER))

    def fetch(url, limit, sink):
        (served if served is not None else []).append(url)
        if url == microg.INDEX:
            sink(json.dumps(index).encode())
            return
        for f, _code in files.values():
            if url == microg.APK_URL.format(apk=f.name):
                sink(f.read_bytes())
                return
        raise AssertionError(url)
    monkeypatch.setattr(microg, "_get", fetch)
    return files


def test_setup_downloads_checks_and_installs_microg(box, tmp_path, monkeypatch, capsys):  # noqa: F811
    served = []
    repo(tmp_path, monkeypatch, served=served)
    assert not microg.installed(Paths.from_env(box.env))
    assert main(["--setup-microg"], env=box.env) == 0
    assert served == [microg.INDEX, "https://microg.org/fdroid/repo/com.google.android.gsf-8.apk",
                      microg.INDEX, "https://microg.org/fdroid/repo/com.google.android.gms-252432034.apk"]
    assert box.calls == [("install", GMS, "microG settings", b"<svg/>")]      # GsfProxy: no menu entry
    assert (box.data / "apps" / f"{GSF}.apk").is_file() and not (box.data / "apps" / f"{GSF}.json").exists()
    assert microg.installed(Paths.from_env(box.env))
    served.clear()
    assert main(["--setup-microg"], env=box.env) == 0 and served == []     # nothing is missing: nothing is fetched


def test_wrong_checksum_or_signer_is_refused(tmp_path, monkeypatch):
    repo(tmp_path, monkeypatch, gms_hash="1" * 64)
    with pytest.raises(StarterError, match="checksum"):
        microg.download(GMS, tmp_path / "x.apk")
    repo(tmp_path, monkeypatch, signer="00" * 32)
    with pytest.raises(StarterError, match="not signed by the NOGAPPS Project"):
        microg.download(GMS, tmp_path / "x.apk")


def test_gsf_must_match_its_pinned_checksum(tmp_path, monkeypatch):
    repo(tmp_path, monkeypatch)
    monkeypatch.setitem(microg.PINNED_SHA256, GSF, "2" * 64)      # the index and the file agree, the pin does not
    with pytest.raises(StarterError, match="checksum"):
        microg.download(GSF, tmp_path / "x.apk")


def test_pinned_signer_is_the_nogapps_certificate():
    assert microg.SIGNER == "9bd06727e62796c0130eb6dab39b73157451582cbd138e86c468acc395d14165"


def test_every_app_shares_microg(box, tmp_path, monkeypatch):  # noqa: F811
    repo(tmp_path, monkeypatch)
    assert main(["--setup-microg"], env=box.env) == 0
    assert main(["--install", str(box.apk)], env=box.env) == 0
    assert main([PKG], env=box.env) == 0 and main([GMS], env=box.env) == 0
    data = box.data / "data"
    assert (box.data / "apps" / f"{GMS}.apk").is_file()                                   # «microG settings»
    assert os.readlink(data / PKG / f"{GMS}.apk_") == str(data / GMS / f"{GMS}.apk_")
    assert os.readlink(data / GMS / f"{GSF}.apk_") == str(data / GSF / f"{GSF}.apk_")
    assert not (data / GMS / f"{GMS}.apk_").is_symlink()      # microG's own folder is the shared one
    assert main(["--remove", PKG, "--purge"], env=box.env) == 0
    assert (data / GMS / f"{GMS}.apk_").is_dir()               # the app's links go, microG's data stays


def test_no_microg_installed_no_links(box):  # noqa: F811
    assert main(["--install", str(box.apk)], env=box.env) == 0 and main([PKG], env=box.env) == 0
    assert not list((box.data / "data" / PKG).glob("*.apk_"))


def test_test_build_makes_no_menu_entries(box, monkeypatch):  # noqa: F811
    """The portal's entries would start stable (no --branch): a test build's apps start from its window."""
    from apk_starter import cli
    monkeypatch.setattr(cli, "MENU_ENTRIES", False)
    assert main(["--install", str(box.apk)], env=box.env) == 0
    assert main(["--remove", PKG], env=box.env) == 0
    assert box.calls == []


def test_setup_without_portal_still_installs_both(box, tmp_path, monkeypatch, capsys):  # noqa: F811
    """No launcher portal (or the entry refused): GmsCore is installed without a menu entry, GsfProxy all the same."""
    repo(tmp_path, monkeypatch)

    def boom(*a, **kw):
        raise box.fake.PortalUnavailable("x")
    box.fake.install_launcher = boom
    assert main(["--setup-microg"], env=box.env) == 1
    assert microg.installed(Paths.from_env(box.env))
    assert f"flatpak run io.github.Jimbambuli.ApkStarter {GMS}" in capsys.readouterr().err
