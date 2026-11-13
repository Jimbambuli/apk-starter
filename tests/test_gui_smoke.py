"""Smoke test of the real window on a headless GTK display (skipped without gtk4-broadwayd/GTK 4)."""
import contextlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

from apkgen import axml_manifest, make_apk

HERE = Path(__file__).parent


def _has_gtk() -> bool:
    try:
        import gi
        gi.require_version("Gtk", "4.0")
        from gi.repository import Gtk  # noqa: F401
        return True
    except (ImportError, ValueError):
        return False


needs_broadway = pytest.mark.skipif(not shutil.which("gtk4-broadwayd") or not _has_gtk(),
                                    reason="no gtk4-broadwayd/GTK 4")


@contextlib.contextmanager
def broadway():
    display = f":{90 + os.getpid() % 50}"
    daemon = subprocess.Popen(["gtk4-broadwayd", display], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        time.sleep(0.5)
        yield {**os.environ, "GDK_BACKEND": "broadway", "BROADWAY_DISPLAY": display,
               "DBUS_SESSION_BUS_ADDRESS": "unix:path=/nonexistent",    # never touch the real menu
               "PYTHONPATH": f"{HERE.parent / 'src'}:{HERE}"}
    finally:
        daemon.kill()


@needs_broadway
def test_window_lists_installs_starts_and_uninstalls(tmp_path):
    with broadway() as env:
        apk = make_apk(tmp_path / "demo.apk", manifest=axml_manifest())
        out = subprocess.run([sys.executable, str(HERE / "gui_probe.py"), str(tmp_path / "d"), str(apk),
                              str(tmp_path / "started")], env=env, capture_output=True, text=True, timeout=60)
        report = json.loads(out.stdout.strip().splitlines()[-1])
    assert "timeout" not in report and "error" not in report, report
    heading, body = report.pop("install_error")
    assert heading == "Cannot install broken.apk" and body.startswith("not an APK"), (body, out.stderr)
    heading, body = report.pop("install_warning")
    assert heading == "Cannot install demo.apk" and body.startswith("Demo is installed without a menu entry")
    heading, body = report.pop("remove_warning")
    assert heading == "Demo uninstalled" and "menu entry was not removed" in body
    assert report.pop("busy") == [1, ["demo.apk"], "list"]     # one spinner, in the row of the job
    assert report == {"listed": [], "page": "list", "drop_accepted": True, "after_install": ["Demo"],
                      "pkg": "org.example.demo", "started": ["org.example.demo"],
                      "clear_dialog": "Delete the data of Demo?", "dialog": "Uninstall Demo?", "after_remove": [],
                      "page_end": "list", "data_left": False, "busy_end": 0}, out.stderr
    assert "CRITICAL" not in out.stderr and "WARNING" not in out.stderr, out.stderr     # e.g. gtk_widget_is_ancestor


@needs_broadway
def test_window_follows_installs_and_removals_of_another_starter_process(tmp_path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    for prog in ("dalvikvm", "dex2oat"):
        (bindir / prog).symlink_to(HERE / "fake_art.py")
    with broadway() as env:
        env |= {"PATH": f"{bindir}:{env['PATH']}", "XDG_CACHE_HOME": str(tmp_path / "d/cache")}
        apk = make_apk(tmp_path / "demo.apk", manifest=axml_manifest())
        out = subprocess.run([sys.executable, str(HERE / "gui_external_probe.py"), str(tmp_path / "d"), str(apk)],
                             env=env, capture_output=True, text=True, timeout=60)
    report = json.loads(out.stdout.strip().splitlines()[-1])
    # install_rc 1: installed, but without a menu entry (no portal on the bus)
    assert report == {"before": [], "install_rc": 1, "after_install": ["org.example.demo"], "remove_rc": 0,
                      "after_remove": []}, out.stderr


@needs_broadway
@pytest.mark.skipif(not shutil.which("msgfmt"), reason="no msgfmt (gettext)")
@pytest.mark.parametrize("lang, title, error", [
    ("de", "Android-Apps auf deinem Linux-Desktop", "broken.apk kann nicht installiert werden"),
    ("ja", "Linux デスクトップで Android アプリを使う", "broken.apk をインストールできません"),
])
def test_window_speaks_the_system_language(tmp_path, lang, title, error):
    d = tmp_path / "prefix/share/locale" / lang / "LC_MESSAGES"
    d.mkdir(parents=True)
    subprocess.run(["msgfmt", "-o", str(d / "apk-starter.mo"), str(HERE.parent / "po" / f"{lang}.po")], check=True)
    with broadway() as env:
        env = {k: v for k, v in env.items() if k not in ("LC_ALL", "LC_MESSAGES", "LANG")} | {"LANGUAGE": lang}
        out = subprocess.run([sys.executable, str(HERE / "gui_lang_probe.py"), str(tmp_path / "d"),
                              str(tmp_path / "prefix")], env=env, capture_output=True, text=True, timeout=60)
    report = json.loads(out.stdout.strip().splitlines()[-1])
    assert report["title"] == title and report["error"][0] == error, (report, out.stderr)


@needs_broadway
def test_search_sort_keys_narrow_layout_and_remembered_state(tmp_path):
    d = tmp_path / "d"
    (d / "apps").mkdir(parents=True)
    for pkg, label, size, started in (("org.a.alpha", "Alpha", 5, None), ("org.b.bravo", "Bravo", 50, 200),
                                      ("org.c.charlie", "Charlie", 0, 100)):
        (d / "apps" / f"{pkg}.json").write_text(json.dumps({"package": pkg, "label": label}))
        if size:
            (d / "data" / pkg).mkdir(parents=True)
            (d / "data" / pkg / "f").write_bytes(b"x" * size)
        if started:
            (d / "apps" / f"{pkg}.started").touch()
            os.utime(d / "apps" / f"{pkg}.started", (started, started))
    (d / "window.json").write_text(json.dumps({"width": 900, "height": 600, "selected": "org.b.bravo"}))
    (d / "logs").mkdir()
    (d / "logs/org.b.bravo-20261101-100000.log").write_text("# apk-starter: exit=0\n")
    (d / "logs/org.b.bravo-20261101-090000").mkdir()
    (d / "logs/org.b.bravo-20261101-090000/manifest.json").write_text("{}")      # a Debug recording
    with broadway() as env:
        out = subprocess.run([sys.executable, str(HERE / "gui_layout_probe.py"), str(d), str(tmp_path / "started")],
                             env=env, capture_output=True, text=True, timeout=60)
    report = json.loads(out.stdout.strip().splitlines()[-1])
    assert report == {
        "restored": ["org.b.bravo", "Bravo", ["Alpha", "Bravo", "Charlie"]], "opened_focus_on_start": True,
        "empty_page": ["list", True, True, True, ["Get apps", "Open APK…"], ["win.get-apps", "win.choose-apk"],
                       "Or drop an APK file into this window, or double-click it in the file manager."],
        "empty_columns": [False, False, True, True, True, True],
        "list_page": "list", "list_columns": [True, True, False],
        "add_menu": ["Install APK…", "Get apps (Aurora Store)", "Set up microG…"],
        "add_menu_aurora_installed": ["Install APK…", "Get apps (Aurora Store)", "Set up microG…"],
        "bravo_menu": [1, False], "version_line": [3, True],
        "about": ["0.8.8", "APK Starter", "io.github.Jimbambuli.ApkStarter",
                  "https://github.com/Jimbambuli/apk-starter", True],
        "about_components": [["Components", ["ATL unknown", "art_standalone unknown", "bionic_translation unknown",
                                              "atl-doctor unknown"]]],
        "charlie_menu": [0, False], "dot_with_debug": True,
        "debug_full": [False, True, False, True, "The archive contains everything in plain text, also passwords and "
                                                  "personal data – do not pass it on", True],
        "debug_back": [False, True, None],
        "crash_dialog": ["Charlie ended unexpectedly.",
                         "Exit code 10: crash"],
        "search": [["Charlie"], "org.c.charlie"], "kept_after_search": "org.c.charlie",
        "by_size": ["Bravo", "Alpha", "Charlie"], "by_started": ["Bravo", "Charlie", "Alpha"],
        "ctrl_f_in_search": True, "enter_to_list": True, "delete_key": "Uninstall Charlie?",
        "started": ["org.c.charlie"], "focus_on_start": False,
        "settings": {"apps": {"org.c.charlie": {"titlebar": "system-navigation", "debug": True}}},
        "debug_started": ["org.c.charlie"], "debug_stays_on": [True, True],
        "menu_corner": {"list": True, "details": True},
        "saved": {"width": 500, "height": 600, "sort": "started", "selected": "org.c.charlie"},
        "narrow_list": [True, False], "narrow_details": [False, True, True, "Bravo"],
        "narrow_back": [True, False], "narrow_empty": [False, True, True]}, out.stderr


@needs_broadway
@pytest.mark.skipif(not shutil.which("dbus-daemon"), reason="no dbus-daemon")
def test_window_follows_the_settings_portal_live():
    bus = subprocess.Popen(["dbus-daemon", "--session", "--nofork", "--print-address"], stdout=subprocess.PIPE,
                           text=True)
    try:
        address = bus.stdout.readline().strip()
        with broadway() as env:
            env = env | {"DBUS_SESSION_BUS_ADDRESS": address, "HOST_XDG_CONFIG_HOME": "/nonexistent"}
            portal = subprocess.Popen([sys.executable, str(HERE / "fake_settings_portal.py")], env=env,
                                      stdout=subprocess.PIPE, text=True)
            try:
                assert portal.stdout.readline().strip() == "ready"
                out = subprocess.run([sys.executable, str(HERE / "gui_theme_probe.py")], env=env,
                                     capture_output=True, text=True, timeout=60)
            finally:
                portal.kill()
    finally:
        bus.kill()
    report = json.loads(out.stdout.strip().splitlines()[-1])
    assert report == {"first": [False, True, False], "changed": [True, False, True]}, out.stderr


@needs_broadway
def test_window_takes_the_system_gtk_theme_live(tmp_path):
    share, cfg = tmp_path / "share", tmp_path / "cfg"
    (share / "themes" / "Testthema" / "gtk-4.0").mkdir(parents=True)
    (share / "themes" / "Testthema" / "gtk-4.0" / "gtk.css").write_text("label.theme-probe { color: rgb(1,2,3); }\n")
    (share / "icons" / "Testsymbole").mkdir(parents=True)        # installed (else Adwaita's icons stay)
    (share / "icons" / "Testsymbole" / "index.theme").write_text("[Icon Theme]\nName=Testsymbole\nDirectories=\n")
    (cfg / "gtk-4.0").mkdir(parents=True)
    (cfg / "gtk-4.0" / "settings.ini").write_text(
        "[Settings]\ngtk-theme-name=Testthema\ngtk-icon-theme-name=Testsymbole\ngtk-font-name=Testschrift 13\n")
    (cfg / "gtk-4.0" / "gtk.css").write_text("@import 'colors.css';\n")
    (cfg / "gtk-4.0" / "colors.css").write_text("label.user-probe { color: rgb(4,5,6); }\n")
    with broadway() as env:
        env = env | {"HOST_XDG_CONFIG_HOME": str(cfg), "XDG_DATA_DIRS": f"/usr/share:{share}"}
        out = subprocess.run([sys.executable, str(HERE / "gui_system_theme_probe.py"), str(cfg), str(share)],
                             env=env, capture_output=True, text=True, timeout=60)
    report = json.loads(out.stdout.strip().splitlines()[-1])
    assert report == {
        "first": {"theme": "Testthema", "icons": "Testsymbole", "font": "Testschrift 13", "theme_css": [1, 2, 3],
                  "user_css": [4, 5, 6], "found": True},
        "changed": {"theme": "Zweites", "icons": "Testsymbole", "font": "Testschrift 13", "theme_css": [10, 20, 30],
                    "user_css": [40, 50, 60], "found": True}}, out.stderr


def _updated_box(tmp_path, monkeypatch):
    """One app installed under an older Flatpak build (fake ART), as the window finds it after an update."""
    from apk_starter import dexopt
    from apk_starter.cli import main
    bindir = tmp_path / "bin"
    bindir.mkdir()
    for prog in ("dalvikvm", "dex2oat"):
        (bindir / prog).symlink_to(HERE / "fake_art.py")
    prefix = tmp_path / "prefix"
    (prefix / "share/icons/hicolor/scalable/apps").mkdir(parents=True)
    (prefix / "share/icons/hicolor/scalable/apps/io.github.Jimbambuli.ApkStarter.svg").write_bytes(b"<svg/>")
    info = tmp_path / "flatpak-info"
    info.write_text("[Instance]\napp-commit=old\n")
    monkeypatch.setattr(dexopt, "FLATPAK_INFO", info)
    monkeypatch.setenv("DBUS_SESSION_BUS_ADDRESS", "unix:path=/nonexistent")
    env = {"XDG_DATA_HOME": str(tmp_path / "data"), "XDG_CACHE_HOME": str(tmp_path / "cache"),
           "APK_STARTER_PREFIX": str(prefix), "PATH": f"{bindir}:{os.environ['PATH']}"}
    main(["--install", str(make_apk(tmp_path / "demo.apk", manifest=axml_manifest()))], env=env)  # no menu entry
    assert (tmp_path / "data/apps/org.example.demo.apk").is_file()
    return env, info


@needs_broadway
@pytest.mark.parametrize("fail", [None, "dex2oat"])
def test_window_after_update_shows_work_per_app_and_ends(tmp_path, monkeypatch, fail):
    env, info = _updated_box(tmp_path, monkeypatch)
    with broadway() as display:
        run_env = {**display, **env} | ({"FAKE_ART_FAIL": fail} if fail else {})
        out = subprocess.run([sys.executable, str(HERE / "gui_prepare_probe.py"), str(info)], env=run_env,
                             capture_output=True, text=True, timeout=60)
    report = json.loads(out.stdout.strip().splitlines()[-1])
    assert report["seen"] == [[["org.example.demo", "Preparing…"]]], (report, out.stderr)
    assert report["during"] == {"spinners": 1, "status": "Preparing…", "start": True}, report
    after = report["after"]
    assert after["spinners"] == 0 and after["start"] is True, report
    if fail:
        assert after["status"].startswith("the Android runtime could not be prepared"), report
        assert after["toast"] == after["status"], report
    else:
        assert after["status"] == "Prepared for this computer" and after["toast"] is None, report


@needs_broadway
def test_details_option_opens_the_window_at_that_app(tmp_path, monkeypatch):
    env, _info = _updated_box(tmp_path, monkeypatch)
    main_apk = make_apk(tmp_path / "second.apk", manifest=axml_manifest(package="org.example.zweite", label="Zweite"))
    from apk_starter.cli import main
    main(["--install", str(main_apk)], env=env)
    with broadway() as display:
        out = subprocess.run([sys.executable, str(HERE / "gui_details_probe.py"), "org.example.zweite"],
                             env={**display, **env}, capture_output=True, text=True, timeout=60)
    report = json.loads(out.stdout.strip().splitlines()[-1])
    assert report == {"selected": "org.example.zweite", "title": "Zweite"}, out.stderr


HOLDER = """
import sys
from gi.repository import Gio, GLib
bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
# like a running app (ATL, notification clicks): owns the Flatpak's name, lets the window take it, queues
bus.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus", "RequestName",
              GLib.Variant("(su)", ("io.github.Jimbambuli.ApkStarter", 1)), None, 0, -1, None)
print("ready", flush=True)
GLib.MainLoop().run()
"""

WINDOW = """
import sys
from apk_starter import gui
app = gui.make_app()
app.register(None)
print("remote" if app.get_is_remote() else "primary", flush=True)
"""


@needs_broadway
def test_window_starts_while_an_app_holds_the_flatpak_name(tmp_path):
    """An app started from its menu entry owns the Flatpak's bus name (ATL, for notification clicks); the window
    must still start as the primary instance, a second window goes to the first."""
    if not shutil.which("dbus-daemon"):
        pytest.skip("dbus-daemon not installed")
    bus = subprocess.Popen(["dbus-daemon", "--session", "--nofork", "--print-address"], stdout=subprocess.PIPE,
                           text=True)
    try:
        with broadway() as display:
            env = {**display, "DBUS_SESSION_BUS_ADDRESS": bus.stdout.readline().strip()}
            holder = subprocess.Popen([sys.executable, "-c", HOLDER], env=env, stdout=subprocess.PIPE, text=True)
            try:
                assert holder.stdout.readline().strip() == "ready"
                first = subprocess.Popen([sys.executable, "-c", WINDOW + "GLib_loop = __import__('gi.repository.GLib', fromlist=['GLib']).MainLoop().run()"],
                                         env=env, stdout=subprocess.PIPE, text=True)
                try:
                    assert first.stdout.readline().strip() == "primary"
                    second = subprocess.run([sys.executable, "-c", WINDOW], env=env, capture_output=True, text=True,
                                            timeout=30)
                    assert second.stdout.strip() == "remote", second.stderr
                finally:
                    first.kill()
            finally:
                holder.kill()
    finally:
        bus.kill()


@needs_broadway
def test_options_switch_permissions_per_app(tmp_path, monkeypatch):
    env, _info = _updated_box(tmp_path, monkeypatch)
    from apk_starter.cli import main
    main(["--install", str(make_apk(tmp_path / "p.apk", manifest=axml_manifest(permissions=[
        "android.permission.CAMERA", "android.permission.INTERNET", "android.permission.ACCESS_FINE_LOCATION"])))],
        env=env)
    with broadway() as display:
        out = subprocess.run([sys.executable, str(HERE / "gui_permissions_probe.py")], env={**display, **env},
                             capture_output=True, text=True, timeout=60)
    report = json.loads(out.stdout.strip().splitlines()[-1])
    assert report == {"shown": [["location", True], ["camera", True]], "labels": ["Location", "Camera"],
                      "saved": ["camera"]}, out.stderr


@needs_broadway
def test_window_tells_aurora_updates_and_a_click_starts_aurora(tmp_path, monkeypatch):
    from dataclasses import replace
    from apk_starter.paths import Paths
    from apk_starter.settings import Settings, save_settings
    from test_aurora import _aurora_db, _installed
    env, _info = _updated_box(tmp_path, monkeypatch)
    p = Paths.from_env(env)
    _installed(p, "com.aurora.store", 76, "Aurora Store")
    _installed(p, "com.whatsapp", 10, "WhatsApp")
    _aurora_db(p, [("com.whatsapp", 11, "2.26", "WhatsApp")])
    save_settings(p.settings(), replace(Settings(), aurora_updates=True))
    with broadway() as display:
        out = subprocess.run([sys.executable, str(HERE / "gui_updates_probe.py"), str(tmp_path / "started")],
                             env={**display, **env}, capture_output=True, text=True, timeout=60)
    report = json.loads(out.stdout.strip().splitlines()[-1])
    assert report == {"id": "aurora-updates", "toggle": "Report updates", "started": ["com.aurora.store"]}, out.stderr


@needs_broadway
def test_first_run_welcome_offers_microg_once_and_get_apps_starts_aurora(tmp_path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    for prog in ("dalvikvm", "dex2oat"):
        (bindir / prog).symlink_to(HERE / "fake_art.py")
    with broadway() as env:
        env |= {"PATH": f"{bindir}:{env['PATH']}", "XDG_CACHE_HOME": str(tmp_path / "d/cache")}
        apk = make_apk(tmp_path / "demo.apk", manifest=axml_manifest(meta_data=["com.google.android.gms.version"]))
        out = subprocess.run([sys.executable, str(HERE / "gui_welcome_probe.py"), str(tmp_path / "d"), str(apk),
                              str(tmp_path / "started")],
                             env=env, capture_output=True, text=True, timeout=60)
    report = json.loads(out.stdout.strip().splitlines()[-1])
    assert report == {"welcome": [True, ["Get apps", "Open APK…"]],
                      "offered": ["This app uses Google services. Set up microG?", ["Not now", "Set up"]],
                      "asked_on_update": False,
                      "get_apps": [["com.aurora.store"], False]}, out.stderr
