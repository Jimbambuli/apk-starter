"""Per-app sandbox: each app runs in a Flatpak sub-sandbox of its own, so it cannot read another app's data.

Android gives every app its own Linux user ID; in the Flatpak all apps run as the same user, so the starter gives
every app its own sandbox instead (flatpak-spawn --sandbox, started by flatpak-portal next to the Starter's own).
It sees only:

- its data folder (rw), the installed APKs (apps/, read only, as Android's /data/app) and ART's caches (read only:
  the starter compiles, as Android's installd does);
- microG's shared data folder (rw): ATL loads Play Services into the app's own process (see microg.py);
- the keyrings of the Secret portal (rw): ATL's AccountManager keeps the accounts there for all apps;
- the user's folders as the Flatpak has them (Downloads rw; Pictures, Documents, Music, Videos read only) and the
  desktop's GTK/KDE settings (read only);
- display, sound, GPU (sandbox flags), network (inherited from the Starter's sandbox);
- D-Bus through a filtered proxy (xdg-dbus-proxy) of the Starter, not flatpak's own sub-sandbox bus: that would let
  the app call org.freedesktop.portal.Flatpak.Spawn without --sandbox, i.e. leave its sandbox.

Install, uninstall, app details and starting another app (ATL_*_COMMAND) need the Starter's sandbox: inside the app's
sandbox the same commands go over a socket to the Starter that started the app (relay), which runs them, as Android's
system_server serves an app's Binder calls with the caller's identity. The files of an install are handed over as
open file descriptors, so an app can hand over only what it can read itself."""
import array
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
from collections.abc import Mapping
from pathlib import Path

from .apkinfo import PACKAGE_RE
from .microg import SERVICES
from .paths import Paths

RELAY_ENV = "APK_STARTER_RELAY"
OFF_ENV = "APK_STARTER_ISOLATION"         # "0": all apps in the Starter's sandbox, as up to 0.9.3 (diagnosis)
SPAWN = "flatpak-spawn"
PROXY = "xdg-dbus-proxy"
SUN_PATH_MAX = 107
# set by flatpak in each sandbox for its own sockets, buses and instance: never handed on
NOT_INHERITED = {"DBUS_SESSION_BUS_ADDRESS", "DBUS_SYSTEM_BUS_ADDRESS", "AT_SPI_BUS_ADDRESS", "DISPLAY",
                 "WAYLAND_DISPLAY", "XAUTHORITY", "PULSE_SERVER", "PULSE_CLIENTCONFIG", "XDG_RUNTIME_DIR",
                 "FLATPAK_ID", "FLATPAK_ARCH", "FLATPAK_DEST", "FLATPAK_SANDBOX_DIR", "container", RELAY_ENV}
# the desktop settings the manifest gives the Flatpak (finish-args xdg-config/...:ro)
DESKTOP_CONFIG = ("gtk-4.0", "gtk-3.0/assets", "kdeglobals", "kwinrc", "breezerc")
USER_DIRS = (("DOWNLOAD", False), ("PICTURES", True), ("DOCUMENTS", True), ("MUSIC", True), ("VIDEOS", True))
MAX_REQUEST = 1 << 16
MAX_FDS = 64


def enabled(env: Mapping[str, str]) -> bool:
    return (Path("/.flatpak-info").is_file() and env.get(OFF_ENV) != "0" and shutil.which(SPAWN) is not None)


def user_dirs(config: Path, home: Path) -> dict[str, Path]:
    """XDG_<NAME>_DIR of user-dirs.dirs (the format of xdg-user-dirs: NAME="$HOME/..." or an absolute path)."""
    out: dict[str, Path] = {}
    try:
        text = (config / "user-dirs.dirs").read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return out
    for m in re.finditer(r'^XDG_([A-Z]+)_DIR="([^"]*)"', text, re.M):
        value = m.group(2)
        path = home / value[6:] if value.startswith("$HOME/") else Path(value)
        if path.is_absolute() and path != home:
            out[m.group(1)] = path
    return out


