"""Read package, label, version, SDKs and icon from an APK without unpacking it."""
import re
import struct
import sys
import zipfile
import zlib
from dataclasses import dataclass
from pathlib import Path

from .arsc import Config, ResourceTable, parse_table
from .axml import LinkFilter, ResRef, Value, parse_manifest
from .errors import ApkError
from .i18n import _

PACKAGE_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z][A-Za-z0-9_]*)+")
MAX_PACKAGE = 255
MAX_MANIFEST = 8 << 20
MAX_ARSC = 128 << 20
MAX_ICON = 2 << 20
MAX_ICON_EDGE = 512
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


@dataclass(frozen=True)
class ApkInfo:
    path: Path
    package: str
    label: str
    version_name: str | None
    version_code: int | None
    min_sdk: int | None
    target_sdk: int | None
    icon: bytes | None
    link_filters: tuple[LinkFilter, ...] = ()      # for the menu entry's MimeType (links opened in the app)


def png_size(data: bytes) -> tuple[int, int] | None:
    if len(data) < 24 or data[:8] != PNG_SIGNATURE or data[12:16] != b"IHDR":
        return None
    return struct.unpack(">II", data[16:24])


def _read(z: zipfile.ZipFile, name: str, limit: int) -> bytes:
    info = z.getinfo(name)
    if info.file_size > limit:
        raise ApkError(_("{file} is too large ({size} bytes)").format(file=name, size=info.file_size))
    return z.read(info)


def _pick(values: list[tuple[Config, str]], lang: str | None) -> str | None:
    for want in ((lang,) if lang else ()) + ("",):
        for cfg, v in values:
            if cfg.language == want:
                return v
    return values[0][1] if values else None


def _text(value: Value | None, table: ResourceTable | None, lang: str | None) -> str | None:
    if isinstance(value, str):
        return value
    if isinstance(value, ResRef) and table is not None:
        return _pick(table.resolve_strings(value.id), lang)
    return None


def _icon(z: zipfile.ZipFile, table: ResourceTable | None, icon_id: int | None) -> bytes | None:
    if table is None or icon_id is None:
        return None
    best: tuple[int, bytes] | None = None
    for cfg, path in table.resolve_strings(icon_id):
        if not path.lower().endswith(".png") or cfg.density in (0xFFFE, 0xFFFF):
            continue
        try:
            data = _read(z, path, MAX_ICON)
        except (KeyError, ApkError, zipfile.BadZipFile, zlib.error, EOFError, OSError, RuntimeError):
            continue
        size = png_size(data)
        if size is None or size[0] != size[1] or not 0 < size[0] <= MAX_ICON_EDGE:
            continue
        density = cfg.density or 160
        if best is None or density > best[0]:
            best = (density, data)
    return best[1] if best else None


def read_apk_info(path: Path, lang: str | None = None) -> ApkInfo:
    path = Path(path)
    if path.suffix.lower() in (".apks", ".xapk"):
        raise ApkError(_("split APK bundles (.apks/.xapk) are not supported yet: {file}").format(file=path))
    try:
        with zipfile.ZipFile(path) as z:
            try:
                raw = _read(z, "AndroidManifest.xml", MAX_MANIFEST)
            except KeyError:
                raise ApkError(_("not an APK (no AndroidManifest.xml): {file}").format(file=path)) from None
            m = parse_manifest(raw)
            pkg = m.package
            if pkg is None or len(pkg) > MAX_PACKAGE or not PACKAGE_RE.fullmatch(pkg):
                raise ApkError(_("invalid package name in manifest: {package}").format(package=repr(pkg)))
            table = None
            try:
                table = parse_table(_read(z, "resources.arsc", MAX_ARSC))
            except KeyError:
                pass
            return ApkInfo(
                path=path,
                package=pkg,
                label=_text(m.label, table, lang) or pkg,
                version_name=_text(m.version_name, table, lang),
                version_code=m.version_code,
                min_sdk=m.min_sdk,
                target_sdk=m.target_sdk,
                icon=_icon(z, table, m.icon),
                link_filters=m.link_filters,
            )
    except zipfile.BadZipFile:
        raise ApkError(_("not an APK (no AndroidManifest.xml): {file}").format(file=path)) from None
    except (zipfile.LargeZipFile, RuntimeError, EOFError, zlib.error, ValueError, struct.error) as e:
        raise ApkError(_("cannot read {file}: {reason}").format(file=path, reason=e)) from None
    except OSError as e:
        raise ApkError(_("cannot read {file}: {reason}").format(file=path, reason=e.strerror)) from None


def main(argv: list[str]) -> int:
    for name in argv:
        try:
            i = read_apk_info(Path(name))
            print("\t".join([i.package, i.label, str(i.version_name or ""), str(i.version_code if i.version_code is not None else ""),
                             str(i.min_sdk or ""), str(i.target_sdk or ""), str(len(i.icon) if i.icon else 0)]))
        except ApkError as e:
            print(f"{name}\tERROR\t{e}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
