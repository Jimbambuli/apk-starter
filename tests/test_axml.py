import pytest

from apk_starter.axml import LinkFilter, ResRef, parse_manifest
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


VIEW = "android.intent.action.VIEW"
BROWSABLE = "android.intent.category.BROWSABLE"
DEFAULT = "android.intent.category.DEFAULT"
JW_ACTIVITIES = [[
    {"actions": ["android.intent.action.SEARCH"]},
    {"actions": [VIEW], "categories": [DEFAULT, BROWSABLE], "data": [{"scheme": "jwlibrary"}]},
    {"actions": [VIEW], "categories": [DEFAULT, BROWSABLE], "data": [{"scheme": "content"}, {"scheme": "file"}]},
    {"actions": [VIEW], "categories": [DEFAULT, BROWSABLE], "autoVerify": True,
     "data": [{"scheme": "https", "host": "www.jw.org", "pathPrefix": "/finder"}]},
    {"actions": [VIEW], "categories": [DEFAULT], "data": [{"scheme": "notbrowsable"}]},
]]


@pytest.mark.parametrize("strip", [False, True])
def test_reads_browsable_view_filters(strip):
    m = parse_manifest(axml_manifest(activities=JW_ACTIVITIES, strip_names=strip))
    assert m.link_filters == (
        LinkFilter(schemes=("jwlibrary",), hosts=(), auto_verify=False),
        LinkFilter(schemes=("content", "file"), hosts=(), auto_verify=False),
        LinkFilter(schemes=("https",), hosts=("www.jw.org",), auto_verify=True),
    )


def test_no_activities_no_link_filters():
    assert parse_manifest(axml_manifest()).link_filters == ()


SEND = "android.intent.action.SEND"
SEND_MULTIPLE = "android.intent.action.SEND_MULTIPLE"
SIGNAL_ACTIVITIES = [[
    {"actions": [SEND], "categories": [DEFAULT], "data": [{"mimeType": "image/*"}, {"mimeType": "text/plain"}]},
    {"actions": [SEND_MULTIPLE], "categories": [DEFAULT], "data": [{"mimeType": "video/*"}, {"mimeType": "*/*"}]},
    {"actions": [SEND], "data": [{"mimeType": "audio/*"}]},              # no DEFAULT: never chosen implicitly
]]


@pytest.mark.parametrize("strip", [False, True])
def test_reads_the_types_the_app_takes_shared(strip):
    m = parse_manifest(axml_manifest(activities=SIGNAL_ACTIVITIES, strip_names=strip))
    assert m.share_types == ("image/*", "text/plain", "video/*", "*/*")
