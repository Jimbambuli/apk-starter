"""Installing split APKs (several files, bundles) and app store requests (--install-request)."""
import json
import zipfile

import pytest

from apk_starter import install_request
from apk_starter.cli import main
from apkgen import axml_manifest, make_apk
from test_cli import PKG, box  # noqa: F401  (fixture)


def parts(tmp, *, arm_only=False):
    d = tmp / "parts"
    d.mkdir(exist_ok=True)
    base_files = {"lib/arm64-v8a/l.so": b"a"} if arm_only else {}
    base = make_apk(d / f"{PKG}_base.apk", manifest=axml_manifest(), files=base_files)
    out = [base, make_apk(d / "m.apk", manifest=axml_manifest(split="config.mdpi"))]
    if not arm_only:
        out.append(make_apk(d / "x.apk", manifest=axml_manifest(split="config.x86_64"),
                            files={"lib/x86_64/libx.so": b"x86"}))
    out.append(make_apk(d / "a.apk", manifest=axml_manifest(split="config.arm64_v8a"),
                        files={"lib/arm64-v8a/libx.so": b"arm"}))
    return out


def test_install_base_and_parts_then_start_with_them(box):
    assert main(["--install", *map(str, parts(box.tmp))], env=box.env) == 0
    apps = box.data / "apps"
    assert (apps / f"{PKG}.apk").is_file()
    assert sorted(p.name for p in (apps / f"{PKG}.splits").iterdir()) == ["config.mdpi.apk", "config.x86_64.apk"]
    assert (box.data / "data" / PKG / f"{PKG}.apk_" / "lib" / "libx.so").read_bytes() == b"x86"
    assert main([PKG], env=box.env) == 0
    rec = json.loads(box.record.read_text())
    a = rec["argv"]
    assert a[:3] == [str(apps / f"{PKG}.apk"), str(apps / f"{PKG}.splits" / "config.mdpi.apk"),
                     str(apps / f"{PKG}.splits" / "config.x86_64.apk")]
    assert rec["installed_apks"] == str(apps)
    assert rec["install_command"].endswith("apk-starter --install-request")
    assert rec["launch_command"].endswith("bin/apk-starter")
    assert main(["--remove", PKG], env=box.env) == 0
    assert not (apps / f"{PKG}.splits").exists()


def test_reinstall_without_parts_removes_old_parts(box):
    assert main(["--install", *map(str, parts(box.tmp))], env=box.env) == 0
    assert main(["--install", str(box.apk)], env=box.env) == 0
    assert not (box.data / "apps" / f"{PKG}.splits").exists()


def test_arm_only_app_is_refused_with_exit_7(box, capsys):
    assert main(["--install", *map(str, parts(box.tmp, arm_only=True))], env=box.env) == 7
    assert "only for ARM processors" in capsys.readouterr().err
    assert not (box.data / "apps" / f"{PKG}.apk").exists()


def test_bundle_install(box):
    bundle = box.tmp / "demo.apks"
    with zipfile.ZipFile(bundle, "w") as z:
        for p in parts(box.tmp):
            z.write(p, f"splits/{p.name}")
    assert main(["--install", str(bundle)], env=box.env) == 0
    assert (box.data / "apps" / f"{PKG}.splits" / "config.x86_64.apk").is_file()


@pytest.mark.parametrize("answer,code", [(True, 0), (False, 3)])
def test_install_request_asks_first(box, monkeypatch, capsys, answer, code):
    asked = []
    monkeypatch.setattr(install_request, "ask", lambda info, installer, update, env: asked.append(
        (info.label, installer, update)) or answer)
    env = dict(box.env, ATL_INSTALLER_LABEL="Aurora Store")
    assert main(["--install-request", *map(str, parts(box.tmp))], env=env) == code
    assert asked == [("Demo", "Aurora Store", False)]
    assert (box.data / "apps" / f"{PKG}.apk").exists() == answer
    if not answer:
        assert capsys.readouterr().err.strip().splitlines()[-1].endswith("cancelled")


def test_install_request_for_arm_only_app_does_not_ask(box, monkeypatch):
    monkeypatch.setattr(install_request, "ask", lambda *a: pytest.fail("asked"))
    told = []
    monkeypatch.setattr(install_request, "_dialog", lambda env, heading, body, buttons: told.append(heading))
    assert main(["--install-request", *map(str, parts(box.tmp, arm_only=True))], env=box.env) == 7
    # said in a window, not only on stderr (the app store would end silently)
    assert told == ["Demo contains program code only for ARM processors and does not run on this computer."]


def test_double_click_on_arm_only_apk_says_why_in_a_window(box, monkeypatch, capsys):
    told = []
    monkeypatch.setattr(install_request, "_dialog", lambda env, heading, body, buttons: told.append(heading))
    arm = make_apk(box.tmp / "arm.apk", manifest=axml_manifest(), files={"lib/armeabi-v7a/l.so": b"a"})
    assert main([str(arm)], env=box.env) == 7
    assert told == ["Demo contains program code only for ARM processors and does not run on this computer."]
    assert "only for ARM processors" in capsys.readouterr().err
    assert not (box.data / "apps" / f"{PKG}.apk").exists()


def test_without_display_the_refusal_needs_no_window(box):
    assert install_request._dialog(box.env, "x", "", [("close", "Close")]) is None     # box.env has no display


