"""Compile ahead of time, like Android's install dexopt (installd/dex2oat, NativeLibraryHelper).

Without this, ART does all of it inside the first start of an app (8.4 s for JW Library): it generates the
boot image, compiles ATL's jars and the app (filter speed), and ATL unpacks the native libraries.
Here the same files are put where ART and ATL look for them, so the first start only loads them:

- runtime (shared by all apps, once per Flatpak build): ART itself (`dalvikvm`, same image location and
  class path as ATL) checks the boot image and api-impl.jar and generates them if missing or invalid;
  ghax.jar is compiled with dex2oat.
- app: dex2oat with the class loader context ATL loads it with (ghax.jar, then api-impl.jar), written to a
  temporary directory and renamed into place, so a running app keeps its mapped files.
- native libraries: lib/x86_64/* to <data>/<pkg>/<pkg>.apk_/lib/, newer than the APK, so ATL skips them.

Every path and argument is spelled exactly as ART/ATL spell them (see rundgang/startzeit-2026-11-01):
ART only takes a file whose name and recorded class loader context match its own."""
import configparser
import contextlib
import fcntl
import hashlib
import json
import os
import shutil
import subprocess
import time
import zipfile
from collections.abc import Callable, Iterator, Mapping
from pathlib import Path, PurePosixPath

from .errors import StarterError
from .i18n import _
from .paths import Paths

FLATPAK_INFO = Path("/.flatpak-info")
ISA = "x86_64"
# what ART passes for this ISA (Runtime::AddCurrentRuntimeFeaturesAsDex2OatArguments)
ISA_FEATURES = "-ssse3,-sse4.1,-sse4.2,-avx,-avx2,-popcnt"
FULL = "speed"          # what ART uses without a filter, i.e. the code quality apps had so far
QUICK = "verify"        # Android's install filter without a profile: no AOT code, 6x faster to make
STALE_TMP_S = 3600


class DexoptError(StarterError):
    pass


def _dex_dir(paths: Paths) -> str:
    # ATL and ART find their jars relative to libart.so: dladdr() path + "/../java/dex", not normalised
    return f"{paths.prefix}/lib/art/../java/dex"


def jar(paths: Paths, name: str) -> str:
    return f"{_dex_dir(paths)}/android_translation_layer/{name}"


def boot_image(paths: Paths) -> str:
    return f"{_dex_dir(paths)}/art/oat/boot.art"


def app_context(paths: Paths) -> str:
    """ATLLoadedApp: PathClassLoader(ghax.jar:app.apk) whose parent loads api-impl.jar."""
    return f"PCL[{jar(paths, 'ghax.jar')}];PCL[{jar(paths, 'api-impl.jar')}]"


def dalvik_cache(paths: Paths, env: Mapping[str, str] | None = None) -> Path:
    """art_standalone GetDalvikCache(): $ANDROID_DATA/dalvik-cache, else $XDG_CACHE_HOME/art."""
    data = (env if env is not None else os.environ).get("ANDROID_DATA")
    return (Path(data) / "dalvik-cache" if data else paths.cache_home / "art") / ISA


def cache_file(paths: Paths, location: str, env: Mapping[str, str] | None = None) -> Path:
    """GetDalvikCacheFilename()."""
    name = location[1:]
    if not name.endswith((".dex", ".art", ".oat")):
        name += "/classes.dex"
    return dalvik_cache(paths, env) / name.replace("/", "@")


def _vdex(oat: Path) -> Path:
    return oat.with_name(oat.name[:-len(oat.suffix)] + ".vdex")


def runtime_key(paths: Paths) -> str:
    """Changes with every Flatpak build (the commit); outside a Flatpak with the build-info file."""
    cp = configparser.ConfigParser(interpolation=None, strict=False)
    try:
        cp.read(FLATPAK_INFO, encoding="utf-8")
        commit = cp.get("Instance", "app-commit", fallback="")
    except (OSError, configparser.Error, UnicodeError):
        commit = ""
    if commit:
        return commit
    try:
        raw = paths.build_info().read_bytes()
    except OSError:
        raw = b""
    return "build-info-" + hashlib.sha256(raw).hexdigest()[:16]


