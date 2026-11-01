import json
import os
import sys
import types
from pathlib import Path

import pytest

import apk_starter
from apk_starter.cli import main, split_diag_option
from apk_starter import dexopt
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
           "FAKE_ATL_RECORD": str(record), "LANG": "de_CH.UTF-8", "FAKE_ART_RECORD": str(tmp_path / "art.jsonl")}
    apk = make_apk(tmp_path / "demo.apk", manifest=axml_manifest())
    def art():
        f = tmp_path / "art.jsonl"
        return [json.loads(line) for line in f.read_text().splitlines()] if f.exists() else []
    return types.SimpleNamespace(env=env, record=record, calls=calls, apk=apk, tmp=tmp_path, fake=fake,
                                 data=tmp_path / "data", art=art, info=info)


def test_log_flag_does_not_eat_apk():
    assert split_diag_option(["--log", "app.apk", "--log=x"]) == (["app.apk", "--log=x"], "log", None)
    assert split_diag_option(["--debug=d", "--", "app.apk"]) == (["--", "app.apk"], "debug", Path("d"))
    assert split_diag_option(["app.apk"]) == (["app.apk"], None, None)


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
    def cancel(*a):
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
    box.fake.install_launcher = lambda *a: order.append(len(box.art()))
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


def test_direct_start_verifies_first_and_compiles_fully_afterwards(box):
    link = box.tmp / "cache" / "apk-starter" / "launch" / f"{PKG}.apk"
    paths = apk_starter.paths.Paths.from_env(box.env)
    box.env["FAKE_ATL_CHECK"] = str(dexopt.cache_file(paths, str(link)))
    assert main([str(box.apk)], env=box.env) == 0
    art = box.art()
    assert [c["prog"] for c in art] == ["dalvikvm", "dex2oat", "dex2oat", "dex2oat"]
    assert _dex(art[2]) == str(link) and _flt(art[2]) == "verify" and not art[2]["sched_idle"]
    assert json.loads(box.record.read_text())["check"]           # compiled code was there when ATL started
    assert _flt(art[3]) == "speed" and art[3]["sched_idle"]       # full pass in the background
    assert dexopt.app_filter(paths, str(link)) == "speed"
    assert main([str(box.apk)], env=box.env) == 0
    assert len(box.art()) == 4


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
    assert [c["prog"] for c in box.art()] == ["dalvikvm"]            # ART does it at the start, as before
    assert box.record.exists() and "something went wrong" in capsys.readouterr().err


def test_apk_details_are_read_once_per_apk(box, monkeypatch):
    import apk_starter.cli as cli
    seen = []
    real = cli.read_apk_info
    monkeypatch.setattr(cli, "read_apk_info", lambda p, lang=None: seen.append(p) or real(p, lang))
    assert main([str(box.apk)], env=box.env) == 0
    assert main([str(box.apk)], env=box.env) == 0
    assert len(seen) == 1
    os.utime(box.apk, ns=(0, os.stat(box.apk).st_mtime_ns + 10**9))
    assert main([str(box.apk)], env=box.env) == 0
    assert len(seen) == 2
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


def _agent(box):
    prefix = Path(box.env["APK_STARTER_PREFIX"])
    for f in ("lib/atl-doctor/libatl_doctor_agent.so", "lib/art/libopenjdkjvmti.so"):
        (prefix / f).parent.mkdir(parents=True, exist_ok=True)
        (prefix / f).write_bytes(b"")
    return prefix


def test_log_level_loads_the_agent_and_keeps_its_output_in_the_log(box, capsys):
    prefix = _agent(box)
    assert main(["--log", str(box.apk)], env=box.env) == 0
    a = json.loads(box.record.read_text())["argv"]
    agent = a[a.index("-X", a.index("--gapplication-app-id")) + 1:]
    log = next((box.data / "logs").glob(f"{PKG}-*.log"))
    assert [x for x in a if x.startswith("-agentpath:")] == [
        f"-agentpath:{prefix}/lib/atl-doctor/libatl_doctor_agent.so={log}.agent,"
        f"late={prefix}/lib/art/libopenjdkjvmti.so,latesec=3"] and agent
    assert log.read_text().splitlines()[-1].startswith("# atl-doctor-agent: E\t200\t7\tjava.lang.IllegalState")
    assert not Path(f"{log}.agent").exists()
    assert json.loads(box.record.read_text())["log_tags"] is None
    pytest.importorskip("atl_doctor")
    capsys.readouterr()
    assert main(["--report"], env=box.env) == 0
    out = capsys.readouterr().out
    assert "java.lang.IllegalStateException: boom" in out and "Exceptions" in out


def test_log_without_agent_is_a_plain_log(box):
    assert main(["--log", str(box.apk)], env=box.env) == 0
    assert not any(x.startswith("-agentpath") for x in json.loads(box.record.read_text())["argv"])


def test_debug_level_records_a_folder_for_atl_doctor(box, capsys):
    pytest.importorskip("atl_doctor")
    make_apk(box.apk, manifest=axml_manifest(), files={"classes.dex": DEX})     # atl-doctor reads the code
    prefix = Path(box.env["APK_STARTER_PREFIX"])
    (prefix / "bin").mkdir()
    (prefix / "bin/android-translation-layer").symlink_to(FAKE)
    assert main(["--debug", str(box.apk)], env={**box.env, "FAKE_ATL_MODE": "exit:3"}) == 3
    rec = json.loads(box.record.read_text())
    assert rec["log_tags"] == "*:v" and rec["data_dir"] == str(box.data / "data" / PKG)
    assert rec["argv"][0] == str(box.tmp / "cache/apk-starter/launch" / f"{PKG}.apk")
    folder = next((box.data / "logs").glob(f"{PKG}-*"))
    m = json.loads((folder / "manifest.json").read_text())
    assert m["exit_status"] == 3 and m["package"] == PKG and "fake atl output" in (folder / "atl.log").read_text()
    capsys.readouterr()
    assert main(["--report", PKG], env=box.env) == 0
    assert "exit status 3" in capsys.readouterr().out
    assert main(["--debug=" + str(box.tmp / "own"), str(box.apk)], env=box.env) == 0
    assert (box.tmp / "own/manifest.json").is_file()


def test_report_and_diag_usage_errors(box, capsys):
    assert main(["--log", "--debug", str(box.apk)], env=box.env) == 2
    assert main(["--debug=", str(box.apk)], env=box.env) == 2
    assert main(["--report", "org.none.app"], env=box.env) == 2
    assert main(["--report"], env=box.env) == 1
    assert main(["--report", "a", "b"], env=box.env) == 2
    assert main(["--report", "--list"], env=box.env) == 2


def test_debug_asks_for_a_view_tree_while_the_app_runs(box, monkeypatch):
    pytest.importorskip("atl_doctor")
    make_apk(box.apk, manifest=axml_manifest(), files={"classes.dex": DEX})
    prefix = Path(box.env["APK_STARTER_PREFIX"])
    (prefix / "bin").mkdir()
    (prefix / "bin/android-translation-layer").symlink_to(FAKE)
    monkeypatch.setattr(dexopt, "FLATPAK_INFO", box.info)
    from apk_starter import diag
    monkeypatch.setattr(diag, "TREE_AFTER_S", 0.3)
    assert main(["--debug", str(box.apk)], env={**box.env, "FAKE_ATL_MODE": "sleep:2"}) == 0
    folder = next((box.data / "logs").glob(f"{PKG}-*"))
    assert [l.split("\t")[3] for l in (folder / "marks.tsv").read_text().splitlines()] == ["start", "running"]
