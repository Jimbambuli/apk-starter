"""Start APK Starter from its AppImage (AppRun runs this with the bundled Python).

An AppImage is mounted at a new place (/tmp/.mount_*) on every start and unmounted when its program ends. Two things
need a place that stays:
- ART's compiled files (boot image, ATL's jars, the apps) record the real paths of the jars (ART resolves links);
  with a new path on each start everything would be compiled again.
- A menu entry, ATL's call-backs (ATL_*_COMMAND) and the window start further processes of the same AppImage while
  others run; none of them may lose its files when one of them ends.
So all of them run from one more mount of this build in a directory that stays, <XDG_RUNTIME_DIR>/apk-starter-appimage-
<build>: the first start mounts the AppImage's file system there with the bundled squashfuse (a process of its own),
and a watcher unmounts it when no process uses it any more. Without FUSE (--appimage-extract-and-run, or an extracted
squashfs-root) everything runs from the tree this file is in.

Then the environment of a native build (as bin/apk-starter sets it for an installation by native/meson.build) and of
the bundled libraries; data and menu entries as a native build has them, the Flatpak's stay apart (README).
  apprun.py [ARGS...]           start the starter
  apprun.py --watch MOUNT PID   (internal) end the squashfuse process PID when nothing uses MOUNT any more
"""
import fcntl
import hashlib
import os
import platform
import shutil
import signal
import struct
import subprocess
import sys
import time

APP_ID = "io.github.Jimbambuli.ApkStarter"
BUILD_PREFIX = "/usr/lib/apk-starter"      # packaging/appimage/build.sh: where the native build was installed
LOADERS = "@LOADERS@"                       # make-appdir.py: the loader directory in GdkPixbuf's cache
# ca bundles of the distributions: OpenSSL (Python, ATL's wolfSSL has its own) is built for Debian's place
CA_FILES = ("/etc/ssl/certs/ca-certificates.crt", "/etc/pki/tls/certs/ca-bundle.crt", "/etc/ssl/ca-bundle.pem",
            "/etc/ssl/cert.pem")
# variables this file changes: their values from outside the AppImage are kept for a nested start
SAVED = ("PATH", "LD_LIBRARY_PATH", "XDG_DATA_DIRS")
WATCH_FIRST_S, WATCH_EVERY_S, WATCH_IDLE_ROUNDS = 30, 15, 2


