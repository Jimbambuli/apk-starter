"""The app's icon for its menu entry and the window: the largest square PNG of the launcher icon, looked up in the
base and in its configuration parts (a density part holds the bitmaps of an app from an app store); without one an
adaptive icon (mipmap-anydpi-v26) is drawn as SVG like a launcher draws it: background and foreground layer of
108 dp, the middle 72 dp shown in a rounded square. Layers: colours, bitmaps, vector drawables (paths, groups,
clip paths, gradients) and insets; the monochrome layer is left out (it is for themed icons)."""
import base64
import struct
import zipfile
import zlib
from collections.abc import Sequence

from .arsc import Config, ResourceTable
from .errors import ApkError
from .resbin import chunk_header, read_string_pool

MAX_ICON = 2 * 1024 * 1024
MAX_ICON_EDGE = 512
MAX_XML = 1024 * 1024
MAX_DEPTH = 6
NONE = 0xFFFFFFFF
ANYDPI, NODPI = 0xFFFE, 0xFFFF
BITMAP_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}

# android:attr IDs (frameworks/base core/res/res/values/public-final.xml); shrunk APKs have no attribute names
ATTRS = {0x01010199: "drawable", 0x01010405: "pathData", 0x01010404: "fillColor", 0x01010406: "strokeColor",
         0x01010407: "strokeWidth", 0x010104cc: "fillAlpha", 0x010104cb: "strokeAlpha", 0x0101051e: "fillType",
         0x01010159: "width", 0x01010155: "height", 0x01010402: "viewportWidth", 0x01010403: "viewportHeight",
         0x0101031f: "alpha", 0x01010326: "rotation", 0x010101b5: "pivotX", 0x010101b6: "pivotY",
         0x01010324: "scaleX", 0x01010325: "scaleY", 0x0101045a: "translateX", 0x0101045b: "translateY",
         0x010104b5: "inset", 0x010101b7: "insetLeft", 0x010101b8: "insetRight", 0x010101b9: "insetTop",
         0x010101ba: "insetBottom", 0x0101019d: "startColor", 0x0101019e: "endColor", 0x0101020b: "centerColor",
         0x010101a1: "type", 0x01010510: "startX", 0x01010511: "startY", 0x01010512: "endX", 0x01010513: "endY",
         0x010101a2: "centerX", 0x010101a3: "centerY", 0x010101a4: "gradientRadius", 0x01010514: "offset",
         0x010101a5: "color", 0x01010119: "src"}

Source = tuple[zipfile.ZipFile, ResourceTable | None]
Attr = tuple[int, int, str | None]          # data type, data, string
Node = tuple[str, dict[str, Attr], list]


def png_size(data: bytes) -> tuple[int, int] | None:
    if len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
        return None
    return struct.unpack(">II", data[16:24])


def _read(z: zipfile.ZipFile, name: str, limit: int) -> bytes | None:
    try:
        info = z.getinfo(name)
        return z.read(info) if info.file_size <= limit else None
    except (KeyError, zipfile.BadZipFile, zlib.error, OSError, RuntimeError, EOFError, ValueError):
        return None


def _values(sources: Sequence[Source], rid: int) -> list[tuple[zipfile.ZipFile, Config, int, int, str | None]]:
    out = []
    for z, table in sources:
        if table is not None:
            out += [(z, cfg, dt, data, s) for cfg, dt, data, s in table.resolve_values(rid)]
    return out


def app_icon(sources: Sequence[Source], icon_id: int | None) -> bytes | None:
    if icon_id is None:
        return None
    values = _values(sources, icon_id)
    best: tuple[int, bytes] | None = None
    for z, cfg, _dt, _d, path in values:
        if not path or not path.lower().endswith(".png") or cfg.density in (ANYDPI, NODPI):
            continue
        data = _read(z, path, MAX_ICON)
        size = png_size(data) if data else None
        if size is None or size[0] != size[1] or not 0 < size[0] <= MAX_ICON_EDGE:
            continue
        density = cfg.density or 160
        if best is None or density > best[0]:
            best = (density, data)
    if best:
        return best[1]
    for z, cfg, _dt, _d, path in sorted(values, key=lambda v: -v[1].sdk):
        if path and path.lower().endswith(".xml"):
            svg = _Renderer(sources).adaptive(z, path)
            if svg:
                return svg
    return None


