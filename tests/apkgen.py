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
                  utf8=True, strip_names=False, split: str | None = None,
                  activities: list | None = None, permissions: list | None = None,
                  meta_data: list | None = None) -> bytes:
    """activities: one list of intent filters per <activity>; a filter is a dict with "actions", "categories",
    "data" (list of {attribute: value}) and optionally "autoVerify": True. Their attribute names are plain
    strings (with strip_names=True empty, found by resource ID like in shrunk APKs). meta_data: android:name of
    <meta-data> elements in <application>."""
    ids = {"label": 0x01010001, "icon": 0x01010002, "versionCode": 0x0101021B,
           "versionName": 0x0101021C, "minSdkVersion": 0x0101020C, "targetSdkVersion": 0x01010270}
    attr_names = ["versionCode", "versionName", "minSdkVersion", "targetSdkVersion", "label"]
    if icon is not None:
        attr_names.append("icon")
    if activities is not None or permissions or meta_data:
        ids.update({"name": 0x01010003, "scheme": 0x01010027, "host": 0x01010028, "pathPrefix": 0x0101002b,
                    "autoVerify": 0x010104ee, "mimeType": 0x01010026})
        attr_names += ["name", "scheme", "host", "pathPrefix", "autoVerify", "mimeType"]
    strings = [("" if strip_names else n) for n in attr_names]  # resource-mapped names come first
    resmap = [ids[n] for n in attr_names]
    idx = {n: i for i, n in enumerate(attr_names)}

    def add(s: str) -> int:
        strings.append(s)
        return len(strings) - 1

    ns = add(NS_URI)
    n_pkg, n_manifest, n_sdk, n_app = add("package"), add("manifest"), add("uses-sdk"), add("application")
    v_pkg, v_vn = add(package), add(version_name)
    n_split, v_split = (add("split"), add(split)) if split else (None, None)
    v_label = add(label) if isinstance(label, str) else None
    A = ns
    manifest_attrs = [
        (0xFFFFFFFF, n_pkg, v_pkg, 0x03, v_pkg),
        (A, idx["versionCode"], 0xFFFFFFFF, 0x10, version_code),
        (A, idx["versionName"], v_vn, 0x03, v_vn),
    ]
    if split:
        manifest_attrs.append((0xFFFFFFFF, n_split, v_split, 0x03, v_split))
    sdk_attrs = [(A, idx["minSdkVersion"], 0xFFFFFFFF, 0x10, min_sdk),
                 (A, idx["targetSdkVersion"], 0xFFFFFFFF, 0x10, target_sdk)]
    app_attrs = [(A, idx["label"], v_label, 0x03, v_label) if v_label is not None
                 else (A, idx["label"], 0xFFFFFFFF, 0x01, label)]
    if icon is not None:
        app_attrs.append((A, idx["icon"], 0xFFFFFFFF, 0x01, icon))
    inner = b""
    perms = b""
    for perm in permissions or []:
        n_p, v_p = add("uses-permission"), add(perm)
        perms += _start(n_p, [(A, idx["name"], v_p, 0x03, v_p)]) + _end(n_p)
    for name in meta_data or []:
        n_m, v_m = add("meta-data"), add(name)
        inner += _start(n_m, [(A, idx["name"], v_m, 0x03, v_m)]) + _end(n_m)
    for filters in activities or []:
        n_act = add("activity")
        inner += _start(n_act, [(A, idx["name"], add(".Main"), 0x03, len(strings) - 1)])
        for f in filters:
            n_f = add("intent-filter")
            fa = [(A, idx["autoVerify"], 0xFFFFFFFF, 0x12, 0xFFFFFFFF)] if f.get("autoVerify") else []
            inner += _start(n_f, fa)
            for tag, key in (("action", "actions"), ("category", "categories")):
                for v in f.get(key, []):
                    n_t, v_i = add(tag), add(v)
                    inner += _start(n_t, [(A, idx["name"], v_i, 0x03, v_i)]) + _end(n_t)
            for d in f.get("data", []):
                n_d = add("data")
                attrs = []
                for k, v in d.items():
                    v_i = add(v)
                    attrs.append((A, idx[k], v_i, 0x03, v_i))
                inner += _start(n_d, attrs) + _end(n_d)
            inner += _end(n_f)
        inner += _end(n_act)
    body = (_chunk(0x0100, 16, struct.pack("<IIII", 1, 0xFFFFFFFF, 0xFFFFFFFF, ns))
            + _start(n_manifest, manifest_attrs)
            + _start(n_sdk, sdk_attrs) + _end(n_sdk) + perms
            + _start(n_app, app_attrs) + inner + _end(n_app)
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
        if isinstance(v, tuple):                    # a typed value (dataType, data), e.g. (0x1C, 0xFF3366CC)
            return v
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


def _lp(b: bytes) -> bytes:
    return struct.pack("<I", len(b)) + b


def _der(tag: int, content: bytes) -> bytes:
    n = len(content)
    head = bytes([n]) if n < 0x80 else bytes([0x82]) + n.to_bytes(2, "big")
    return bytes([tag]) + head + content


def fake_cert(name: str) -> bytes:
    """A DER SEQUENCE standing for a certificate (signers are compared by the hash of its bytes)."""
    return _der(0x30, _der(0x0C, name.encode()))


def sign_block(path: Path, cert: bytes, scheme: int = 2) -> Path:
    """Put an APK signing block with one signer (scheme 2 or 3) before the central directory. Only the layout:
    the certificate is what the installed app is compared by, the signature is not real."""
    data = path.read_bytes()
    eocd = data.rfind(b"PK\x05\x06")
    cd = struct.unpack_from("<I", data, eocd + 16)[0]
    signed = _lp(b"") + _lp(_lp(cert)) + (struct.pack("<II", 24, 0x7FFFFFFF) if scheme == 3 else b"") + _lp(b"")
    signer = _lp(signed) + _lp(b"") + _lp(b"key")
    value = _lp(_lp(signer))
    pid = {2: 0x7109871A, 3: 0xF05368C0}[scheme]
    pair = struct.pack("<QI", len(value) + 4, pid) + value
    size = len(pair) + 8 + 16
    block = struct.pack("<Q", size) + pair + struct.pack("<Q", size) + b"APK Sig Block 42"
    out = bytearray(data[:cd] + block + data[cd:])
    struct.pack_into("<I", out, eocd + len(block) + 16, cd + len(block))
    path.write_bytes(bytes(out))
    return path


def pkcs7(cert: bytes) -> bytes:
    """META-INF/CERT.RSA of a v1 (JAR) signature: ContentInfo { signedData, SignedData { ..., [0] certs } }."""
    signed_data = _der(0x30, _der(0x02, b"\x01") + _der(0x31, b"") + _der(0x30, _der(0x06, b"\x2a\x86\x48\x86\xf7\x0d\x01\x07\x01"))
                       + _der(0xA0, cert) + _der(0x31, b""))
    return _der(0x30, _der(0x06, b"\x2a\x86\x48\x86\xf7\x0d\x01\x07\x02") + _der(0xA0, signed_data))


ANDROID_ATTRS = {"drawable": 0x01010199, "pathData": 0x01010405, "fillColor": 0x01010404, "strokeColor": 0x01010406,
                 "strokeWidth": 0x01010407, "fillAlpha": 0x010104cc, "fillType": 0x0101051e, "width": 0x01010159,
                 "height": 0x01010155, "viewportWidth": 0x01010402, "viewportHeight": 0x01010403,
                 "rotation": 0x01010326, "pivotX": 0x010101b5, "pivotY": 0x010101b6, "scaleX": 0x01010324,
                 "scaleY": 0x01010325, "translateX": 0x0101045a, "translateY": 0x0101045b, "inset": 0x010104b5}


def axml_doc(root: tuple) -> bytes:
    """Binary XML of a tree (tag, {android attribute: value}, [children]). A value is a str, ("ref", id),
    ("color", 0xAARRGGBB), ("float", f) or ("dimen", dp) - as aapt2 writes them."""
    names = list(ANDROID_ATTRS)
    strings = list(names)

    def add(x: str) -> int:
        strings.append(x)
        return len(strings) - 1
    ns = add(NS_URI)

    def element(node) -> bytes:
        tag, attrs, children = node
        n = add(tag)
        out = []
        for k, v in attrs.items():
            i = names.index(k)
            if isinstance(v, str):
                r = add(v)
                out.append((ns, i, r, 0x03, r))
            elif v[0] == "ref":
                out.append((ns, i, 0xFFFFFFFF, 0x01, v[1]))
            elif v[0] == "color":
                out.append((ns, i, 0xFFFFFFFF, 0x1C, v[1]))
            elif v[0] == "float":
                out.append((ns, i, 0xFFFFFFFF, 0x04, struct.unpack("<I", struct.pack("<f", v[1]))[0]))
            elif v[0] == "dimen":
                out.append((ns, i, 0xFFFFFFFF, 0x05, (int(v[1]) << 8) | 0x01))   # radix 23p0, unit dp
            elif v[0] == "fraction":
                out.append((ns, i, 0xFFFFFFFF, 0x06, (int(v[1] * (1 << 23)) << 8) & 0xFFFFFF00 | 0x30))
        return _start(n, out) + b"".join(element(c) for c in children) + _end(n)
    body = _chunk(0x0100, 16, struct.pack("<IIII", 1, 0xFFFFFFFF, 0xFFFFFFFF, ns)) + element(root)
    pool = string_pool(strings, True)
    rm = _chunk(0x0180, 8, b"".join(struct.pack("<I", ANDROID_ATTRS[n]) for n in names))
    payload = pool + rm + body
    return struct.pack("<HHI", 0x0003, 8, 8 + len(payload)) + payload
