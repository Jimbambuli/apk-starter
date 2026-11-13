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


def _corrupt_member(apk, member):
    """Flip bytes inside the stored data of one ZIP member (the central directory stays valid)."""
    import zipfile
    with zipfile.ZipFile(apk) as z:
        zi = z.getinfo(member)
    raw = bytearray(apk.read_bytes())
    start = zi.header_offset + 30 + len(zi.filename.encode()) + int.from_bytes(raw[zi.header_offset + 28:zi.header_offset + 30], "little")
    for k in range(start, start + min(zi.compress_size, 16)):
        raw[k] ^= 0xFF
    apk.write_bytes(bytes(raw))


def test_corrupt_deflate_manifest_is_apkerror(tmp_path):
    apk = _app(tmp_path, {}, {})
    _corrupt_member(apk, "AndroidManifest.xml")
    with pytest.raises(ApkError):
        read_apk_info(apk)


def test_corrupt_icon_is_skipped_not_fatal(tmp_path):
    apk = _app(tmp_path, {"xxhdpi": "res/a.png", "xhdpi": "res/b.png"},
               {"res/a.png": png_bytes(144, 144), "res/b.png": png_bytes(96, 96)})
    _corrupt_member(apk, "res/a.png")
    i = read_apk_info(apk)
    assert i.package == "org.example.demo"
    assert png_size(i.icon) == (96, 96)


def test_fuzz_damaged_zip_bytes_never_traceback(tmp_path):
    import random
    apk = _app(tmp_path, {"xxhdpi": "res/a.png"}, {"res/a.png": png_bytes(144, 144)})
    good = apk.read_bytes()
    rnd = random.Random(1234)
    for n in range(400):
        raw = bytearray(good)
        for _ in range(rnd.randint(1, 6)):
            raw[rnd.randrange(len(raw))] = rnd.randrange(256)
        if n % 7 == 0:
            del raw[rnd.randrange(len(raw)):]
        bad = tmp_path / "bad.apk"
        bad.write_bytes(bytes(raw))
        try:
            read_apk_info(bad)
        except ApkError:
            pass


# -- icons of apps from an app store: in the density parts, adaptive icons ---------------------
import xml.etree.ElementTree as ET  # noqa: E402

from apkgen import axml_doc  # noqa: E402


def _res(icon_cfgs, extra=()):
    return [Res("string", "app_name", {"": "Demo"}), Res("mipmap", "ic", icon_cfgs), *extra]


def _split_app(tmp_path, base_res, base_files, split_res, split_files):
    """A base and its config.xxhdpi part, both with a resources.arsc of the same IDs, like bundletool makes them."""
    arsc, ids = arsc_table("org.example.demo", base_res)
    man = axml_manifest(label=ids["string/app_name"], icon=ids["mipmap/ic"])
    base = make_apk(tmp_path / "base.apk", manifest=man, arsc=arsc, files=base_files)
    sarsc, _ = arsc_table("org.example.demo", split_res)
    part = make_apk(tmp_path / "x.apk", manifest=axml_manifest(split="config.xxhdpi"), arsc=sarsc, files=split_files)
    return base, part, ids


def test_icon_from_the_density_part(tmp_path):
    from apk_starter.cli import read_parts
    base, part, _ = _split_app(tmp_path, _res({"anydpi-v26": "res/c.xml"}), {"res/c.xml": b"<x/>"},
                               _res({"xxhdpi": "res/ic.png"}), {"res/ic.png": png_bytes(144, 144)})
    info, _parts = read_parts(None, [base, part], None, tmp_path)
    assert png_size(info.icon) == (144, 144)


def _adaptive(ids, foreground):
    return axml_doc(("adaptive-icon", {}, [("background", {"drawable": ("ref", ids["color/bg"])}, []),
                                           ("foreground", {"drawable": ("ref", ids[foreground])}, [])]))


