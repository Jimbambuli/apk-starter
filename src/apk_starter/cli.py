"""Command line: start an APK, install/list/remove menu entries."""
import argparse
import contextlib
import dataclasses
import fcntl
import json
import os
import shutil
import sys
import tempfile
import threading
from collections.abc import Callable, Iterator, Mapping, MutableMapping
from datetime import datetime
from pathlib import Path

from .apkinfo import PACKAGE_RE, ApkInfo, read_apk_info, read_icon
from .errors import ApkError, NoMenuEntry, SignatureConflict, StarterError, UsageError, describe_error
from .i18n import _
from . import dexopt, diag, i18n, splits
from .launch import (DEFAULT_SIZE, build_command, build_env, cached_apk_info, parse_size, prepare_apk,
                     remember_size, remembered_size, sdk_for)
from .paths import PROFILE_RE, Paths, apply_profile
from .settings import load_settings
from .run import (read_build_info, run_atl)

VERSION = "0.8.7"
COMPILE_GRACE_S = 20.0      # how long the end of the app waits for the full compile that runs in the background
APP_ID = "io.github.Jimbambuli.ApkStarter"
# A test build's menu entries and windows are its own (profile), so they do not replace those of stable.
_PROFILE = os.environ.get("APK_STARTER_PROFILE", "")
ENTRY_PREFIX = f"{APP_ID}.{_PROFILE}" if PROFILE_RE.fullmatch(_PROFILE) else APP_ID
RUN_REF = f"{APP_ID}//{_PROFILE}" if PROFILE_RE.fullmatch(_PROFILE) else APP_ID     # its Flatpak branch
# The portal's menu entries run the current branch (no --branch), so a test build makes none: its apps start from
# its own window.
MENU_ENTRIES = RUN_REF == APP_ID
VALUE_OPTIONS = ("--sdk", "--size", "--uri", "--install", "--install-request", "--remove", "--uninstall-request", "--details")
DISPLAY_VARS = ("WAYLAND_DISPLAY", "DISPLAY")


def _scan(argv: list[str]) -> tuple[list[str], str | None, Path | None, int]:
    """Options before the first positional (or `--`): returns (kept options, diagnose level "debug", "debug-full" or
    None, its folder, index of the tail)."""
    kept: list[str] = []
    level: str | None = None
    target: Path | None = None
    i = 0
    while i < len(argv):
        tok = argv[i]
        if tok == "--" or not tok.startswith("-"):
            break
        name, eq, value = tok[2:].partition("=")
        if name in ("debug", "debug-full") and tok.startswith("--"):
            if eq and not value:
                raise UsageError(_("--{option}= needs a folder name").format(option=name))
            level, target = name, Path(value) if value else None
        elif tok in VALUE_OPTIONS:
            kept += argv[i:i + 2]
            i += 1
        else:
            kept.append(tok)
        i += 1
    return kept, level, target, i


def split_diag_option(argv: list[str]) -> tuple[list[str], str | None, Path | None]:
    kept, level, target, i = _scan(argv)
    return kept + argv[i:], level, target


def resolve_target(paths: Paths, target: str) -> Path:
    if Path(target).is_file():
        return Path(target)
    if "/" not in target and PACKAGE_RE.fullmatch(target) and paths.apk(target).is_file():
        return paths.apk(target)
    if "/" in target or target.lower().endswith((".apk", ".apks", ".xapk")):
        raise ApkError(
            _("cannot read {file}: no such file (inside the Flatpak sandbox only paths passed with "
              "--file-forwarding are visible: {command})").format(
                file=target, command=f"flatpak run --file-forwarding {RUN_REF} @@ FILE @@"))
    raise UsageError(_("not installed: {package}").format(package=target))


def _desktop_id(pkg: str) -> str:
    return f"{ENTRY_PREFIX}.{pkg}.desktop"


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


def _install_splits(paths: Paths, pkg: str, parts: tuple[tuple[str, Path], ...]) -> None:
    """The configuration parts as <pkg>.splits/<name>.apk; replaces the parts of an earlier install."""
    dest = paths.splits_dir(pkg)
    old = dest.with_name(f"{dest.name}.{os.getpid()}.old")
    if parts:
        tmp = dest.with_name(f"{dest.name}.{os.getpid()}.tmp")
        shutil.rmtree(tmp, ignore_errors=True)
        tmp.mkdir()
        for name, src in parts:
            shutil.copyfile(src, tmp / f"{name}.apk")
        if dest.exists():
            os.replace(dest, old)
        os.replace(tmp, dest)
    elif dest.exists():
        os.replace(dest, old)
    shutil.rmtree(old, ignore_errors=True)


