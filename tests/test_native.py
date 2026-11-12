"""A native build (native/, no Flatpak): menu entries written by the starter itself, the one-time sandbox notice."""
from apk_starter.cli import native_notice
from apk_starter.paths import Paths
from apk_starter.portal import install_launcher, launcher_entry, uninstall_launcher


def _native(monkeypatch, tmp_path, prefix: str):
    monkeypatch.setenv("APK_STARTER_MENU_DIR", str(tmp_path / "applications"))
    monkeypatch.setenv("APK_STARTER_PREFIX", prefix)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("DBUS_SESSION_BUS_ADDRESS", "unix:path=/nonexistent")    # the portal is never asked


def test_menu_entry_without_portal(monkeypatch, tmp_path):
    _native(monkeypatch, tmp_path, "/opt/apk starter")
    install_launcher("org.x.y", "Demo", b"\x89PNG", mime_types=["x-scheme-handler/demo"])
    entry = tmp_path / "applications/io.github.Jimbambuli.ApkStarter.org.x.y.desktop"
    icon = tmp_path / "data/menu-icons/io.github.Jimbambuli.ApkStarter.org.x.y"
    text = entry.read_text()
    assert 'Exec="/opt/apk starter/bin/apk-starter" --uri %u org.x.y\n' in text
    assert f"Icon={icon}\n" in text and icon.read_bytes() == b"\x89PNG"
    assert launcher_entry("org.x.y") == text
    uninstall_launcher("org.x.y")
    assert not entry.exists() and not icon.exists()
    assert launcher_entry("org.x.y") is None
    uninstall_launcher("org.x.y")                   # gone already: no error


def test_exec_quoting(monkeypatch, tmp_path):
    _native(monkeypatch, tmp_path, '/o/a"b$c\\d%e')
    install_launcher("org.x.y", "Demo", b"")
    assert 'Exec="/o/a\\\\"b\\\\$c\\\\\\\\d%%e/bin/apk-starter" org.x.y\n' in launcher_entry("org.x.y")


def test_notice_once_and_only_native(tmp_path):
    paths = Paths(data_home=tmp_path / "d", cache_home=tmp_path / "c", prefix=tmp_path / "p")
    assert native_notice(paths, {}) is None
    assert "without a sandbox" in native_notice(paths, {"APK_STARTER_NATIVE": "1"})
    assert native_notice(paths, {"APK_STARTER_NATIVE": "1"}) is None
