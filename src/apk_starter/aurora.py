"""«Set up Aurora Store»: the official Aurora Store APK from F-Droid, checked against Aurora's signing certificate,
installed like any app. Aurora Store is not part of the Flatpak (Spec 15)."""
import json
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from pathlib import Path

from . import apksig
from .apkinfo import ApkInfo, read_apk_info
from .errors import StarterError
from .i18n import _
from .paths import Paths

PACKAGE = "com.aurora.store"
# the certificate F-Droid's index lists as signer of com.aurora.store (reproducible builds signed by Aurora OSS),
# checked 2.11.2026 for version 4.8.4 (76): APK sha256 fd9c75d90d0f4a7c132b9b4a5a2cf1992a45e03b8d8ff988b7dcfbc0db2c4d11
SIGNER = "5c83c7672b929955dc0a1db89a5e6ae4389e2eae7ec939956041694e5815f532"
API = "https://f-droid.org/api/v1/packages/" + PACKAGE
APK_URL = "https://f-droid.org/repo/{package}_{code}.apk"
MAX_SIZE = 100 << 20
TIMEOUT = 60


def _get(url: str, limit: int, sink) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": "apk-starter"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        total = 0
        while chunk := r.read(1 << 16):
            total += len(chunk)
            if total > limit:
                raise StarterError(_("the download is larger than expected: {url}").format(url=url))
            sink(chunk)


def latest_version(fetch: Callable[[str, int, Callable[[bytes], None]], None] | None = None) -> int:
    """F-Droid's suggested version code of Aurora Store."""
    data = bytearray()
    fetch = fetch or _get
    fetch(API, 1 << 20, data.extend)
    try:
        code = json.loads(data)["suggestedVersionCode"]
    except (ValueError, KeyError, TypeError):
        raise StarterError(_("F-Droid gave no version of Aurora Store")) from None
    if not isinstance(code, int) or code <= 0:
        raise StarterError(_("F-Droid gave no version of Aurora Store"))
    return code


def download(dest: Path, fetch: Callable[[str, int, Callable[[bytes], None]], None] | None = None,
             signer: str = SIGNER) -> ApkInfo:
    """Aurora Store's APK from F-Droid into dest, checked: v2 signature by Aurora's certificate, package name and the
    version F-Droid named."""
    fetch = fetch or _get
    try:
        code = latest_version(fetch)
        with open(dest, "wb") as f:
            fetch(APK_URL.format(package=PACKAGE, code=code), MAX_SIZE, f.write)
    except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
        raise StarterError(_("cannot download Aurora Store from F-Droid: {reason}").format(
            reason=getattr(e, "reason", e))) from None
    found = apksig.verify(dest).cert_sha256
    if found != signer:
        raise StarterError(_("the downloaded Aurora Store is not signed by Aurora OSS (certificate {found}); "
                             "it was not installed").format(found=found))
    info = read_apk_info(dest)
    if info.package != PACKAGE or info.version_code != code:
        raise StarterError(_("the downloaded file is not Aurora Store {code}").format(code=code))
    return info


def setup(paths: Paths, env: Mapping[str, str], report: Callable[[str], None], warn: Callable[[str], None],
          fetch: Callable[[str, int, Callable[[bytes], None]], None] | None = None) -> ApkInfo:
    from .cli import install_files, lang_from_env, parts_dir
    report(_("Downloading Aurora Store from F-Droid…"))
    with parts_dir(paths) as tmp:
        apk = Path(tmp) / f"{PACKAGE}.apk"
        download(apk, fetch)
        return install_files(paths, [apk], lang_from_env(env), env, report, warn)


# -- updates (Spec 18 Phase 3) -------------------------------------------------------------------------------------
# Aurora Store keeps the updates it found (when it checks: at its start and with its own periodic check) in its
# database, with the updates the user chose to ignore. The starter reads that list instead of asking Google Play
# itself: no second Play client, no account data, Aurora's choice (ignored updates, its device profile) counts.
def _database(paths: Paths) -> Path:
    return paths.data(PACKAGE) / f"{PACKAGE}.apk_" / "databases" / "aurora_database"


def pending_updates(paths: Paths) -> list[tuple[str, str]]:
    """(package, name) of the installed apps Aurora knows a newer version of, by name; [] without Aurora's data."""
    import json
    import sqlite3
    db = _database(paths)
    if not db.is_file():
        return []
    try:
        con = sqlite3.connect(f"{db.as_uri()}?mode=ro", uri=True, timeout=2)
        try:
            found = con.execute("SELECT packageName, MAX(versionCode) FROM `update` GROUP BY packageName").fetchall()
            ignored = dict(con.execute("SELECT packageName, ignoredVersionCode FROM ignored_update").fetchall())
        finally:
            con.close()
    except sqlite3.Error:
        return []
    out = []
    for pkg, code in found:
        if pkg in ignored and (ignored[pkg] is None or ignored[pkg] >= code):
            continue
        try:
            meta = json.loads(paths.meta(pkg).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue                                # not installed with the starter
        have = meta.get("version_code")
        if isinstance(have, int) and isinstance(code, int) and code > have:
            out.append((pkg, str(meta.get("label") or pkg)))
    return sorted(out, key=lambda u: u[1].casefold())


def updates_note(paths: Paths, updates: list[tuple[str, str]]) -> tuple[str, str] | None:
    """Title and text of the desktop notification, or None if there is nothing new to tell (the same updates were
    told already)."""
    marker = paths.cache_home / "apk-starter" / "aurora-updates"
    seen = "\n".join(pkg for pkg, _label in updates)
    try:
        if marker.read_text(encoding="utf-8") == seen:
            return None
    except OSError:
        pass
    try:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(seen, encoding="utf-8")
    except OSError:
        pass
    if not updates:
        return None
    return _("Updates: {count}").format(count=len(updates)), ", ".join(label for _pkg, label in updates)