# -- binary XML -----------------------------------------------------------------------------------------------------
def parse_xml(data: bytes) -> Node | None:
    """The element tree of a compiled XML file; attributes by their android name."""
    try:
        ctype, hsize, size = chunk_header(data, 0)
        if ctype != 0x0003:
            return None
        strings: list[str] = []
        resmap: list[int] = []
        stack: list[Node] = [("", {}, [])]
        pos = hsize
        while pos + 8 <= min(size, len(data)):
            t, hs, sz = chunk_header(data, pos)
            if sz < 8:
                return None
            if t == 0x0001:
                strings = read_string_pool(data, pos)
            elif t == 0x0180:
                resmap = list(struct.unpack_from(f"<{(sz - hs) // 4}I", data, pos + hs))
            elif t == 0x0102:
                tag = strings[struct.unpack_from("<I", data, pos + 20)[0]]
                astart, asize, acount = struct.unpack_from("<3H", data, pos + 24)
                attrs: dict[str, Attr] = {}
                for k in range(acount):
                    _ns, name, raw, _s, _r, dt, val = struct.unpack_from("<IIIHBBI", data, pos + 16 + astart + asize * k)
                    key = ATTRS.get(resmap[name]) if name < len(resmap) else None
                    if key is None and name < len(strings):
                        key = strings[name]
                    if key:
                        attrs[key] = (dt, val, strings[raw] if raw != NONE and raw < len(strings) else None)
                node: Node = (tag, attrs, [])
                stack[-1][2].append(node)
                stack.append(node)
                if len(stack) > 64:
                    return None
            elif t == 0x0103 and len(stack) > 1:
                stack.pop()
            pos += sz
        return stack[0][2][0] if stack[0][2] else None
    except (ApkError, struct.error, IndexError, UnicodeError, ValueError):
        return None


def _float(a: Attr | None, default: float = 0.0) -> float:
    if a is None:
        return default
    dt, val, s = a
    if dt == 0x04:
        return struct.unpack("<f", struct.pack("<I", val))[0]
    if dt in (0x05, 0x06):                  # complex: mantissa << 8 | radix << 4 | unit
        mant = (val & 0xFFFFFF00) - (1 << 32 if val & 0x80000000 else 0)
        f = mant * (1.0 / (1 << 8), 1.0 / (1 << 15), 1.0 / (1 << 31), 1.0 / (1 << 39))[(val >> 4) & 3]
        return f
    if dt in (0x10, 0x11):
        return float(val - (1 << 32) if val & 0x80000000 else val)
    if dt == 0x03 and s:
        try:
            return float(s.rstrip("dp%"))
        except ValueError:
            return default
    return default


def _num(x: float) -> str:
    return f"{x:.4g}" if abs(x) < 1e4 else f"{x:.0f}"


def _rgb(argb: int) -> tuple[str, float]:
    return f"#{argb & 0xFFFFFF:06x}", ((argb >> 24) & 0xFF) / 255


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace('"', "&quot;")


