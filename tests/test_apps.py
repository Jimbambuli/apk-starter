"""Shared app management used by the command line and the window: data size, clearing data, package locks."""
import threading
import time
from pathlib import Path

from apk_starter.cli import clear_data, data_size, package_lock, remove_app
from apk_starter.paths import Paths

PKG = "org.example.demo"


def _paths(tmp_path) -> Paths:
    return Paths(data_home=tmp_path / "d", cache_home=tmp_path / "c", prefix=tmp_path / "p")


def test_data_size_and_clear_data(tmp_path):
    p = _paths(tmp_path)
    assert data_size(p, PKG) == 0
    (p.data(PKG) / "sub").mkdir(parents=True)
    (p.data(PKG) / "a").write_bytes(b"x" * 100)
    (p.data(PKG) / "sub" / "b").write_bytes(b"y" * 23)
    assert data_size(p, PKG) == 123
    clear_data(p, PKG)
    assert not p.data(PKG).exists() and data_size(p, PKG) == 0
    clear_data(p, PKG)                                    # already gone: no error


def test_remove_waits_for_the_package_lock(tmp_path, monkeypatch):
    import sys, types
    import apk_starter
    fake = types.ModuleType("apk_starter.portal")
    fake.PortalUnavailable = type("PortalUnavailable", (Exception,), {})
    fake.uninstall_launcher = lambda pkg: None
    monkeypatch.setitem(sys.modules, "apk_starter.portal", fake)
    monkeypatch.setattr(apk_starter, "portal", fake, raising=False)
    p = _paths(tmp_path)
    p.apps_dir().mkdir(parents=True)
    p.apk(PKG).write_bytes(b"apk")
    done = []
    with package_lock(p, PKG):
        t = threading.Thread(target=lambda: done.append(remove_app(p, PKG, purge=False)))
        t.start()
        time.sleep(0.3)
        assert not done and p.apk(PKG).exists()         # blocked while another holder has the lock
    t.join(5)
    assert done and not p.apk(PKG).exists()


def test_lock_is_per_package(tmp_path):
    p = _paths(tmp_path)
    with package_lock(p, PKG):
        with package_lock(p, "org.example.other"):      # different package: does not block
            pass
