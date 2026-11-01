"""Command line: start an APK, install/list/remove menu entries."""
import argparse
import contextlib
import fcntl
import json
import os
import shutil
import sys
from collections.abc import Iterator, Mapping
from datetime import datetime
from pathlib import Path

from .apkinfo import PACKAGE_RE, ApkInfo, read_apk_info
from .errors import ApkError, StarterError, UsageError
from .launch import (DEFAULT_SIZE, build_command, build_env, parse_size, prepare_apk, remember_size,
                     remembered_size, sdk_for)
from .paths import Paths
from .run import (default_log_path, header_fields, read_build_info, run_atl, runtime_name)

VERSION = "0.1.0"
APP_ID = "io.github.Jimbambuli.ApkStarter"
VALUE_OPTIONS = ("--sdk", "--size", "--install", "--remove")
DISPLAY_VARS = ("WAYLAND_DISPLAY", "DISPLAY")


def _scan(argv: list[str]) -> tuple[list[str], Path | bool | None, int]:
    """Options before the first positional (or `--`): returns (kept options, log, index of the tail)."""
    kept: list[str] = []
    log: Path | bool | None = None
    i = 0
    while i < len(argv):
        tok = argv[i]
        if tok == "--" or not tok.startswith("-"):
            break
        if tok == "--log":
            log = True
        elif tok.startswith("--log="):
            if not tok[6:]:
                raise UsageError("--log= needs a file name")
            log = Path(tok[6:])
        elif tok in VALUE_OPTIONS:
            kept += argv[i:i + 2]
            i += 1
        else:
            kept.append(tok)
        i += 1
    return kept, log, i


def split_log_option(argv: list[str]) -> tuple[list[str], Path | bool | None]:
    kept, log, i = _scan(argv)
    return kept + argv[i:], log


def resolve_target(paths: Paths, target: str) -> Path:
    if Path(target).is_file():
        return Path(target)
    if "/" not in target and PACKAGE_RE.fullmatch(target) and paths.apk(target).is_file():
        return paths.apk(target)
    if "/" in target or target.lower().endswith((".apk", ".apks", ".xapk")):
        raise ApkError(
            f"cannot read {target}: no such file (inside the Flatpak sandbox only paths passed with "
            f"--file-forwarding are visible: flatpak run --file-forwarding {APP_ID} @@ FILE @@)")
    raise UsageError(f"not installed: {target}")


def _desktop_id(pkg: str) -> str:
    return f"{APP_ID}.{pkg}.desktop"


def _write_atomic(dest: Path, data: bytes) -> None:
    tmp = dest.with_name(f"{dest.name}.{os.getpid()}.tmp")
    tmp.write_bytes(data)
    os.replace(tmp, dest)


@contextlib.contextmanager
def package_lock(paths: Paths, pkg: str) -> Iterator[None]:
    """Serialise install/remove/clear of one package across processes (command line and window)."""
    f = paths.lock(pkg)
    f.parent.mkdir(parents=True, exist_ok=True)
    with open(f, "a") as fd:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield


def install_app(paths: Paths, info: ApkInfo) -> None:
    dest = paths.apk(info.package)
    paths.apps_dir().mkdir(parents=True, exist_ok=True)
    if not (dest.exists() and os.path.samefile(info.path, dest)):
        tmp = dest.with_name(f"{dest.name}.{os.getpid()}.tmp")
        shutil.copyfile(info.path, tmp)
        os.replace(tmp, dest)
    if info.icon:
        _write_atomic(paths.icon(info.package), info.icon)
    else:
        paths.icon(info.package).unlink(missing_ok=True)
    meta = {"package": info.package, "label": info.label, "version_name": info.version_name,
            "version_code": info.version_code, "desktop_id": _desktop_id(info.package)}
    _write_atomic(paths.meta(info.package), json.dumps(meta).encode("utf-8"))


def install_apk(paths: Paths, apk: Path, lang: str | None) -> ApkInfo:
    """Copy the APK into apps/ and add a menu entry through the DynamicLauncher portal."""
    from . import portal
    info = read_apk_info(apk, lang)
    with package_lock(paths, info.package):
        install_app(paths, info)
        icon = info.icon
        if icon is None:
            try:
                icon = paths.default_icon().read_bytes()
            except OSError:
                icon = b""
        try:
            portal.install_launcher(info.package, info.label, icon)
        except portal.PortalUnavailable:
            raise StarterError(
                "the DynamicLauncher portal is not available; start the app with: "
                f"flatpak run {APP_ID} {info.package}") from None
    return info


def list_installed(paths: Paths) -> list[dict]:
    out = []
    for f in paths.apps_dir().glob("*.json"):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            if isinstance(d, dict) and isinstance(d.get("package"), str):
                out.append(d)
        except (OSError, ValueError):
            continue
    return sorted(out, key=lambda d: d["package"])


def data_size(paths: Paths, pkg: str) -> int:
    total = 0
    for root, _dirs, files in os.walk(paths.data(pkg)):
        for name in files:
            try:
                total += os.lstat(os.path.join(root, name)).st_size
            except OSError:
                pass
    return total


def _delete_data(paths: Paths, pkg: str) -> None:
    try:
        shutil.rmtree(paths.data(pkg), ignore_errors=False)
    except FileNotFoundError:
        pass
    except OSError as e:
        raise StarterError(f"cannot remove {paths.data(pkg)}: {e.strerror}") from None


def clear_data(paths: Paths, pkg: str) -> None:
    """Delete the app data; the app stays installed."""
    with package_lock(paths, pkg):
        _delete_data(paths, pkg)


