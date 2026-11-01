from pathlib import Path

import pytest

from apk_starter.apkinfo import ApkInfo
from apk_starter.errors import UsageError
from apk_starter.launch import (build_command, build_env, parse_size, prepare_apk, remember_size,
                                remembered_size, sdk_for)
from apk_starter.paths import Paths

P = Paths(Path("/nonexistent"), Path("/nonexistent"), Path("/app"))


def info(path=Path("/x/a.apk"), target_sdk=34):
    return ApkInfo(path, "org.example.demo", "Demo", "1.2", 7, 21, target_sdk, None)


def test_sdk_clamped_and_override():
    assert sdk_for(info(target_sdk=35), None) == 34
    assert sdk_for(info(target_sdk=28), None) == 28
    assert sdk_for(info(target_sdk=None), None) is None
    assert sdk_for(info(target_sdk=35), 35) == 35


@pytest.mark.parametrize("bad", ["1200", "0x0", "12x10", "1200x850x1", "abc"])
def test_parse_size_rejects(bad):
    with pytest.raises(UsageError):
        parse_size(bad)


def test_size_is_remembered(tmp_path):
    p = Paths(tmp_path / "d", tmp_path / "c", tmp_path / "p")
    assert remembered_size(p, "org.example.demo") is None
    remember_size(p, "org.example.demo", (800, 600))
    assert remembered_size(p, "org.example.demo") == (800, 600)
    (p.data("org.example.demo") / "apk-starter.json").write_text("{kaputt")
    assert remembered_size(p, "org.example.demo") is None


def test_same_package_same_data_dir_regardless_of_filename(tmp_path):
    p = Paths(tmp_path / "d", tmp_path / "c", tmp_path / "p")
    a = prepare_apk(p, info(path=tmp_path / "app-v1.apk"))
    b = prepare_apk(p, info(path=tmp_path / "x.apk"))
    assert a == b == p.launch_dir() / "org.example.demo.apk" and b.resolve() == (tmp_path / "x.apk").resolve()


def test_command_and_env(tmp_path):
    p = Paths(tmp_path / "d", tmp_path / "c", Path("/app"))
    cmd = build_command(p, Path("/a/org.example.demo.apk"), size=(1200, 850), sdk=34, atl_args=["-X", "-verbose:jni"])
    assert cmd == ["android-translation-layer", "/a/org.example.demo.apk", "-w", "1200", "-h", "850", "--sdk-int", "34",
                   "-X", f"-Djavax.net.ssl.trustStore={p.prefix}/share/apk-starter/cacerts", "-X", "-verbose:jni"]
    env = build_env(p, "org.example.demo", {"LD_LIBRARY_PATH": "/x", "LANG": "de_CH.UTF-8"})
    assert env["LD_LIBRARY_PATH"] == f"{p.prefix}/lib/art:/x" and env["LANG"] == "de_CH.UTF-8"
    assert env["ANDROID_APP_DATA_DIR"] == str(p.data("org.example.demo")) and env["ATL_UGLY_ENABLE_WEBVIEW"] == "1"
    assert p.data("org.example.demo").is_dir()
