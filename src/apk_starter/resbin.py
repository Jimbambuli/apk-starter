"""Shared pieces of Android binary resources: chunk header and string pool."""
import struct

from .errors import ApkError
from .i18n import _

UTF8_FLAG = 0x100


def chunk_header(data: bytes, pos: int) -> tuple[int, int, int]:
    """Return (type, header_size, size) of the chunk at pos, bounds-checked."""
    if pos < 0 or pos + 8 > len(data):
        raise ApkError(_("corrupt resource chunk at {offset}").format(offset=pos))
    ctype, hsize, size = struct.unpack_from("<HHI", data, pos)
    if size < 8 or hsize < 8 or hsize > size or pos + size > len(data):
        raise ApkError(_("corrupt resource chunk at {offset}").format(offset=pos))
    return ctype, hsize, size


def read_string_pool(data: bytes, pos: int) -> list[str]:
    try:
        ctype, hsize, size = chunk_header(data, pos)
        if ctype != 0x0001:
            raise ApkError(_("corrupt resource chunk at {offset}").format(offset=pos))
        count, _styles, flags, start = struct.unpack_from("<4I", data, pos + 8)
        end = pos + size
        offsets = struct.unpack_from(f"<{count}I", data, pos + hsize)
        base = pos + start
        utf8 = bool(flags & UTF8_FLAG)
        out = []
        for off in offsets:
            o = base + off
            if o >= end:
                raise ApkError(_("corrupt resource chunk at {offset}").format(offset=pos))
            if utf8:
                n = data[o]
                o += 1
                if n & 0x80:
                    o += 1
                n = data[o]
                o += 1
                if n & 0x80:
                    n = ((n & 0x7F) << 8) | data[o]
                    o += 1
                raw = data[o:o + n]
                out.append(raw.decode("utf-8", errors="replace"))
            else:
                n = struct.unpack_from("<H", data, o)[0]
                o += 2
                if n & 0x8000:
                    n = ((n & 0x7FFF) << 16) | struct.unpack_from("<H", data, o)[0]
                    o += 2
                if o + 2 * n > end:
                    raise ApkError(_("corrupt resource chunk at {offset}").format(offset=pos))
                out.append(data[o:o + 2 * n].decode("utf-16le", errors="replace"))
        return out
    except (struct.error, IndexError, MemoryError) as e:
        raise ApkError(_("corrupt resource chunk at {offset}").format(offset=pos)) from e
