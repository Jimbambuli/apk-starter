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
