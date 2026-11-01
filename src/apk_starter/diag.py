"""Diagnose levels and reports (Spec 12).

Log: the log of run.py plus atl-doctor's JVMTI agent (exceptions with stack, lifecycle), its output appended to the
log so that `atl-doctor report --log` reads both. Debug: atl-doctor's light recorder (agent attached when the runtime
runs, stub calls counted by ATL, time line from ART's sampling profiler, view trees, verbose Android log) writes a
recording folder; the app keeps nearly its normal speed (no full method trace). Reports come from atl-doctor's report code (English on purpose)."""
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .apkinfo import PACKAGE_RE
from .errors import StarterError, UsageError
from .i18n import _
from .paths import Paths

AGENT_PREFIX = "# atl-doctor-agent: "   # atl-doctor's log mode reads agent lines after this prefix
AGENT_PATH_MAX = 480                    # the agent keeps paths in 512-byte buffers
VERBOSE_LOG = {"ANDROID_LOG_TAGS": "*:v"}   # liblog: every Android log level for every tag
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


def find_recording(paths: Paths, target: str | None) -> Path:
    """`--report` argument: a log file or recording folder, a package (its newest recording), or none (the newest)."""
    if target and Path(target).exists():
        return Path(target)
    if target and not PACKAGE_RE.fullmatch(target):
        raise UsageError(_("no such recording: {file}").format(file=target))
    found = recordings(paths, target)
    if not found:
        if target:
            raise UsageError(_("no recording of {package}; start it with --log or --debug").format(package=target))
        raise StarterError(_("no recordings yet; start an app with --log or --debug"))
    return found[0].path


def load_report(path: Path):
    """atl-doctor's report of a log file or a recording folder."""
    try:
        from atl_doctor.errors import DoctorError
        from atl_doctor.report import build_capture_report, build_log_report
    except ImportError as e:
        raise StarterError(_("the report needs atl-doctor: {reason}").format(reason=e)) from None
    try:
        if path.is_dir():
            return build_capture_report(path)[0]
        return build_log_report([path])
    except (DoctorError, OSError, ValueError, KeyError, TypeError) as e:
        raise StarterError(_("cannot read the recording {file}: {reason}").format(file=path, reason=e)) from None


def report_text(report) -> str:
    """atl-doctor's text report (also what «Save report» writes)."""
    from atl_doctor.report import render_text

    return render_text(report)


# -- what the window shows (English like the report itself) -------------------------------------------------
@dataclass(frozen=True)
class ReportItem:
    title: str
    details: str = ""


@dataclass(frozen=True)
class ReportSection:
    title: str
    items: list[ReportItem]


GAP_KINDS = ("missing", "stub", "stublog")
STACK_LINES = 8
SECTIONS = (("Crash", lambda f: f.kind == "crash" and f.klass == "crash"),
            ("ATL gaps", lambda f: f.kind in GAP_KINDS),
            ("Exceptions", lambda f: f.kind == "exc"),
            ("Other findings", lambda f: not (f.kind in GAP_KINDS or f.kind == "exc" or f.klass == "crash"
                                               or (f.kind == "crash" and f.key.startswith("exit:")))))
# a normal end (exit status 0, System.exit(0)) is no finding here: the summary shows the exit status


def _summary(env: Mapping[str, str]) -> list[ReportItem]:
    items = []
    if env.get("package"):
        items.append(ReportItem(" ".join(filter(None, (env["package"], env.get("version_name"))))))
    status = f"exit status {env['exit']}" if env.get("exit") else ""
    if env.get("signal"):
        items.append(ReportItem(f"killed by {env['signal']}" + (f" ({status})" if status else "")))
    elif status:
        items.append(ReportItem(status))
    for key in ("agent", "trace", "tracecount"):
        if env.get(key):
            items.append(ReportItem(f"{key}: {env[key]}"))
    return items


def _item(f) -> ReportItem:
    title = f.title + (f" ({f.total_count}x)" if f.total_count > 1 else "")
    details = [f.suggestion] if f.suggestion else []
    stack = f.details.get("stack") or []
    details += [f"at {frame}" for frame in stack[:STACK_LINES]]
    if len(stack) > STACK_LINES:
        details.append(f"... {len(stack) - STACK_LINES} more")
    callers = f.details.get("callers") or []
    details += [f"called from {c}" for c in callers[:3]]
    return ReportItem(title, "\n".join(details))


def report_view(report) -> list[ReportSection]:
    """Summary, then crash, ATL gaps (missing and stub), exceptions and the rest, each in fix-first order."""
    from atl_doctor.findings import rank

    ranked = rank(report.findings)
    view = [ReportSection("Summary", _summary(report.environment))]
    for title, pred in SECTIONS:
        items = [_item(f) for f in ranked if pred(f)]
        if items:
            view.append(ReportSection(title, items))
    if len(view) == 1:
        view.append(ReportSection("Findings", [ReportItem("No crash, ATL gap or exception was found.")]))
    return view
