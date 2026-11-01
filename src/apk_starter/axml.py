"""Binary AndroidManifest.xml reader (only the fields the starter needs)."""
import struct
from dataclasses import dataclass

from .errors import ApkError
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


@dataclass(frozen=True)
class ResRef:
    id: int


Value = str | ResRef


@dataclass(frozen=True)
class ManifestInfo:
    package: str | None
    version_code: int | None
    version_name: Value | None
    min_sdk: int | None
    target_sdk: int | None
    label: Value | None
    icon: int | None


def parse_manifest(data: bytes) -> ManifestInfo:
    try:
        return _parse(data)
    except (struct.error, IndexError, UnicodeError) as e:
        raise ApkError("corrupt AndroidManifest.xml") from e


def _parse(data: bytes) -> ManifestInfo:
    ctype, hsize, size = chunk_header(data, 0)
    if ctype != 0x0003:
        raise ApkError("corrupt AndroidManifest.xml")
    strings: list[str] = []
    resmap: list[int] = []
    package = None
    found: dict[tuple[str, str], object] = {}
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
            for k in range(acount):
                ao = pos + 16 + astart + asize * k
                ns, name, raw, _s, _r, dt, val = struct.unpack_from("<IIIHBBI", data, ao)
                aid = resmap[name] if name < len(resmap) else 0
                if aid not in ATTR_IDS:
                    aid = NAME_TO_ID.get(strings[name], 0) if name < len(strings) else 0
                if tag == "manifest" and ns == NONE and name < len(strings) \
                        and strings[name] == "package" and dt == 0x03 and raw != NONE:
                    package = strings[raw]
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
    )
