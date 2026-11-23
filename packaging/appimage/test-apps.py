"""test-apps.py APPIMAGE RESULTS PKG[:SECONDS][@URI]... (ct-test.sh, as the test user): start each installed app (with
--uri URI if given) from the
AppImage in a private headless KWin session (atl-doctor's HeadlessSession; PYTHONPATH must hold atl_doctor), wait
for its window, take a screenshot after SECONDS (default 20), end it. Then the menu entry of the first app (its Exec
line, started with `gio launch` as a desktop does; the first app whose entry has no %u), then the starter's window
beside it, the window closed first: the
app must keep running (one shared mount for both, apprun.py). Prints one line per check; the screenshots and logs go to RESULTS. With PULSE_SERVER set, each app's
sound is recorded from the null sink «aip» into RESULTS/PKG.wav (ct-test.sh runs that PulseAudio)."""
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from atl_doctor.headless import HeadlessSession

WINDOW_TIMEOUT_S = 300
MEMORY_LIMIT_MB = 3000                       # an app above this is ended (a runaway would starve the test node)


def memory_mb(pgid: int) -> int:
    total = 0
    for pid in filter(str.isdigit, os.listdir("/proc")):
        try:
            if os.getpgid(int(pid)) == pgid:
                with open(f"/proc/{pid}/statm") as f:
                    total += int(f.read().split()[1]) * 4096 // 1048576
        except (OSError, ValueError, IndexError):
            continue
    return total


def guarded_sleep(p: subprocess.Popen, secs: float) -> str:
    """Wait SECS; ends the app (its process group) if it uses more than MEMORY_LIMIT_MB."""
    end = time.monotonic() + secs
    while time.monotonic() < end:
        if (mb := memory_mb(p.pid)) > MEMORY_LIMIT_MB:
            os.killpg(p.pid, signal.SIGKILL)
            return f" ENDED: {mb} MB memory"
        time.sleep(2)
    return ""


def wait_window(s: HeadlessSession, match: str, timeout: float = WINDOW_TIMEOUT_S,
                p: subprocess.Popen | None = None) -> float | None:
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        if any(match in (w["resource_class"] + " " + w["caption"]) for w in s.windows()):
            return time.monotonic() - t0
        if p is not None and (p.poll() is not None or guarded_sleep(p, 2)):
            return None
        time.sleep(2)
    return None


def start(cmd: list[str], env: dict, log: Path) -> subprocess.Popen:
    with open(log, "wb") as f:
        return subprocess.Popen(cmd, env=env, stdout=f, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                start_new_session=True)


def end(p: subprocess.Popen) -> None:
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(p.pid, sig)
        except ProcessLookupError:
            return
        try:
            p.wait(15)
            return
        except subprocess.TimeoutExpired:
            continue


def menu_entry(pkg: str) -> Path:
    data = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    return Path(data) / "applications" / f"io.github.Jimbambuli.ApkStarter.{pkg}.desktop"


def atl_pids(pkg: str) -> list[int]:
    """The ATL processes of the app (started through the menu entry, not children of this script)."""
    pids = []
    for pid in filter(str.isdigit, os.listdir("/proc")):
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as f:
                args = f.read().split(b"\0")
        except OSError:
            continue
        if args and args[0].endswith(b"android-translation-layer") and any(pkg.encode() in a for a in args):
            pids.append(int(pid))
    return pids


def main() -> None:
    appimage, res = sys.argv[1], Path(sys.argv[2])
    apps = []
    for a in sys.argv[3:]:
        a, _, uri = a.partition("@")
        apps.append((a.split(":")[0], int(a.split(":")[1]) if ":" in a else 20, uri))
    res.mkdir(parents=True, exist_ok=True)
    s = HeadlessSession(res / "session")
    s.start()
    env = {**os.environ, **s.env()}
    try:
        for pkg, secs, uri in apps:
            rec = None
            if env.get("PULSE_SERVER"):
                rec = subprocess.Popen(["parec", "-d", "aip.monitor", "--rate=48000", "--channels=2", "--format=s16le",
                                        "--file-format=wav", str(res / f"{pkg}.wav")], env=env)
            p = start([appimage, *(["--uri", uri] if uri else []), pkg], env, res / f"{pkg}.log")
            t = wait_window(s, pkg, p=p)
            note = guarded_sleep(p, secs) if t is not None and p.poll() is None else ""
            alive = p.poll() is None
            shot = s.screenshot(res / f"{pkg}.png")
            print(f"app {pkg}: window {'after %.0f s' % t if t is not None else 'NONE'}, "
                  f"{'running' if alive else 'ended rc=%s' % p.returncode} after +{secs} s{note}, screenshot {shot}", flush=True)
            end(p)
            if rec:
                rec.terminate()
                rec.wait()
            time.sleep(3)
        # an entry without %u: with GLib, "--uri %u PKG" without a link becomes "--uri PKG" (the starter's window)
        exec_lines = {pkg: next(x for x in menu_entry(pkg).read_text().splitlines() if x.startswith("Exec="))
                      for pkg, _s, _u in apps}
        pkg = next(k for k, v in exec_lines.items() if "%u" not in v)
        entry, exec_line = menu_entry(pkg), exec_lines[pkg]
        print(f"menu entry {pkg}: {exec_line}, started with gio launch (GLib expands it as a desktop does)", flush=True)
        subprocess.run(["gio", "launch", str(entry)], env=env, stdout=open(res / f"menu-{pkg}.log", "wb"),
                       stderr=subprocess.STDOUT, timeout=30)
        ta = wait_window(s, pkg)
        gui = start([appimage], env, res / "gui.log")
        tg = wait_window(s, "APK Starter", 120)
        s.screenshot(res / "menu-both.png")
        end(gui)
        time.sleep(45)                       # longer than the watcher's first look (apprun.py)
        alive = bool(atl_pids(pkg))
        shot = s.screenshot(res / "menu-after-gui-closed.png")
        print(f"menu entry: app window {'after %.0f s' % ta if ta is not None else 'NONE'}, then starter window "
              f"{'after %.0f s' % tg if tg is not None else 'NONE'}; starter closed, app 45 s later "
              f"{'running' if alive else 'ENDED'}, screenshot {shot}", flush=True)
        for pid in atl_pids(pkg):
            os.kill(pid, signal.SIGTERM)
    finally:
        s.stop()
    time.sleep(50)                           # the watcher ends the extra mount after two idle looks
    mounts = [x for x in Path("/proc/self/mountinfo").read_text().splitlines()
              if ".mount_" in x or "apk-starter-appimage-" in x]
    print(f"AppImage mounts left 50 s after the last process: {len(mounts)}", flush=True)


if __name__ == "__main__":
    main()
