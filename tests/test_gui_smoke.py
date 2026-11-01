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
    heading, body = report.pop("install_error")
    assert heading == "Cannot install broken.apk" and body.startswith("not an APK"), (body, out.stderr)
    heading, body = report.pop("install_warning")
    assert heading == "Cannot install demo.apk" and body.startswith("Demo is installed without a menu entry")
    heading, body = report.pop("remove_warning")
    assert heading == "Demo uninstalled" and "menu entry was not removed" in body
    assert report == {"listed": [], "page": "empty", "drop_accepted": True, "after_install": ["Demo"],
                      "pkg": "org.example.demo", "started": ["org.example.demo"],
                      "clear_dialog": "Delete the data of Demo?", "dialog": "Uninstall Demo?", "after_remove": [],
                      "page_end": "empty", "data_left": False, "busy_end": False}, out.stderr


@needs_broadway
@pytest.mark.skipif(not shutil.which("msgfmt"), reason="no msgfmt (gettext)")
@pytest.mark.parametrize("lang, title, error", [
    ("de", "Keine Apps installiert", "broken.apk kann nicht installiert werden"),
    ("ja", "インストール済みのアプリはありません", "broken.apk をインストールできません"),
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
def test_show_report_lists_recordings_shows_and_saves(tmp_path):
    pytest.importorskip("atl_doctor")
    d = tmp_path / "d"
    (d / "apps").mkdir(parents=True)
    for pkg, label in (("org.example.demo", "Demo"), ("org.example.none", "None")):
        (d / "apps" / f"{pkg}.json").write_text(json.dumps({"package": pkg, "label": label}))
    (d / "logs").mkdir()
    (d / "logs/org.example.demo-20261101-090000.log").write_text(
        "# apk-starter: package=org.example.demo\nall fine\n# apk-starter: exit=0\n")
    (d / "logs/org.example.demo-20261101-100000.log").write_text(
        "# apk-starter: package=org.example.demo\n# apk-starter: version_name=1.2\n"
        "java.lang.NoSuchMethodError: No virtual method foo()V in class Landroid/view/View; or its super classes\n"
        "# apk-starter: signal=SIGSEGV\n# apk-starter: exit=139\n"
        "# atl-doctor-agent: E\t200\t7\tjava.lang.IllegalStateException\tboom\tB:android.view.View.measure\t"
        "B:android.view.View.measure|org.example.demo.Main.onCreate\n")
    with broadway() as env:
        out = subprocess.run([sys.executable, str(HERE / "gui_report_probe.py"), str(d), str(tmp_path / "r.txt")],
                             env=env, capture_output=True, text=True, timeout=60)
    report = json.loads(out.stdout.strip().splitlines()[-1])
    assert report == {
        "title": "Report: Demo",
        "choices": ["2026-11-01 10:00:00 · Log", "2026-11-01 09:00:00 · Log"],
        "newest": [["Summary", ["org.example.demo 1.2", "killed by SIGSEGV (exit status 139)"]],
                   ["Crash", ["fatal signal SIGSEGV"]],
                   ["ATL gaps", ["missing method android.view.View#foo()V"]],
                   ["Exceptions", ["java.lang.IllegalStateException: boom"]]],
        "save_enabled": True,
        "older": [["Summary", ["org.example.demo", "exit status 0"]],
                  ["Findings", ["No crash, ATL gap or exception was found."]]],
        "empty_page": "none", "empty_choice_visible": False}, out.stderr
    assert "Environment" in (tmp_path / "r.txt").read_text()


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
    with broadway() as env:
        out = subprocess.run([sys.executable, str(HERE / "gui_layout_probe.py"), str(d), str(tmp_path / "started")],
                             env=env, capture_output=True, text=True, timeout=60)
    report = json.loads(out.stdout.strip().splitlines()[-1])
    assert report == {
        "restored": ["org.b.bravo", "Bravo", ["Alpha", "Bravo", "Charlie"]],
        "search": [["Charlie"], "org.c.charlie"], "kept_after_search": "org.c.charlie",
        "by_size": ["Bravo", "Alpha", "Charlie"], "by_started": ["Bravo", "Charlie", "Alpha"],
        "ctrl_f_in_search": True, "enter_to_list": True, "delete_key": "Uninstall Charlie?",
        "started": ["org.c.charlie"],
        "saved": {"width": 500, "height": 600, "sort": "started", "selected": "org.c.charlie"},
        "narrow_list": [True, False], "narrow_details": [False, True, True, "Bravo"],
        "narrow_back": [True, False]}, out.stderr