def check_signer(paths: Paths, info: ApkInfo) -> None:
    """Like Android: an update must be signed like the installed app, whatever offers it (an app store, a file).
    An installed app without a readable signature is not compared."""
    from . import apksig
    installed = paths.apk(info.package)
    if not installed.is_file() or os.path.samefile(info.path, installed):
        return
    try:
        old = apksig.signers(installed)
    except ApkError:
        return
    if old and apksig.signers(info.path) != old:
        raise SignatureConflict(_("Different signature – not installed"))


def install_app(paths: Paths, info: ApkInfo, parts: tuple[tuple[str, Path], ...] = ()) -> None:
    dest = paths.apk(info.package)
    paths.apps_dir().mkdir(parents=True, exist_ok=True)
    if not (dest.exists() and os.path.samefile(info.path, dest)):
        tmp = dest.with_name(f"{dest.name}.{os.getpid()}.tmp")
        shutil.copyfile(info.path, tmp)
        os.replace(tmp, dest)
    _install_splits(paths, info.package, parts)
    if info.icon:
        _write_atomic(paths.icon(info.package), info.icon)
    else:
        paths.icon(info.package).unlink(missing_ok=True)
    meta = {"package": info.package, "label": info.label, "version_name": info.version_name,
            "version_code": info.version_code, "target_sdk": info.target_sdk, "desktop_id": _desktop_id(info.package)}
    _write_atomic(paths.meta(info.package), json.dumps(meta).encode("utf-8"))


def _stderr(text: str) -> None:
    print(f"apk-starter: {text}", file=sys.stderr, flush=True)


def prepare_installed(paths: Paths, env: Mapping[str, str], info: ApkInfo, report: Callable[[str], None],
                      warn: Callable[[str], None], flt: str = dexopt.FULL) -> None:
    """Install dexopt: runtime, app code (FULL) and native libraries (in parallel). On failure the app still
    works: ART compiles at the start and ATL unpacks, as before; the reason is reported."""
    try:
        dexopt.ensure_runtime(paths, env, report)
        report(_("Preparing {app} for this computer…").format(app=info.label))
        _compile_and_unpack(paths, env, info.package, paths.apk(info.package), flt)
    except (StarterError, OSError) as e:
        warn(_("{app} could not be prepared in advance: {reason}").format(app=info.label,
                                                                            reason=describe_error(e)))


def _compile_and_unpack(paths: Paths, env: Mapping[str, str], pkg: str, apk: Path, flt: str,
                        is_urgent: bool = False) -> None:
    """dex2oat and unpacking the native libraries side by side (F181); raises the first failure."""
    failed: list[Exception] = []
    def natives() -> None:
        try:
            for f in [apk] + (paths.installed_splits(pkg) if apk == paths.apk(pkg) else []):
                dexopt.extract_natives(paths, pkg, f)        # the base, then the ABI part of a split APK
        except (StarterError, OSError) as e:
            failed.append(e)
    t = threading.Thread(target=natives)
    t.start()
    try:
        dexopt.compile_app(paths, env, str(apk), flt, is_urgent=is_urgent)
    finally:
        t.join()
    if failed:
        raise failed[0]


def install_apk(paths: Paths, apk: Path, lang: str | None, env: Mapping[str, str] | None = None,
                report: Callable[[str], None] = _stderr, warn: Callable[[str], None] = _stderr,
                label: str | None = None) -> ApkInfo:
    """Copy the APK into apps/, compile it for this computer and add a menu entry (DynamicLauncher portal)."""
    return install_files(paths, [apk], lang, env, report, warn, label)


