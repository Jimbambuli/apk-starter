"""Builders for synthetic binary AndroidManifest/resources.arsc/PNG/APK test data."""
import struct

NS_URI = "http://schemas.android.com/apk/res/android"


def _varlen8(n: int) -> bytes:
    return bytes([n]) if n < 0x80 else bytes([0x80 | (n >> 8), n & 0xFF])


def string_pool(strings: list[str], utf8: bool = True) -> bytes:
    blobs = []
    for s in strings:
        if utf8:
            raw = s.encode("utf-8")
            n16 = len(s.encode("utf-16le")) // 2
            blobs.append(_varlen8(n16) + _varlen8(len(raw)) + raw + b"\0")
        else:
            n = len(s.encode("utf-16le")) // 2
            head = struct.pack("<H", n) if n < 0x8000 else struct.pack("<HH", 0x8000 | (n >> 16), n & 0xFFFF)
            blobs.append(head + s.encode("utf-16le") + b"\0\0")
    offsets, body = [], b""
    for b in blobs:
        offsets.append(len(body))
        body += b
    body += b"\0" * (-len(body) % 4)
    start = 28 + 4 * len(strings)
    size = start + len(body)
    return (struct.pack("<HHIIIIII", 0x0001, 28, size, len(strings), 0, 0x100 if utf8 else 0, start, 0)
            + b"".join(struct.pack("<I", o) for o in offsets) + body)


def _chunk(ctype: int, hsize: int, payload: bytes) -> bytes:
    return struct.pack("<HHI", ctype, hsize, 8 + len(payload)) + payload


def _start(name: int, attrs: list[tuple[int, int, int, int, int]]) -> bytes:
    """attrs: (ns, name, raw, dataType, data)."""
    ext = struct.pack("<IIHHHHHH", 0xFFFFFFFF, name, 20, 20, len(attrs), 0, 0, 0)
    for ns, an, raw, dt, data in attrs:
        ext += struct.pack("<IIIHBBI", ns, an, raw, 8, 0, dt, data)
    return _chunk(0x0102, 16, struct.pack("<II", 1, 0xFFFFFFFF) + ext)


def _end(name: int) -> bytes:
    return _chunk(0x0103, 16, struct.pack("<IIII", 1, 0xFFFFFFFF, 0xFFFFFFFF, name))


def axml_manifest(*, package="org.example.demo", version_code=7, version_name="1.2", min_sdk=21,
                  target_sdk=34, label: str | int = "Demo", icon: int | None = None,
                  utf8=True, strip_names=False) -> bytes:
    ids = {"label": 0x01010001, "icon": 0x01010002, "versionCode": 0x0101021B,
           "versionName": 0x0101021C, "minSdkVersion": 0x0101020C, "targetSdkVersion": 0x01010270}
    attr_names = ["versionCode", "versionName", "minSdkVersion", "targetSdkVersion", "label"]
    if icon is not None:
        attr_names.append("icon")
    strings = [("" if strip_names else n) for n in attr_names]  # resource-mapped names come first
    resmap = [ids[n] for n in attr_names]
    idx = {n: i for i, n in enumerate(attr_names)}

    def add(s: str) -> int:
        strings.append(s)
        return len(strings) - 1

    ns = add(NS_URI)
    n_pkg, n_manifest, n_sdk, n_app = add("package"), add("manifest"), add("uses-sdk"), add("application")
    v_pkg, v_vn = add(package), add(version_name)
    v_label = add(label) if isinstance(label, str) else None
    A = ns
    manifest_attrs = [
        (0xFFFFFFFF, n_pkg, v_pkg, 0x03, v_pkg),
        (A, idx["versionCode"], 0xFFFFFFFF, 0x10, version_code),
        (A, idx["versionName"], v_vn, 0x03, v_vn),
    ]
    sdk_attrs = [(A, idx["minSdkVersion"], 0xFFFFFFFF, 0x10, min_sdk),
                 (A, idx["targetSdkVersion"], 0xFFFFFFFF, 0x10, target_sdk)]
    app_attrs = [(A, idx["label"], v_label, 0x03, v_label) if v_label is not None
                 else (A, idx["label"], 0xFFFFFFFF, 0x01, label)]
    if icon is not None:
        app_attrs.append((A, idx["icon"], 0xFFFFFFFF, 0x01, icon))
    body = (_chunk(0x0100, 16, struct.pack("<IIII", 1, 0xFFFFFFFF, 0xFFFFFFFF, ns))
            + _start(n_manifest, manifest_attrs)
            + _start(n_sdk, sdk_attrs) + _end(n_sdk)
            + _start(n_app, app_attrs) + _end(n_app)
            + _end(n_manifest))
    pool = string_pool(strings, utf8)
    rm = _chunk(0x0180, 8, b"".join(struct.pack("<I", r) for r in resmap))
    payload = pool + rm + body
    return struct.pack("<HHI", 0x0003, 8, 8 + len(payload)) + payload
