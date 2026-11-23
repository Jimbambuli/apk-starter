"""make-appdir.py APPDIR SOURCE BUILD_ID: turn the native build installed in APPDIR/usr/lib/apk-starter (build.sh) into
the AppDir of the AppImage, on Debian 13 (x86_64), with the files of this system:
- Python (interpreter, standard library, PyGObject) and the typelibs, squashfuse, GIO's TLS module, GdkPixbuf's loaders, GSettings
  schemas, the Adwaita icon theme, WebKitGTK's helper processes;
- every library these and the native build need, except those of the host (excludelist), into APPDIR/usr/lib, with
  the Debian copyright file of each;
- RUNPATHs into the build prefix become $ORIGIN-relative (the AppImage is mounted somewhere else on every start);
- WebKitGTK's compiled-in directory of its helper processes, which no variable changes, becomes ././/lib/... (same
  length), relative to <AppDir>/usr, the working directory apprun.py sets;
- AppRun, apprun.py, desktop entry and icon at the top."""
import fnmatch
import os
import re
import shutil
import subprocess
import sys
import sysconfig
from pathlib import Path

PREFIX = "/usr/lib/apk-starter"
APP_ID = "io.github.Jimbambuli.ApkStarter"
MULTIARCH = "/usr/lib/x86_64-linux-gnu"
WEBKIT_OLD = f"{MULTIARCH}/webkitgtk-6.0".encode()
WEBKIT_NEW = b"././" + WEBKIT_OLD[len(b"/usr"):]       # ././/lib/...: same length, relative to <AppDir>/usr
assert len(WEBKIT_NEW) == len(WEBKIT_OLD)
NOT_ELF = (".oat", ".odex", ".vdex", ".art", ".py", ".pyc", ".jar", ".apk", ".typelib")


def log(msg: str) -> None:
    print(f"make-appdir: {msg}", flush=True)


def is_elf(f: Path) -> bool:
    if f.is_symlink() or not f.is_file() or f.suffix in NOT_ELF:
        return False
    with open(f, "rb") as fh:
        return fh.read(4) == b"\x7fELF"


def elfs(top: Path, skip: Path | None = None) -> list[Path]:
    return [f for f in sorted(top.rglob("*")) if (skip is None or skip not in f.parents) and is_elf(f)]


