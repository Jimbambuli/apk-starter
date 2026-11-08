import gzip
import json
import tarfile
import time
import os
import re
import subprocess
import sys
import types
from pathlib import Path

import pytest

from apk_starter.portal import desktop_entry as real_desktop_entry, link_mime_types as real_link_mime_types, \
    share_mime_types as real_share_mime_types

import apk_starter
from apk_starter.cli import main, split_diag_option
from apk_starter import dexopt, diag
from apk_starter.errors import StarterError
from apkgen import axml_manifest, make_apk

FAKE = Path(__file__).with_name("fake_atl.py")
FAKE_ART = Path(__file__).with_name("fake_art.py")
PKG = "org.example.demo"
# smallest valid DEX header (version 035, no classes): enough for atl-doctor's APK reader
DEX = b"dex\n035\0" + bytes(24) + (0x70).to_bytes(4, "little") + (0x70).to_bytes(4, "little") + \
    (0x12345678).to_bytes(4, "little") + bytes(0x70 - 44)
DEFAULT_ICON = b"<svg/>"


class PortalUnavailable(StarterError):
    pass


@pytest.fixture
def box(tmp_path, monkeypatch):
    """Isolated XDG/prefix dirs, a PATH with the fake ATL, and a fake portal module."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "android-translation-layer").symlink_to(FAKE)
    for prog in ("dalvikvm", "dex2oat"):
        (bindir / prog).symlink_to(FAKE_ART)
    info = tmp_path / "flatpak-info"
    info.write_text("[Instance]\napp-commit=aaaa\n")
    monkeypatch.setattr(dexopt, "FLATPAK_INFO", info)
    record = tmp_path / "record.json"
    calls = []
    links = {}
    fake = types.ModuleType("apk_starter.portal")
    fake.PortalUnavailable = PortalUnavailable
    fake.link_mime_types = real_link_mime_types
    fake.share_mime_types = real_share_mime_types
    fake.desktop_entry = real_desktop_entry
    fake.launcher_entry = lambda pkg: None
    def install_launcher(pkg, label, icon, mime_types=()):
        calls.append(("install", pkg, label, icon))
        links[pkg] = list(mime_types)
    fake.install_launcher = install_launcher
    fake.uninstall_launcher = lambda pkg: calls.append(("uninstall", pkg))
    monkeypatch.setitem(sys.modules, "apk_starter.portal", fake)
    monkeypatch.setattr(apk_starter, "portal", fake, raising=False)
    prefix = tmp_path / "prefix"
    (prefix / "share/icons/hicolor/scalable/apps").mkdir(parents=True)
    (prefix / "share/icons/hicolor/scalable/apps/io.github.Jimbambuli.ApkStarter.svg").write_bytes(DEFAULT_ICON)
    env = {"XDG_DATA_HOME": str(tmp_path / "data"), "XDG_CACHE_HOME": str(tmp_path / "cache"),
           "APK_STARTER_PREFIX": str(prefix), "PATH": f"{bindir}:{os.environ['PATH']}",
           "FAKE_ATL_RECORD": str(record), "LANG": "de_CH.UTF-8", "FAKE_ART_RECORD": str(tmp_path / "art.jsonl")}
    apk = make_apk(tmp_path / "demo.apk", manifest=axml_manifest())
    def art():
        f = tmp_path / "art.jsonl"
        return [json.loads(line) for line in f.read_text().splitlines()] if f.exists() else []
    return types.SimpleNamespace(env=env, record=record, calls=calls, apk=apk, tmp=tmp_path, fake=fake,
                                 data=tmp_path / "data", art=art, info=info, links=links)


def test_debug_flag_does_not_eat_apk():
    assert split_diag_option(["--debug", "app.apk", "--debug=x"]) == (["app.apk", "--debug=x"], "debug", None)
    assert split_diag_option(["--debug=d", "--", "app.apk"]) == (["--", "app.apk"], "debug", Path("d"))
    assert split_diag_option(["app.apk"]) == (["app.apk"], None, None)
    assert split_diag_option(["--debug-full", "app.apk"]) == (["app.apk"], "debug-full", None)
    assert split_diag_option(["--debug-full=d", "--", "app.apk"]) == (["--", "app.apk"], "debug-full", Path("d"))


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
    def boom(*a, **kw):
        raise PortalUnavailable("x")
    box.fake.install_launcher = boom
    assert main(["--install", str(box.apk)], env=box.env) == 1
    assert (box.data / "apps" / f"{PKG}.apk").exists()
    assert f"flatpak run io.github.Jimbambuli.ApkStarter {PKG}" in capsys.readouterr().err


def test_version_line(box, capsys):
    assert main(["--version"], env=box.env) == 0
    assert capsys.readouterr().out == "apk-starter 0.8.3 (ATL unknown, ART unknown)\n"


def test_normal_start_writes_no_log(box):
    assert main([str(box.apk)], env=box.env) == 0
    assert box.record.exists() and not (box.data / "logs").exists()


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


def test_remove_without_portal_removes_files_and_warns(box, capsys):
    assert main(["--install", str(box.apk)], env=box.env) == 0

    def gone(pkg):
        raise PortalUnavailable("x")
    box.fake.uninstall_launcher = gone
    capsys.readouterr()
    assert main(["--remove", PKG], env=box.env) == 0
    assert not (box.data / "apps" / f"{PKG}.apk").exists() and not (box.data / "apps" / f"{PKG}.json").exists()
    assert "menu entry was not removed" in capsys.readouterr().err


def test_portal_error_on_remove_does_not_leave_the_app_half_installed(box):
    from apk_starter.cli import remove_app
    from apk_starter.paths import Paths
    assert main(["--install", str(box.apk)], env=box.env) == 0

    def broken(pkg):
        raise StarterError("portal call Uninstall failed: boom")
    box.fake.uninstall_launcher = broken
    warnings = remove_app(Paths.from_env(box.env), PKG, purge=False)
    assert not (box.data / "apps" / f"{PKG}.apk").exists() and not (box.data / "apps" / f"{PKG}.json").exists()
    assert len(warnings) == 1 and "menu entry was not removed" in warnings[0] and "boom" in warnings[0]


def test_cancelled_menu_entry_says_the_app_is_installed(box, capsys):
    def cancel(*a, **kw):
        raise StarterError("menu entry not created (cancelled)")
    box.fake.install_launcher = cancel
    assert main(["--install", str(box.apk)], env=box.env) == 1
    assert (box.data / "apps" / f"{PKG}.apk").exists()
    err = capsys.readouterr().err
    assert "Demo is installed without a menu entry" in err and "cancelled" in err


def test_remove_all_goes_on_after_a_failure(box, capsys, monkeypatch):
    from apk_starter import cli
    assert main(["--install", str(box.apk)], env=box.env) == 0
    (box.data / "apps" / "a.broken.apk").write_bytes(b"x")
    real = cli.remove_app

    def remove(paths, pkg, purge):
        if pkg == "a.broken":
            raise StarterError("cannot remove a.broken: busy")
        return real(paths, pkg, purge)
    monkeypatch.setattr(cli, "remove_app", remove)
    capsys.readouterr()
    assert main(["--remove-all"], env=box.env) == 1
    assert not (box.data / "apps" / f"{PKG}.apk").exists()          # the next app was still removed
    assert "cannot remove a.broken: busy" in capsys.readouterr().err


# -- ahead-of-time preparation (dexopt.py) ------------------------------------------------------------------
def _flt(call):
    return next(a.split("=", 1)[1] for a in call["args"] if a.startswith("--compiler-filter="))


def _dex(call):
    return next(a.split("=", 1)[1] for a in call["args"] if a.startswith("--dex-file="))


def test_install_prepares_runtime_app_and_natives_before_the_menu_entry(box, capsys):
    apk = make_apk(box.tmp / "libs.apk", manifest=axml_manifest(), files={"lib/x86_64/libx.so": b"x"})
    order = []
    box.fake.install_launcher = lambda *a, **kw: order.append(len(box.art()))
    assert main(["--install", str(apk)], env=box.env) == 0
    art = box.art()
    installed = str(box.data / "apps" / f"{PKG}.apk")
    assert [c["prog"] for c in art] == ["dalvikvm", "dex2oat", "dex2oat"]
    assert _dex(art[2]) == installed and _flt(art[2]) == "speed"
    assert order == [3]                                          # menu entry after the preparation
    assert (box.data / "data" / PKG / f"{PKG}.apk_" / "lib" / "libx.so").read_bytes() == b"x"
    assert "Preparing the Android runtime" in capsys.readouterr().err     # progress on stderr


def test_install_goes_on_when_preparing_fails(box, capsys):
    box.env["FAKE_ART_FAIL"] = "dex2oat"
    assert main(["--install", str(box.apk)], env=box.env) == 0
    assert (box.data / "apps" / f"{PKG}.apk").exists() and box.calls[-1][0] == "install"
    assert "something went wrong" in capsys.readouterr().err


def test_installed_app_starts_without_compiling(box):
    assert main(["--install", str(box.apk)], env=box.env) == 0
    n = len(box.art())
    assert main([PKG], env=box.env) == 0
    assert len(box.art()) == n
    assert json.loads(box.record.read_text())["argv"][0] == str(box.data / "apps" / f"{PKG}.apk")


def test_apk_file_is_installed_on_the_first_start_then_the_installed_app_starts(box):
    paths = apk_starter.paths.Paths.from_env(box.env)
    assert main([str(box.apk)], env=box.env) == 0                # e.g. a double click on the file
    assert paths.meta(PKG).is_file() and paths.started(PKG).is_file()
    assert json.loads(box.record.read_text())["argv"][0] == str(box.data / "apps" / f"{PKG}.apk")
    n = len(box.art())
    assert main([str(box.apk)], env=box.env) == 0                # installed: start only, no new install
    assert len(box.art()) == n
    newer = make_apk(box.tmp / "newer.apk", manifest=axml_manifest(version_code=8, version_name="1.3"))
    assert main([str(newer)], env=box.env) == 0                  # another version: installed again
    assert json.loads(paths.meta(PKG).read_text())["version_name"] == "1.3"


def test_apk_file_starts_although_the_menu_entry_cannot_be_made(box, capsys):
    def boom(*a, **kw):
        raise PortalUnavailable("x")
    box.fake.install_launcher = boom                             # e.g. no DynamicLauncher portal (headless)
    paths = apk_starter.paths.Paths.from_env(box.env)
    assert main([str(box.apk)], env=box.env) == 0
    assert paths.meta(PKG).is_file() and box.record.is_file()      # installed and started
    assert "installed without a menu entry" in capsys.readouterr().err


def test_after_a_flatpak_update_runtime_and_apps_are_rebuilt(box):
    other = make_apk(box.tmp / "other.apk", manifest=axml_manifest(package="org.example.other"))
    assert main(["--install", str(box.apk)], env=box.env) == 0
    assert main(["--install", str(other)], env=box.env) == 0
    n = len(box.art())
    box.info.write_text("[Instance]\napp-commit=bbbb\n")
    assert main([PKG], env=box.env) == 0
    new = box.art()[n:]
    assert [c["prog"] for c in new[:2]] == ["dalvikvm", "dex2oat"]   # ART checks the runtime again, ghax
    assert (_dex(new[2]).endswith(f"{PKG}.apk"), _flt(new[2]), new[2]["sched_idle"]) == (True, "verify", False)
    rest = sorted((_dex(c).rsplit("/", 1)[1], _flt(c), c["sched_idle"]) for c in new[3:])
    assert rest == [("org.example.demo.apk", "speed", True), ("org.example.other.apk", "speed", True)]


def test_start_works_when_the_runtime_cannot_be_prepared(box, capsys):
    box.env["FAKE_ART_FAIL"] = "dalvikvm"
    assert main([str(box.apk)], env=box.env) == 0
    assert [c["prog"] for c in box.art()] == ["dalvikvm", "dalvikvm"]  # tried at the install and at the start
    assert box.record.exists() and "something went wrong" in capsys.readouterr().err


def test_apk_details_are_read_once_per_apk(box, monkeypatch):
    import apk_starter.cli as cli
    seen = []
    real = cli.read_apk_info
    monkeypatch.setattr(cli, "read_apk_info", lambda p, lang=None: seen.append(p) or real(p, lang))
    assert main([str(box.apk)], env=box.env) == 0                # first start: read, installed
    n = len(seen)
    assert main([str(box.apk)], env=box.env) == 0
    assert len(seen) == n
    os.utime(box.apk, ns=(0, os.stat(box.apk).st_mtime_ns + 10**9))
    assert main([str(box.apk)], env=box.env) == 0
    assert len(seen) == n + 1
    rec = json.loads(box.record.read_text())
    assert rec["argv"][rec["argv"].index("--sdk-int") + 1] == "34"


def test_window_rebuild_prepares_runtime_and_every_stale_app(box):
    from apk_starter.cli import prepare_all
    assert main(["--install", str(box.apk)], env=box.env) == 0
    box.info.write_text("[Instance]\napp-commit=bbbb\n")
    paths = apk_starter.paths.Paths.from_env(box.env)
    msgs = []
    prepare_all(paths, box.env, msgs.append)
    assert dexopt.runtime_ready(paths) and dexopt.stale_installed(paths) == []
    assert msgs == ["Preparing the Android runtime for this computer…", "Preparing apps for this computer…"]
    n = len(box.art())
    prepare_all(paths, box.env, msgs.append)
    assert len(box.art()) == n and len(msgs) == 2


def test_install_fills_the_apk_details_cache(box, monkeypatch):
    import apk_starter.cli as cli
    assert main(["--install", str(box.apk)], env=box.env) == 0
    seen = []
    monkeypatch.setattr(cli, "read_apk_info", lambda p, lang=None: seen.append(p))
    assert main([PKG], env=box.env) == 0
    assert seen == []


def test_direct_start_unpacks_native_libraries_before_atl(box):
    apk = make_apk(box.tmp / "libs.apk", manifest=axml_manifest(), files={"lib/x86_64/libx.so": b"x"})
    box.env["FAKE_ATL_CHECK"] = str(box.data / "data" / PKG / f"{PKG}.apk_" / "lib" / "libx.so")
    assert main([str(apk)], env=box.env) == 0
    assert json.loads(box.record.read_text())["check"]


def test_normal_start_has_no_agent(box):
    assert main([str(box.apk)], env=box.env) == 0
    assert not any(x.startswith("-agentpath") for x in json.loads(box.record.read_text())["argv"])


def test_remembered_debug_applies_to_every_start_of_the_app(box, monkeypatch):
    """«Debug» ticked in the window: also a start from the menu entry (`apk-starter PACKAGE`) is recorded."""
    from apk_starter import cli
    from apk_starter.paths import Paths
    from apk_starter.settings import Settings, save_settings
    assert main(["--install", str(box.apk)], env=box.env) == 0
    save_settings(Paths.from_env(box.env).settings(), Settings().with_app(PKG, debug=True))
    seen = []
    monkeypatch.setattr(cli.diag, "record_restarting", lambda paths, cmd, env, out, pkg, shots: seen.append((out, shots)) or 0)
    assert main([PKG], env=box.env) == 0
    assert [x[1] for x in seen] == [False] and not list((box.data / "logs").glob("*.log"))     # a recording folder instead of a log
    save_settings(Paths.from_env(box.env).settings(), Settings().with_app(PKG, debug=True, debug_full=True))
    packed = []
    monkeypatch.setattr(cli.diag, "pack", lambda paths, path, full=False: packed.append(full))
    assert main([PKG], env=box.env) == 0
    assert packed == [True] and [x[1] for x in seen] == [False, True]          # «Debug full» wins, only it takes images


def _unpacked(box, archive: Path) -> Path:
    """A Debug recording is packed when the app has ended: its folder, unpacked for the test."""
    out = box.tmp / "unpacked"
    with tarfile.open(archive) as t:
        t.extractall(out, filter="data")
    return out / archive.name.removesuffix(".tar.gz").removesuffix("-FULL")


def test_debug_level_records_a_folder_for_atl_doctor(box):
    pytest.importorskip("atl_doctor")
    make_apk(box.apk, manifest=axml_manifest(), files={"classes.dex": DEX})     # atl-doctor reads the code
    prefix = Path(box.env["APK_STARTER_PREFIX"])
    (prefix / "bin").mkdir()
    (prefix / "bin/android-translation-layer").symlink_to(FAKE)
    assert main(["--debug", str(box.apk)], env={**box.env, "FAKE_ATL_MODE": "exit:3"}) == 3
    rec = json.loads(box.record.read_text())
    assert rec["log_tags"] == "*:v [bionic_linker]:i" and rec["data_dir"] == str(box.data / "data" / PKG)
    assert rec["argv"][0] == str(box.data / "apps" / f"{PKG}.apk")
    folder = _unpacked(box, next((box.data / "logs").glob(f"{PKG}-*.tar.gz")))
    m = json.loads((folder / "manifest.json").read_text())
    assert m["exit_status"] == 3 and m["package"] == PKG and "fake atl output" in (folder / "atl.log").read_text()
    assert "stage: cleaned" in (folder / "SANITIZED.txt").read_text()     # «Debug»: cleaned, and it says so
    assert main(["--debug=" + str(box.tmp / "own"), str(box.apk)], env=box.env) == 0
    assert (box.tmp / "own/manifest.json").is_file()


def test_debug_full_archive_is_named_full_and_not_cleaned(box):
    pytest.importorskip("atl_doctor")
    make_apk(box.apk, manifest=axml_manifest(), files={"classes.dex": DEX})
    prefix = Path(box.env["APK_STARTER_PREFIX"])
    (prefix / "bin").mkdir()
    (prefix / "bin/android-translation-layer").symlink_to(FAKE)
    assert main(["--debug-full", str(box.apk)], env=box.env) == 0
    archives = list((box.data / "logs").iterdir())
    assert len(archives) == 1 and re.fullmatch(rf"{re.escape(PKG)}-\d{{8}}-\d{{6}}-FULL\.tar\.gz", archives[0].name)
    folder = _unpacked(box, archives[0])                          # the folder inside has the name without -FULL
    assert folder.name == archives[0].name.removesuffix("-FULL.tar.gz") and (folder / "manifest.json").is_file()
    assert "stage: full" in (folder / "SANITIZED.txt").read_text()


def test_failed_pack_leaves_the_folder_and_says_so(box, monkeypatch, capsys):
    pytest.importorskip("atl_doctor")
    make_apk(box.apk, manifest=axml_manifest(), files={"classes.dex": DEX})
    prefix = Path(box.env["APK_STARTER_PREFIX"])
    (prefix / "bin").mkdir()
    (prefix / "bin/android-translation-layer").symlink_to(FAKE)
    real = subprocess.run
    monkeypatch.setattr(diag.subprocess, "run", lambda cmd, **kw: real(
        [sys.executable, "-c", "import sys; print('no space left', file=sys.stderr); sys.exit(4)"], **kw))
    assert main(["--debug", str(box.apk)], env=box.env) == 0
    folders = list((box.data / "logs").iterdir())
    assert len(folders) == 1 and folders[0].is_dir() and (folders[0] / "manifest.json").is_file()
    assert f"the recording stays in {folders[0]}: no space left" in capsys.readouterr().err


def test_diag_usage_errors_and_no_report_option(box):
    assert main(["--log", str(box.apk)], env=box.env) == 2          # the option is gone
    assert main(["--debug=", str(box.apk)], env=box.env) == 2
    assert main(["--report"], env=box.env) == 2          # reports come from atl-doctor itself


@pytest.mark.parametrize("level", ["--debug", "--debug-full"])
def test_debug_asks_for_a_view_tree_while_the_app_runs(box, monkeypatch, level):
    pytest.importorskip("atl_doctor")
    make_apk(box.apk, manifest=axml_manifest(), files={"classes.dex": DEX})
    prefix = Path(box.env["APK_STARTER_PREFIX"])
    (prefix / "bin").mkdir()
    (prefix / "bin/android-translation-layer").symlink_to(FAKE)
    import zipfile
    from atl_doctor.env import lib_dir
    jars = lib_dir(prefix) / "java/dex/android_translation_layer"
    jars.mkdir(parents=True)
    with zipfile.ZipFile(jars / "api-impl.jar", "w") as z:      # ATL with ATLViewDump
        z.writestr("classes.dex", b"Landroid/atl/ATLViewDump;")
    monkeypatch.setattr(dexopt, "FLATPAK_INFO", box.info)
    from apk_starter import diag
    monkeypatch.setattr(diag, "TREE_AFTER_S", 0.3)
    assert main([level, str(box.apk)], env={**box.env, "FAKE_ATL_MODE": "viewdump:2"}) == 0
    folder = _unpacked(box, next((box.data / "logs").glob(f"{PKG}-*.tar.gz")))
    assert [l.split("\t")[3] for l in (folder / "marks.tsv").read_text().splitlines()] == ["start", "running"]
    assert (folder / "trees/01-running.xml").is_file()                        # the tree is recorded at both levels
    if level == "--debug-full":
        assert (folder / "shots/01-running.png").read_bytes() == b"png"       # the window image ATL wrote; no portal
    else:                                                                      # «Debug»: no image made, none packed
        assert not (folder / "shots").exists() and not list(folder.rglob("*.png"))
        assert "screenshots: omitted" in (folder / "SANITIZED.txt").read_text()


def test_install_keeps_target_sdk_and_start_notes_the_time(box):
    paths = apk_starter.paths.Paths.from_env(box.env)
    assert main(["--install", str(box.apk)], env=box.env) == 0
    assert json.loads(paths.meta(PKG).read_text())["target_sdk"] == 34
    assert not paths.started(PKG).exists()
    assert main([PKG], env=box.env) == 0
    assert paths.started(PKG).is_file()
    assert main(["--remove", PKG], env=box.env) == 0
    assert not paths.started(PKG).exists()


def test_install_offers_the_app_for_its_links(box):
    view = {"actions": ["android.intent.action.VIEW"],
            "categories": ["android.intent.category.DEFAULT", "android.intent.category.BROWSABLE"]}
    apk = make_apk(box.tmp / "links.apk", manifest=axml_manifest(activities=[[
        dict(view, data=[{"scheme": "demo"}]),
        dict(view, data=[{"scheme": "https", "host": "www.example.net"}]),
        dict(view, autoVerify=True, data=[{"scheme": "https", "host": "example.com"}])]]))
    assert main(["--install", str(apk)], env=box.env) == 0
    assert box.links[PKG] == ["x-scheme-handler/demo", "x-scheme-handler/https"]
    web = make_apk(box.tmp / "web.apk", manifest=axml_manifest(activities=[[
        dict(view, data=[{"scheme": "http"}, {"scheme": "https"}, {"host": "www.example.net"}])]]))
    assert main(["--install", str(web)], env=box.env) == 0
    assert box.links[PKG] == ["x-scheme-handler/http", "x-scheme-handler/https"]
    assert main(["--install", str(box.apk)], env=box.env) == 0
    assert box.links[PKG] == []


def test_uri_goes_to_atl(box):
    assert main(["--uri", "demo://open?x=1&y=2", str(box.apk)], env=box.env) == 0
    assert "--uri=demo://open?x=1&y=2" in json.loads(box.record.read_text())["argv"]


def test_empty_uri_from_the_menu_starts_the_app(box):
    assert main(["--uri", "", str(box.apk)], env=box.env) == 0
    assert not any(a.startswith("--uri") for a in json.loads(box.record.read_text())["argv"])


OLD_ENTRY = """[Desktop Entry]
Type=Application
Name=Mein Demo
Exec=flatpak run --command=apk-starter io.github.Jimbambuli.ApkStarter org.example.demo
Icon=/x/io.github.Jimbambuli.ApkStarter.org.example.demo.png
Terminal=false
Categories=Utility;
StartupWMClass=io.github.Jimbambuli.ApkStarter.org.example.demo
X-ApkStarter-Package=org.example.demo
"""


def _links_apk(box):
    view = {"actions": ["android.intent.action.VIEW"],
            "categories": ["android.intent.category.DEFAULT", "android.intent.category.BROWSABLE"]}
    return make_apk(box.tmp / "links.apk", manifest=axml_manifest(activities=[[dict(view, data=[{"scheme": "demo"}])]]))


def test_starter_update_renews_menu_entries_without_new_fields(box):
    """An entry from an older starter lacks MimeType: after an update it is made again, with the user's name."""
    from apk_starter.cli import refresh_menu_entries
    assert main(["--install", str(_links_apk(box))], env=box.env) == 0
    entries = {PKG: OLD_ENTRY}
    box.fake.launcher_entry = entries.get
    box.calls.clear()
    box.info.write_text("[Instance]\napp-commit=bbbb\n")
    paths = apk_starter.paths.Paths.from_env(box.env)
    assert refresh_menu_entries(paths, "de") == [PKG]
    assert box.calls == [("install", PKG, "Mein Demo", box.calls[0][3])] and box.calls[0][3]
    assert box.links[PKG] == ["x-scheme-handler/demo"]
    box.calls.clear()
    assert refresh_menu_entries(paths, "de") == []      # once per starter build
    assert box.calls == []


