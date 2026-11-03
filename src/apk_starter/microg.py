"""microG: GmsCore and GsfProxy, set up like Aurora Store: downloaded from the official microG F-Droid repository,
checked, installed (Spec 15).

ATL loads Play Services into the app's own process from its installed APKs, which the starter points to apps/
(ATL_INSTALLED_APKS_DIR, as for app stores), and gives them the data folder <ANDROID_APP_DATA_DIR>/<package>.apk_.
So the installed microG APKs sit in apps/ (as on Android, Play Services count as installed), and every app, which has
its own ANDROID_APP_DATA_DIR here, links to ONE shared data folder per microG package: the folder microG uses when
it is started itself (its settings), so a login or registration there is seen by every app."""
import hashlib
import json
import os
import re
import urllib.error
from collections.abc import Callable, Mapping
from pathlib import Path

from . import apksig
from .apkinfo import ApkInfo, read_apk_info
from .aurora import _get
from .errors import StarterError
from .i18n import _
from .paths import Paths

GMS = "com.google.android.gms"
GSF = "com.google.android.gsf"
SERVICES = (GMS, GSF)
INDEX = "https://microg.org/fdroid/repo/index-v1.json"
APK_URL = "https://microg.org/fdroid/repo/{apk}"
# GmsCore is signed (APK signature v2) by the NOGAPPS Project; checked 3.11.2026 for 0.3.17.252432 (252432034).
SIGNER = "9bd06727e62796c0130eb6dab39b73157451582cbd138e86c468acc395d14165"
# GsfProxy 8 (2016, final) has only a v1 signature: its file checksum is pinned instead (same signer, per the index)
PINNED_SHA256 = {GSF: "86891b174301f06a1c84187b545a0a2a57044c6b768f3e84e865908743349692"}
MAX_SIZE = {GMS: 200 << 20, GSF: 1 << 20}
APK_NAME = re.compile(r"[A-Za-z0-9._-]+\.apk")


def installed(paths: Paths) -> bool:
    """microG is set up: GmsCore as an app (list, menu entry) and GsfProxy in apps/."""
    return paths.meta(GMS).is_file() and paths.apk(GSF).is_file()


def _entry(index: bytes, pkg: str) -> dict:
    try:
        versions = json.loads(index)["packages"][pkg]
        best = max(versions, key=lambda v: v["versionCode"])
        name, digest, code = best["apkName"], best["hash"], best["versionCode"]
        ok = best.get("hashType") == "sha256" and APK_NAME.fullmatch(name) and re.fullmatch(r"[0-9a-f]{64}", digest)
        if not ok or not isinstance(code, int):
            raise ValueError
    except (ValueError, KeyError, TypeError):
        raise StarterError(_("the microG repository lists no usable version of {package}").format(package=pkg)) \
            from None
    return {"apk": name, "sha256": digest, "code": code}


def download(pkg: str, dest: Path, fetch: Callable[[str, int, Callable[[bytes], None]], None] | None = None,
             signer: str = SIGNER) -> ApkInfo:
    """The newest APK of `pkg` from the microG repository into dest, checked: SHA-256 of the file (from the index,
    and pinned for GsfProxy), for GmsCore also the v2 signature by the NOGAPPS certificate, package name and version."""
    fetch = fetch or _get
    try:
        index = bytearray()
        fetch(INDEX, 8 << 20, index.extend)
        entry = _entry(bytes(index), pkg)
        digest = hashlib.sha256()

        def sink(chunk: bytes) -> None:
            digest.update(chunk)
            f.write(chunk)
        with open(dest, "wb") as f:
            fetch(APK_URL.format(apk=entry["apk"]), MAX_SIZE[pkg], sink)
    except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
        raise StarterError(_("cannot download microG from the microG repository: {reason}").format(
            reason=getattr(e, "reason", e))) from None
    if digest.hexdigest() != entry["sha256"] or digest.hexdigest() != PINNED_SHA256.get(pkg, entry["sha256"]):
        raise StarterError(_("the downloaded {package} does not match its checksum; it was not installed").format(
            package=pkg))
    if pkg == GMS:
        found = apksig.verify(dest).cert_sha256
        if found != signer:
            raise StarterError(_("the downloaded microG is not signed by the NOGAPPS Project (certificate {found}); "
                                 "it was not installed").format(found=found))
    info = read_apk_info(dest)
    if info.package != pkg or info.version_code != entry["code"]:
        raise StarterError(_("the downloaded file is not {package} {code}").format(package=pkg, code=entry["code"]))
    return info


def setup(paths: Paths, env: Mapping[str, str], lang: str | None, report: Callable[[str], None],
          warn: Callable[[str], None],
          fetch: Callable[[str, int, Callable[[bytes], None]], None] | None = None) -> ApkInfo:
    """Download and install what is missing: GmsCore as «microG settings» (list and menu entry), GsfProxy into
    apps/ only. Returns GmsCore's app."""
    from .cli import install_files, parts_dir
    info = None
    with parts_dir(paths) as tmp:
        if not paths.meta(GMS).is_file():
            report(_("Downloading microG…"))
            apk = Path(tmp) / f"{GMS}.apk"
            download(GMS, apk, fetch)
            info = install_files(paths, [apk], lang, env, report, warn, label=_("microG settings"))
        if not paths.apk(GSF).is_file():
            apk = Path(tmp) / f"{GSF}.apk"
            download(GSF, apk, fetch)
            paths.apps_dir().mkdir(parents=True, exist_ok=True)
            part = paths.apk(GSF).with_name(f"{GSF}.apk.{os.getpid()}.tmp")
            part.write_bytes(apk.read_bytes())
            os.replace(part, paths.apk(GSF))    # a dangling link from an older build is replaced, not followed
    return info or read_apk_info(paths.apk(GMS), lang)


def _symlink(link: Path, target: Path) -> None:
    if link.is_symlink() and link.readlink() == target:
        return
    if link.is_symlink():
        link.unlink()
    elif link.exists():
        return                  # a real file or folder: leave it alone
    link.symlink_to(target)


def link_into(paths: Paths, pkg: str) -> None:
    """Make microG visible to `pkg` (before ATL starts it): the data folders of the installed microG packages."""
    base = paths.data(pkg)
    for mpkg in SERVICES:
        if mpkg != pkg and paths.apk(mpkg).is_file():
            name = f"{mpkg}.apk_"
            shared = paths.data(mpkg) / name
            shared.mkdir(parents=True, exist_ok=True)
            _symlink(base / name, shared)