class Sandbox:
    """The app's sandbox for one start: command() wraps ATL's command line, close() ends the proxy and the relay."""

    def __init__(self, paths: Paths, pkg: str, app_id: str, env: Mapping[str, str]):
        self.paths, self.pkg, self.app_id, self.env = paths, pkg, app_id, env
        self.home = paths.data_home / "sandbox" / pkg            # the app's XDG_CONFIG/DATA/CACHE_HOME
        self.sockets = paths.cache_home / "sandbox"
        self._proxy: subprocess.Popen | None = None
        self._proxy_ready = -1
        self._relay: socket.socket | None = None
        self.bus = self.sockets / f"{os.getpid()}.bus"
        self.relay = self.sockets / f"{os.getpid()}.relay"

    # -- what the sandbox sees ---------------------------------------------------------------------------------
    def _config_sources(self) -> list[Path]:
        home = Path(self.env.get("HOME", "/"))
        bases = [Path(p) for p in (self.env.get("XDG_CONFIG_HOME"), self.env.get("HOST_XDG_CONFIG_HOME")) if p]
        return [*bases, home / ".config"]

    def _desktop_config(self) -> dict[str, Path]:
        """name -> the path it has outside the Flatpak's own folder (flatpak-portal exposes only paths that are the
        same file inside and outside the sandbox; ~/.var/app/<id>/config/gtk-4.0 is a bind mount of ~/.config/...)."""
        instance = Path(self.env.get("HOME", "/")) / ".var/app" / self.env.get("FLATPAK_ID", "-")
        out: dict[str, Path] = {}
        for name in DESKTOP_CONFIG:
            for base in self._config_sources():
                p = base / name
                if p.exists() and not p.resolve().is_relative_to(instance.resolve()):
                    out[name] = p
                    break
        return out

    def _prepare_home(self, config: dict[str, Path]) -> None:
        """XDG folders of the app: its own, with ART's cache, the keyrings and the desktop settings linked in."""
        cfg, data, cache = self.home / "config", self.home / "data", self.home / "cache"
        for d in (cfg, data, cache, self.paths.data_home / "keyrings", self.paths.cache_home / "art"):
            d.mkdir(parents=True, exist_ok=True)
        _link(cache / "art", self.paths.cache_home / "art")      # art_standalone: $XDG_CACHE_HOME/art
        _link(data / "keyrings", self.paths.data_home / "keyrings")    # libsecret's file backend
        for name, src in config.items():
            dest = cfg / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            _link(dest, src)
        own = Path(self.env.get("XDG_CONFIG_HOME") or Path(self.env.get("HOME", "/")) / ".config")
        try:
            dirs = (own / "user-dirs.dirs").read_bytes()
        except OSError:
            dirs = None
        if dirs is not None:
            tmp = cfg / f"user-dirs.dirs.{os.getpid()}.tmp"
            tmp.write_bytes(dirs)
            os.replace(tmp, cfg / "user-dirs.dirs")

    def exposed(self) -> tuple[list[Path], list[Path], list[Path], list[Path]]:
        """(rw, ro, rw if present, ro if present)."""
        p, pkg = self.paths, self.pkg
        rw = [p.data(pkg), self.home, p.data_home / "keyrings"]
        rw += [p.data(m) / f"{m}.apk_" for m in SERVICES if m != pkg and (p.data(pkg) / f"{m}.apk_").is_symlink()]
        ro = [p.apps_dir(), p.cache_home / "art"]
        home = Path(self.env.get("HOME", "/"))
        dirs = user_dirs(Path(self.env.get("XDG_CONFIG_HOME") or home / ".config"), home)
        rw_try = [dirs[n] for n, read_only in USER_DIRS if not read_only and n in dirs]
        ro_try = [dirs[n] for n, read_only in USER_DIRS if read_only and n in dirs]
        ro_try += list(self._desktop_config().values())
        code = Path(__file__).resolve().parent.parent               # the starter itself, when not from /app
        if not code.is_relative_to(p.prefix):
            ro_try.append(code)
        return rw, ro, rw_try, ro_try

    def child_env(self, env: Mapping[str, str]) -> dict[str, str]:
        out = {k: v for k, v in env.items() if k not in NOT_INHERITED}
        out["XDG_CONFIG_HOME"] = str(self.home / "config")
        out["XDG_DATA_HOME"] = str(self.home / "data")
        out["XDG_CACHE_HOME"] = str(self.home / "cache")
        out[RELAY_ENV] = str(self.relay)
        if self._proxy is not None:
            out["DBUS_SESSION_BUS_ADDRESS"] = f"unix:path={self.bus}"
        return out

    # -- start and end -----------------------------------------------------------------------------------------
    def open(self) -> None:
        self.sockets.mkdir(parents=True, exist_ok=True)
        for s in (self.bus, self.relay):
            if len(os.fsencode(s)) > SUN_PATH_MAX:
                raise OSError(f"socket path too long for the app's sandbox: {s}")
            s.unlink(missing_ok=True)
        self._prepare_home(self._desktop_config())
        self._start_relay()
        self._start_proxy()

    def command(self, cmd: list[str], env: Mapping[str, str]) -> list[str]:
        rw, ro, rw_try, ro_try = self.exposed()
        out = [SPAWN, "--sandbox", "--watch-bus", "--sandbox-flag=share-display", "--sandbox-flag=share-sound",
               "--sandbox-flag=share-gpu"]
        out += [f"--sandbox-expose-path={x}" for x in [*rw, self.relay]]
        if self._proxy is not None:
            out.append(f"--sandbox-expose-path={self.bus}")
        out += [f"--sandbox-expose-path-ro={x}" for x in ro]
        out += [f"--sandbox-expose-path-try={x}" for x in rw_try]
        out += [f"--sandbox-expose-path-ro-try={x}" for x in ro_try]
        out += [f"--env={k}={v}" for k, v in sorted(self.child_env(env).items())]
        return [*out, *cmd]

    def close(self) -> None:
        if self._relay is not None:
            self._relay.close()
            self._relay = None
        if self._proxy_ready >= 0:                # the proxy ends when this end of its --fd pipe closes
            os.close(self._proxy_ready)
            self._proxy_ready = -1
        if self._proxy is not None:
            try:
                self._proxy.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._proxy.kill()
            self._proxy = None
        for s in (self.bus, self.relay):
            s.unlink(missing_ok=True)

    def __enter__(self) -> "Sandbox":
        self.open()
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    def _start_proxy(self) -> None:
        bus = self.env.get("DBUS_SESSION_BUS_ADDRESS")
        if not bus or shutil.which(PROXY) is None:
            return
        flatpak_id = self.env.get("FLATPAK_ID", "")
        rules = ["--filter", f"--own={self.app_id}", f"--own=org.mpris.MediaPlayer2.{self.app_id}",
                 "--talk=org.freedesktop.portal.Desktop", "--talk=org.freedesktop.secrets"]
        if flatpak_id:
            rules.append(f"--own={flatpak_id}")      # ATL's notifications (Flatpak name, queued)
        r, w = os.pipe()
        try:
            self._proxy = subprocess.Popen([PROXY, f"--fd={w}", bus, str(self.bus), *rules], pass_fds=(w,),
                                           stdin=subprocess.DEVNULL)
        finally:
            os.close(w)
        self._proxy_ready = r
        if os.read(r, 1) != b"x":                  # it writes one byte when it listens
            self.close()
            raise OSError("xdg-dbus-proxy did not start")

    def _start_relay(self) -> None:
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM | socket.SOCK_CLOEXEC)
        srv.bind(str(self.relay))
        srv.listen(8)
        self._relay = srv
        threading.Thread(target=self._serve, args=(srv,), daemon=True).start()

    def _serve(self, srv: socket.socket) -> None:
        while True:
            try:
                conn, _a = srv.accept()
            except OSError:
                return
            threading.Thread(target=self._handle, args=(conn,), daemon=True).start()

    def _handle(self, conn: socket.socket) -> None:
        fds: list[int] = []
        try:
            with conn:
                request, fds = _receive(conn)
                rc = self.run_request(request, fds)
                conn.sendall(rc.to_bytes(4, "little", signed=True))
        except (OSError, ValueError) as e:
            print(f"apk-starter: request of {self.pkg} refused: {e}", file=sys.stderr, flush=True)
        finally:
            for fd in fds:
                os.close(fd)

    def run_request(self, request: dict, fds: list[int]) -> int:
        """One request of the app (its stdout and stderr are fds[0:2], install files fds[2:]): exit status."""
        argv = request.get("argv")
        if not isinstance(argv, list) or not all(isinstance(a, str) for a in argv) or len(fds) < 2:
            raise ValueError("malformed request")
        out, err, files = fds[0], fds[1], fds[2:]
        env = {**self.env, "ATL_INSTALLER_PACKAGE": self.pkg}
        label = request.get("label")
        env.pop("ATL_INSTALLER_LABEL", None)
        if isinstance(label, str) and label and "\n" not in label:
            env["ATL_INSTALLER_LABEL"] = label[:200]
        program = str(self.paths.program())
        if argv[:1] == ["--install-request"]:
            names = argv[1:]
            if not names or len(names) != len(files):
                raise ValueError("install request without its files")
            with tempfile.TemporaryDirectory(dir=self.sockets) as tmp:
                local = []
                for i, (name, fd) in enumerate(zip(names, files)):
                    base = Path(name).name if Path(name).name.endswith(".apk") else f"{i}.apk"
                    dest = Path(tmp) / f"{i}-{base}"
                    with os.fdopen(os.dup(fd), "rb") as src, open(dest, "wb") as dst:
                        shutil.copyfileobj(src, dst, 1 << 20)
                    local.append(str(dest))
                return subprocess.run([program, "--install-request", *local], env=env, stdin=subprocess.DEVNULL,
                                      stdout=out, stderr=err).returncode
        if files:
            raise ValueError("files only come with an install")
        if len(argv) == 2 and argv[0] in ("--uninstall-request", "--details") and PACKAGE_RE.fullmatch(argv[1]):
            return subprocess.run([program, *argv], env=env, stdin=subprocess.DEVNULL, stdout=out,
                                  stderr=err).returncode
        launch = argv[2:] if argv[:1] == ["--uri"] and len(argv) == 3 else argv
        if len(launch) == 1 and PACKAGE_RE.fullmatch(launch[0]):
            # another app, in a sandbox of its own: started and not waited for, like ATL's ProcessBuilder.start()
            env.pop("ATL_INSTALLER_PACKAGE")
            env.pop("ATL_INSTALLER_LABEL", None)
            subprocess.Popen([program, *argv], env=env, stdin=subprocess.DEVNULL, stdout=out, stderr=err,
                             start_new_session=True)
            return 0
        raise ValueError(f"not a request of an app: {argv[:1]}")