VECTOR = ("vector", {"width": ("dimen", 108), "height": ("dimen", 108), "viewportWidth": ("float", 108.0),
                     "viewportHeight": ("float", 108.0)},
          [("group", {"translateX": ("float", 10.0), "rotation": ("float", 45.0), "pivotX": ("float", 54.0),
                      "pivotY": ("float", 54.0)},
            [("path", {"pathData": "M30,30h48v48h-48z", "fillColor": ("color", 0xFFFFFFFF)}, [])])])


def test_adaptive_icon_with_a_vector_foreground_is_drawn_as_svg(tmp_path):
    names = _res({"anydpi-v26": "res/ic.xml"}, [Res("color", "bg", {"": (0x1C, 0xFF3366CC)}),
                                               Res("drawable", "fg", {"": "res/fg.xml"})])
    arsc, ids = arsc_table("org.example.demo", names)
    apk = make_apk(tmp_path / "a.apk", manifest=axml_manifest(label=ids["string/app_name"], icon=ids["mipmap/ic"]),
                   arsc=arsc, files={"res/ic.xml": _adaptive(ids, "drawable/fg"), "res/fg.xml": axml_doc(VECTOR)})
    svg = read_apk_info(apk).icon
    root = ET.fromstring(svg)                       # well-formed
    text = svg.decode()
    assert root.tag.endswith("svg") and 'fill="#3366cc"' in text and 'd="M30,30h48v48h-48z"' in text
    assert 'fill="#ffffff"' in text and "rotate(45" in text and "translate(64" in text


def test_adaptive_icon_with_a_bitmap_foreground_in_the_density_part(tmp_path):
    from apk_starter.cli import read_parts
    extra = [Res("color", "bg", {"": (0x1C, 0xFF000000)}), Res("mipmap", "fg", {})]   # its bitmaps: only in the part
    _a, ids = arsc_table("org.example.demo", _res({}, extra))
    base, part, ids = _split_app(
        tmp_path, _res({"anydpi-v26": "res/ic.xml"}, extra), {"res/ic.xml": _adaptive(ids, "mipmap/fg")},
        _res({}, [Res("color", "bg", {"": (0x1C, 0xFF000000)}), Res("mipmap", "fg", {"xxhdpi": "res/fg.png"})]),
        {"res/fg.png": png_bytes(162, 162)})
    info, _parts = read_parts(None, [base, part], None, tmp_path)
    assert b"data:image/png;base64," in info.icon and ET.fromstring(info.icon) is not None


@pytest.mark.parametrize("strip_names", [False, True])
def test_play_services_are_found_by_their_meta_data(tmp_path, strip_names):
    gms = make_apk(tmp_path / "g.apk", manifest=axml_manifest(
        strip_names=strip_names, meta_data=["com.google.firebase.messaging.default", "com.google.android.gms.version"]))
    other = make_apk(tmp_path / "o.apk", manifest=axml_manifest(strip_names=strip_names,
                                                                 meta_data=["com.google.android.gms.games.APP_ID"]))
    plain = make_apk(tmp_path / "p.apk", manifest=axml_manifest())
    assert [read_apk_info(f).uses_gms for f in (gms, other, plain)] == [True, False, False]


@pytest.mark.parametrize("strip_names", [False, True])
def test_geckoview_is_found_by_its_gpu_service(tmp_path, strip_names):
    services = ["org.mozilla.gecko.process.GeckoChildProcessServices$tab0",
                "org.mozilla.gecko.process.GeckoChildProcessServices$gpu"]
    gecko = make_apk(tmp_path / "g.apk", manifest=axml_manifest(strip_names=strip_names, services=services))
    other = make_apk(tmp_path / "o.apk", manifest=axml_manifest(strip_names=strip_names, services=services[:1]))
    named = make_apk(tmp_path / "m.apk", manifest=axml_manifest(meta_data=services[1:]))
    assert [read_apk_info(f).gecko_gpu for f in (gecko, other, named)] == [True, False, False]