def test_starter_update_leaves_current_and_removed_entries(box):
    from apk_starter.cli import refresh_menu_entries
    from apk_starter.portal import desktop_entry
    assert main(["--install", str(_links_apk(box))], env=box.env) == 0
    paths = apk_starter.paths.Paths.from_env(box.env)
    box.calls.clear()
    current = desktop_entry(PKG, "Demo", ["x-scheme-handler/demo"]).replace("Exec=apk-starter", "Exec=flatpak run x")
    box.fake.launcher_entry = {PKG: current}.get
    box.info.write_text("[Instance]\napp-commit=bbbb\n")
    assert refresh_menu_entries(paths, "de") == []
    box.fake.launcher_entry = {}.get                     # the user removed the entry: not made again
    box.info.write_text("[Instance]\napp-commit=cccc\n")
    assert refresh_menu_entries(paths, "de") == []
    assert box.calls == []


def test_window_after_update_renews_menu_entries(box):
    from apk_starter.cli import prepare_all
    assert main(["--install", str(_links_apk(box))], env=box.env) == 0
    box.fake.launcher_entry = {PKG: OLD_ENTRY}.get
    box.calls.clear()
    box.info.write_text("[Instance]\napp-commit=bbbb\n")
    prepare_all(apk_starter.paths.Paths.from_env(box.env), box.env, lambda _m: None)
    assert [c[:3] for c in box.calls] == [("install", PKG, "Mein Demo")]