def remove_app(paths: Paths, pkg: str, purge: bool) -> None:
    from . import portal
    with package_lock(paths, pkg):
        if not paths.meta(pkg).exists() and not paths.apk(pkg).exists():
            raise UsageError(f"not installed: {pkg}")
        try:
            portal.uninstall_launcher(pkg)
        except portal.PortalUnavailable:
            print("apk-starter: the DynamicLauncher portal is not available; the menu entry was not removed",
                  file=sys.stderr)
        for f in (paths.apk(pkg), paths.meta(pkg), paths.icon(pkg), paths.launch_dir() / f"{pkg}.apk"):
            f.unlink(missing_ok=True)
        if purge:
            _delete_data(paths, pkg)


def remove_all(paths: Paths, purge: bool) -> None:
    pkgs = {d["package"] for d in list_installed(paths)} | {f.stem for f in paths.apps_dir().glob("*.apk")}
    for pkg in sorted(pkgs):
        if PACKAGE_RE.fullmatch(pkg):
            remove_app(paths, pkg, purge)


def _lang(env: Mapping[str, str]) -> str | None:
    raw = env.get("LC_ALL") or env.get("LC_MESSAGES") or env.get("LANG") or ""
    lang = raw.replace("@", ".").split(".")[0].split("_")[0]
    return lang.lower() if lang.isalpha() and lang not in ("C", "POSIX") else None


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="apk-starter", allow_abbrev=False,
        usage="%(prog)s [--log[=FILE]] [--sdk N] [--size WxH] [--] [APK|PACKAGE] [ATL-ARGS...]\n"
              "       %(prog)s --install APK | --list | --remove PACKAGE [--purge] | --remove-all [--purge]\n"
              "       %(prog)s --version\n"
              "       %(prog)s            (no arguments: open the management window)",
        description="Start Android apps (APK) with Android Translation Layer.")
    p.add_argument("--log", nargs="?", const=True, metavar="FILE",
                   help="write a log for atl-doctor (default: logs/<package>-<time>.log); use --log=FILE")
    p.add_argument("--sdk", type=int, metavar="N", help="override the SDK level passed to ATL")
    p.add_argument("--size", metavar="WxH", help="window size (remembered per app)")
    p.add_argument("--purge", action="store_true", help="with --remove/--remove-all: also delete the app data")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--install", metavar="APK", help="install the APK and add a menu entry")
    g.add_argument("--list", action="store_true", help="list installed apps")
    g.add_argument("--remove", metavar="PACKAGE", help="remove an installed app")
    g.add_argument("--remove-all", action="store_true",
                   help="remove all installed apps and their menu entries (before uninstalling the Flatpak)")
    g.add_argument("--version", action="store_true")
    return p


def main(argv: list[str] | None = None, env: Mapping[str, str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    env = os.environ if env is None else env
    try:
        return _main(argv, env)
    except StarterError as e:
        print(f"apk-starter: {e}", file=sys.stderr)
        return e.code
    except OSError as e:
        print(f"apk-starter: {e.strerror or e}" + (f": {e.filename}" if e.filename else ""), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    except SystemExit as e:                     # parser.error() after parsing
        return e.code if isinstance(e.code, int) else 1


def _main(argv: list[str], env: Mapping[str, str]) -> int:
    kept, log, i = _scan(argv)
    tail = argv[i:]
    if tail[:1] == ["--"]:
        tail = tail[1:]
    parser = _parser()
    try:
        args = parser.parse_args([a for a in kept if a != "--"])
    except SystemExit as e:
        return e.code if isinstance(e.code, int) else 0
    mode = args.install or args.list or args.remove or args.remove_all or args.version
    if mode and (tail or log is not None or args.sdk is not None or args.size):
        parser.error("--install, --list, --remove, --remove-all and --version take no other arguments")
    if args.purge and not (args.remove or args.remove_all):
        parser.error("--purge only works with --remove or --remove-all")
    paths = Paths.from_env(env)

    if args.version:
        b = read_build_info(paths.build_info())
        print(f"apk-starter {VERSION} (ATL {b['atl_commit'][:12]}, ART {b['art_commit'][:12]})")
        return 0
    if args.list:
        for d in list_installed(paths):
            print("\t".join(" ".join(str(d.get(k) or "").split()) for k in ("package", "label", "version_name")))
        return 0
    if args.remove:
        remove_app(paths, args.remove, args.purge)
        return 0
    if args.remove_all:
        remove_all(paths, args.purge)
        return 0
    if args.install:
        install_apk(paths, Path(args.install), _lang(env))
        return 0
    if not tail and log is None and args.sdk is None and not args.size:
        if not any(env.get(v) for v in DISPLAY_VARS):
            parser.error("no APK given and no display for the window")
        from . import gui
        return gui.run(paths, env)
    if not tail:
        parser.error("no APK given")
    return _start(paths, args, log, tail, env)


def _start(paths: Paths, args, log, tail: list[str], env: Mapping[str, str]) -> int:
    target, atl_args = tail[0], tail[1:]
    apk = resolve_target(paths, target)
    info = read_apk_info(apk, _lang(env))
    if args.size:
        size = parse_size(args.size)
    else:
        size = remembered_size(paths, info.package) or DEFAULT_SIZE
    started = datetime.now().astimezone()
    log_path = None
    if log is True:
        log_path = default_log_path(paths, info.package, started)
    elif log:
        log_path = log
    apk_path = prepare_apk(paths, info)
    cmd = build_command(paths, apk_path, size=size, sdk=sdk_for(info, args.sdk), atl_args=atl_args,
                        app_id=f"{APP_ID}.{info.package}")
    child_env = build_env(paths, info.package, env)
    if args.size:
        remember_size(paths, info.package, size)
    header = header_fields(info, read_build_info(paths.build_info()), runtime_name(), cmd, started, VERSION) \
        if log_path else {}
    return run_atl(cmd, child_env, log_path, header)