def read_parts(paths: Paths, files: list[Path], lang: str | None, tmp: Path) -> tuple[ApkInfo, splits.SplitSet]:
    """The app of one APK, of a base with its configuration parts, or of a bundle (.apks/.xapk); only the parts
    for this computer (refused with IncompatibleError if there is no x86_64 code)."""
    parts = splits.expand(files, tmp)
    if len(parts) == 1:
        info = read_apk_info(parts[0], lang)
        return info, splits.select(splits.SplitSet(info.package, parts[0], ()), info.label)
    s = splits.collect(parts)
    info = read_apk_info(s.base, lang)
    chosen = splits.select(s, info.label)
    if chosen.splits:                               # an app store puts the bitmaps into the density parts
        info = dataclasses.replace(info, icon=read_icon(s.base, [p for _n, p in chosen.splits]) or info.icon)
    return info, chosen


def parts_dir(paths: Paths) -> tempfile.TemporaryDirectory:
    """Where bundles are unpacked while installing (next to the cache, not in a small /tmp)."""
    paths.cache_home.mkdir(parents=True, exist_ok=True)
    return tempfile.TemporaryDirectory(prefix="apk-starter-", dir=paths.cache_home)


def install_files(paths: Paths, files: list[Path], lang: str | None, env: Mapping[str, str] | None = None,
                  report: Callable[[str], None] = _stderr, warn: Callable[[str], None] = _stderr,
                  label: str | None = None, flt: str = dexopt.FULL) -> ApkInfo:
    """Install an APK, a split APK (base and parts) or a bundle; `label` replaces the app's own name; `flt` is
    the compiler filter of the install (QUICK when the app is started right after: FULL then follows)."""
    with parts_dir(paths) as tmp:
        info, parts = read_parts(paths, files, lang, Path(tmp))
        if label:
            info = dataclasses.replace(info, label=label)
        return install_parts(paths, info, parts.splits, lang, env, report, warn, flt)


def install_parts(paths: Paths, info: ApkInfo, parts: tuple[tuple[str, Path], ...], lang: str | None,
                  env: Mapping[str, str] | None, report: Callable[[str], None] = _stderr,
                  warn: Callable[[str], None] = _stderr, flt: str = dexopt.FULL) -> ApkInfo:
    from . import portal
    with package_lock(paths, info.package):
        check_signer(paths, info)
        install_app(paths, info, parts)
        with contextlib.suppress(OSError):          # the first start then needs not parse the APK (F182)
            cached_apk_info(paths, paths.apk(info.package), lang, lambda p, _l: dataclasses.replace(info, path=p))
        prepare_installed(paths, os.environ if env is None else env, info, report, warn, flt)
        if not MENU_ENTRIES:
            return info
        icon = info.icon
        if icon is None:
            try:
                icon = paths.default_icon().read_bytes()
            except OSError:
                icon = b""
        try:
            portal.install_launcher(info.package, info.label, icon,
                                    mime_types=menu_mime_types(portal, info))
        except portal.PortalUnavailable:
            raise NoMenuEntry(
                _("{app} is installed without a menu entry: the DynamicLauncher portal is not available; "
                  "start the app with: {command}").format(app=info.label,
                                                         command=f"flatpak run {RUN_REF} {info.package}")) from None
        except StarterError as e:
            raise NoMenuEntry(_("{app} is installed without a menu entry: {reason}").format(
                app=info.label, reason=e)) from None
    return info


def microg_hint() -> str:
    return _("In «microG settings» switch on «Google device registration» and «Cloud Messaging». "
             "To sign in: Accounts → Add Google account.")


def setup_microg(paths: Paths, lang: str | None, env: Mapping[str, str] | None = None,
                 report: Callable[[str], None] = _stderr, warn: Callable[[str], None] = _stderr) -> ApkInfo:
    """Download microG from its official repository, check it and install it («microG settings» in the list)."""
    from . import microg
    return microg.setup(paths, os.environ if env is None else env, lang, report, warn)


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
        raise StarterError(_("cannot remove {folder}: {reason}").format(folder=paths.data(pkg),
                                                                         reason=e.strerror)) from None


def clear_data(paths: Paths, pkg: str) -> None:
    """Delete the app data; the app stays installed."""
    with package_lock(paths, pkg):
        _delete_data(paths, pkg)


