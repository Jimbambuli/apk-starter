"""Binary AndroidManifest.xml reader (only the fields the starter needs)."""
import struct
from dataclasses import dataclass

from .errors import ApkError
from .i18n import _
from .resbin import chunk_header, read_string_pool

NONE = 0xFFFFFFFF
ATTR_IDS = {
    0x01010001: "label",
    0x01010002: "icon",
    0x0101021B: "version_code",
    0x0101021C: "version_name",
    0x0101020C: "min_sdk",
    0x01010270: "target_sdk",
}
NAME_TO_ID = {
    "label": 0x01010001, "icon": 0x01010002, "versionCode": 0x0101021B,
    "versionName": 0x0101021C, "minSdkVersion": 0x0101020C, "targetSdkVersion": 0x01010270,
}
# <intent-filter android:autoVerify>, <action|category android:name>, <data android:scheme|host>
LINK_ATTRS = {0x01010003: "name", 0x01010027: "scheme", 0x01010028: "host", 0x010104EE: "autoVerify",
              0x01010026: "mimeType"}
LINK_NAMES = {v: k for k, v in LINK_ATTRS.items()}
VIEW = "android.intent.action.VIEW"
BROWSABLE = "android.intent.category.BROWSABLE"
DEFAULT = "android.intent.category.DEFAULT"
SEND_ACTIONS = ("android.intent.action.SEND", "android.intent.action.SEND_MULTIPLE")
GMS_VERSION = "com.google.android.gms.version"     # meta-data of every app built with Google Play Services
GECKO_GPU = "org.mozilla.gecko.process.GeckoChildProcessServices$gpu"  # <service> of GeckoView's GPU process


@dataclass(frozen=True)
class ResRef:
    id: int


Value = str | ResRef


@dataclass(frozen=True)
class LinkFilter:
    """An activity's intent filter for links: action VIEW, category BROWSABLE (what a browser may start)."""
    schemes: tuple[str, ...]
    hosts: tuple[str, ...]
    auto_verify: bool


@dataclass(frozen=True)
class ManifestInfo:
    package: str | None
    version_code: int | None
    version_name: Value | None
    min_sdk: int | None
    target_sdk: int | None
    label: Value | None
    icon: int | None
    split: str | None = None        # <manifest split="config.x86_64">: a configuration part of a split APK
    link_filters: tuple[LinkFilter, ...] = ()
    share_types: tuple[str, ...] = ()   # MIME types of SEND/SEND_MULTIPLE filters in CATEGORY_DEFAULT
    permissions: tuple[str, ...] = ()   # <uses-permission android:name>
    uses_gms: bool = False              # <meta-data android:name="com.google.android.gms.version">: Play Services
    gecko_gpu: bool = False             # <service android:name="…GeckoChildProcessServices$gpu">: GeckoView (Firefox)


def parse_manifest(data: bytes) -> ManifestInfo:
    try:
        return _parse(data)
    except (struct.error, IndexError, UnicodeError) as e:
        raise ApkError(_("corrupt AndroidManifest.xml")) from e


