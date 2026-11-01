import pytest

from apk_starter import apkinfo
from apk_starter.apkinfo import png_size, read_apk_info
from apk_starter.errors import ApkError
from apkgen import Res, arsc_table, axml_manifest, make_apk, png_bytes


def _app(tmp_path, icon_cfgs, files, label="@string/app_name", **kw):
    arsc, ids = arsc_table("org.example.demo", [
        Res("string", "app_name", {"": "Demo", "de": "Vorführung"}),
        Res("mipmap", "ic", icon_cfgs)])
    lab = ids["string/app_name"] if label == "@string/app_name" else label
    man = axml_manifest(label=lab, icon=ids["mipmap/ic"], **kw)
    return make_apk(tmp_path / "a.apk", manifest=man, arsc=arsc, files=files)


def test_full_metadata(tmp_path):
    apk = _app(tmp_path, {"xxhdpi": "res/a.png", "xxxhdpi": "res/b.png", "anydpi-v26": "res/c.xml"},
               {"res/a.png": png_bytes(144, 144), "res/b.png": png_bytes(192, 192), "res/c.xml": b"<x/>"})
    i = read_apk_info(apk, lang="de")
    assert (i.package, i.label, i.version_name, i.version_code, i.min_sdk, i.target_sdk) == \
           ("org.example.demo", "Vorführung", "1.2", 7, 21, 34)
    assert png_size(i.icon) == (192, 192)


def test_label_falls_back_to_default_then_package(tmp_path):
    apk = _app(tmp_path, {}, {})
    assert read_apk_info(apk, lang="fr").label == "Demo"
    bare = make_apk(tmp_path / "b.apk", manifest=axml_manifest(label=0x7f010000))
    assert read_apk_info(bare).label == "org.example.demo"


def test_icon_xml_or_webp_only_gives_none(tmp_path):
    apk = _app(tmp_path, {"anydpi-v26": "res/c.xml", "xxhdpi": "res/d.webp"}, {"res/c.xml": b"<x/>", "res/d.webp": b"RIFF"})
    assert read_apk_info(apk).icon is None


def test_icon_skips_non_square_and_oversized(tmp_path):
    apk = _app(tmp_path, {"xxxhdpi": "res/a.png", "xxhdpi": "res/b.png", "xhdpi": "res/c.png"},
               {"res/a.png": png_bytes(1024, 1024), "res/b.png": png_bytes(192, 100), "res/c.png": png_bytes(144, 144)})
    assert png_size(read_apk_info(apk).icon) == (144, 144)


@pytest.mark.parametrize("name", ["../x", "a/b", ".hidden", "a b.c", "nodot", "a." + "b" * 300])
def test_rejects_hostile_package_names(tmp_path, name):
    apk = make_apk(tmp_path / "x.apk", manifest=axml_manifest(package=name))
    with pytest.raises(ApkError, match="invalid package name"):
        read_apk_info(apk)


def test_not_a_zip_and_split_bundle(tmp_path):
    (tmp_path / "a.apk").write_bytes(b"hello")
    with pytest.raises(ApkError, match="not an APK"):
        read_apk_info(tmp_path / "a.apk")
    with pytest.raises(ApkError, match="split APK"):
        read_apk_info(tmp_path / "a.xapk")


def test_oversized_manifest_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(apkinfo, "MAX_MANIFEST", 100)
    with pytest.raises(ApkError):
        read_apk_info(make_apk(tmp_path / "a.apk", manifest=axml_manifest()))