@pytest.mark.parametrize("gms,microg_there,update,offered", [
    (True, False, False, True),
    (True, False, True, False),         # asked once: not again on an update
    (True, True, False, False),         # microG is there already
    (False, False, False, False),       # the app does not use Google services
])
def test_install_request_offers_microg_once_to_apps_with_play_services(box, monkeypatch, gms, microg_there, update,
                                                                         offered):
    from apk_starter import microg
    monkeypatch.setattr(install_request, "ask", lambda *a: True)
    meta = ["com.google.android.gms.version"] if gms else []
    apk = make_apk(box.tmp / "g.apk", manifest=axml_manifest(meta_data=meta))
    if update:
        assert main(["--install", str(apk)], env=box.env) == 0
    if microg_there:
        monkeypatch.setattr(microg, "installed", lambda paths: True)
    asked, opened = [], []
    monkeypatch.setattr(install_request, "_dialog", lambda env, heading, body, buttons: asked.append(heading) or "ok")
    monkeypatch.setattr(install_request, "setup_microg_in_window", lambda: opened.append(True))
    assert main(["--install-request", str(apk)], env=box.env) == 0
    assert asked == (["This app uses Google services. Set up microG?"] if offered else [])
    assert opened == ([True] if offered else [])


def test_microg_offer_declined_opens_nothing(box, monkeypatch):
    monkeypatch.setattr(install_request, "ask", lambda *a: True)
    monkeypatch.setattr(install_request, "_dialog", lambda *a: "cancel")
    monkeypatch.setattr(install_request, "setup_microg_in_window", lambda: pytest.fail("opened"))
    apk = make_apk(box.tmp / "g.apk", manifest=axml_manifest(meta_data=["com.google.android.gms.version"]))
    assert main(["--install-request", str(apk)], env=box.env) == 0


def test_setup_microg_in_window_starts_the_window_detached(monkeypatch):
    pytest.importorskip("gi")                       # the window's module (gui) imports GTK
    import subprocess
    started = []
    monkeypatch.setattr(subprocess, "Popen", lambda argv, **kw: started.append((argv[-1], kw["start_new_session"])))
    install_request.setup_microg_in_window()
    assert started == [("--setup-microg-window", True)]


def test_install_request_failure_is_status_failure(box, monkeypatch, capsys):
    monkeypatch.setattr(install_request, "ask", lambda *a: True)
    bad = box.tmp / "bad.apk"
    bad.write_bytes(b"no zip")
    assert main(["--install-request", str(bad)], env=box.env) == 4
    assert "not an APK" in capsys.readouterr().err


def test_install_request_without_menu_entry_is_still_installed(box, monkeypatch, capsys):
    from test_cli import PortalUnavailable

    def boom(*a, **kw):
        raise PortalUnavailable("x")
    box.fake.install_launcher = boom
    monkeypatch.setattr(install_request, "ask", lambda *a: True)
    assert main(["--install-request", *map(str, parts(box.tmp))], env=box.env) == 0
    assert "without a menu entry" in capsys.readouterr().err


def test_install_request_with_another_signer_is_a_conflict_without_asking(box, monkeypatch, capsys):
    """Aurora offers Google Play services as an update of microG: refused like Android, STATUS_FAILURE_CONFLICT."""
    from apkgen import fake_cert, sign_block
    old = sign_block(make_apk(box.tmp / "old.apk", manifest=axml_manifest()), fake_cert("microG"))
    assert main(["--install", str(old)], env=box.env) == 0
    monkeypatch.setattr(install_request, "ask", lambda *a: pytest.fail("asked"))
    new = parts(box.tmp)
    sign_block(new[0], fake_cert("Google"))
    assert main(["--install-request", *map(str, new)], env=box.env) == 5
    assert capsys.readouterr().err.strip().splitlines()[-1].endswith("Different signature – not installed")
    assert not (box.data / "apps" / f"{PKG}.splits").exists()


@pytest.mark.parametrize("answer,purge,code", [(False, False, 3), (True, False, 0), (True, True, 0)])
def test_uninstall_request_asks_and_reports_like_package_installer(box, monkeypatch, capsys, answer, purge, code):
    """Aurora's «Uninstall» (ACTION_DELETE, package:<name>) through ATL_UNINSTALL_COMMAND."""
    assert main(["--install", str(box.apk)], env=box.env) == 0
    (box.data / "data" / PKG).mkdir(parents=True, exist_ok=True)
    asked = []
    monkeypatch.setattr(install_request, "ask_uninstall", lambda label, env: asked.append(
        install_request.uninstall_question(label)) or (answer, purge))
    assert main(["--uninstall-request", PKG], env=dict(box.env, ATL_INSTALLER_LABEL="Aurora Store")) == code
    assert asked == [("Uninstall Demo?", "Also delete app data", "Cancel", "Uninstall")]
    assert (box.data / "apps" / f"{PKG}.apk").exists() == (not answer)
    assert (box.data / "data" / PKG).exists() == (not purge)
    if not answer:
        assert capsys.readouterr().err.strip().splitlines()[-1].endswith("cancelled")


def test_uninstall_request_for_an_unknown_package_fails_without_asking(box, monkeypatch):
    monkeypatch.setattr(install_request, "ask_uninstall", lambda *a: pytest.fail("asked"))
    assert main(["--uninstall-request", "org.example.none"], env=box.env) == 1


def test_setup_microg_window_option_opens_the_window_for_it(box, monkeypatch):
    pytest.importorskip("gi")
    from apk_starter import gui
    calls = []
    monkeypatch.setattr(gui, "run", lambda paths, env, **kw: calls.append(kw) or 0)
    assert main(["--setup-microg-window"], env=box.env) == 0
    assert calls == [{"setup_microg": True}]
