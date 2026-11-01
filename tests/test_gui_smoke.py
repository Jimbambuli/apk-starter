"""Smoke test of the real window on a headless GTK display (skipped without gtk4-broadwayd/libadwaita)."""
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


def _has_adw() -> bool:
    try:
        import gi
        gi.require_version("Gtk", "4.0")
        gi.require_version("Adw", "1")
        from gi.repository import Adw  # noqa: F401
        return True
    except (ImportError, ValueError):
        return False


needs_broadway = pytest.mark.skipif(not shutil.which("gtk4-broadwayd") or not _has_adw(),
                                    reason="no gtk4-broadwayd/libadwaita")


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