class _Renderer:
    def __init__(self, sources: Sequence[Source]):
        self.sources = sources
        self.defs: list[str] = []
        self.ids = 0

    def new_id(self, kind: str) -> str:
        self.ids += 1
        return f"{kind}{self.ids}"

    def _pick_file(self, rid: int) -> tuple[zipfile.ZipFile, str] | int | None:
        """A drawable resource: its colour (int ARGB) or the file to draw (XML first, else the largest bitmap)."""
        best = None
        for z, cfg, dt, data, path in _values(self.sources, rid):
            if 0x1C <= dt <= 0x1F:
                return data
            if not path or path not in z.NameToInfo:
                continue
            ext = path[path.rfind("."):].lower()
            rank = 10000 + cfg.sdk if ext == ".xml" else (cfg.density or 160) if ext in BITMAP_TYPES else -1
            if rank >= 0 and (best is None or rank > best[0]):
                best = (rank, z, path)
        return (best[1], best[2]) if best else None

    def adaptive(self, z: zipfile.ZipFile, path: str) -> bytes | None:
        root = parse_xml(_read(z, path, MAX_XML) or b"")
        if root is None or root[0] != "adaptive-icon":
            return None
        layers = []
        for tag in ("background", "foreground"):
            node = next((c for c in root[2] if c[0] == tag), None)
            if node is not None:
                layers.append(self.layer(node, 108, 108, 0))
        if not any(layers):
            return None
        mask = self.new_id("m")
        self.defs.insert(0, f'<clipPath id="{mask}"><rect x="18" y="18" width="72" height="72" rx="16"/></clipPath>')
        return ('<svg xmlns="http://www.w3.org/2000/svg" width="72" height="72" viewBox="18 18 72 72">'
                f'<defs>{"".join(self.defs)}</defs><g clip-path="url(#{mask})">{"".join(layers)}</g></svg>').encode()

    def layer(self, node: Node, w: float, h: float, depth: int) -> str:
        """The drawable of an element (android:drawable or its child element) in a w x h box."""
        a = node[1].get("drawable")
        if a is not None:
            return self.drawable(a, w, h, depth)
        return self.element(node[2][0], w, h, depth) if node[2] else ""

    def drawable(self, a: Attr, w: float, h: float, depth: int) -> str:
        dt, val, _s = a
        if 0x1C <= dt <= 0x1F:
            return self.rect(val, w, h)
        if dt != 0x01 or depth > MAX_DEPTH:
            return ""
        found = self._pick_file(val)
        if isinstance(found, int):
            return self.rect(found, w, h)
        if found is None:
            return ""
        z, path = found
        ext = path[path.rfind("."):].lower()
        if ext in BITMAP_TYPES:
            data = _read(z, path, MAX_ICON)
            if not data:
                return ""
            href = f"data:{BITMAP_TYPES[ext]};base64,{base64.b64encode(data).decode()}"
            return (f'<image width="{_num(w)}" height="{_num(h)}" preserveAspectRatio="none" href="{href}"/>')
        node = parse_xml(_read(z, path, MAX_XML) or b"")
        return self.element(node, w, h, depth + 1) if node else ""

    @staticmethod
    def rect(argb: int, w: float, h: float) -> str:
        color, alpha = _rgb(argb)
        return f'<rect width="{_num(w)}" height="{_num(h)}" fill="{color}"' + \
            (f' fill-opacity="{_num(alpha)}"' if alpha < 1 else "") + "/>"

    def element(self, node: Node, w: float, h: float, depth: int) -> str:
        tag, attrs, _children = node
        if tag == "vector":
            return self.vector(node, w, h)
        if tag == "inset":
            whole = attrs.get("inset")

            def side(name: str, size: float) -> float:
                a = attrs.get(name, whole)
                if a is None:
                    return 0.0
                return _float(a) * size if a[0] == 0x06 else _float(a)
            left, right, top, bottom = side("insetLeft", w), side("insetRight", w), side("insetTop", h), \
                side("insetBottom", h)
            inner = self.layer(node, max(0.0, w - left - right), max(0.0, h - top - bottom), depth + 1)
            return f'<g transform="translate({_num(left)} {_num(top)})">{inner}</g>' if inner else ""
        if tag == "color" and "color" in attrs:
            return self.drawable(attrs["color"], w, h, depth)
        if tag == "layer-list":
            return "".join(self.layer(item, w, h, depth + 1) for item in node[2] if item[0] == "item")
        if tag == "bitmap" and "src" in attrs:
            return self.drawable(attrs["src"], w, h, depth)
        return ""

    def vector(self, node: Node, w: float, h: float) -> str:
        attrs = node[1]
        vw = _float(attrs.get("viewportWidth"), 0) or _float(attrs.get("width"), w) or w
        vh = _float(attrs.get("viewportHeight"), 0) or _float(attrs.get("height"), h) or h
        alpha = _float(attrs.get("alpha"), 1.0)
        body = self.children(node[2], vw, vh)
        return (f'<svg width="{_num(w)}" height="{_num(h)}" viewBox="0 0 {_num(vw)} {_num(vh)}" '
                f'preserveAspectRatio="none"' + (f' opacity="{_num(alpha)}"' if alpha < 1 else "") + f">{body}</svg>")

    def children(self, nodes: list, vw: float, vh: float) -> str:
        out, open_clips = [], 0
        for tag, attrs, kids in nodes:
            if tag == "group":
                px, py = _float(attrs.get("pivotX")), _float(attrs.get("pivotY"))
                tx, ty = _float(attrs.get("translateX")), _float(attrs.get("translateY"))
                sx, sy = _float(attrs.get("scaleX"), 1.0), _float(attrs.get("scaleY"), 1.0)
                r = _float(attrs.get("rotation"))
                # like VectorDrawable's group matrix: pivot, scale, rotate, translate + pivot
                t = (f"translate({_num(tx + px)} {_num(ty + py)}) rotate({_num(r)}) scale({_num(sx)} {_num(sy)}) "
                     f"translate({_num(-px)} {_num(-py)})")
                out.append(f'<g transform="{t}">{self.children(kids, vw, vh)}</g>')
            elif tag == "clip-path" and attrs.get("pathData", (0, 0, None))[2]:
                cid = self.new_id("c")
                self.defs.append(f'<clipPath id="{cid}"><path d="{_esc(attrs["pathData"][2])}"/></clipPath>')
                out.append(f'<g clip-path="url(#{cid})">')     # clips what follows in this group
                open_clips += 1
            elif tag == "path" and attrs.get("pathData", (0, 0, None))[2]:
                out.append(self.path(attrs, kids, vw, vh))
        return "".join(out) + "</g>" * open_clips

    def path(self, attrs: dict[str, Attr], kids: list, vw: float, vh: float) -> str:
        parts = [f'<path d="{_esc(attrs["pathData"][2])}"']
        fill = self.paint(attrs.get("fillColor"), kids, "fillColor")
        parts.append(f' fill="{fill[0]}"' if fill else ' fill="none"')
        if fill:
            opacity = fill[1] * _float(attrs.get("fillAlpha"), 1.0)
            if opacity < 1:
                parts.append(f' fill-opacity="{_num(opacity)}"')
            if attrs.get("fillType", (0, 0, None))[1] == 1:
                parts.append(' fill-rule="evenodd"')
        stroke = self.paint(attrs.get("strokeColor"), kids, "strokeColor")
        if stroke and _float(attrs.get("strokeWidth")) > 0:
            parts.append(f' stroke="{stroke[0]}" stroke-width="{_num(_float(attrs.get("strokeWidth")))}"')
            opacity = stroke[1] * _float(attrs.get("strokeAlpha"), 1.0)
            if opacity < 1:
                parts.append(f' stroke-opacity="{_num(opacity)}"')
        return "".join(parts) + "/>"

    def paint(self, a: Attr | None, kids: list, name: str) -> tuple[str, float] | None:
        """A colour or a gradient («aapt:attr» puts it into an own file, referenced like a colour)."""
        if a is None:
            return None
        dt, val, _s = a
        if 0x1C <= dt <= 0x1F:
            color, alpha = _rgb(val)
            return (color, alpha) if alpha > 0 else None
        if dt != 0x01:
            return None
        found = self._pick_file(val)
        if isinstance(found, int):
            return self.paint((0x1C, found, None), kids, name)
        if found is None or not found[1].lower().endswith(".xml"):
            return None
        node = parse_xml(_read(found[0], found[1], MAX_XML) or b"")
        if node is None:
            return None
        if node[0] == "gradient":
            return self.gradient(node), 1.0
        if node[0] == "selector":               # a colour state list: its default (last) colour
            items = [i for i in node[2] if i[0] == "item" and "color" in i[1]]
            return self.paint(items[-1][1]["color"], [], name) if items else None
        return None

    def gradient(self, node: Node) -> str:
        attrs = node[1]
        stops = []
        for tag, a, _k in node[2]:
            if tag == "item" and "color" in a:
                stops.append((_float(a.get("offset")), a["color"]))
        if not stops:
            keys = [k for k in ("startColor", "centerColor", "endColor") if k in attrs]
            stops = [(i / max(1, len(keys) - 1), attrs[k]) for i, k in enumerate(keys)]
        gid = self.new_id("g")
        kind = attrs.get("type", (0, 0, None))[1]
        stop_xml = ""
        for offset, a in stops:
            argb = a[1] if 0x1C <= a[0] <= 0x1F else 0xFF000000
            color, alpha = _rgb(argb)
            stop_xml += f'<stop offset="{_num(offset)}" stop-color="{color}"' + \
                (f' stop-opacity="{_num(alpha)}"' if alpha < 1 else "") + "/>"
        if kind == 1:                           # radial
            self.defs.append(
                f'<radialGradient id="{gid}" gradientUnits="userSpaceOnUse" cx="{_num(_float(attrs.get("centerX")))}" '
                f'cy="{_num(_float(attrs.get("centerY")))}" r="{_num(_float(attrs.get("gradientRadius")))}">'
                f'{stop_xml}</radialGradient>')
        else:                                   # linear (sweep is drawn as linear)
            self.defs.append(
                f'<linearGradient id="{gid}" gradientUnits="userSpaceOnUse" x1="{_num(_float(attrs.get("startX")))}" '
                f'y1="{_num(_float(attrs.get("startY")))}" x2="{_num(_float(attrs.get("endX")))}" '
                f'y2="{_num(_float(attrs.get("endY")))}">{stop_xml}</linearGradient>')
        return f"url(#{gid})"