def _link(link: Path, target: Path) -> None:
    if link.is_symlink() and os.readlink(link) == str(target):
        return
    tmp = link.with_name(f"{link.name}.{os.getpid()}.tmp")
    tmp.unlink(missing_ok=True)
    os.symlink(target, tmp)
    os.replace(tmp, link)


def _receive(conn: socket.socket) -> tuple[dict, list[int]]:
    fds = array.array("i")
    msg, anc, _flags, _addr = conn.recvmsg(MAX_REQUEST, socket.CMSG_SPACE(MAX_FDS * fds.itemsize))
    for level, kind, data in anc:
        if level == socket.SOL_SOCKET and kind == socket.SCM_RIGHTS:
            fds.frombytes(data[:len(data) - len(data) % fds.itemsize])
    try:
        request = json.loads(msg)
    except (ValueError, UnicodeError):
        for fd in fds:
            os.close(fd)
        raise ValueError("malformed request") from None
    if not isinstance(request, dict):
        for fd in fds:
            os.close(fd)
        raise ValueError("malformed request")
    return request, list(fds)


def relay(argv: list[str], env: Mapping[str, str]) -> int:
    """In an app's sandbox: hand ATL's request (ATL_*_COMMAND) to the Starter that started the app."""
    files = [os.open(a, os.O_RDONLY | os.O_CLOEXEC) for a in argv[1:]] if argv[:1] == ["--install-request"] else []
    try:
        request = json.dumps({"argv": argv, "label": env.get("ATL_INSTALLER_LABEL", "")}).encode()
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM | socket.SOCK_CLOEXEC) as s:
            s.connect(env[RELAY_ENV])
            s.sendmsg([request], [(socket.SOL_SOCKET, socket.SCM_RIGHTS, array.array("i", [1, 2, *files]))])
            reply = b""
            while len(reply) < 4:
                chunk = s.recv(4 - len(reply))
                if not chunk:
                    print("apk-starter: the Starter refused the request", file=sys.stderr)
                    return 1
                reply += chunk
        return int.from_bytes(reply, "little", signed=True)
    finally:
        for fd in files:
            os.close(fd)