def test_window_rebuild_names_the_apps_it_works_on_and_always_ends(box):
    """The window shows a spinner per app: every state reaches {} at the end."""
    from apk_starter.cli import prepare_all
    assert main(["--install", str(_links_apk(box))], env=box.env) == 0
    box.fake.launcher_entry = {PKG: OLD_ENTRY}.get
    box.info.write_text("[Instance]\napp-commit=bbbb\n")
    seen = []
    problems = prepare_all(apk_starter.paths.Paths.from_env(box.env), box.env, lambda _m: None, seen.append)
    assert seen == [{PKG: "Preparing…"}, {PKG: "Updating the menu entry…"},
                    {PKG: "Preparing…"}, {}] and problems == []


def test_window_rebuild_failure_is_reported_per_app_and_ends(box):
    from apk_starter.cli import prepare_all
    assert main(["--install", str(box.apk)], env=box.env) == 0
    box.info.write_text("[Instance]\napp-commit=bbbb\n")
    box.env["FAKE_ART_FAIL"] = "dex2oat"
    seen = []
    problems = prepare_all(apk_starter.paths.Paths.from_env(box.env), box.env, lambda _m: None, seen.append)
    assert seen[-1] == {} and len(problems) == 1 and problems[0][0] is None


def test_menu_entry_kept_old_is_a_problem_of_that_app(box):
    from apk_starter.cli import prepare_all
    assert main(["--install", str(_links_apk(box))], env=box.env) == 0
    box.fake.launcher_entry = {PKG: OLD_ENTRY}.get
    def refused(*_a, **_k):
        raise StarterError("menu entry not created (cancelled)")
    box.fake.install_launcher = refused
    box.info.write_text("[Instance]\napp-commit=bbbb\n")
    problems = prepare_all(apk_starter.paths.Paths.from_env(box.env), box.env, lambda _m: None, lambda _w: None)
    assert [p for p, _ in problems] == [PKG]