def _parse(data: bytes) -> ManifestInfo:
    ctype, hsize, size = chunk_header(data, 0)
    if ctype != 0x0003:
        raise ApkError(_("corrupt AndroidManifest.xml"))
    strings: list[str] = []
    resmap: list[int] = []
    package = None
    split = None
    found: dict[tuple[str, str], object] = {}
    links: list[LinkFilter] = []
    share: list[str] = []
    permissions: list[str] = []
    uses_gms = False
    gecko_gpu = False
    in_activity = False
    flt: dict | None = None             # the <intent-filter> being read inside an <activity>
    pos = hsize
    end = size
    while pos + 8 <= end:
        t, hs, sz = chunk_header(data, pos)
        if t == 0x0001:
            strings = read_string_pool(data, pos)
        elif t == 0x0180:
            n = (sz - hs) // 4
            resmap = list(struct.unpack_from(f"<{n}I", data, pos + hs))
        elif t == 0x0102:
            tag = strings[struct.unpack_from("<I", data, pos + 20)[0]]
            astart, asize, acount = struct.unpack_from("<3H", data, pos + 24)
            if tag in ("activity", "activity-alias"):
                in_activity = True
            elif tag in ("uses-permission", "uses-permission-sdk-23"):
                name_ = _link_attrs(data, pos, astart, asize, acount, strings, resmap).get("name")
                if isinstance(name_, str) and name_ not in permissions:
                    permissions.append(name_)
            elif tag == "meta-data":
                name_ = _link_attrs(data, pos, astart, asize, acount, strings, resmap).get("name")
                uses_gms = uses_gms or name_ == GMS_VERSION
            elif tag == "service":
                name_ = _link_attrs(data, pos, astart, asize, acount, strings, resmap).get("name")
                gecko_gpu = gecko_gpu or name_ == GECKO_GPU
            elif tag in ("intent-filter", "action", "category", "data") and in_activity:
                attrs = _link_attrs(data, pos, astart, asize, acount, strings, resmap)
                if tag == "intent-filter":
                    flt = {"auto_verify": attrs.get("autoVerify") is True, "actions": set(), "categories": set(),
                           "schemes": [], "hosts": [], "types": []}
                elif flt is not None and tag == "action" and isinstance(attrs.get("name"), str):
                    flt["actions"].add(attrs["name"])
                elif flt is not None and tag == "category" and isinstance(attrs.get("name"), str):
                    flt["categories"].add(attrs["name"])
                elif flt is not None and tag == "data":
                    for key, out in (("scheme", "schemes"), ("host", "hosts"), ("mimeType", "types")):
                        v = attrs.get(key)
                        if isinstance(v, str) and v not in flt[out]:
                            flt[out].append(v)
            for k in range(acount):
                ao = pos + 16 + astart + asize * k
                ns, name, raw, _s, _r, dt, val = struct.unpack_from("<IIIHBBI", data, ao)
                aid = resmap[name] if name < len(resmap) else 0
                if aid not in ATTR_IDS:
                    aid = NAME_TO_ID.get(strings[name], 0) if name < len(strings) else 0
                if tag == "manifest" and ns == NONE and name < len(strings) \
                        and strings[name] in ("package", "split") and dt == 0x03 and raw != NONE:
                    if strings[name] == "package":
                        package = strings[raw]
                    else:
                        split = strings[raw]
                    continue
                field = ATTR_IDS.get(aid)
                if field is None:
                    continue
                if (tag, field) in found:
                    continue
                if dt == 0x03 and raw != NONE:
                    v: object = strings[raw]
                elif dt == 0x01:
                    v = ResRef(val)
                elif dt in (0x10, 0x11):
                    v = val
                else:
                    v = None
                found[(tag, field)] = v
        elif t == 0x0103:
            tag = strings[struct.unpack_from("<I", data, pos + 20)[0]]
            if tag in ("activity", "activity-alias"):
                in_activity, flt = False, None
            elif tag == "intent-filter" and flt is not None:
                if VIEW in flt["actions"] and BROWSABLE in flt["categories"] and flt["schemes"]:
                    links.append(LinkFilter(tuple(flt["schemes"]), tuple(flt["hosts"]), flt["auto_verify"]))
                if DEFAULT in flt["categories"] and flt["actions"] & set(SEND_ACTIONS):
                    share += [t for t in flt["types"] if t not in share]
                flt = None
        pos += sz

    def num(tag: str, field: str) -> int | None:
        v = found.get((tag, field))
        return v if isinstance(v, int) else None

    def val_(tag: str, field: str) -> Value | None:
        v = found.get((tag, field))
        return v if isinstance(v, (str, ResRef)) else None

    icon = found.get(("application", "icon"))
    return ManifestInfo(
        package=package,
        version_code=num("manifest", "version_code"),
        version_name=val_("manifest", "version_name"),
        min_sdk=num("uses-sdk", "min_sdk"),
        target_sdk=num("uses-sdk", "target_sdk"),
        label=val_("application", "label"),
        icon=icon.id if isinstance(icon, ResRef) else None,
        split=split,
        link_filters=tuple(links),
        share_types=tuple(share),
        permissions=tuple(permissions),
        uses_gms=uses_gms,
        gecko_gpu=gecko_gpu,
    )


def _link_attrs(data: bytes, pos: int, astart: int, asize: int, acount: int, strings: list[str],
                resmap: list[int]) -> dict[str, object]:
    """The link attributes of an element: literal strings, autoVerify as bool (by resource ID or name)."""
    out: dict[str, object] = {}
    for k in range(acount):
        ao = pos + 16 + astart + asize * k
        _ns, name, raw, _s, _r, dt, val = struct.unpack_from("<IIIHBBI", data, ao)
        key = LINK_ATTRS.get(resmap[name] if name < len(resmap) else 0)
        if key is None and name < len(strings) and strings[name] in LINK_NAMES:
            key = strings[name]
        if key is None:
            continue
        if dt == 0x03 and raw != NONE:
            out[key] = strings[raw]
        elif dt == 0x12:
            out[key] = val != 0
    return out
