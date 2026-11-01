import pytest

from apk_starter.arsc import parse_table
from apk_starter.errors import ApkError
from apkgen import Res, arsc_table


@pytest.mark.parametrize("mode", ["dense", "sparse", "offset16", "compact"])
def test_resolves_string_in_every_entry_layout(mode):
    data, ids = arsc_table("org.example.demo", [Res("string", "app_name", {"": "Demo", "de": "Vorführung"})], mode=mode)
    vals = dict((c.language, v) for c, v in parse_table(data).resolve_strings(ids["string/app_name"]))
    assert vals == {"": "Demo", "de": "Vorführung"}


def test_follows_reference_chain():
    data, ids = arsc_table("p.q", [Res("string", "a", {"": "Ziel"}), Res("string", "b", {"": "@string/a"})])
    assert [v for _, v in parse_table(data).resolve_strings(ids["string/b"])] == ["Ziel"]


def test_reference_loop_raises():
    data, ids = arsc_table("p.q", [Res("string", "a", {"": "@string/a"})])
    with pytest.raises(ApkError, match="loop"):
        parse_table(data).resolve_strings(ids["string/a"])


def test_icon_paths_with_density():
    data, ids = arsc_table("p.q", [Res("mipmap", "ic", {"xxhdpi": "res/mipmap-xxhdpi/ic.png", "anydpi-v26": "res/mipmap-anydpi-v26/ic.xml"})])
    got = {(c.density, c.sdk): v for c, v in parse_table(data).resolve_strings(ids["mipmap/ic"])}
    assert got == {(480, 0): "res/mipmap-xxhdpi/ic.png", (0xfffe, 26): "res/mipmap-anydpi-v26/ic.xml"}


def test_unknown_id_is_empty():
    data, _ = arsc_table("p.q", [Res("string", "a", {"": "x"})])
    assert parse_table(data).resolve_strings(0x7f990001) == []
