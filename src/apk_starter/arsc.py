"""resources.arsc reader: resolve a resource id to its string values per configuration."""
import struct
from dataclasses import dataclass

from .errors import ApkError
from .i18n import _
from .resbin import chunk_header, read_string_pool

MAX_DEPTH = 8
MAX_STEPS = 10000
MAX_ENTRIES = 2_000_000


@dataclass(frozen=True)
class Config:
    language: str
    density: int
    sdk: int


class ResourceTable:
    def __init__(self, strings: list[str], entries: dict[int, list[tuple[Config, int, int]]]):
        self._strings = strings
        self._entries = entries

    def resolve_strings(self, res_id: int) -> list[tuple[Config, str]]:
        steps = [0]
        return self._resolve(res_id, 0, steps)

    def resolve_values(self, res_id: int) -> list[tuple[Config, int, int, str | None]]:
        """Every value of the resource after following references: (config, data type, data, string or None)."""
        out: list[tuple[Config, int, int, str | None]] = []
        todo, seen = [res_id], set()
        while todo and len(seen) < MAX_STEPS:
            rid = todo.pop(0)
            if rid in seen:
                continue
            seen.add(rid)
            for cfg, dtype, data in self._entries.get(rid, []):
                if dtype == 0x01:
                    todo.append(data)
                else:
                    out.append((cfg, dtype, data,
                                self._strings[data] if dtype == 0x03 and data < len(self._strings) else None))
        return out

    def _resolve(self, res_id: int, depth: int, steps: list[int]) -> list[tuple[Config, str]]:
        if depth > MAX_DEPTH:
            raise ApkError(_("resource reference loop"))
        out: list[tuple[Config, str]] = []
        for cfg, dtype, data in self._entries.get(res_id, []):
            steps[0] += 1
            if steps[0] > MAX_STEPS:
                raise ApkError(_("resource reference loop"))
            if dtype == 0x03 and data < len(self._strings):
                out.append((cfg, self._strings[data]))
            elif dtype == 0x01:
                out.extend(self._resolve(data, depth + 1, steps))
        return out


def _config(data: bytes, pos: int) -> Config:
    size = struct.unpack_from("<I", data, pos)[0]
    lang = ""
    if size >= 10:
        raw = data[pos + 8:pos + 10]
        if len(raw) == 2 and all(0x61 <= b <= 0x7A or 0x41 <= b <= 0x5A for b in raw):
            lang = raw.decode("ascii")
    density = struct.unpack_from("<H", data, pos + 14)[0] if size >= 16 else 0
    sdk = struct.unpack_from("<H", data, pos + 24)[0] if size >= 26 else 0
    return Config(lang, density, sdk)


def parse_table(data: bytes) -> ResourceTable:
    try:
        return _parse(data)
    except (struct.error, IndexError) as e:
        raise ApkError(_("corrupt resources.arsc")) from e


def _parse(data: bytes) -> ResourceTable:
    ctype, hsize, size = chunk_header(data, 0)
    if ctype != 0x0002:
        raise ApkError(_("corrupt resources.arsc"))
    strings: list[str] = []
    entries: dict[int, list[tuple[Config, int, int]]] = {}
    count = [0]
    pos = hsize
    while pos + 8 <= size:
        t, hs, sz = chunk_header(data, pos)
        if t == 0x0001 and not strings:
            strings = read_string_pool(data, pos)
        elif t == 0x0200:
            _package(data, pos, hs, sz, entries, count)
        pos += sz
    return ResourceTable(strings, entries)


def _package(data: bytes, pos: int, hs: int, sz: int, entries, count) -> None:
    pkg_id = struct.unpack_from("<I", data, pos + 8)[0]
    p = pos + hs
    end = pos + sz
    while p + 8 <= end:
        t, ths, tsz = chunk_header(data, p)
        if p + tsz > end:
            raise ApkError(_("corrupt resources.arsc"))
        if t == 0x0201:
            _type(data, p, ths, tsz, pkg_id, entries, count)
        p += tsz


def _type(data: bytes, pos: int, hs: int, sz: int, pkg_id: int, entries, count) -> None:
    type_id, flags = struct.unpack_from("<BB", data, pos + 8)
    n, estart = struct.unpack_from("<II", data, pos + 12)
    end = pos + sz
    cfg = _config(data, pos + 20)
    base = pos + hs
    if estart > sz or estart < hs:
        raise ApkError(_("corrupt resources.arsc"))
    width = 2 if flags & 0x02 and not flags & 0x01 else 4
    if base + n * width > end:
        raise ApkError(_("corrupt resources.arsc"))
    if flags & 0x01:       # sparse: (idx u16, off/4 u16)
        offs = [(i, o * 4) for i, o in (struct.unpack_from("<HH", data, base + 4 * k) for k in range(n))]
    elif flags & 0x02:     # offset16
        vals = struct.unpack_from(f"<{n}H", data, base)
        offs = [(i, o * 4) for i, o in enumerate(vals) if o != 0xFFFF]
    else:                  # dense
        vals = struct.unpack_from(f"<{n}I", data, base)
        offs = [(i, o) for i, o in enumerate(vals) if o != 0xFFFFFFFF]
    count[0] += len(offs)
    if count[0] > MAX_ENTRIES:
        raise ApkError(_("corrupt resources.arsc"))
    for idx, off in offs:
        e = pos + estart + off
        if e + 8 > end:
            raise ApkError(_("corrupt resources.arsc"))
        esize, eflags = struct.unpack_from("<HH", data, e)
        if eflags & 0x08:              # compact
            dtype, val = eflags >> 8, struct.unpack_from("<I", data, e + 4)[0]
        elif eflags & 0x01:            # map entry
            continue
        else:
            if e + esize + 8 > end:
                raise ApkError(_("corrupt resources.arsc"))
            dtype = data[e + esize + 3]
            val = struct.unpack_from("<I", data, e + esize + 4)[0]
        rid = (pkg_id << 24) | (type_id << 16) | idx
        entries.setdefault(rid, []).append((cfg, dtype, val))
