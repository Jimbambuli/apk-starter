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


# ---------------------------------------------------------------- resources.arsc
from dataclasses import dataclass  # noqa: E402

DENSITY = {"ldpi": 120, "mdpi": 160, "hdpi": 240, "xhdpi": 320, "xxhdpi": 480, "xxxhdpi": 640, "anydpi": 0xFFFE}


@dataclass
class Res:
    type_name: str
    key: str
    configs: dict  # qualifier ("", "de", "xxhdpi", "anydpi-v26") -> str | int (int = reference)


def _config_bytes(qualifier: str) -> bytes:
    lang, density, sdk = b"\0\0", 0, 0
    for part in [p for p in qualifier.split("-") if p]:
        if part in DENSITY:
            density = DENSITY[part]
        elif part.startswith("v") and part[1:].isdigit():
            sdk = int(part[1:])
        elif len(part) == 2 and part.isalpha():
            lang = part.encode("ascii")
    cfg = bytearray(28)
    struct.pack_into("<I", cfg, 0, 28)
    cfg[8:10] = lang
    struct.pack_into("<H", cfg, 14, density)
    struct.pack_into("<H", cfg, 24, sdk)
    return bytes(cfg)


def arsc_table(package: str, res: list[Res], *, utf8=True, mode: str = "dense") -> tuple[bytes, dict[str, int]]:
    types: dict[str, list[Res]] = {}
    for r in res:
        types.setdefault(r.type_name, []).append(r)
    ids = {}
    for tno, (tname, items) in enumerate(types.items(), start=1):
        for eno, r in enumerate(items):
            ids[f"{tname}/{r.key}"] = (0x7F << 24) | (tno << 16) | eno
    values: list[str] = []

    def value(v):
        if isinstance(v, int):
            return 0x01, v
        if v.startswith("@"):
            return 0x01, ids[v[1:]]
        if v not in values:
            values.append(v)
        return 0x03, values.index(v)

    keys = [r.key for r in res]
    body = b""
    for tno, (tname, items) in enumerate(types.items(), start=1):
        n = len(items)
        body += _chunk(0x0202, 16, struct.pack("<BBHI", tno, 0, 0, n) + b"\0\0\0\0" * n)
        quals = []
        for r in items:
            for q in r.configs:
                if q not in quals:
                    quals.append(q)
        for q in quals:
            entry_blobs, offs = b"", {}
            for i, r in enumerate(items):
                if q not in r.configs:
                    continue
                dt, data = value(r.configs[q])
                offs[i] = len(entry_blobs)
                if mode == "compact":
                    entry_blobs += struct.pack("<HHI", i, 0x08 | (dt << 8), data)
                else:
                    entry_blobs += struct.pack("<HHIHBBI", 8, 0, keys.index(r.key), 8, 0, dt, data)
            if mode == "sparse":
                flags = 0x01
                arr = b"".join(struct.pack("<HH", i, o // 4) for i, o in offs.items())
                count = len(offs)
            elif mode == "offset16":
                flags = 0x02
                arr = b"".join(struct.pack("<H", offs[i] // 4 if i in offs else 0xFFFF) for i in range(n))
                arr += b"\0" * (-len(arr) % 4)
                count = n
            else:
                flags = 0
                arr = b"".join(struct.pack("<I", offs.get(i, 0xFFFFFFFF)) for i in range(n))
                count = n
            cfg = _config_bytes(q)
            hs = 20 + len(cfg)
            payload = struct.pack("<BBHII", tno, flags, 0, count, hs + len(arr)) + cfg + arr + entry_blobs
            body += struct.pack("<HHI", 0x0201, hs, 8 + len(payload)) + payload
    type_pool = string_pool(list(types), utf8)
    key_pool = string_pool(keys, utf8)
    name = package.encode("utf-16le")[:254].ljust(256, b"\0")
    hs = 288
    pkg_payload = struct.pack("<I", 0x7F) + name + struct.pack("<IIIII", hs, 0, hs + len(type_pool), 0, 0)
    pkg = struct.pack("<HHI", 0x0200, hs, hs + len(type_pool) + len(key_pool) + len(body)) + pkg_payload \
        + type_pool + key_pool + body
    gpool = string_pool(values, utf8)
    table = struct.pack("<HHII", 0x0002, 12, 12 + len(gpool) + len(pkg), 1) + gpool + pkg
    return table, ids


# ---------------------------------------------------------------- PNG / APK
import zlib  # noqa: E402
import zipfile  # noqa: E402
from pathlib import Path  # noqa: E402


def png_bytes(w: int, h: int) -> bytes:
    def chunk(tag: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + tag + body + struct.pack(">I", zlib.crc32(tag + body))
    raw = b"".join(b"\0" + b"\x20\x40\x80\xff" * w for _ in range(h))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def make_apk(path: Path, *, manifest: bytes, arsc: bytes | None = None, files: dict | None = None) -> Path:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("AndroidManifest.xml", manifest)
        if arsc is not None:
            z.writestr("resources.arsc", arsc)
        for name, data in (files or {}).items():
            z.writestr(name, data)
    return path
