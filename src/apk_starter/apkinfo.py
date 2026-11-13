"""Read package, label, version, SDKs and icon from an APK without unpacking it."""
import contextlib
import re
import struct
import sys
import zipfile
import zlib
from dataclasses import dataclass
from pathlib import Path

from .arsc import Config, ResourceTable, parse_table
from .axml import LinkFilter, ResRef, Value, parse_manifest
from .errors import ApkError, StarterError
from .icons import png_size  # noqa: F401  (part of this module's interface)
from . import icons
from .i18n import _

PACKAGE_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z][A-Za-z0-9_]*)+")
MAX_PACKAGE = 255
MAX_MANIFEST = 8 << 20
MAX_ARSC = 128 << 20


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
    share_types: tuple[str, ...] = ()               # likewise: files it takes shared (ACTION_SEND)
    permissions: tuple[str, ...] = ()               # <uses-permission>, for the switches in the window
    uses_gms: bool = False                          # built with Google Play Services (microG offered)
    gecko_gpu: bool = False                         # GeckoView app with a GPU process (Firefox): gecko.py


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


def read_icon(base: Path, parts: list[Path]) -> bytes | None:
    """The icon looked up in the base and its configuration parts (an app store puts the bitmaps into density
    parts); None if there is none or a file cannot be read."""
    try:
        with contextlib.ExitStack() as stack:
            sources = []
            icon_id = None
            for i, p in enumerate([base, *parts]):
                z = stack.enter_context(zipfile.ZipFile(p))
                if i == 0:
                    icon_id = parse_manifest(_read(z, "AndroidManifest.xml", MAX_MANIFEST)).icon
                sources.append((z, _table(z)))
            return icons.app_icon(sources, icon_id)
    except (StarterError, KeyError, OSError, zipfile.BadZipFile, zlib.error, EOFError, ValueError, RuntimeError,
            struct.error):
        return None


def _table(z: zipfile.ZipFile) -> ResourceTable | None:
    try:
        return parse_table(_read(z, "resources.arsc", MAX_ARSC))
    except (KeyError, ApkError):
        return None


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
            try:
                table = parse_table(_read(z, "resources.arsc", MAX_ARSC))
            except KeyError:
                table = None
            try:
                icon = icons.app_icon([(z, table)], m.icon)
            except (ApkError, KeyError, zipfile.BadZipFile, zlib.error, EOFError, OSError, RuntimeError):
                icon = None
            return ApkInfo(
                path=path,
                package=pkg,
                label=_text(m.label, table, lang) or pkg,
                version_name=_text(m.version_name, table, lang),
                version_code=m.version_code,
                min_sdk=m.min_sdk,
                target_sdk=m.target_sdk,
                icon=icon,
                link_filters=m.link_filters,
                share_types=m.share_types,
                permissions=m.permissions,
                uses_gms=m.uses_gms,
                gecko_gpu=m.gecko_gpu,
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
