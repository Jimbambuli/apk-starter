import pytest

from apk_starter.axml import ResRef, parse_manifest
from apk_starter.errors import ApkError
from apkgen import axml_manifest


def test_reads_literal_fields_utf8():
    m = parse_manifest(axml_manifest())
    assert (m.package, m.version_code, m.version_name, m.min_sdk, m.target_sdk, m.label) == \
           ("org.example.demo", 7, "1.2", 21, 34, "Demo")


def test_reads_utf16_pool():
    assert parse_manifest(axml_manifest(utf8=False, label="Zürich")).label == "Zürich"


def test_label_and_icon_as_references():
    m = parse_manifest(axml_manifest(label=0x7f0c0001, icon=0x7f080002))
    assert m.label == ResRef(0x7f0c0001) and m.icon == 0x7f080002


def test_stripped_attribute_names_use_resource_map():
    m = parse_manifest(axml_manifest(strip_names=True))
    assert m.target_sdk == 34 and m.label == "Demo"


def test_truncated_manifest_raises_apk_error():
    with pytest.raises(ApkError):
        parse_manifest(axml_manifest()[:200])
