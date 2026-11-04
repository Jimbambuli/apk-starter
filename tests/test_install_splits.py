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
    assert "only available for ARM" in capsys.readouterr().err
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
    assert main(["--install-request", *map(str, parts(box.tmp, arm_only=True))], env=box.env) == 7


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
