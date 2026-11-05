import pytest

from apk_starter.portal import (PortalUnavailable, desktop_entry, desktop_file_id, install_launcher,
                                request_path)

EXPECTED = """[Desktop Entry]
Type=Application
Name=Demo
Exec=apk-starter org.x.y
Terminal=false
Categories=Utility;
StartupWMClass=io.github.Jimbambuli.ApkStarter.org.x.y
X-ApkStarter-Package=org.x.y
"""


def test_desktop_file_id():
    assert desktop_file_id("org.x.y") == "io.github.Jimbambuli.ApkStarter.org.x.y.desktop"


def test_desktop_entry_exact():
    assert desktop_entry("org.x.y", "Demo") == EXPECTED


def test_desktop_entry_sanitizes_label():
    assert "Name=a b\n" in desktop_entry("org.x.y", "a\nb\x07")
    assert desktop_entry("org.x.y", "x" * 300).splitlines()[2] == "Name=" + "x" * 100
    with pytest.raises(Exception, match="invalid package"):
        desktop_entry("org.x.y\nExec=evil", "Demo")


def test_request_path():
    assert request_path(":1.42", "tok") == "/org/freedesktop/portal/desktop/request/1_42/tok"


def test_no_session_bus_is_portal_unavailable(monkeypatch):
    monkeypatch.setenv("DBUS_SESSION_BUS_ADDRESS", "unix:path=/nonexistent")
    with pytest.raises(PortalUnavailable):
        install_launcher("org.x.y", "Demo", b"")


CLIENT = """
import threading
from gi.repository import GLib
from apk_starter import portal
loop, result = GLib.MainLoop(), []
def work():
    try:
        portal.install_launcher("org.x.y", "Demo", b"")
        result.append("ok")
    except Exception as e:
        result.append(repr(e))
    GLib.idle_add(loop.quit)
threading.Thread(target=work, daemon=True).start()
GLib.timeout_add_seconds(10, loop.quit)
loop.run()
print(result)
"""


def test_install_from_worker_thread_while_main_loop_runs(tmp_path):
    """The window calls the portal from a worker thread while GTK's main loop owns the default context."""
    import os
    import shutil
    import subprocess
    import sys
    from pathlib import Path
    pytest.importorskip("gi")
    if not shutil.which("dbus-daemon"):
        pytest.skip("dbus-daemon not installed")
    here = Path(__file__).parent
    bus = subprocess.Popen(["dbus-daemon", "--session", "--nofork", "--print-address"], stdout=subprocess.PIPE,
                           text=True)
    try:
        env = {**os.environ, "DBUS_SESSION_BUS_ADDRESS": bus.stdout.readline().strip(),
               "PYTHONPATH": str(here.parent / "src")}
        record = tmp_path / "calls"
        svc = subprocess.Popen([sys.executable, str(here / "fake_portal.py"), str(record)], env=env,
                               stdout=subprocess.PIPE, text=True)
        try:
            assert svc.stdout.readline().strip() == "ready"
            out = subprocess.run([sys.executable, "-c", CLIENT], env=env, capture_output=True, text=True,
                                 timeout=30).stdout
            assert out.strip() == "['ok']"
            assert record.read_text() == '["t1", "io.github.Jimbambuli.ApkStarter.org.x.y.desktop"]\n'
        finally:
            svc.kill()
    finally:
        bus.kill()


def test_link_mime_types_own_schemes_and_verified_web():
    from apk_starter.axml import LinkFilter
    from apk_starter.portal import link_mime_types
    jw = (LinkFilter(("jwlibrary",), (), False), LinkFilter(("jwlplaylist",), (), False),
          LinkFilter(("content", "file"), (), False), LinkFilter(("https",), ("www.jw.org",), True))
    assert link_mime_types(jw) == [
        "x-scheme-handler/https", "x-scheme-handler/jwlibrary", "x-scheme-handler/jwlplaylist"]


def test_link_mime_types_web_links_of_any_host():
    """A web link filter offers the app for http(s) under «Open with» (MimeType only lists a handler, the default
    stays with mimeapps.list, i.e. the browser), like Android's chooser for unverified links."""
    from apk_starter.axml import LinkFilter
    from apk_starter.portal import link_mime_types
    newpipe = (LinkFilter(("http", "https"), ("youtube.com", "youtu.be"), False),
               LinkFilter(("vnd.youtube",), (), False))
    assert link_mime_types(newpipe) == [
        "x-scheme-handler/http", "x-scheme-handler/https", "x-scheme-handler/vnd.youtube"]


def test_link_mime_types_skips_odd_schemes():
    from apk_starter.axml import LinkFilter
    from apk_starter.portal import link_mime_types
    odd = (LinkFilter(("javascript", "data", "intent", "about", "Bad Scheme", "x;y", "MAILTO"), (), False),)
    assert link_mime_types(odd) == ["x-scheme-handler/mailto"]


def test_desktop_entry_with_links_takes_the_uri():
    entry = desktop_entry("org.x.y", "Demo", ["x-scheme-handler/demo", "x-scheme-handler/https"])
    assert "Exec=apk-starter --uri %u org.x.y\n" in entry
    assert "MimeType=x-scheme-handler/demo;x-scheme-handler/https;\n" in entry
    assert desktop_entry("org.x.y", "Demo", []) == EXPECTED


ENTRY_CLIENT = """
from apk_starter import portal
print(repr((portal.launcher_entry("org.x.y"), portal.launcher_entry("org.x.z"))))
"""


def test_launcher_entry_reads_the_entry_or_none(tmp_path):
    """GetDesktopEntry: the entry the portal keeps; a missing file (removed entry) is None."""
    import os
    import shutil
    import subprocess
    import sys
    from pathlib import Path
    pytest.importorskip("gi")
    if not shutil.which("dbus-daemon"):
        pytest.skip("dbus-daemon not installed")
    here = Path(__file__).parent
    bus = subprocess.Popen(["dbus-daemon", "--session", "--nofork", "--print-address"], stdout=subprocess.PIPE,
                           text=True)
    try:
        env = {**os.environ, "DBUS_SESSION_BUS_ADDRESS": bus.stdout.readline().strip(),
               "PYTHONPATH": str(here.parent / "src")}
        svc = subprocess.Popen([sys.executable, str(here / "fake_portal.py"), str(tmp_path / "calls")], env=env,
                               stdout=subprocess.PIPE, text=True)
        try:
            assert svc.stdout.readline().strip() == "ready"
            out = subprocess.run([sys.executable, "-c", ENTRY_CLIENT], env=env, capture_output=True, text=True,
                                 timeout=30)
            assert out.stdout.strip() == repr(("[Desktop Entry]\nName=Demo\n", None)), out.stderr
        finally:
            svc.kill()
    finally:
        bus.kill()


def test_share_mime_types_offer_the_app_under_open_with():
    """ACTION_SEND/SEND_MULTIPLE: the app is offered for those files; */* would put it on
    every file, it is left out."""
    from apk_starter.portal import share_mime_types
    assert share_mime_types(("image/*", "text/plain", "video/*", "*/*", "image/*", "bad type", "IMAGE/PNG")) == [
        "image/*", "image/png", "text/plain", "video/*"]
