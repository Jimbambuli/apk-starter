"""Run android-translation-layer as a child: optional log with versioned header and trailer, exit mapping."""
import configparser
import os
import shlex
import signal
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from .apkinfo import ApkInfo
from .errors import StarterError
from .paths import Paths

LOG_FORMAT = 1
HEADER_PREFIX = "# apk-starter: "
FORWARDED = (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)


def default_log_path(paths: Paths, pkg: str, now: datetime) -> Path:
    return paths.logs_dir() / f"{pkg}-{now:%Y%m%d-%H%M%S}.log"


def read_build_info(path: Path) -> dict[str, str]:
    info: dict[str, str] = {}
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            key, sep, value = line.partition("=")
            if sep and key.strip() and not key.lstrip().startswith("#"):
                info[key.strip()] = value.strip()
    except (OSError, UnicodeError):
        pass
    info.setdefault("atl_commit", "unknown")
    info.setdefault("art_commit", "unknown")
    return info


def runtime_name(flatpak_info: Path = Path("/.flatpak-info")) -> str:
    cp = configparser.ConfigParser(interpolation=None, strict=False)
    try:
        cp.read(flatpak_info, encoding="utf-8")
        return cp.get("Application", "runtime") or "none"
    except (OSError, configparser.Error, UnicodeError):
        return "none"


def header_fields(info: ApkInfo, build: dict[str, str], runtime: str, cmd: list[str],
                  started: datetime, version: str) -> dict[str, str]:
    def s(v: object) -> str:
        return "" if v is None else " ".join(str(v).splitlines() or [""])

    return {
        "format": str(LOG_FORMAT),
        "launcher_version": s(version),
        "apk": s(info.path),
        "package": s(info.package),
        "version_name": s(info.version_name),
        "version_code": s(info.version_code),
        "min_sdk": s(info.min_sdk),
        "target_sdk": s(info.target_sdk),
        "atl_commit": s(build.get("atl_commit", "unknown")),
        "art_commit": s(build.get("art_commit", "unknown")),
        "runtime": s(runtime),
        "started": started.isoformat(timespec="seconds"),
        "args": s(shlex.join(cmd)),
    }


def _emit_stderr(chunk: bytes) -> None:
    err = sys.stderr
    buf = getattr(err, "buffer", None)
    if buf is not None:
        err.flush()
        buf.write(chunk)
        buf.flush()
    else:
        err.write(chunk.decode("utf-8", errors="replace"))
        err.flush()


def run_atl(cmd: list[str], env: dict[str, str], log: Path | None, header: dict[str, str]) -> int:
    logf = None
    if log is not None:
        try:
            log.parent.mkdir(parents=True, exist_ok=True)
            logf = open(log, "wb")
            for k, v in header.items():
                logf.write(f"{HEADER_PREFIX}{k}={v}\n".encode("utf-8"))
            logf.flush()
        except OSError as e:
            raise StarterError(f"cannot write log {log}: {e.strerror}") from None
    t0 = time.monotonic()
    try:
        proc = subprocess.Popen(cmd, env=env, **({"stdout": subprocess.PIPE, "stderr": subprocess.STDOUT}
                                                 if logf else {}))
    except FileNotFoundError:
        if logf:
            logf.close()
            log.unlink(missing_ok=True)
        raise StarterError("android-translation-layer not found") from None
    except OSError as e:
        if logf:
            logf.close()
            log.unlink(missing_ok=True)
        raise StarterError(f"cannot start android-translation-layer: {e.strerror}") from None

    previous = {}
    try:
        for sig in FORWARDED:
            previous[sig] = signal.signal(sig, lambda n, _f: proc.poll() is None and proc.send_signal(n))
    except ValueError:      # not the main thread
        previous = {}
    last = b"\n"
    try:
        if logf:
            fd = proc.stdout.fileno()
            while True:
                chunk = os.read(fd, 65536)
                if not chunk:
                    break
                logf.write(chunk)
                logf.flush()
                _emit_stderr(chunk)
                last = chunk[-1:]
        rc = proc.wait()
    finally:
        for sig, old in previous.items():
            signal.signal(sig, old)
        if proc.stdout:
            proc.stdout.close()
    duration = time.monotonic() - t0
    sig_name = None
    if rc < 0:
        try:
            sig_name = signal.Signals(-rc).name
        except ValueError:
            sig_name = f"SIG{-rc}"
        rc = 128 - rc
        sys.stderr.write(f"apk-starter: android-translation-layer was killed by {sig_name}\n")
        sys.stderr.flush()
    if logf:
        tail = b"" if last == b"\n" else b"\n"
        if sig_name:
            tail += f"{HEADER_PREFIX}signal={sig_name}\n".encode()
        tail += f"{HEADER_PREFIX}exit={rc}\n{HEADER_PREFIX}duration={duration:.1f}\n".encode()
        logf.write(tail)
        logf.close()
    return rc