def test_update_with_another_signer_is_refused_like_android(box, capsys):
    """INSTALL_FAILED_UPDATE_INCOMPATIBLE: e.g. Google Play services from Aurora over microG."""
    from apkgen import fake_cert, sign_block
    a = sign_block(make_apk(box.tmp / "a.apk", manifest=axml_manifest(version_name="1.0")), fake_cert("microG"))
    b = sign_block(make_apk(box.tmp / "b.apk", manifest=axml_manifest(version_name="2.0")), fake_cert("Google"))
    assert main(["--install", str(a)], env=box.env) == 0
    before = (box.data / "apps" / f"{PKG}.apk").read_bytes()
    capsys.readouterr()
    assert main(["--install", str(b)], env=box.env) == 1
    assert "Different signature – not installed" in capsys.readouterr().err
    assert (box.data / "apps" / f"{PKG}.apk").read_bytes() == before
    same = sign_block(make_apk(box.tmp / "c.apk", manifest=axml_manifest(version_name="3.0")), fake_cert("microG"))
    assert main(["--install", str(same)], env=box.env) == 0


def test_starter_update_finds_the_icon_an_older_one_missed(box):
    """Apps from an app store had no icon (bitmaps in the density part): the update finds it and renews the entry."""
    from test_apkinfo import _split_app, _res
    from apkgen import Res, png_bytes
    from apk_starter.portal import desktop_entry
    from apk_starter.cli import refresh_menu_entries
    base, part, _ids = _split_app(box.tmp, _res({"anydpi-v26": "res/c.xml"}), {"res/c.xml": b"<x/>"},
                                  _res({"xxhdpi": "res/ic.png"}), {"res/ic.png": png_bytes(144, 144)})
    assert main(["--install", str(base), str(part)], env=box.env) == 0
    paths = apk_starter.paths.Paths.from_env(box.env)
    paths.icon(PKG).unlink()                            # as an older starter left it
    box.fake.launcher_entry = {PKG: desktop_entry(PKG, "Demo")}.get
    box.calls.clear()
    box.info.write_text("[Instance]\napp-commit=bbbb\n")
    assert refresh_menu_entries(paths, None) == [PKG]
    assert box.calls[0][3] == png_bytes(144, 144) == paths.icon(PKG).read_bytes()


