"""Split APKs: base and configuration parts, as files or as a bundle, and the ABI choice."""
import zipfile

import pytest

from apk_starter import splits
from apk_starter.errors import ApkError
from apkgen import axml_manifest, make_apk

PKG = "org.example.demo"


def part(tmp, name, split=None, package=PKG, files=None):
    return make_apk(tmp / name, manifest=axml_manifest(package=package, split=split), files=files)


def test_collect_finds_base_and_sorts_parts(tmp_path):
    base = part(tmp_path, "base.apk")
    mdpi = part(tmp_path, "m.apk", "config.mdpi")
    abi = part(tmp_path, "a.apk", "config.x86_64", files={"lib/x86_64/liba.so": b"x"})
    s = splits.collect([mdpi, abi, base])
    assert s.package == PKG and s.base == base
    assert [n for n, _p in s.splits] == ["config.mdpi", "config.x86_64"]


@pytest.mark.parametrize("files,msg", [
    (lambda t: [part(t, "m.apk", "config.mdpi")], "no base"),
    (lambda t: [part(t, "a.apk"), part(t, "b.apk")], "more than one base"),
    (lambda t: [part(t, "a.apk"), part(t, "b.apk", "config.de", package="org.other.app")], "different apps"),
    (lambda t: [part(t, "a.apk"), part(t, "b.apk", "config.de"), part(t, "c.apk", "config.de")], "twice"),
])
def test_collect_refuses_inconsistent_sets(tmp_path, files, msg):
    with pytest.raises(ApkError, match=msg):
        splits.collect(files(tmp_path))


def test_select_keeps_x86_64_and_drops_other_abis(tmp_path):
    s = splits.collect([part(tmp_path, "b.apk"),
                        part(tmp_path, "x.apk", "config.x86_64", files={"lib/x86_64/l.so": b"x"}),
                        part(tmp_path, "a.apk", "config.arm64_v8a", files={"lib/arm64-v8a/l.so": b"x"}),
                        part(tmp_path, "d.apk", "config.xxhdpi")])
    assert [n for n, _p in splits.select(s, "Demo").splits] == ["config.x86_64", "config.xxhdpi"]


@pytest.mark.parametrize("base_files,parts", [
    ({"lib/arm64-v8a/l.so": b"x", "lib/armeabi-v7a/l.so": b"x"}, []),
    ({}, [("config.arm64_v8a", {"lib/arm64-v8a/l.so": b"x"})]),
    ({}, [("config.arm64_v8a", {})]),                                   # the name alone tells the ABI
    ({}, [("feature", {"lib/arm64-v8a/l.so": b"x"}), ("config.de", {})]),   # lib/ in any part
])
def test_select_refuses_arm_only_apps(tmp_path, base_files, parts):
    s = splits.collect([part(tmp_path, "b.apk", files=base_files)]
                       + [part(tmp_path, f"{n}.apk", n, files=f) for n, f in parts])
    with pytest.raises(splits.IncompatibleError, match="only for ARM processors") as e:
        splits.select(s, "Demo")
    assert e.value.code == 7


def test_app_without_native_code_needs_no_abi(tmp_path):
    s = splits.collect([part(tmp_path, "b.apk"), part(tmp_path, "d.apk", "config.de")])
    assert splits.select(s, "Demo") == s


@pytest.mark.parametrize("suffix", [".apks", ".xapk"])
def test_bundles_are_unpacked(tmp_path, suffix):
    src = tmp_path / "src"
    src.mkdir()
    b = part(src, "base-master.apk")
    d = part(src, "base-xxhdpi.apk", "config.xxhdpi")
    bundle = tmp_path / f"demo{suffix}"
    with zipfile.ZipFile(bundle, "w") as z:
        z.write(b, "splits/base-master.apk")
        z.write(d, "splits/base-xxhdpi.apk")
        z.writestr("manifest.json", "{}")
        z.writestr("toc.pb", b"")
    out = tmp_path / "out"
    files = splits.expand([bundle], out)
    assert len(files) == 2 and all(f.parent.parent == out for f in files)
    s = splits.collect(files)
    assert s.package == PKG and [n for n, _p in s.splits] == ["config.xxhdpi"]


def test_bundle_without_apk_is_refused(tmp_path):
    bundle = tmp_path / "empty.apks"
    with zipfile.ZipFile(bundle, "w") as z:
        z.writestr("toc.pb", b"")
    with pytest.raises(ApkError, match="no APK"):
        splits.expand([bundle], tmp_path / "out")


def test_base_with_x86_64_part_is_allowed(tmp_path):
    s = splits.collect([part(tmp_path, "b.apk"), part(tmp_path, "x.apk", "config.x86_64", files={"lib/x86_64/l.so": b"x"})])
    assert splits.select(s, "Demo") == s
