"""Management window (GTK 4 + libadwaita): list, start, install and remove apps.

All work is done by the same functions as the command line (cli.py); this module only shows and asks."""
import os
import sys
import threading
from collections.abc import Callable, Mapping
from pathlib import Path

from . import dexopt, diag
from .cli import APP_ID, clear_data, install_apk, lang_from_env, prepare_all, remove_app
from .errors import StarterError, describe_error
from .i18n import _
from .gui_model import AppRow, load_rows, start_argv
from .paths import Paths

try:
    import gi
    gi.require_version("Gtk", "4.0")
    gi.require_version("Gdk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Adw, Gdk, Gio, GLib, Gtk
except (ImportError, ValueError) as e:      # pragma: no cover - depends on the system
    raise StarterError(_("the window needs GTK 4 and libadwaita: {reason}").format(reason=e)) from None

APK_MIME = "application/vnd.android.package-archive"


class Window(Adw.ApplicationWindow):
    def __init__(self, app: Adw.Application, paths: Paths, lang: str | None, launcher: list[str],
                 env: Mapping[str, str] | None = None):
        super().__init__(application=app, title="APK Starter", default_width=640, default_height=560)
        self.paths, self.lang, self.launcher = paths, lang, launcher
        self.env = dict(os.environ if env is None else env)
        self.rows: dict[str, AppRow] = {}
        self.busy = 0                               # running install/remove/clear jobs
        self.generation = 0                         # newest list load; older results are dropped

        install = Gtk.Button(label=_("Install APK…"), tooltip_text=_("Choose an APK file and add it to the menu"))
        install.add_css_class("suggested-action")
        install.connect("clicked", lambda _b: self.choose_apk())
        header = Adw.HeaderBar()
        header.pack_start(install)
        self.spinner = Adw.Spinner(visible=False, tooltip_text=_("Working…"))
        header.pack_start(self.spinner)

        self.listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE, valign=Gtk.Align.START)
        self.listbox.add_css_class("boxed-list")
        clamp = Adw.Clamp(child=self.listbox, margin_top=24, margin_bottom=24, margin_start=12, margin_end=12)
        self.stack = Gtk.Stack()
        self.stack.add_named(Adw.StatusPage(icon_name=APP_ID, title=_("No apps installed"),
                                            description=_("Install an APK or drop one here.")), "empty")
        self.stack.add_named(Gtk.ScrolledWindow(child=clamp, hscrollbar_policy=Gtk.PolicyType.NEVER), "list")
        self.toasts = Adw.ToastOverlay(child=self.stack)
        view = Adw.ToolbarView(content=self.toasts)
        view.add_top_bar(header)
        self.banner = Adw.Banner(revealed=False)            # «Preparing …» while compiling for this computer
        view.add_top_bar(self.banner)
        self.set_content(view)

        drop = Gtk.DropTarget.new(Gdk.FileList, Gdk.DragAction.COPY)
        drop.connect("drop", self.on_drop)
        self.add_controller(drop)

        for name, handler in (("start", lambda p: self.start(p, None)),
                              ("start-log", lambda p: self.start(p, "log")),
                              ("start-debug", lambda p: self.start(p, "debug")),
                              ("show-report", self.show_report),
                              ("open-logs", lambda _p: self.open_logs()),
                              ("clear-data", self.ask_clear_data),
                              ("uninstall", self.ask_uninstall)):
            action = Gio.SimpleAction.new(name, GLib.VariantType.new("s"))
            action.connect("activate", lambda _a, v, h=handler: h(v.get_string()))
            self.add_action(action)
        self.refresh()
        if not dexopt.runtime_ready(self.paths, self.env) or dexopt.stale_installed(self.paths):
            # after a Flatpak update: rebuild once in the background; apps started meanwhile wait for the runtime
            self.background(lambda: prepare_all(self.paths, self.env, self.progress), None, "")

    # -- helpers -----------------------------------------------------------------------------------------
    def toast(self, text: str) -> None:
        """Short confirmation; replaces the one shown so it appears at once."""
        self.toasts.dismiss_all()
        self.toasts.add_toast(Adw.Toast(title=GLib.markup_escape_text(text), timeout=4))

    def alert(self, heading: str, body: str) -> None:
        """Errors and warnings stay until the user closes them."""
        dialog = Adw.AlertDialog(heading=heading, body=body)
        dialog.add_response("close", _("Close"))
        dialog.present(self)

    def background(self, work: Callable[[], object], done: Callable[[object], None] | None,
                   failed: str, job: bool = True) -> None:
        """Run file and portal work off the main loop; show errors as a dialog headed `failed`, then refresh."""
        def thread() -> None:
            try:
                result, error = work(), None
            except Exception as e:                  # shown to the user, never a traceback
                result, error = None, e
            GLib.idle_add(finish, result, error)

        def finish(result, error) -> bool:
            if job:
                self.busy -= 1
                self.spinner.set_visible(self.busy > 0)
                self.banner.set_revealed(self.banner.get_revealed() and self.busy > 0)
            if error is not None:
                self.alert(failed, describe_error(error))
            elif done is not None:
                done(result)
            if job:
                self.refresh()
            return False

        if job:
            self.busy += 1
            self.spinner.set_visible(True)
        threading.Thread(target=thread, daemon=True).start()

    def progress(self, text: str) -> None:
        """Called from worker threads."""
        def show() -> bool:
            self.banner.set_title(GLib.markup_escape_text(text))
            self.banner.set_revealed(self.busy > 0)
            return False
        GLib.idle_add(show)

    # -- list ----------------------------------------------------------------------------------------------
    def refresh(self) -> None:
        self.generation += 1
        generation = self.generation
        self.background(lambda: load_rows(self.paths),
                        lambda rows: generation == self.generation and self.show_rows(rows),
                        _("Cannot read the app list"), job=False)

    def show_rows(self, rows: list[AppRow]) -> None:
        self.rows = {r.package: r for r in rows}
        self.listbox.remove_all()
        for r in rows:
            self.listbox.append(self.make_row(r))
        self.stack.set_visible_child_name("list" if rows else "empty")

    def make_row(self, r: AppRow) -> Gtk.Widget:
        details = [r.version] if r.version else []
        details.append(_("data {size}").format(size=GLib.format_size(r.size)) if r.size else _("no data"))
        row = Adw.ActionRow(title=r.label, subtitle=" · ".join(details), use_markup=False,
                            tooltip_text=r.package)
        image = Gtk.Image(pixel_size=48, icon_name=APP_ID)
        if r.icon is not None:
            try:
                image.set_from_paintable(Gdk.Texture.new_from_filename(str(r.icon)))
            except GLib.Error:
                pass
        row.add_prefix(image)

        target = GLib.Variant("s", r.package)
        start = Gtk.Button(label=_("Start"), valign=Gtk.Align.CENTER, action_name="win.start", action_target=target)
        menu = Gio.Menu()
        for label, action in ((_("Start with log"), "win.start-log"), (_("Start with debug"), "win.start-debug"),
                              (_("Show report"), "win.show-report"), (_("Open log folder"), "win.open-logs"),
                              (_("Delete data…"), "win.clear-data"), (_("Uninstall…"), "win.uninstall")):
            item = Gio.MenuItem.new(label, None)
            item.set_action_and_target_value(action, target)
            menu.append_item(item)
        more = Gtk.MenuButton(icon_name="view-more-symbolic", menu_model=menu, valign=Gtk.Align.CENTER,
                              tooltip_text=_("More"))
        more.add_css_class("flat")
        row.add_suffix(start)
        row.add_suffix(more)
        return row

    # -- actions -------------------------------------------------------------------------------------------
    def start(self, pkg: str, level: str | None) -> None:
        label = self.rows[pkg].label if pkg in self.rows else pkg
        try:
            proc = Gio.Subprocess.new(start_argv(self.launcher, pkg, level), Gio.SubprocessFlags.NONE)
        except GLib.Error as e:
            self.alert(_("Cannot start {app}").format(app=label), e.message)
            return

        def ended(p: Gio.Subprocess, res) -> None:
            p.wait_finish(res)
            hint = _("Look at “Show report”.") if level else _("Try “Start with log”, then “Show report”.")
            if p.get_if_exited() and p.get_exit_status() != 0:
                self.alert(_("{app} ended with exit code {code}").format(app=label, code=p.get_exit_status()), hint)
            elif p.get_if_signaled():
                self.alert(_("{app} was killed by signal {signal}").format(app=label, signal=p.get_term_sig()), hint)
        proc.wait_async(None, ended)
        if level == "log":
            self.toast(_("{app} starts with a log").format(app=label))
        elif level == "debug":
            self.toast(_("{app} starts with debug; it runs much slower").format(app=label))

    def show_report(self, pkg: str) -> None:
        label = self.rows[pkg].label if pkg in self.rows else pkg
        ReportDialog(self, pkg, label).present(self)

    def open_logs(self) -> None:
        folder = self.paths.logs_dir()
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            self.alert(_("Cannot open the log folder"), describe_error(e))
            return
        launcher = Gtk.FileLauncher.new(Gio.File.new_for_path(str(folder)))

        def opened(lnc: Gtk.FileLauncher, res) -> None:
            try:
                lnc.launch_finish(res)
            except GLib.Error as e:
                self.alert(_("Cannot open the log folder"), f"{folder}: {e.message}")
        launcher.launch(self, None, opened)

    def choose_apk(self) -> None:
        apks = Gtk.FileFilter(name=_("Android apps"))
        apks.add_suffix("apk")
        apks.add_mime_type(APK_MIME)
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(apks)
        dialog = Gtk.FileDialog(title=_("Install APK"), filters=filters, default_filter=apks)

        def chosen(d: Gtk.FileDialog, res) -> None:
            try:
                f = d.open_finish(res)
            except GLib.Error:                      # cancelled
                return
            if f is not None and f.get_path():
                self.install(Path(f.get_path()))
        dialog.open(self, None, chosen)

    def on_drop(self, _target, files, _x, _y) -> bool:
        paths = [f.get_path() for f in files.get_files()]
        for p in paths:
            if p:
                self.install(Path(p))
        return any(paths)

    def install(self, apk: Path) -> None:
        warnings: list[str] = []
        def done(info) -> None:
            if warnings:
                self.alert(_("{app} installed").format(app=info.label), "\n".join(warnings))
            else:
                self.toast(_("{app} installed").format(app=info.label))
        self.background(lambda: install_apk(self.paths, apk, self.lang, self.env, self.progress, warnings.append),
                        done, _("Cannot install {file}").format(file=apk.name))

    def _confirm(self, heading: str, body: str, verb: str, then: Callable[[], None],
                 extra: Gtk.Widget | None = None) -> None:
        dialog = Adw.AlertDialog(heading=heading, body=body)
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("ok", verb)
        dialog.set_response_appearance("ok", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        if extra is not None:
            dialog.set_extra_child(extra)
        dialog.connect("response", lambda _d, resp: then() if resp == "ok" else None)
        dialog.present(self)

    def ask_clear_data(self, pkg: str) -> None:
        label = self.rows[pkg].label if pkg in self.rows else pkg
        self._confirm(_("Delete the data of {app}?").format(app=label),
                      _("The app stays installed. Its settings, accounts and files are deleted. "
                        "Close the app first."), _("Delete Data"),
                      lambda: self.background(lambda: clear_data(self.paths, pkg),
                                              lambda _r: self.toast(_("Data of {app} deleted").format(app=label)),
                                              _("Cannot delete the data of {app}").format(app=label)))

    def ask_uninstall(self, pkg: str) -> None:
        label = self.rows[pkg].label if pkg in self.rows else pkg
        purge = Gtk.CheckButton(label=_("Also delete app data"))
        def removed(warnings: list[str]) -> None:
            if warnings:
                self.alert(_("{app} uninstalled").format(app=label), "\n".join(warnings))
            else:
                self.toast(_("{app} uninstalled").format(app=label))
        self._confirm(_("Uninstall {app}?").format(app=label),
                      _("The app and its menu entry are removed. Close the app first."), _("Uninstall"),
                      lambda: self.background(lambda: remove_app(self.paths, pkg, purge.get_active()), removed,
                                              _("Cannot uninstall {app}").format(app=label)),
                      extra=purge)


class ReportDialog(Adw.Dialog):
    """«Show report»: the recordings of one app (newest first) and atl-doctor's report of the chosen one."""

    def __init__(self, win: Window, pkg: str, label: str):
        super().__init__(title=_("Report: {app}").format(app=label), content_width=760, content_height=640)
        self.win, self.label = win, label
        self.recordings = diag.recordings(win.paths, pkg)
        self.report = None
        self.generation = 0
        header = Adw.HeaderBar()
        self.save = Gtk.Button(label=_("Save Report…"), sensitive=False)
        self.save.connect("clicked", lambda _b: self.ask_save())
        header.pack_end(self.save)
        kinds = {"log": _("Log"), "debug": _("Debug")}
        names = [f"{r.started:%Y-%m-%d %H:%M:%S} · {kinds[r.kind]}" for r in self.recordings]
        self.choice = Gtk.DropDown.new_from_strings(names or [""])
        self.choice.set_tooltip_text(_("Recording"))
        self.choice.set_visible(bool(names))
        self.choice.connect("notify::selected", lambda d, _p: self.load(d.get_selected()))
        header.pack_start(self.choice)

        self.sections = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        clamp = Adw.Clamp(child=self.sections, maximum_size=720, margin_top=18, margin_bottom=18, margin_start=12,
                          margin_end=12)
        self.stack = Gtk.Stack()
        self.stack.add_named(Adw.StatusPage(icon_name="document-open-recent-symbolic", title=_("No recordings"),
                                            description=_("Start the app with log or with debug first.")), "none")
        self.stack.add_named(Adw.Spinner(), "loading")
        self.stack.add_named(Gtk.ScrolledWindow(child=clamp, hscrollbar_policy=Gtk.PolicyType.NEVER), "report")
        view = Adw.ToolbarView(content=self.stack)
        view.add_top_bar(header)
        self.set_child(view)
        if self.recordings:
            self.load(0)
        else:
            self.stack.set_visible_child_name("none")

    def load(self, index: int) -> None:
        if not 0 <= index < len(self.recordings):
            return
        self.generation += 1
        generation, path = self.generation, self.recordings[index].path
        self.report = None
        self.save.set_sensitive(False)
        self.stack.set_visible_child_name("loading")

        def work():
            report = diag.load_report(path)
            return report, diag.report_view(report)

        def done(result) -> None:
            if generation == self.generation:
                self.report = result[0]
                self.show(result[1])
        self.win.background(work, done, _("Cannot read the report"), job=False)

    def show(self, view: list[diag.ReportSection]) -> None:
        while (child := self.sections.get_first_child()) is not None:
            self.sections.remove(child)
        for section in view:
            group = Adw.PreferencesGroup(title=GLib.markup_escape_text(section.title))
            for item in section.items:
                if item.details:
                    row = Adw.ExpanderRow(title=item.title, use_markup=False)
                    text = Gtk.Label(label=item.details, xalign=0, wrap=True, selectable=True, margin_top=8,
                                     margin_bottom=8, margin_start=12, margin_end=12)
                    text.add_css_class("monospace")
                    row.add_row(text)
                else:
                    row = Adw.ActionRow(title=item.title, use_markup=False)
                group.add(row)
            self.sections.append(group)
        self.stack.set_visible_child_name("report")
        self.save.set_sensitive(True)

    def ask_save(self) -> None:
        rec = self.recordings[self.choice.get_selected()]
        name = (rec.path.stem if rec.kind == "log" else rec.path.name) + "-report.txt"
        dialog = Gtk.FileDialog(title=_("Save Report"), initial_name=name)

        def chosen(d: Gtk.FileDialog, res) -> None:
            try:
                f = d.save_finish(res)
            except GLib.Error:                      # cancelled
                return
            if f is not None and f.get_path():
                self.save_to(Path(f.get_path()))
        dialog.save(self.win, None, chosen)

    def save_to(self, path: Path) -> None:
        """atl-doctor's text report, the same as `apk-starter --report`."""
        try:
            path.write_text(diag.report_text(self.report), encoding="utf-8")
        except OSError as e:
            self.win.alert(_("Cannot save the report"), describe_error(e))
            return
        self.win.toast(_("Report saved"))


def default_launcher() -> list[str]:
    """How to start an app: run this very program again (`/app/bin/apk-starter` inside the Flatpak)."""
    return [sys.executable, os.path.realpath(sys.argv[0])]


def run(paths: Paths, env: Mapping[str, str], launcher: list[str] | None = None) -> int:
    app = Adw.Application(application_id=APP_ID)
    lang = lang_from_env(env)

    def activate(a: Adw.Application) -> None:
        win = a.get_active_window() or Window(a, paths, lang, launcher or default_launcher(), env)
        win.present()
    app.connect("activate", activate)
    return app.run([sys.argv[0]])