def remove_app(paths: Paths, pkg: str, purge: bool) -> list[str]:
    """Remove the app even when the portal fails (else it could never be removed); returns warnings."""
    from . import portal
    warnings = []
    with package_lock(paths, pkg):
        if not paths.meta(pkg).exists() and not paths.apk(pkg).exists():
            raise UsageError(_("not installed: {package}").format(package=pkg))
        try:
            if MENU_ENTRIES:
                portal.uninstall_launcher(pkg)
        except portal.PortalUnavailable:
            warnings.append(_("the DynamicLauncher portal is not available; the menu entry was not removed"))
        except StarterError as e:
            warnings.append(_("the menu entry was not removed: {reason}").format(reason=e))
        for f in (paths.apk(pkg), paths.meta(pkg), paths.icon(pkg), paths.started(pkg), paths.launch_dir() / f"{pkg}.apk"):
            f.unlink(missing_ok=True)
        shutil.rmtree(paths.splits_dir(pkg), ignore_errors=True)
        if purge:
            _delete_data(paths, pkg)
    return warnings


def remove_all(paths: Paths, purge: bool) -> tuple[list[str], list[str]]:
    """Remove every app, going on after a failure; returns (warnings, errors)."""
    pkgs = {d["package"] for d in list_installed(paths)} | {f.stem for f in paths.apps_dir().glob("*.apk")}
    warnings, errors = [], []
    for pkg in sorted(pkgs):
        if PACKAGE_RE.fullmatch(pkg):
            try:
                warnings += remove_app(paths, pkg, purge)
            except (StarterError, OSError) as e:
                errors.append(describe_error(e))
    return warnings, errors


def lang_from_env(env: Mapping[str, str]) -> str | None:
    raw = env.get("LC_ALL") or env.get("LC_MESSAGES") or env.get("LANG") or ""
    lang = raw.replace("@", ".").split(".")[0].split("_")[0]
    return lang.lower() if lang.isalpha() and lang not in ("C", "POSIX") else None


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="apk-starter", allow_abbrev=False,
        usage="%(prog)s [--debug[=DIR] | --debug-full[=DIR]] [--sdk N] [--size WxH] [--] [APK|PACKAGE] [ATL-ARGS...]\n"
              "       %(prog)s --install APK | --list | --remove PACKAGE [--purge] | --remove-all [--purge]\n"
              "       %(prog)s --version\n"
              "       %(prog)s            " + _("(no arguments: open the management window)"),
        description=_("Start Android apps (APK) with Android Translation Layer."))
    p.add_argument("--debug", nargs="?", const=True, metavar="DIR",
                   help=_("record the app for atl-doctor: exceptions, ATL stub calls, time line, view trees, "
                          "clicks and typed text, verbose log; afterwards one archive "
                          "cleaned of personal data (default: logs/<package>-<time>.tar.gz); use --debug=DIR"))
    p.add_argument("--debug-full", nargs="?", const=True, metavar="DIR",
                   help=_("like --debug, but the archive keeps everything in plain text, passwords and personal "
                          "data and screenshots included (name ends in -FULL): do not pass it on"))
    p.add_argument("--sdk", type=int, metavar="N", help=_("override the SDK level passed to ATL"))
    p.add_argument("--size", metavar="WxH", help=_("window size (remembered per app)"))
    p.add_argument("--uri", metavar="URI", help=_("open a link in the app (the menu entry passes it)"))
    p.add_argument("--purge", action="store_true", help=_("with --remove/--remove-all: also delete the app data"))
    g = p.add_mutually_exclusive_group()
    g.add_argument("--install", metavar="APK", help=_("install the APK and add a menu entry; a split APK: the base "
                                                      "and its parts, or one .apks/.xapk file"))
    g.add_argument("--install-request", metavar="APK", help=argparse.SUPPRESS)  # from an app store, via ATL
    g.add_argument("--uninstall-request", metavar="PACKAGE", help=argparse.SUPPRESS)  # likewise
    g.add_argument("--details", metavar="PACKAGE", help=argparse.SUPPRESS)  # an app's settings: the window
    g.add_argument("--setup-microg-window", action="store_true", help=argparse.SUPPRESS)  # microG offered: the window
    g.add_argument("--setup-aurora", action="store_true",
                   help=_("download Aurora Store from F-Droid, check its signature and install it"))
    g.add_argument("--list", action="store_true", help=_("list installed apps"))
    g.add_argument("--remove", metavar="PACKAGE", help=_("remove an installed app"))
    g.add_argument("--remove-all", action="store_true",
                   help=_("remove all installed apps and their menu entries (before uninstalling the Flatpak)"))
    g.add_argument("--version", action="store_true", help=_("show the version and exit"))
    g.add_argument("--setup-microg", action="store_true", help=_("download microG from its repository, check it and install it (Google services for the apps)"))
    return p


