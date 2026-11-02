"""microG for every app, «microG settings», and profile folders of a test build beside stable."""
import os
from pathlib import Path

from apk_starter.cli import main
from apk_starter.paths import apply_profile
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


def test_setup_without_microg_is_usage_error(box):  # noqa: F811
    assert main(["--setup-microg"], env=box.env) == 2


def test_microg_settings_and_every_app_shares_microg(box, capsys):  # noqa: F811
    bundle = Path(box.env["APK_STARTER_PREFIX"]) / "share/apk-starter/microg"
    bundle.mkdir(parents=True)
    for pkg in (GMS, GSF):
        make_apk(bundle / f"{pkg}.apk", manifest=axml_manifest(package=pkg, label="microG Services"))
    assert main(["--setup-microg"], env=box.env) == 0
    assert box.calls == [("install", GMS, "microG settings", b"<svg/>")]
    assert "«Google device registration»" in capsys.readouterr().out
    assert main(["--install", str(box.apk)], env=box.env) == 0
    assert main([PKG], env=box.env) == 0 and main([GMS], env=box.env) == 0
    data = box.data / "data"
    assert os.readlink(box.data / "apps" / f"{GSF}.apk") == str(bundle / f"{GSF}.apk")    # ATL_INSTALLED_APKS_DIR
    assert (box.data / "apps" / f"{GMS}.apk").is_file()                                   # «microG settings»
    assert os.readlink(data / PKG / f"{GMS}.apk_") == str(data / GMS / f"{GMS}.apk_")
    assert os.readlink(data / GMS / f"{GSF}.apk_") == str(data / GSF / f"{GSF}.apk_")
    assert not (data / GMS / f"{GMS}.apk_").is_symlink()      # microG's own folder is the shared one
    assert main(["--remove", PKG, "--purge"], env=box.env) == 0
    assert (data / GMS / f"{GMS}.apk_").is_dir()               # the app's links go, microG's data stays


def test_test_build_makes_no_menu_entries(box, monkeypatch):  # noqa: F811
    """The portal's entries would start stable (no --branch): a test build's apps start from its window."""
    from apk_starter import cli
    monkeypatch.setattr(cli, "MENU_ENTRIES", False)
    assert main(["--install", str(box.apk)], env=box.env) == 0
    assert main(["--remove", PKG], env=box.env) == 0
    assert box.calls == []
