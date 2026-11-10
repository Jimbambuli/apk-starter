"""Split APKs: a base APK and its configuration parts (ABI, screen density, language), given as several files or
as one bundle (.apks from bundletool / SAI, .xapk from APKPure). ATL loads the base first and the parts after it
(class path and resources), like Android's LoadedApk with splitSourceDirs."""
import re
import zipfile
import zlib
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from .apkinfo import MAX_MANIFEST, MAX_PACKAGE, PACKAGE_RE
from .axml import parse_manifest
from .errors import ApkError
from .i18n import _

ISA = "x86_64"                  # the ABI ATL runs (dexopt.ISA)
BUNDLES = (".apks", ".xapk", ".apkm")
SPLIT_NAME_RE = re.compile(r"[A-Za-z0-9_.-]{1,100}")
# Play's ABI parts are named after the ABI with "_" for "-" (config.arm64_v8a)
ABI_SPLITS = {"config.armeabi": "armeabi", "config.armeabi_v7a": "armeabi-v7a", "config.arm64_v8a": "arm64-v8a",
              "config.x86": "x86", "config.x86_64": "x86_64", "config.mips": "mips", "config.mips64": "mips64",
              "config.riscv64": "riscv64"}
MAX_PARTS = 200


class IncompatibleError(ApkError):
    """No code for this computer (e.g. only ARM): PackageInstaller.STATUS_FAILURE_INCOMPATIBLE."""
    code = 7


@dataclass(frozen=True)
class SplitSet:
    package: str
    base: Path
    splits: tuple[tuple[str, Path], ...]        # (split name, file), in name order


def expand(files: list[Path], tmp: Path) -> list[Path]:
    """The APK files: bundles are unpacked into tmp (only their *.apk members, flat), other files stay."""
    out: list[Path] = []
    for f in files:
        f = Path(f)
        if f.suffix.lower() not in BUNDLES:
            out.append(f)
            continue
        try:
            with zipfile.ZipFile(f) as z:
                members = [m for m in z.infolist() if not m.is_dir() and m.filename.lower().endswith(".apk")]
                if not members:
                    raise ApkError(_("no APK in {file}").format(file=f))
                if len(members) > MAX_PARTS:
                    raise ApkError(_("too many parts in {file}").format(file=f))
                dest = tmp / f"bundle{len(out)}"
                dest.mkdir(parents=True, exist_ok=True)
                for k, m in enumerate(members):
                    name = PurePosixPath(m.filename).name
                    target = dest / f"{k:03d}-{name}"
                    with z.open(m) as src, open(target, "wb") as dst:
                        while chunk := src.read(1 << 20):
                            dst.write(chunk)
                    out.append(target)
        except (zipfile.BadZipFile, zipfile.LargeZipFile, RuntimeError, EOFError, zlib.error, ValueError) as e:
            raise ApkError(_("cannot read {file}: {reason}").format(file=f, reason=e)) from None
        except OSError as e:
            raise ApkError(_("cannot read {file}: {reason}").format(file=f, reason=e.strerror)) from None
    return out


def read_part(path: Path) -> tuple[str, str | None, set[str]]:
    """(package, split name or None for the base, ABIs of its lib/ folders)."""
    try:
        with zipfile.ZipFile(path) as z:
            try:
                info = z.getinfo("AndroidManifest.xml")
            except KeyError:
                raise ApkError(_("not an APK (no AndroidManifest.xml): {file}").format(file=path)) from None
            if info.file_size > MAX_MANIFEST:
                raise ApkError(_("{file} is too large ({size} bytes)").format(file="AndroidManifest.xml",
                                                                              size=info.file_size))
            m = parse_manifest(z.read(info))
            abis = {n.split("/")[1] for n in z.namelist() if n.startswith("lib/") and n.count("/") >= 2}
    except zipfile.BadZipFile:
        raise ApkError(_("not an APK (no AndroidManifest.xml): {file}").format(file=path)) from None
    except (zipfile.LargeZipFile, RuntimeError, EOFError, zlib.error, ValueError) as e:
        raise ApkError(_("cannot read {file}: {reason}").format(file=path, reason=e)) from None
    except OSError as e:
        raise ApkError(_("cannot read {file}: {reason}").format(file=path, reason=e.strerror)) from None
    pkg = m.package
    if pkg is None or len(pkg) > MAX_PACKAGE or not PACKAGE_RE.fullmatch(pkg):
        raise ApkError(_("invalid package name in manifest: {package}").format(package=repr(pkg)))
    if m.split is not None and not SPLIT_NAME_RE.fullmatch(m.split):
        raise ApkError(_("invalid split name in {file}").format(file=path))
    return pkg, m.split, abis


def collect(files: list[Path]) -> SplitSet:
    """The base and its parts; all of one package, exactly one base, no part twice."""
    base: Path | None = None
    package: str | None = None
    parts: dict[str, Path] = {}
    abis: set[str] = set()
    for f in files:
        pkg, split, found = read_part(f)
        if package is not None and pkg != package:
            raise ApkError(_("the parts belong to different apps: {a} and {b}").format(a=package, b=pkg))
        package = pkg
        if split is None:
            if base is not None:
                raise ApkError(_("more than one base APK: {a} and {b}").format(a=base.name, b=f.name))
            base = f
            abis |= found
        else:
            if split in parts:
                raise ApkError(_("the part {split} is there twice").format(split=split))
            parts[split] = f
    if base is None or package is None:
        raise ApkError(_("no base APK among the files (only configuration parts)"))
    return SplitSet(package, base, tuple(sorted(parts.items())))


def abis(s: SplitSet) -> set[str]:
    """The ABIs with native code: lib/ folders of the base and of every part, and the names of the ABI parts."""
    found = read_part(s.base)[2]
    for n, p in s.splits:
        found |= read_part(p)[2]
        found |= {ABI_SPLITS[k] for k in (n, n.removeprefix("split_")) if k in ABI_SPLITS}
    return found


def select(s: SplitSet, label: str) -> SplitSet:
    """The parts for this computer: without the ABI parts of other ABIs; refused if the app has native code but none
    for x86_64 (Android would refuse it with INSTALL_FAILED_NO_MATCHING_ABIS)."""
    native = abis(s)
    if native and ISA not in native:
        raise IncompatibleError(_("{app} contains program code only for ARM processors and does not run on this "
                                  "computer.").format(app=label))
    keep = tuple((n, p) for n, p in s.splits if n not in ABI_SPLITS or ABI_SPLITS[n] == ISA)
    return SplitSet(s.package, s.base, keep)

