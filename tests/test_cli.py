import json
import os
import sys
import types
from pathlib import Path

import pytest

import apk_starter
from apk_starter.cli import main, split_log_option
from apk_starter.errors import StarterError
from apkgen import axml_manifest, make_apk

FAKE = Path(__file__).with_name("fake_atl.py")
PKG = "org.example.demo"
DEFAULT_ICON = b"<svg/>"


class PortalUnavailable(StarterError):
    pass


@pytest.fixture
def box(tmp_path, monkeypatch):
    """Isolated XDG/prefix dirs, a PATH with the fake ATL, and a fake portal module."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "android-translation-layer").symlink_to(FAKE)
    record = tmp_path / "record.json"
    calls = []
    fake = types.ModuleType("apk_starter.portal")
    fake.PortalUnavailable = PortalUnavailable
    fake.install_launcher = lambda pkg, label, icon: calls.append(("install", pkg, label, icon))
    fake.uninstall_launcher = lambda pkg: calls.append(("uninstall", pkg))
    monkeypatch.setitem(sys.modules, "apk_starter.portal", fake)
    monkeypatch.setattr(apk_starter, "portal", fake, raising=False)
    prefix = tmp_path / "prefix"
    (prefix / "share/icons/hicolor/scalable/apps").mkdir(parents=True)
    (prefix / "share/icons/hicolor/scalable/apps/io.github.Jimbambuli.ApkStarter.svg").write_bytes(DEFAULT_ICON)
    env = {"XDG_DATA_HOME": str(tmp_path / "data"), "XDG_CACHE_HOME": str(tmp_path / "cache"),
           "APK_STARTER_PREFIX": str(prefix), "PATH": f"{bindir}:{os.environ['PATH']}",
           "FAKE_ATL_RECORD": str(record), "LANG": "de_CH.UTF-8"}
    apk = make_apk(tmp_path / "demo.apk", manifest=axml_manifest())
    return types.SimpleNamespace(env=env, record=record, calls=calls, apk=apk, tmp=tmp_path, fake=fake,
                                 data=tmp_path / "data")


def test_log_flag_does_not_eat_apk():
    argv, log = split_log_option(["--log", "app.apk", "--log=x"])
    assert argv == ["app.apk", "--log=x"] and log is True


def test_unwritable_log_file_fails_before_launch(box):
    assert main(["--log=/proc/nope/a.log", str(box.apk)], env=box.env) == 1
    assert not box.record.exists()          # fake ATL never started


def test_missing_apk_path_exit_3_with_hint(box, capsys):
    assert main(["/host/Downloads/app.apk"], env=box.env) == 3
    assert "--file-forwarding" in capsys.readouterr().err


def test_unknown_package_exit_2(box):
    assert main(["org.nope.app"], env=box.env) == 2
    assert main(["xx..yy"], env=box.env) == 2


def test_invalid_apk_exit_3(box):
    bad = box.tmp / "text.apk"
    bad.write_text("not a zip")
    assert main([str(bad)], env=box.env) == 3
    assert not box.record.exists()


def test_start_passes_args_and_remembers_size(box):
    assert main(["--size", "800x600", str(box.apk), "-X", "-v"], env=box.env) == 0
    rec = json.loads(box.record.read_text())
    a = rec["argv"]
    assert a[a.index("-w") + 1] == "800" and a[a.index("-h") + 1] == "600"
    assert a[a.index("--sdk-int") + 1] == "34" and a[-2:] == ["-X", "-v"]
    assert a[a.index("--gapplication-app-id") + 1] == f"io.github.Jimbambuli.ApkStarter.{PKG}"
    assert rec["data_dir"] == str(box.data / "data" / PKG)
    assert main([str(box.apk)], env=box.env) == 0
    a = json.loads(box.record.read_text())["argv"]
    assert a[a.index("-w") + 1] == "800"


def test_install_list_remove_purge(box, capsys):
    assert main(["--install", str(box.apk)], env=box.env) == 0
    assert box.calls == [("install", PKG, "Demo", DEFAULT_ICON)]
    capsys.readouterr()
    assert main(["--list"], env=box.env) == 0
    assert capsys.readouterr().out == f"{PKG}\tDemo\t1.2\n"
    assert main([PKG, "-X", "-v"], env=box.env) == 0          # start by package name
    assert (box.data / "data" / PKG).is_dir()
    assert main(["--remove", PKG, "--purge"], env=box.env) == 0
    assert not (box.data / "apps" / f"{PKG}.apk").exists() and not (box.data / "data" / PKG).exists()
    assert box.calls[-1] == ("uninstall", PKG)
    assert main(["--remove", PKG], env=box.env) == 2


def test_install_without_portal_keeps_apk(box, capsys):
    def boom(*a):
        raise PortalUnavailable("x")
    box.fake.install_launcher = boom
    assert main(["--install", str(box.apk)], env=box.env) == 1
    assert (box.data / "apps" / f"{PKG}.apk").exists()
    assert f"flatpak run io.github.Jimbambuli.ApkStarter {PKG}" in capsys.readouterr().err


def test_version_line(box, capsys):
    assert main(["--version"], env=box.env) == 0
    assert capsys.readouterr().out == "apk-starter 0.1.0 (ATL unknown, ART unknown)\n"


def test_log_default_location(box):
    assert main(["--log", str(box.apk)], env=box.env) == 0
    logs = list((box.data / "logs").glob(f"{PKG}-*.log"))
    assert len(logs) == 1
    lines = logs[0].read_text().splitlines()
    assert lines[0] == "# apk-starter: format=1" and lines[-2].startswith("# apk-starter: exit=0")


def test_file_errors_end_as_one_line_exit_1(box, capsys):
    box.data.write_text("not a directory")          # apps/ cannot be created below a file
    assert main(["--install", str(box.apk)], env=box.env) == 1
    err = capsys.readouterr().err
    assert err.startswith("apk-starter: ") and "Traceback" not in err


def test_install_keeps_icon_and_remove_deletes_it(box):
    from apkgen import Res, arsc_table, png_bytes
    arsc, ids = arsc_table(PKG, [Res("string", "app_name", {"": "Demo"}), Res("mipmap", "ic", {"": "res/a.png"})])
    apk = make_apk(box.tmp / "icon.apk", manifest=axml_manifest(label=ids["string/app_name"], icon=ids["mipmap/ic"]),
                   arsc=arsc, files={"res/a.png": png_bytes(96, 96)})
    assert main(["--install", str(apk)], env=box.env) == 0
    icon = box.data / "apps" / f"{PKG}.png"
    assert icon.read_bytes() == png_bytes(96, 96)
    assert main(["--remove", PKG], env=box.env) == 0
    assert not icon.exists()


def test_remove_all(box, capsys):
    assert main(["--install", str(box.apk)], env=box.env) == 0
    assert main([PKG], env=box.env) == 0
    assert main(["--remove-all", "--purge"], env=box.env) == 0
    assert box.calls[-1] == ("uninstall", PKG)
    assert not (box.data / "apps" / f"{PKG}.apk").exists() and not (box.data / "data" / PKG).exists()
    assert main(["--remove-all"], env=box.env) == 0          # nothing left: still fine


def test_no_arguments_without_display_is_usage_error(box, capsys):
    assert main([], env=box.env) == 2
    err = capsys.readouterr().err
    assert "usage: apk-starter" in err and "no display" in err


def test_no_arguments_with_display_opens_window(box, monkeypatch):
    fake = types.ModuleType("apk_starter.gui")
    seen = []
    fake.run = lambda paths, env: seen.append(paths.data_home) or 0
    monkeypatch.setitem(sys.modules, "apk_starter.gui", fake)
    monkeypatch.setattr(apk_starter, "gui", fake, raising=False)
    assert main([], env={**box.env, "WAYLAND_DISPLAY": "wayland-0"}) == 0
    assert seen == [box.data]