def copy(src: str | Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if Path(src).is_dir():
        shutil.copytree(src, dst, symlinks=True, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns("test", "tests", "idlelib", "tkinter", "turtledemo", "ensurepip"))
    else:
        shutil.copy2(src, dst)


def runtime_files(a: Path) -> None:
    ver = f"python{sys.version_info[0]}.{sys.version_info[1]}"
    copy(f"/usr/bin/{ver}", a / "usr/bin/python3")
    copy("/usr/bin/squashfuse", a / "usr/bin/squashfuse")    # apprun.py: the mount at a place that stays
    copy(sysconfig.get_path("stdlib"), a / f"usr/lib/{ver}")
    copy("/usr/lib/python3/dist-packages/gi", a / "usr/lib/python3/dist-packages/gi")
    copy(f"{MULTIARCH}/girepository-1.0", a / "usr/lib/girepository-1.0")
    copy(f"{MULTIARCH}/webkitgtk-6.0", a / f"usr/lib/x86_64-linux-gnu/webkitgtk-6.0")
    copy(f"{MULTIARCH}/gio/modules/libgiognutls.so", a / "usr/lib/gio/modules/libgiognutls.so")
    loaders = a / "usr/lib/gdk-pixbuf-2.0/loaders"
    for f in Path(f"{MULTIARCH}/gdk-pixbuf-2.0/2.10.0/loaders").glob("*.so"):
        copy(f, loaders / f.name)
    # the loaders' directory as a placeholder: apprun.py writes the cache with the real one
    cache = subprocess.run([f"{MULTIARCH}/gdk-pixbuf-2.0/gdk-pixbuf-query-loaders", *sorted(map(str, loaders.glob("*.so")))],
                           capture_output=True, text=True, check=True).stdout
    (loaders.parent / "loaders.cache").write_text(cache.replace(str(loaders), "@LOADERS@"), encoding="utf-8")
    # GTK's icon theme, as the Flatpak's runtime has it (a KDE system often has none: GTK would lack icons)
    copy("/usr/share/icons/Adwaita", a / "usr/share/icons/Adwaita")
    copy("/usr/share/icons/hicolor/index.theme", a / "usr/share/icons/hicolor/index.theme")
    schemas = a / "usr/share/glib-2.0/schemas"
    for f in Path("/usr/share/glib-2.0/schemas").glob("*.xml"):
        copy(f, schemas / f.name)
    subprocess.run(["glib-compile-schemas", schemas], check=True)


def excluded(name: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(name, p) for p in patterns)


def bundle_libraries(a: Path, patterns: list[str]) -> list[str]:
    """ldd of every ELF file of the AppDir (with the private tree in the library path); what comes from the system
    and is not excluded goes to usr/lib. Returns the sources."""
    p = a / PREFIX.lstrip("/")
    env = dict(os.environ, LD_LIBRARY_PATH=":".join(map(str, [p / "lib", p / "lib/art", a / "usr/lib",
                                                                 p / "lib/java/dex/android_translation_layer/natives"])))
    own = set()
    for f in elfs(a):
        own.add(f.name)
    seen: dict[str, str] = {}
    todo = elfs(a, skip=p / "lib/digitalis")
    while todo:
        f = todo.pop()
        out = subprocess.run(["ldd", f], capture_output=True, text=True, env=env).stdout
        for m in re.finditer(r"^\s*(\S+) => (/\S+)", out, re.M):
            name, path = m[1], m[2]
            if name in seen or name in own or excluded(name, patterns) or path.startswith(str(a)):
                continue
            seen[name] = os.path.realpath(path)
            dst = a / "usr/lib" / name
            shutil.copy2(seen[name], dst)
            todo.append(dst)                 # ldd lists all of a file's dependencies; the copy is checked for new ones
    for name, path in sorted(seen.items()):
        log(f"bundled {name} <- {path}")
    missing = set()
    for f in elfs(a, skip=p / "lib/digitalis"):
        out = subprocess.run(["ldd", f], capture_output=True, text=True, env=env).stdout
        missing.update(m[1] for m in re.finditer(r"^\s*(\S+) => not found", out, re.M))
    if missing:
        log(f"not found (ok only for the libraries ART/bionic load on their own): {' '.join(sorted(missing))}")
    return sorted(set(seen.values()))


def copyrights(a: Path, sources: list[str]) -> None:
    """The Debian copyright file of each package a bundled file comes from."""
    files = sources + [f"/usr/bin/python{sys.version_info[0]}.{sys.version_info[1]}",
                       "/usr/lib/python3/dist-packages/gi/__init__.py", f"{MULTIARCH}/gio/modules/libgiognutls.so",
                       f"{MULTIARCH}/webkitgtk-6.0", f"{MULTIARCH}/gdk-pixbuf-2.0/2.10.0/loaders",
                       "/usr/share/icons/Adwaita/index.theme", "/usr/bin/squashfuse", "/usr/share/glib-2.0/schemas/org.gtk.gtk4.Settings.FileChooser.gschema.xml"]
    pkgs = set()
    for f in files:
        r = subprocess.run(["dpkg", "-S", f], capture_output=True, text=True)
        if r.returncode == 0:
            pkgs.update(x.strip().split(":")[0] for x in r.stdout.split(": ")[0].split(","))
    for pkg in sorted(pkgs):
        src = Path("/usr/share/doc") / pkg / "copyright"
        if src.is_file():
            copy(src, a / "usr/share/doc" / pkg / "copyright")
    log(f"copyright files of {len(pkgs)} Debian packages")


def relative_rpaths(a: Path) -> None:
    p = a / PREFIX.lstrip("/")
    n = 0
    for f in elfs(p, skip=p / "lib/digitalis"):
        old = subprocess.run(["patchelf", "--print-rpath", f], capture_output=True, text=True).stdout.strip()
        if PREFIX not in old:
            continue
        parts = []
        for e in old.split(":"):
            if e.startswith(PREFIX):
                e = "$ORIGIN/" + os.path.relpath(a / e.lstrip("/"), f.parent)
            parts.append(e)
        subprocess.run(["patchelf", "--set-rpath", ":".join(parts), f], check=True)
        n += 1
    log(f"{n} RUNPATHs made relative")


def squashfuse_libs(a: Path) -> None:
    """The bundled libraries squashfuse needs: apprun.py copies them with it out of the AppImage."""
    out = subprocess.run(["ldd", a / "usr/bin/squashfuse"], capture_output=True, text=True,
                         env=dict(os.environ, LD_LIBRARY_PATH=str(a / "usr/lib"))).stdout
    names = sorted(m[1] for m in re.finditer(r"^\s*(\S+) => (\S+)", out, re.M) if m[2].startswith(str(a)))
    (a / "usr/lib/squashfuse.libs").write_text("\n".join(names) + "\n", encoding="utf-8")
    log(f"squashfuse needs from the AppImage: {' '.join(names)}")


def webkit_dir(a: Path) -> None:
    n = 0
    for f in elfs(a / "usr/lib", skip=a / PREFIX.lstrip("/")):
        data = f.read_bytes()
        if WEBKIT_OLD in data:
            f.write_bytes(data.replace(WEBKIT_OLD, WEBKIT_NEW))
            n += 1
            log(f"WebKit directory relative in {f.relative_to(a)} ({data.count(WEBKIT_OLD)}x)")
    if not n:
        sys.exit("make-appdir: WebKitGTK's directory not found in any bundled file")


def top_files(a: Path, src: Path, build_id: str) -> None:
    here = src / "packaging/appimage"
    copy(here / "AppRun", a / "AppRun")
    os.chmod(a / "AppRun", 0o755)
    copy(here / "apprun.py", a / "apprun.py")
    p = a / PREFIX.lstrip("/")
    entry = (p / f"share/applications/{APP_ID}.desktop").read_text(encoding="utf-8")
    entry = re.sub(r"^Exec=.*$", "Exec=apk-starter %f", entry, flags=re.M)
    entry = re.sub(r"^Icon=.*$", f"Icon={APP_ID}", entry, flags=re.M)
    (a / f"{APP_ID}.desktop").write_text(entry, encoding="utf-8")
    icon = p / f"share/icons/hicolor/scalable/apps/{APP_ID}.svg"
    copy(icon, a / f"{APP_ID}.svg")
    copy(icon, a / f"usr/share/icons/hicolor/scalable/apps/{APP_ID}.svg")
    copy(p / f"share/metainfo/{APP_ID}.metainfo.xml", a / f"usr/share/metainfo/{APP_ID}.appdata.xml")
    os.symlink(f"{APP_ID}.svg", a / ".DirIcon")
    (a / "usr/share/apk-starter-appimage.id").write_text(build_id + "\n", encoding="utf-8")


def main() -> None:
    a, src, build_id = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
    patterns = [line.strip() for line in (src / "packaging/appimage/excludelist").read_text().splitlines()
                if line.strip() and not line.startswith("#")]
    runtime_files(a)
    relative_rpaths(a)
    copyrights(a, bundle_libraries(a, patterns))
    squashfuse_libs(a)
    webkit_dir(a)
    top_files(a, src, build_id)


if __name__ == "__main__":
    main()