def test_menu_entry_offers_the_app_for_files_it_takes_shared(box):
    """Desktop -> app: «Open with» for the types of its SEND filters; ATL hands the file over as ACTION_SEND."""
    send = {"actions": ["android.intent.action.SEND"], "categories": ["android.intent.category.DEFAULT"],
            "data": [{"mimeType": "image/*"}, {"mimeType": "*/*"}]}
    view = {"actions": ["android.intent.action.VIEW"],
            "categories": ["android.intent.category.DEFAULT", "android.intent.category.BROWSABLE"],
            "data": [{"scheme": "demo"}]}
    apk = make_apk(box.tmp / "share.apk", manifest=axml_manifest(activities=[[send, view]]))
    assert main(["--install", str(apk)], env=box.env) == 0
    assert box.links[PKG] == ["x-scheme-handler/demo", "image/*"]


def test_start_ends_soon_after_the_app_even_when_the_background_compile_is_still_running(box, monkeypatch):
    import apk_starter.cli as cli
    monkeypatch.setattr(cli, "COMPILE_GRACE_S", 0.2)
    monkeypatch.setattr(cli, "_compile_later", lambda paths, env, locations: time.sleep(5))
    began = time.monotonic()
    assert main([str(box.apk)], env=box.env) == 0
    assert time.monotonic() - began < 3


