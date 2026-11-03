"""Diagnose levels (Spec 12).

Log: the log of run.py plus atl-doctor's JVMTI agent (exceptions with stack, lifecycle), its output appended to the
log so that `atl-doctor report --log` reads both. Debug: atl-doctor's light recorder (agent attached when the runtime
runs, stub calls counted by ATL, time line from ART's sampling profiler, view trees, verbose Android log) writes a
recording folder; the app keeps nearly its normal speed (no full method trace). The starter only records: reports
come from atl-doctor itself (`atl-doctor report`)."""
import re
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .apkinfo import PACKAGE_RE
from .errors import StarterError
from .i18n import _
from .paths import Paths

AGENT_PREFIX = "# atl-doctor-agent: "   # atl-doctor's log mode reads agent lines after this prefix
AGENT_PATH_MAX = 480                    # the agent keeps paths in 512-byte buffers
# liblog: every Android log level for every tag, but the bionic linker only from info on: its verbose/debug lines
# (one per symbol it binds, ~550 000 for JW Library) made the start several times slower
VERBOSE_LOG = {"ANDROID_LOG_TAGS": "*:v [bionic_linker]:i"}
TREE_AFTER_S = 10.0                     # Debug: one phase mark "running" then (view tree, memory) if the app still runs
_NAME = re.compile(r"(?P<pkg>.+)-(?P<time>\d{8}-\d{6})(?P<log>\.log)?")


def agent_args(paths: Paths, out: Path) -> list[str]:
    """ATL arguments for the Log level: the agent records exceptions and lifecycle callbacks into `out`; ART's
    JVMTI plugin is loaded 3 s after the start (the runtime stays fast). Empty if something is missing."""
    agent, plugin = paths.agent(), paths.jvmti_plugin()
    if not (agent.is_file() and plugin.is_file()) or "," in str(out) or len(str(out)) > AGENT_PATH_MAX:
        return []
    return ["-X", f"-agentpath:{agent}={out},late={plugin},latesec=3"]


def append_agent_output(log: Path, out: Path) -> None:
    """Complete agent lines go to the end of the log, each after AGENT_PREFIX; the agent's file is removed."""
    try:
        data = out.read_bytes()
    except FileNotFoundError:
        return
    lines = data.decode("utf-8", "replace").split("\n")
    lines.pop()                             # "" or a line the agent did not finish
    with open(log, "a", encoding="utf-8") as f:
        f.writelines(f"{AGENT_PREFIX}{line}\n" for line in lines)
    out.unlink(missing_ok=True)


def default_debug_dir(paths: Paths, pkg: str, now: datetime) -> Path:
    return paths.logs_dir() / f"{pkg}-{now:%Y%m%d-%H%M%S}"


def record_options(paths: Paths, cmd: list[str], env: Mapping[str, str], out: Path):
    """atl-doctor's recorder for the ATL command line of launch.build_command (ATL, APK, arguments)."""
    from atl_doctor.record_atl import RecordOptions

    stubs = paths.stub_list()
    return RecordOptions(apk=Path(cmd[1]), out=out, prefix=paths.prefix, atl_src=None, agent=None,
                         atl_args=cmd[2:], env={**env, **VERBOSE_LOG}, stub_list=stubs if stubs.is_file() else None,
                         light=True)


def record(paths: Paths, cmd: list[str], env: Mapping[str, str], out: Path) -> int:
    """Run the app under atl-doctor's recorder; returns ATL's exit code like run.run_atl (128+N for a signal)."""
    try:
        from atl_doctor.capture import Capture
        from atl_doctor.errors import DoctorError
        from atl_doctor.record_atl import record_atl
    except ImportError as e:
        raise StarterError(_("debug needs atl-doctor: {reason}").format(reason=e)) from None
    import signal
    import threading
    from atl_doctor.record_atl import send_mark

    done = threading.Event()

    def mark() -> None:
        """atl-doctor asks for the view tree at a mark; without one a recording has none."""
        if not done.wait(TREE_AFTER_S):
            try:
                send_mark(out, "running", timeout_s=2.0)
            except (DoctorError, OSError):
                pass                        # the app has ended meanwhile
    marker = threading.Thread(target=mark, daemon=True)
    marker.start()
    try:
        record_atl(record_options(paths, cmd, env, out))
        m = Capture.open(out).read_manifest()
    except DoctorError as e:
        raise StarterError(str(e)) from None
    finally:
        done.set()
    if m.signal:
        try:
            return 128 + signal.Signals[m.signal].value
        except KeyError:
            return 128
    return m.exit_status or 0


def record_restarting(paths: Paths, cmd: list[str], env: Mapping[str, str], out: Path, pkg: str,
                      record_once=None) -> int:
    """record() and, like run.run_atl, again when the app restarts itself (exit 75, at most MAX_RESTARTS
    times); each run is its own recording, the next one under a new default name (a new second)."""
    from .run import EXIT_RESTART, MAX_RESTARTS
    record_once = record_once or record
    restarts = 0
    while True:
        rc = record_once(paths, cmd, env, out)
        if rc != EXIT_RESTART or restarts == MAX_RESTARTS:
            return rc
        restarts += 1
        while (nxt := default_debug_dir(paths, pkg, datetime.now().astimezone())).exists() or nxt == out:
            time.sleep(0.2)
        out = nxt


@dataclass(frozen=True)
class Recording:
    path: Path
    package: str
    started: datetime
    kind: str                               # "log" | "debug"


def recordings(paths: Paths, pkg: str | None = None) -> list[Recording]:
    """The recordings in the log folder (default names only), newest first."""
    out = []
    try:
        entries = list(paths.logs_dir().iterdir())
    except OSError:
        return []
    for p in entries:
        m = _NAME.fullmatch(p.name)
        if not m or not PACKAGE_RE.fullmatch(m["pkg"]) or (pkg is not None and m["pkg"] != pkg):
            continue
        if m["log"] and p.is_file():
            kind = "log"
        elif not m["log"] and (p / "manifest.json").is_file():
            kind = "debug"
        else:
            continue
        try:
            started = datetime.strptime(m["time"], "%Y%m%d-%H%M%S")
        except ValueError:
            continue
        out.append(Recording(p, m["pkg"], started, kind))
    return sorted(out, key=lambda r: (r.started, r.path.name), reverse=True)