def state_dir() -> str:
    run = os.environ.get("XDG_RUNTIME_DIR", "")
    try:
        st = os.stat(run)
        if run and st.st_uid == os.getuid() and (st.st_mode & 0o077) == 0:
            return run
    except OSError:
        pass
    d = os.path.join(os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache"), APP_ID)
    os.makedirs(d, mode=0o700, exist_ok=True)
    return d


def build_id(tree: str) -> str:
    with open(os.path.join(tree, BUILD_PREFIX.lstrip("/"), "share/apk-starter/build-info"), "rb") as f:
        info = f.read()
    with open(os.path.join(tree, "usr/share/apk-starter-appimage.id"), "rb") as f:
        info += f.read()
    return hashlib.sha256(info).hexdigest()[:12]


def is_tree(path: str, bid: str) -> bool:
    try:
        return build_id(path) == bid
    except OSError:
        return False


def squashfs_offset(appimage: str) -> int:
    """Where the file system starts: after the runtime, an ELF file whose section headers come last."""
    with open(appimage, "rb") as f:
        head = f.read(64)
    shoff, = struct.unpack_from("<Q", head, 0x28)
    shentsize, shnum = struct.unpack_from("<HH", head, 0x3A)
    return shoff + shentsize * shnum


def squashfuse(here: str, base: str, bid: str) -> str:
    """The bundled squashfuse and the libraries it needs (usr/lib/squashfuse.libs, make-appdir.py), copied into the
    state directory once: it outlives the start that runs it, and with it that start's own mount."""
    d = os.path.join(base, f"apk-starter-appimage-{bid}-squashfuse")
    if not os.path.isfile(os.path.join(d, "squashfuse")):
        tmp = f"{d}.{os.getpid()}"
        os.makedirs(tmp)
        shutil.copy2(os.path.join(here, "usr/bin/squashfuse"), tmp)
        with open(os.path.join(here, "usr/lib/squashfuse.libs"), encoding="utf-8") as f:
            for name in f.read().split():
                shutil.copy2(os.path.join(here, "usr/lib", name), tmp)
        try:
            os.rename(tmp, d)
        except OSError:                      # made by another start meanwhile
            shutil.rmtree(tmp, ignore_errors=True)
    return d


def mount(here: str, base: str, bid: str, appimage: str, mp: str) -> subprocess.Popen | None:
    """The AppImage's file system at MP (squashfuse in the foreground of its own session). None without FUSE."""
    os.makedirs(mp, mode=0o700, exist_ok=True)
    tool = squashfuse(here, base, bid)
    env = dict(os.environ, LD_LIBRARY_PATH=tool)
    try:
        p = subprocess.Popen([os.path.join(tool, "squashfuse"), "-f", "-o",
                              f"offset={squashfs_offset(appimage)}", appimage, mp], env=env, stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True, cwd="/")
    except OSError:
        return None
    for _ in range(200):
        if os.path.isfile(os.path.join(mp, "AppRun")):
            return p
        if p.poll() is not None:
            return None
        time.sleep(0.05)
    p.kill()
    return None


def stable_root(here: str, base: str, bid: str) -> str:
    """The mount of this build at its stable place, made under a lock if there is none; else HERE."""
    mp = os.path.join(base, f"apk-starter-appimage-{bid}")
    with open(mp + ".lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if is_tree(mp, bid):
            return mp
        appimage = os.environ.get("APPIMAGE", "")
        if not appimage or not os.path.isfile(appimage) or os.environ.get("APPIMAGE_EXTRACT_AND_RUN"):
            return here
        # a mount whose squashfuse was killed ("Transport endpoint is not connected")
        subprocess.run(["fusermount3", "-u", "-z", mp], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL)
        p = mount(here, base, bid, appimage, mp)
        if p is None or not is_tree(mp, bid):
            if p is not None:
                p.terminate()
            return here
        # the watcher runs from the stable mount too (it does not count itself)
        subprocess.Popen([os.path.join(mp, "usr/bin/python3"), "-I", os.path.join(mp, "apprun.py"), "--watch", mp,
                          str(p.pid)],
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True, cwd="/")
        return mp


def runtime_file(base: str, bid: str, name: str, make) -> str:
    """A file of this build in the state directory, written once (make() gives its text)."""
    path = os.path.join(base, f"apk-starter-appimage-{bid}-{name}")
    if not os.path.isfile(path):
        tmp = f"{path}.{os.getpid()}"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(make())
        os.replace(tmp, path)
    return path


def digitalis_root(top: str, base: str) -> str | None:
    """BERBERIS_SYSTEM_ROOT for ATL's native bridge: the guest linker reads <root>/system/etc/ld.config.arm64.txt,
    whose namespaces must name the real directories of the tree (in the stable mount). A tree of links to the bundle
    with that one file written here."""
    tree = os.path.join(top, BUILD_PREFIX.lstrip("/"), "lib/digitalis/system")
    root = os.path.join(base, f"apk-starter-appimage-{hashlib.sha256(tree.encode()).hexdigest()[:12]}-digitalis")
    if platform.machine() != "x86_64" or not os.path.isdir(tree):
        return None
    if os.path.isdir(os.path.join(root, "system/etc")):
        return root
    tmp = f"{root}.{os.getpid()}"
    os.makedirs(os.path.join(tmp, "system/etc"))
    for name in os.listdir(tree):
        if name != "etc":
            os.symlink(os.path.join(tree, name), os.path.join(tmp, "system", name))
    for name in os.listdir(os.path.join(tree, "etc")):
        src, dst = os.path.join(tree, "etc", name), os.path.join(tmp, "system/etc", name)
        if name.startswith("ld.config."):
            with open(src, encoding="utf-8") as f:
                text = f.read()
            with open(dst, "w", encoding="utf-8") as f:
                f.write(text.replace(f"{BUILD_PREFIX}/lib/digitalis/system", tree))
        else:
            os.symlink(src, dst)
    try:
        os.rename(tmp, root)
    except OSError:                          # made by another start meanwhile
        pass
    return root


def uses(pid: str, mount: str) -> bool:
    try:
        for name in ("cwd", "exe"):
            if os.readlink(f"/proc/{pid}/{name}").startswith(mount + "/"):
                return True
        with open(f"/proc/{pid}/maps", encoding="utf-8", errors="replace") as f:
            return any(mount + "/" in line for line in f)
    except OSError:
        return False


def watch(mount: str, keeper: int) -> None:
    """End the extra mount (squashfuse, process KEEPER) once nothing has a file of it open, a directory or a mapping."""
    me = str(os.getpid())
    time.sleep(WATCH_FIRST_S)
    idle = 0
    while idle < WATCH_IDLE_ROUNDS:
        try:
            os.kill(keeper, 0)
        except OSError:
            break
        busy = any(uses(p, mount) for p in os.listdir("/proc") if p.isdigit() and p not in (me, str(keeper)))
        idle = 0 if busy else idle + 1
        if idle < WATCH_IDLE_ROUNDS:
            time.sleep(WATCH_EVERY_S)
    with open(mount + ".lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            os.kill(keeper, signal.SIGTERM)      # squashfuse unmounts and ends
        except OSError:
            pass
        for _ in range(100):
            if not os.path.ismount(mount):
                break
            time.sleep(0.1)
        try:
            os.rmdir(mount)
        except OSError:
            pass


def ca_file() -> str | None:
    return next((f for f in CA_FILES if os.path.isfile(f)), None)


def main() -> None:
    if sys.argv[1:2] == ["--watch"]:
        watch(sys.argv[2], int(sys.argv[3]))
        return
    here = os.path.dirname(os.path.realpath(__file__))
    base, bid = state_dir(), build_id(here)
    root = stable_root(here, base, bid)
    prefix = os.path.join(root, BUILD_PREFIX.lstrip("/"))
    env = os.environ
    for var in SAVED:                        # the values from outside, also for a start from inside the AppImage
        key = f"APK_STARTER_APPIMAGE_OUTSIDE_{var}"
        if key not in env:
            env[key] = env.get(var, "")
    outside = {var: env[f"APK_STARTER_APPIMAGE_OUTSIDE_{var}"] for var in SAVED}
    if env.get("APK_STARTER_NATIVE") != "1":
        # the native build's folders (bin/apk-starter): the Flatpak has ~/.var/app/<id>, so the two never meet
        home = os.path.expanduser("~")
        data = env.get("XDG_DATA_HOME") or os.path.join(home, ".local/share")
        cache = env.get("XDG_CACHE_HOME") or os.path.join(home, ".cache")
        env["APK_STARTER_NATIVE"] = "1"
        env["APK_STARTER_MENU_DIR"] = os.path.join(data, "applications")
        env["XDG_DATA_HOME"] = os.path.join(data, APP_ID)
        env["XDG_CACHE_HOME"] = os.path.join(cache, APP_ID)
    env["APK_STARTER_PREFIX"] = prefix
    # menu entries and ATL's call-backs start the AppImage file again (or AppRun of an extracted tree)
    env["APK_STARTER_PROGRAM"] = env.get("APPIMAGE") or os.path.join(here, "AppRun")
    lib = os.path.join(root, "usr/lib")
    env["PATH"] = ":".join(filter(None, [os.path.join(prefix, "bin"), os.path.join(root, "usr/bin"), outside["PATH"]]))
    env["LD_LIBRARY_PATH"] = ":".join(filter(None, [os.path.join(prefix, "lib"), os.path.join(prefix, "lib/art"), lib,
                                                    outside["LD_LIBRARY_PATH"]]))
    # <prefix>/share as in the Flatpak (/app/share): bionic_translation reads its library overrides from
    # <data dir>/bionic_translation/cfg.d (libc.so of the native bridge, libmediandk.so)
    env["XDG_DATA_DIRS"] = ":".join([os.path.join(prefix, "share"), os.path.join(root, "usr/share"),
                                     outside["XDG_DATA_DIRS"] or "/usr/local/share:/usr/share"])
    env["GI_TYPELIB_PATH"] = os.path.join(root, "usr/lib/girepository-1.0")
    env["GIO_MODULE_DIR"] = os.path.join(root, "usr/lib/gio/modules")
    # GdkPixbuf's loaders (SVG icons) by absolute path in its cache: written for this mount
    loaders = os.path.join(root, "usr/lib/gdk-pixbuf-2.0/loaders")
    with open(os.path.join(root, "usr/lib/gdk-pixbuf-2.0/loaders.cache"), encoding="utf-8") as f:
        cache = f.read().replace(LOADERS, loaders)
    env["GDK_PIXBUF_MODULE_FILE"] = runtime_file(base, hashlib.sha256(loaders.encode()).hexdigest()[:12],
                                                 "loaders.cache", lambda: cache)
    # WebKit runs its helper processes from a path compiled in, rewritten to one relative to <root>/usr (make-appdir.py);
    # its bubblewrap sandbox cannot bind that relative path (README, «AppImage»)
    env["WEBKIT_DISABLE_SANDBOX_THIS_IS_DANGEROUS"] = "1"
    if "SSL_CERT_FILE" not in env and (ca := ca_file()):
        env["SSL_CERT_FILE"] = ca
    if bridge_root := digitalis_root(root, base):
        env["BERBERIS_SYSTEM_ROOT"] = bridge_root
    for k in ("PYTHONHOME", "PYTHONPATH"):
        env.pop(k, None)
    args = [os.path.abspath(a) if os.path.exists(a) else a for a in sys.argv[1:]]   # the working directory changes
    os.chdir(os.path.join(root, "usr"))
    python = os.path.join(root, "usr/bin/python3")
    os.execv(python, [python, os.path.join(prefix, "bin/apk-starter"), *args])


if __name__ == "__main__":
    main()