def _state_dir(paths: Paths) -> Path:
    return paths.cache_home / "apk-starter" / "dexopt"


def _marker(paths: Paths) -> Path:
    return _state_dir(paths) / "runtime"


def _state_file(paths: Paths, location: str) -> Path:
    return _state_dir(paths) / (cache_file(paths, location).name + ".json")


@contextlib.contextmanager
def _flock(path: Path, blocking: bool = True) -> Iterator[bool]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as fd:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        except BlockingIOError:
            yield False
            return
        yield True


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _run(cmd: list[str], env: Mapping[str, str], idle: bool = False) -> subprocess.CompletedProcess:
    def lower_priority() -> None:
        with contextlib.suppress(OSError, AttributeError):
            os.sched_setscheduler(0, os.SCHED_IDLE, os.sched_param(0))
        with contextlib.suppress(OSError):
            os.nice(19)
    try:
        return subprocess.run(cmd, env=dict(env), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                              stderr=subprocess.PIPE, preexec_fn=lower_priority if idle else None)
    except OSError as e:
        raise DexoptError(_("cannot run {program}: {reason}").format(program=cmd[0], reason=e.strerror)) from None


def _failure(proc: subprocess.CompletedProcess) -> str:
    lines = [l for l in proc.stderr.decode("utf-8", "replace").splitlines() if l.strip()]
    return lines[-1].strip() if lines else f"exit {proc.returncode}"


def _dex2oat(paths: Paths, env: Mapping[str, str], location: str, context: str, flt: str, idle: bool) -> None:
    """Compile into a private directory next to the target, then rename: never rewrite a mapped file."""
    out = cache_file(paths, location, env)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.parent / f".apk-starter-{os.getpid()}-{time.monotonic_ns()}"
    tmp.mkdir()
    try:
        proc = _run(["dex2oat", f"--dex-file={location}", f"--oat-file={tmp / out.name}",
                     f"--instruction-set={ISA}", f"--instruction-set-features={ISA_FEATURES}", "--host",
                     f"--boot-image={boot_image(paths)}", f"--class-loader-context={context}",
                     f"--compiler-filter={flt}"], env, idle)
        if proc.returncode != 0 or not (tmp / out.name).is_file():
            raise DexoptError(_failure(proc))
        if _vdex(tmp / out.name).is_file():
            os.replace(_vdex(tmp / out.name), _vdex(out))
        os.replace(tmp / out.name, out)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _clean_tmp(cache: Path) -> None:
    """Temp dirs of compiles that were killed (Flatpak ends all processes of an instance with its main one)."""
    with contextlib.suppress(OSError):
        for d in cache.glob(".apk-starter-*"):
            with contextlib.suppress(OSError):
                if time.time() - d.stat().st_mtime > STALE_TMP_S:
                    shutil.rmtree(d, ignore_errors=True)


def runtime_ready(paths: Paths, env: Mapping[str, str] | None = None) -> bool:
    try:
        ready = _marker(paths).read_text(encoding="utf-8") == runtime_key(paths)
    except OSError:
        return False
    return ready and all(cache_file(paths, loc, env).is_file()
                         for loc in (boot_image(paths), jar(paths, "api-impl.jar"), jar(paths, "ghax.jar")))