def main(argv: list[str] | None = None, env: Mapping[str, str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    env = os.environ if env is None else env
    if isinstance(env, MutableMapping):
        apply_profile(env)
        i18n.follow_desktop(env)
    i18n.setup(Paths.from_env(env).prefix)
    try:
        return _main(argv, env)
    except StarterError as e:
        print(f"apk-starter: {describe_error(e)}", file=sys.stderr)
        return e.code
    except OSError as e:
        print(f"apk-starter: {describe_error(e)}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    except SystemExit as e:                     # parser.error() after parsing
        return e.code if isinstance(e.code, int) else 1


def _main(argv: list[str], env: Mapping[str, str]) -> int:
    kept, level, target, i = _scan(argv)
    tail = argv[i:]
    if tail[:1] == ["--"]:
        tail = tail[1:]
    parser = _parser()
    try:
        args = parser.parse_args([a for a in kept if a != "--"])
    except SystemExit as e:
        return e.code if isinstance(e.code, int) else 0
    mode = (args.install or args.install_request or args.setup_aurora or args.setup_microg or args.list or args.remove
            or args.remove_all or args.version)
    more = tail if (args.install or args.install_request) else []       # the further parts of a split APK
    if mode and ((tail and not more) or level is not None or args.sdk is not None or args.size or args.uri):
        parser.error(_("--install, --list, --remove, --remove-all and --version take no other arguments"))
    if args.purge and not (args.remove or args.remove_all):
        parser.error(_("--purge only works with --remove or --remove-all"))
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
        for w in remove_app(paths, args.remove, args.purge):
            print(f"apk-starter: {w}", file=sys.stderr)
        return 0
    if args.remove_all:
        warnings, errors = remove_all(paths, args.purge)
        for line in warnings + errors:
            print(f"apk-starter: {line}", file=sys.stderr)
        return 1 if errors else 0
    if args.install:
        install_files(paths, [Path(args.install), *map(Path, more)], lang_from_env(env), env)
        return 0
    if args.setup_microg:
        setup_microg(paths, lang_from_env(env), env)
        print(microg_hint())
        return 0
    if args.setup_aurora:
        from . import aurora
        aurora.setup(paths, env, _stderr, _stderr)
        return 0
    if args.install_request:
        from . import install_request
        return install_request.run(paths, env, [Path(args.install_request), *map(Path, more)])
    if args.uninstall_request:
        from . import install_request
        return install_request.run_uninstall(paths, env, args.uninstall_request)
    if args.details:
        from . import gui
        return gui.run(paths, env, show=args.details)
    if args.setup_microg_window:
        from . import gui
        return gui.run(paths, env, setup_microg=True)
    if not tail and level is None and args.sdk is None and not args.size:
        if not any(env.get(v) for v in DISPLAY_VARS):
            parser.error(_("no APK given and no display for the window"))
        from . import gui
        return gui.run(paths, env)
    if not tail:
        parser.error(_("no APK given"))
    return _start(paths, args, level, target, tail, env)


def _start(paths: Paths, args, level: str | None, into: Path | None, tail: list[str],
           env: Mapping[str, str]) -> int:
    target, atl_args = tail[0], tail[1:]
    if args.uri:                                    # empty: the menu entry started without a link
        atl_args = [f"--uri={args.uri}", *atl_args]
    apk = resolve_target(paths, target)
    info = cached_apk_info(paths, apk, lang_from_env(env), read_apk_info)
    if apk != paths.apk(info.package):
        # no start without an install: an APK file (a double click) is installed the first time and again when it
        # is another version than the installed one; otherwise the installed app starts
        own = paths.apk(info.package)
        mine = cached_apk_info(paths, own, lang_from_env(env), read_apk_info) \
            if paths.meta(info.package).is_file() and own.is_file() else None
        if mine is None or (mine.version_code, mine.version_name) != (info.version_code, info.version_name):
            try:                                    # this app first: QUICK is all the start needs, FULL follows
                with dexopt.urgent(paths, str(paths.apk(info.package))):
                    install_files(paths, [apk], lang_from_env(env), env, flt=dexopt.QUICK)
            except NoMenuEntry as e:                # installed all the same: start it, the menu entry is optional
                _stderr(str(e))
            except splits.IncompatibleError as e:   # a double click has no terminal: say it in a window
                from . import install_request
                install_request.tell(describe_error(e), env)
                raise
        apk = paths.apk(info.package)
        info = cached_apk_info(paths, apk, lang_from_env(env), read_apk_info)
    if args.size:
        size = parse_size(args.size)
    else:
        size = remembered_size(paths, info.package) or DEFAULT_SIZE
    started = datetime.now().astimezone()
    mine = load_settings(paths.settings()).app(info.package)
    if level is None:                               # «Debug» / «Debug full» ticked for this app in the window: every start
        level = "debug-full" if mine.debug_full else "debug" if mine.debug else None
    debug_dir = None
    if level is not None:                           # a normal start writes no log
        debug_dir = into or diag.default_debug_dir(paths, info.package, started)
    apk_path = prepare_apk(paths, info)
    parts = paths.installed_splits(info.package) if apk_path == paths.apk(info.package) else []
    cmd = build_command(paths, apk_path, size=size, sdk=sdk_for(info, args.sdk if args.sdk is not None else mine.sdk), atl_args=atl_args,
                        app_id=f"{ENTRY_PREFIX}.{info.package}", splits=parts)
    child_env = build_env(paths, info.package, env)
    if args.size:
        remember_size(paths, info.package, size)
    later = _prepare_start(paths, env, info.package, apk_path)
    if paths.meta(info.package).is_file():         # «last started» in the window
        with contextlib.suppress(OSError):
            paths.started(info.package).touch()
    background = threading.Thread(target=_compile_later, args=(paths, env, later), daemon=True)
    background.start()
    try:
        if debug_dir is not None:
            return diag.record_restarting(paths, cmd, child_env, debug_dir, info.package,
                                          shots=level == "debug-full")
        return run_atl(cmd, child_env, None, {})
    finally:
        if debug_dir is not None:                   # one archive, as soon as the app has ended
            try:
                diag.pack(paths, debug_dir, full=level == "debug-full")
            except StarterError as e:               # the folder stays
                _stderr(str(e))
        diag.prune(paths, info.package)
        # the Flatpak instance (and every process in it) ends with this one: a compile that is nearly done may
        # finish, a long one is given up (renamed into place only when done, tried again at the next start), so
        # that closing the last window always ends the Starter
        background.join(COMPILE_GRACE_S)


def _prepare_start(paths: Paths, env: Mapping[str, str], pkg: str, apk: Path) -> list[str]:
    """Before ATL: runtime if needed (e.g. after a Flatpak update), the app with QUICK if it has no current
    code (direct start, F180), its native libraries meanwhile. Returns the APKs to compile with FULL in the
    background while the app runs. The app being started comes first: every other compile is paused until
    this is done (dexopt.urgent), and what the start needs is only the runtime and this app."""
    location = str(apk)
    with dexopt.urgent(paths, location):
        return _prepare_start_urgent(paths, env, pkg, apk, location)


def _prepare_start_urgent(paths: Paths, env: Mapping[str, str], pkg: str, apk: Path, location: str) -> list[str]:
    try:
        dexopt.ensure_runtime(paths, env, _stderr)
    except (StarterError, OSError) as e:
        _stderr(_("the Android runtime could not be prepared in advance: {reason}").format(reason=describe_error(e)))
        return []
    flt = dexopt.app_filter(paths, location)
    if flt is None:
        try:
            _compile_and_unpack(paths, env, pkg, apk, dexopt.QUICK, is_urgent=True)
        except (StarterError, OSError):
            pass                # ART compiles it at the start and ATL unpacks, as before
    later = [] if flt == dexopt.FULL else [location]
    return later + [loc for loc in dexopt.stale_installed(paths) if loc != location]


def prepare_all(paths: Paths, env: Mapping[str, str], report: Callable[[str], None],
                working: Callable[[dict[str, str]], None] = lambda _w: None) -> list[tuple[str | None, str]]:
    """Window opened after a Flatpak update (or before anything was prepared): runtime and all apps, once;
    then the menu entries get what this starter writes. `working` gets the apps worked on with a short text
    whenever that changes, and {} at the end on every path. Returns the problems (package or None for all,
    text); they are also noted on stderr: ART then compiles at the start, as before."""
    problems: list[tuple[str | None, str]] = []
    try:
        installed = [m["package"] for m in list_installed(paths)]
        if not dexopt.runtime_ready(paths, env):
            working(dict.fromkeys(installed, _("Preparing…")))
        try:
            dexopt.ensure_runtime(paths, env, report)
        except (StarterError, OSError) as e:
            text = _("the Android runtime could not be prepared in advance: {reason}").format(
                reason=describe_error(e))
            _stderr(text)
            problems.append((None, text))
            return problems
        refresh_menu_entries(paths, lang_from_env(env),
                             lambda pkg: working({pkg: _("Updating the menu entry…")}), problems)
        stale = dexopt.stale_installed(paths)
        if stale:
            report(_("Preparing apps for this computer…"))
        _compile_later(paths, env, stale, working)
        return problems
    finally:
        working({})


def menu_mime_types(portal, info: ApkInfo) -> list[str]:
    """MimeType of the menu entry: the links the app opens and the files it takes shared («Open with»)."""
    return portal.link_mime_types(info.link_filters) + portal.share_mime_types(info.share_types)


def _entry_fields(entry: str) -> dict[str, str]:
    return dict(line.split("=", 1) for line in entry.splitlines() if "=" in line and not line.startswith("#"))


def refresh_menu_entries(paths: Paths, lang: str | None, renewing: Callable[[str], None] = lambda _p: None,
                         problems: list[tuple[str | None, str]] | None = None) -> list[str]:
    """Once per starter build: menu entries made by an older starter get the fields it did not write yet
    (e.g. MimeType). Name (the user may have changed it) and Exec (the portal rewrites it) are kept; removed
    entries stay removed. Each renewed entry is a portal confirmation, like installing."""
    from . import portal
    marker = paths.cache_home / "apk-starter" / "menu-entries"
    key = dexopt.runtime_key(paths)
    try:
        if not MENU_ENTRIES or marker.read_text(encoding="utf-8") == key:
            return []
    except OSError:
        pass
    renewed = []
    for meta in list_installed(paths):
        pkg = meta["package"]
        try:
            current = portal.launcher_entry(pkg)
            if current is None:
                continue
            have = _entry_fields(current)
            info = read_apk_info(paths.apk(pkg), lang)
            mime = menu_mime_types(portal, info)
            want = _entry_fields(portal.desktop_entry(pkg, have.get("Name", info.label), mime))
            # an icon an older starter did not find (density parts, adaptive icons)
            new_icon = None if paths.icon(pkg).is_file() else \
                read_icon(paths.apk(pkg), paths.installed_splits(pkg)) or info.icon
            if new_icon is None and all(have.get(k) == v for k, v in want.items() if k not in ("Name", "Exec")):
                continue
            renewing(pkg)
            with package_lock(paths, pkg):
                if new_icon is not None:
                    _write_atomic(paths.icon(pkg), new_icon)
                icon = paths.icon(pkg).read_bytes() if paths.icon(pkg).is_file() else paths.default_icon().read_bytes()
                portal.install_launcher(pkg, want["Name"], icon, mime_types=mime)
            renewed.append(pkg)
        except portal.PortalUnavailable:
            return renewed              # no menu entries without the portal; tried again next time
        except (StarterError, OSError) as e:
            text = _("{app} keeps its old menu entry: {reason}").format(app=meta.get("label", pkg),
                                                                       reason=describe_error(e))
            _stderr(text)
            if problems is not None:
                problems.append((pkg, text))
    with contextlib.suppress(OSError):
        marker.parent.mkdir(parents=True, exist_ok=True)
        _write_atomic(marker, key.encode("utf-8"))
    return renewed


def _compile_later(paths: Paths, env: Mapping[str, str], locations: list[str],
                   working: Callable[[dict[str, str]], None] = lambda _w: None) -> None:
    """Like Android's background dexopt: idle priority, renamed into place, a running app keeps its files."""
    for i, loc in enumerate(locations):
        if Path(loc).parent == paths.apps_dir():    # an installed app: its row in the window
            working({Path(l).stem: _("Preparing…") for l in locations[i:] if Path(l).parent == paths.apps_dir()})
        try:
            dexopt.compile_app(paths, env, loc, dexopt.FULL, idle=True)
        except (StarterError, OSError):
            pass                # stays QUICK (or ART's own); tried again at the next start