def test_start_marks_its_app_as_first_while_it_prepares(box, monkeypatch):
    """The app being started pauses every other compile from before the runtime check until its own code and
    libraries are there; the background FULL compile afterwards is not part of that."""
    from apk_starter import dexopt
    paths = apk_starter.paths.Paths.from_env(box.env)
    seen = {}
    real_runtime, real_compile = dexopt.ensure_runtime, dexopt.compile_app

    def runtime(*a, **k):
        seen["runtime"] = dexopt.urgent_active(paths)
        return real_runtime(*a, **k)

    def compile_app(p, e, loc, flt, idle=False, is_urgent=False):
        seen[flt] = (dexopt.urgent_active(paths), is_urgent, idle)
        return real_compile(p, e, loc, flt, idle=idle, is_urgent=is_urgent)
    monkeypatch.setattr(dexopt, "ensure_runtime", runtime)
    monkeypatch.setattr(dexopt, "compile_app", compile_app)
    assert main(["--install", str(box.apk)], env=box.env) == 0
    seen.clear()
    dexopt._state_file(paths, str(paths.apk(PKG))).unlink()         # its code is not current: the start makes it
    assert main([PKG], env=box.env) == 0
    assert seen["runtime"] is True
    assert seen[dexopt.QUICK] == (True, True, False)
    assert seen[dexopt.FULL][1:] == (False, True)                   # in the background: idle, not first
    assert not dexopt.urgent_active(paths)


def test_opening_an_apk_file_compiles_quick_first_and_full_in_the_background(box, monkeypatch):
    """Double click on an APK: the install makes only what the start needs, with this app first; FULL follows."""
    from apk_starter import dexopt
    paths = apk_starter.paths.Paths.from_env(box.env)
    calls = []
    real = dexopt.compile_app

    def compile_app(p, e, loc, flt, idle=False, is_urgent=False):
        calls.append((flt, idle, dexopt.urgent_active(paths)))
        return real(p, e, loc, flt, idle=idle, is_urgent=is_urgent)
    monkeypatch.setattr(dexopt, "compile_app", compile_app)
    assert main([str(box.apk)], env=box.env) == 0
    assert calls == [(dexopt.QUICK, False, True), (dexopt.FULL, True, False)]
    assert dexopt.app_filter(paths, str(paths.apk(PKG))) == dexopt.FULL