def ensure_runtime(paths: Paths, env: Mapping[str, str], report: Callable[[str], None]) -> None:
    """Boot image + ATL's jars for this computer, once per Flatpak build; waits while another process does it."""
    if runtime_ready(paths, env):
        return
    with _flock(paths.cache_home / "apk-starter" / "locks" / "runtime.lock"):
        if runtime_ready(paths, env):
            return
        report(_("Preparing the Android runtime for this computer…"))
        _clean_tmp(dalvik_cache(paths, env))
        # ART checks what is there (checksums against the jars of this build) and generates what is missing
        # or invalid; dalvikvm then stops with "Class name required", so success is judged by the files.
        proc = _run(["dalvikvm", f"-Ximage:{boot_image(paths)}",
                     "-cp", f"{jar(paths, 'api-impl.jar')}:{jar(paths, 'framework-res.apk')}"], env)
        if not (cache_file(paths, boot_image(paths), env).is_file()
                and cache_file(paths, jar(paths, "api-impl.jar"), env).is_file()):
            raise DexoptError(_failure(proc))
        _dex2oat(paths, env, jar(paths, "ghax.jar"), f"PCL[];PCL[{jar(paths, 'api-impl.jar')}]", FULL, False)
        _write(_marker(paths), runtime_key(paths))


def _apk_id(location: str) -> list[int] | None:
    try:
        st = os.stat(location)
    except OSError:
        return None
    return [st.st_size, st.st_mtime_ns]


def app_filter(paths: Paths, location: str) -> str | None:
    """Filter of the app's compiled code if it is current (same Flatpak build, same APK), else None."""
    try:
        state = json.loads(_state_file(paths, location).read_text(encoding="utf-8"))
        if (state["key"] == runtime_key(paths) and state["apk"] == _apk_id(location)
                and cache_file(paths, location).is_file()):
            return state["filter"]
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return None


def compile_app(paths: Paths, env: Mapping[str, str], location: str, flt: str, idle: bool = False) -> None:
    """dex2oat for the APK at `location` (the path ATL is given). Skipped if another process compiles it."""
    lock = paths.cache_home / "apk-starter" / "locks" / (cache_file(paths, location).name + ".lock")
    with _flock(lock, blocking=False) as got:
        if not got:
            return
        apk = _apk_id(location)
        _dex2oat(paths, env, location, app_context(paths), flt, idle)
        _write(_state_file(paths, location), json.dumps({"key": runtime_key(paths), "apk": apk, "filter": flt}))


def extract_natives(paths: Paths, pkg: str, apk: Path) -> None:
    """ATL's extractFromAPK("lib/x86_64/", "lib/"): same target, and a file newer than the APK is kept."""
    prefix = f"lib/{ISA}/"
    lib = paths.data(pkg) / f"{pkg}.apk_" / "lib"
    apk_mtime = os.stat(apk).st_mtime_ns
    try:
        with zipfile.ZipFile(apk) as z:
            for entry in z.infolist():
                rel = PurePosixPath(entry.filename[len(prefix):])
                if (not entry.filename.startswith(prefix) or entry.is_dir() or not rel.parts
                        or rel.is_absolute() or ".." in rel.parts):
                    continue
                dest = lib.joinpath(*rel.parts)
                with contextlib.suppress(OSError):
                    if dest.stat().st_mtime_ns >= apk_mtime:
                        continue
                dest.parent.mkdir(parents=True, exist_ok=True)
                tmp = dest.with_name(f"{dest.name}.{os.getpid()}.tmp")
                with z.open(entry) as src, open(tmp, "wb") as dst:
                    shutil.copyfileobj(src, dst, 1 << 20)
                os.replace(tmp, dest)
    except (zipfile.BadZipFile, zipfile.LargeZipFile, RuntimeError, EOFError, ValueError) as e:
        raise DexoptError(str(e)) from None


def stale_installed(paths: Paths) -> list[str]:
    """Installed apps whose compiled code is missing or not FULL for this Flatpak build."""
    out = []
    for meta in sorted(paths.apps_dir().glob("*.json")):
        loc = str(paths.apps_dir() / f"{meta.stem}.apk")
        if os.path.isfile(loc) and app_filter(paths, loc) != FULL:
            out.append(loc)
    return out
