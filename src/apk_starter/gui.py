"""Management window (plain GTK 4, no libadwaita: the compositor draws its own title bar, e.g. Breeze under KWin;
GTK draws a simple one where the compositor wants none). List of apps on the left, details on the right.

All work is done by the same functions as the command line (cli.py); this module only shows and asks."""
import os
import sys
import threading
from collections.abc import Callable, Mapping
from pathlib import Path

from . import dexopt, diag, theme
from .cli import APP_ID, clear_data, install_apk, lang_from_env, prepare_all, remove_app
from .errors import StarterError, describe_error
from .i18n import _
from .gui_model import (AppRow, WindowState, filter_rows, is_narrow, key_action, load_rows, load_state,
                        save_state, sort_rows, start_argv)
from .paths import Paths

try:
    import gi
    gi.require_version("Gtk", "4.0")
    gi.require_version("Gdk", "4.0")
    from gi.repository import Gdk, Gio, GLib, Gtk, Pango
except (ImportError, ValueError) as e:      # pragma: no cover - depends on the system
    raise StarterError(_("the window needs GTK 4: {reason}").format(reason=e)) from None

APK_MIME = "application/vnd.android.package-archive"
RECORDINGS_SHOWN = 5
SIDE_WIDTH = 300
DETAILS_MAX = 640
DETAILS_MARGIN = 28

CSS = """
.app-name { font-size: 1.7em; font-weight: 700; }
.welcome-title { font-size: 2em; font-weight: 700; }
.section-title { font-weight: 700; margin-top: 6px; }
.row-name { font-weight: 600; }
.caption { font-size: 0.85em; }
.start-button { font-size: 1.15em; font-weight: 700; padding: 8px 28px; border-radius: 999px; }
.facts { border: 1px solid alpha(currentColor, 0.15); border-radius: 12px; padding: 14px 18px;
         background-color: alpha(currentColor, 0.03); }
.toast { border-radius: 999px; padding: 8px 20px; margin: 18px; }
.drop-zone { border: 3px dashed alpha(currentColor, 0.45); border-radius: 18px; margin: 12px;
             background-color: alpha(@theme_bg_color, 0.92); }
.progress-bar { padding: 6px 12px; border-bottom: 1px solid alpha(currentColor, 0.12);
                background-color: alpha(currentColor, 0.05); }
"""


def when(ts: float | None) -> str:
    if ts is None:
        return _("never")
    return GLib.DateTime.new_from_unix_local(int(ts)).format("%x %H:%M") or ""


def label(text: str = "", *classes: str, **kw) -> Gtk.Label:
    lb = Gtk.Label(label=text, xalign=kw.pop("xalign", 0), **kw)
    for c in classes:
        lb.add_css_class(c)
    return lb


def app_image(row: AppRow | None, size: int) -> Gtk.Image:
    image = Gtk.Image(pixel_size=size, icon_name=APP_ID)
    if row is not None and row.icon is not None:
        try:
            image.set_from_paintable(Gdk.Texture.new_from_filename(str(row.icon)))
        except GLib.Error:
            pass
    return image


class Dialog(Gtk.Window):
    """Question or message on top of the window: heading, text, optional extra widget, buttons (id, label)."""

    def __init__(self, parent: Gtk.Window, heading: str, body: str, buttons: list[tuple[str, str]],
                 then: Callable[[str], None] | None = None, extra: Gtk.Widget | None = None,
                 destructive: str | None = None):
        super().__init__(transient_for=parent, modal=True, resizable=False, title=heading, default_width=420)
        self.heading, self.body, self.extra, self.then = heading, body, extra, then
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, margin_top=24, margin_bottom=20,
                      margin_start=24, margin_end=24)
        box.append(label(heading, "section-title", wrap=True, max_width_chars=48))
        if body:
            box.append(label(body, wrap=True, max_width_chars=56, selectable=True))
        if extra is not None:
            box.append(extra)
        row = Gtk.Box(spacing=8, halign=Gtk.Align.END, margin_top=12)
        for rid, text in buttons:
            b = Gtk.Button(label=text)
            if rid == destructive:
                b.add_css_class("destructive-action")
            b.connect("clicked", lambda _b, r=rid: self.respond(r))
            row.append(b)
        box.append(row)
        self.set_child(box)
        self.set_default_widget(row.get_first_child())          # Enter = the first, harmless button
        self.connect("close-request", lambda _w: self.respond(buttons[0][0]) or True)
        parent.dialog = self

    def respond(self, rid: str) -> None:
        then, self.then = self.then, None
        parent = self.get_transient_for()
        if parent is not None and getattr(parent, "dialog", None) is self:
            parent.dialog = None
        self.destroy()
        if then is not None:
            then(rid)


class Window(Gtk.ApplicationWindow):
    def __init__(self, app: Gtk.Application, paths: Paths, lang: str | None, launcher: list[str],
                 env: Mapping[str, str] | None = None):
        self.state = load_state(paths.window_state())
        super().__init__(application=app, title="APK Starter", default_width=self.state.width,
                         default_height=self.state.height, icon_name=APP_ID)
        self.paths, self.lang, self.launcher = paths, lang, launcher
        self.env = dict(os.environ if env is None else env)
        self.all_rows: list[AppRow] = []
        self.rows: dict[str, AppRow] = {}
        self.sort = self.state.sort
        self.selected: str | None = self.state.selected
        self.busy = 0                               # running install/remove/clear jobs
        self.generation = 0                         # newest list load; older results are dropped
        self.dialog: Dialog | None = None
        self.narrow = False
        self.page = "list"                          # narrow window: "list" or "details"
        self.toast_timer = 0

        self.search = Gtk.SearchEntry(placeholder_text=_("Search apps"), hexpand=True)
        self.search.connect("search-changed", lambda _e: self.show_list())
        self.search.connect("activate", lambda _e: self.focus_list())
        self.search.set_key_capture_widget(self)
        sort_menu = Gio.Menu()
        for key, text in (("name", _("Name")), ("started", _("Last started")), ("installed", _("Last installed")),
                          ("size", _("Data size"))):
            sort_menu.append(text, f"win.sort::{key}")
        sort = Gtk.MenuButton(icon_name="view-sort-descending-symbolic", menu_model=sort_menu,
                              tooltip_text=_("Sort"))
        install = Gtk.Button(icon_name="list-add-symbolic", tooltip_text=_("Install APK…"))
        install.connect("clicked", lambda _b: self.choose_apk())
        self.spinner = Gtk.Spinner(visible=False, spinning=True, tooltip_text=_("Working…"))
        top = Gtk.Box(spacing=6, margin_top=10, margin_bottom=8, margin_start=10, margin_end=10)
        for w in (self.search, sort, install, self.spinner):
            top.append(w)
        self.listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE, activate_on_single_click=False)
        self.listbox.add_css_class("navigation-sidebar")
        self.listbox.connect("row-selected", self.on_row_selected)
        self.listbox.connect("row-activated", self.on_row_activated)
        self.nothing = label(_("No app matches."), "dim-label", xalign=0.5, margin_top=24, visible=False)
        side = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, width_request=SIDE_WIDTH)
        side.append(top)
        scroll = Gtk.ScrolledWindow(child=self.listbox, vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        side.append(self.nothing)
        side.append(scroll)
        self.side = side

        self.back = Gtk.Button(icon_name="go-previous-symbolic", tooltip_text=_("Back"), visible=False,
                               halign=Gtk.Align.START, margin_top=10, margin_start=10)
        self.back.connect("clicked", lambda _b: self.go_back())
        self.details = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18, margin_top=24, margin_bottom=24,
                               margin_start=DETAILS_MARGIN, margin_end=DETAILS_MARGIN)
        detail_side = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True)
        detail_side.append(self.back)
        detail_side.append(Gtk.ScrolledWindow(child=self.details, vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER))
        self.detail_side = detail_side
        self.split = Gtk.Separator(orientation=Gtk.Orientation.VERTICAL)
        apps = Gtk.Box()
        for w in (side, self.split, detail_side):
            apps.append(w)

        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE, vexpand=True)
        self.stack.add_named(Gtk.Box(), "loading")
        self.stack.add_named(self.make_welcome(), "empty")
        self.stack.add_named(apps, "list")

        self.progress_label = label("", hexpand=True, ellipsize=Pango.EllipsizeMode.END)
        bar = Gtk.Box(spacing=10)
        bar.add_css_class("progress-bar")
        bar.append(Gtk.Spinner(spinning=True))
        bar.append(self.progress_label)
        self.banner = Gtk.Revealer(child=bar, reveal_child=False)
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        body.append(self.banner)
        body.append(self.stack)

        self.toast_label = label("", wrap=True, xalign=0.5)
        toast = Gtk.Box()
        toast.add_css_class("toast")
        toast.add_css_class("osd")
        toast.append(self.toast_label)
        self.toasts = Gtk.Revealer(child=toast, reveal_child=False, halign=Gtk.Align.CENTER,
                                   valign=Gtk.Align.END, transition_type=Gtk.RevealerTransitionType.SLIDE_UP,
                                   can_target=False)
        drop_hint = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, valign=Gtk.Align.CENTER)
        drop_hint.append(Gtk.Image(icon_name=APP_ID, pixel_size=96))
        drop_hint.append(label(_("Drop the APK to install it"), "welcome-title", xalign=0.5))
        zone = Gtk.Box(hexpand=True, vexpand=True)
        zone.add_css_class("drop-zone")
        drop_hint.set_hexpand(True)
        zone.append(drop_hint)
        self.drop_zone = Gtk.Revealer(child=zone, reveal_child=False, can_target=False,
                                      transition_type=Gtk.RevealerTransitionType.CROSSFADE)
        overlay = Gtk.Overlay(child=body)
        overlay.add_overlay(self.drop_zone)
        overlay.add_overlay(self.toasts)
        self.set_child(overlay)

        drop = Gtk.DropTarget.new(Gdk.FileList, Gdk.DragAction.COPY)
        drop.connect("drop", self.on_drop)
        drop.connect("enter", lambda *_a: self.drop_zone.set_reveal_child(True) or Gdk.DragAction.COPY)
        drop.connect("leave", lambda *_a: self.drop_zone.set_reveal_child(False))
        self.add_controller(drop)
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self.on_key)
        self.add_controller(keys)

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
        sort_action = Gio.SimpleAction.new_stateful("sort", GLib.VariantType.new("s"), GLib.Variant("s", self.sort))
        sort_action.connect("activate", lambda a, v: (a.set_state(v), self.set_sort(v.get_string())))
        self.add_action(sort_action)
        self.connect("close-request", lambda _w: self.remember() or False)
        self.stack.set_visible_child_name("loading")
        self.refresh()
        if not dexopt.runtime_ready(self.paths, self.env) or dexopt.stale_installed(self.paths):
            # after a Flatpak update: rebuild once in the background; apps started meanwhile wait for the runtime
            self.background(lambda: prepare_all(self.paths, self.env, self.progress), None, "")

    def make_welcome(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14, valign=Gtk.Align.CENTER,
                      halign=Gtk.Align.CENTER, margin_start=24, margin_end=24)
        box.append(Gtk.Image(icon_name=APP_ID, pixel_size=128, margin_bottom=8))
        self.welcome_title = label(_("No apps installed"), "welcome-title", xalign=0.5, wrap=True)
        self.welcome_text = label(_("Drag an APK here or choose one."), "dim-label", xalign=0.5, wrap=True,
                                  justify=Gtk.Justification.CENTER)
        choose = Gtk.Button(label=_("Choose APK…"), halign=Gtk.Align.CENTER, margin_top=10)
        choose.add_css_class("suggested-action")
        choose.add_css_class("start-button")
        choose.connect("clicked", lambda _b: self.choose_apk())
        for w in (self.welcome_title, self.welcome_text, choose):
            box.append(w)
        return box

    # -- helpers -----------------------------------------------------------------------------------------
    def toast(self, text: str) -> None:
        """Short confirmation, shown at once for 4 s."""
        self.toast_label.set_text(text)
        self.toasts.set_reveal_child(True)
        if self.toast_timer:
            GLib.source_remove(self.toast_timer)

        def hide() -> bool:
            self.toast_timer = 0
            self.toasts.set_reveal_child(False)
            return False
        self.toast_timer = GLib.timeout_add_seconds(4, hide)

    def alert(self, heading: str, body: str) -> None:
        """Errors and warnings stay until the user closes them."""
        Dialog(self, heading, body, [("close", _("Close"))]).present()

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
                self.banner.set_reveal_child(self.banner.get_reveal_child() and self.busy > 0)
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
            self.progress_label.set_text(text)
            self.banner.set_reveal_child(self.busy > 0)
            return False
        GLib.idle_add(show)

    def remember(self) -> None:
        width, height = self.get_default_size()
        state = WindowState(width=width if width > 0 else self.state.width,
                            height=height if height > 0 else self.state.height, sort=self.sort,
                            selected=self.selected)
        try:
            save_state(self.paths.window_state(), state)
        except OSError:
            pass                                    # only a convenience

    # -- layout: wide = list + details, narrow = one page at a time ----------------------------------------
    def do_size_allocate(self, width: int, height: int, baseline: int) -> None:
        Gtk.ApplicationWindow.do_size_allocate(self, width, height, baseline)
        margin = max(DETAILS_MARGIN, (width - (0 if is_narrow(width) else SIDE_WIDTH) - DETAILS_MAX) // 2)
        if is_narrow(width) != self.narrow or margin != self.details.get_margin_start():
            GLib.idle_add(lambda: self.set_narrow(is_narrow(width), margin) or False)

    def set_narrow(self, narrow: bool, margin: int) -> None:
        """Details keep a readable width: wide windows get wider margins instead of longer lines."""
        self.details.set_margin_start(margin)
        self.details.set_margin_end(margin)
        if narrow == self.narrow:
            return
        self.narrow = narrow
        self.listbox.set_activate_on_single_click(narrow)
        self.update_pages()

    def update_pages(self) -> None:
        details = self.narrow and self.page == "details" and self.selected in self.rows
        self.side.set_visible(not details)
        self.split.set_visible(not self.narrow)
        self.detail_side.set_visible(not self.narrow or details)
        self.side.set_hexpand(self.narrow)
        self.back.set_visible(details)

    def go_back(self) -> None:
        if self.narrow and self.page == "details":
            self.page = "list"
            self.update_pages()
            row = self.listbox.get_selected_row()
            (row or self.listbox).grab_focus()

    # -- list ----------------------------------------------------------------------------------------------
    def refresh(self) -> None:
        self.generation += 1
        generation = self.generation
        self.background(lambda: load_rows(self.paths),
                        lambda rows: generation == self.generation and self.show_rows(rows),
                        _("Cannot read the app list"), job=False)

    def show_rows(self, rows: list[AppRow]) -> None:
        self.all_rows = rows
        self.rows = {r.package: r for r in rows}
        self.stack.set_visible_child_name("list" if rows else "empty")
        self.show_list()

    def visible_rows(self) -> list[AppRow]:
        return filter_rows(sort_rows(self.all_rows, self.sort), self.search.get_text())

    def show_list(self) -> None:
        rows = self.visible_rows()
        keep = self.selected
        self.filling = True
        self.listbox.remove_all()
        chosen = None
        for r in rows:
            item = self.make_row(r)
            self.listbox.append(item)
            if r.package == keep:
                chosen = item
        if chosen is None and rows:
            chosen = self.listbox.get_row_at_index(0)
        self.listbox.select_row(chosen)
        self.filling = False
        self.nothing.set_visible(bool(self.all_rows) and not rows)
        self.selected = chosen.package if chosen is not None else (keep if keep in self.rows else None)
        self.show_details()
        self.update_pages()

    def make_row(self, r: AppRow) -> Gtk.ListBoxRow:
        box = Gtk.Box(spacing=12, margin_top=6, margin_bottom=6, margin_start=6, margin_end=6)
        box.append(app_image(r, 40))
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER, spacing=2)
        text.append(label(r.label, "row-name", ellipsize=Pango.EllipsizeMode.END))
        text.append(label(r.version or r.package, "dim-label", "caption", ellipsize=Pango.EllipsizeMode.END))
        box.append(text)
        item = Gtk.ListBoxRow(child=box, tooltip_text=r.package)
        item.package = r.package
        item.title = r.label
        return item

    def on_row_selected(self, _box, item) -> None:
        if getattr(self, "filling", False) or item is None:
            return
        self.selected = item.package
        self.show_details()

    def on_row_activated(self, _box, item) -> None:
        """Double click or Enter starts; in a narrow window a click opens the details page."""
        if self.narrow:
            self.selected = item.package
            self.page = "details"
            self.show_details()
            self.update_pages()
        else:
            self.start(item.package, None)

    def focus_list(self) -> None:
        row = self.listbox.get_selected_row() or self.listbox.get_row_at_index(0)
        if row is not None:
            self.listbox.select_row(row)
            row.grab_focus()

    def set_sort(self, key: str) -> None:
        self.sort = key
        self.show_list()

    def on_key(self, _ctl, keyval: int, _code: int, state: Gdk.ModifierType) -> bool:
        focus = self.get_focus()
        in_search = focus is not None and (focus is self.search or focus.is_ancestor(self.search))
        name = Gdk.keyval_name(keyval) or ""
        action = key_action(name, bool(state & Gdk.ModifierType.CONTROL_MASK), in_search)
        if action == "search":
            self.search.grab_focus()
        elif action == "to-list":
            self.focus_list()
        elif action == "start" and self.selected in self.rows and isinstance(focus, Gtk.ListBoxRow):
            self.start(self.selected, None)
        elif action == "uninstall" and self.selected in self.rows:
            self.ask_uninstall(self.selected)
        elif action == "back" and self.narrow and self.page == "details":
            self.go_back()
        else:
            return False
        return True

    # -- details -------------------------------------------------------------------------------------------
    def show_details(self) -> None:
        while (child := self.details.get_first_child()) is not None:
            self.details.remove(child)
        r = self.rows.get(self.selected or "")
        if r is None:
            return
        target = GLib.Variant("s", r.package)

        head = Gtk.Box(spacing=20)
        head.append(app_image(r, 96))
        names = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, valign=Gtk.Align.CENTER)
        self.details_title = label(r.label, "app-name", wrap=True, selectable=True)
        names.append(self.details_title)
        names.append(label(r.package, "dim-label", selectable=True, wrap=True))
        head.append(names)
        self.details.append(head)

        start = Gtk.Button(label=_("Start"), action_name="win.start", action_target=target,
                           halign=Gtk.Align.START)
        start.add_css_class("suggested-action")
        start.add_css_class("start-button")
        more = Gtk.Box(spacing=8)
        for text, action in ((_("Start with log"), "win.start-log"), (_("Start with debug"), "win.start-debug")):
            more.append(Gtk.Button(label=text, action_name=action, action_target=target))
        starts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        starts.append(start)
        starts.append(more)
        starts.append(label(_("Debug runs much slower; use it for error reports."), "dim-label", "caption",
                            wrap=True))
        self.details.append(starts)

        facts = Gtk.Grid(column_spacing=24, row_spacing=8)
        facts.add_css_class("facts")
        lines = ((_("Version"), r.version or "–"),
                 (_("Target SDK"), str(r.target_sdk) if r.target_sdk else "–"),
                 (_("Installed"), when(r.installed)),
                 (_("Last started"), when(r.started)),
                 (_("Data"), GLib.format_size(r.size) if r.size else _("no data")),
                 (_("Status"), _("Prepared for this computer") if r.prepared
                  else _("Not prepared yet (done at the next start)")))
        for i, (key, value) in enumerate(lines):
            facts.attach(label(key, "dim-label"), 0, i, 1, 1)
            facts.attach(label(value, wrap=True, selectable=True, hexpand=True), 1, i, 1, 1)
        self.details.append(facts)

        heading = Gtk.Box(spacing=8)
        heading.append(label(_("Recordings"), "section-title", hexpand=True))
        logs = Gtk.Button(label=_("Open log folder"), action_name="win.open-logs", action_target=target)
        logs.add_css_class("flat")
        heading.append(logs)
        self.details.append(heading)
        recs = diag.recordings(self.paths, r.package)
        if recs:
            kinds = {"log": _("Log"), "debug": _("Debug")}
            for i, rec in enumerate(recs[:RECORDINGS_SHOWN]):
                line = Gtk.Box(spacing=8)
                line.append(label(f"{rec.started:%Y-%m-%d %H:%M} · {kinds[rec.kind]}", hexpand=True))
                show = Gtk.Button(label=_("Show report"))
                show.connect("clicked", lambda _b, n=i, p=r.package: self.show_report(p, n))
                save = Gtk.Button(label=_("Save Report…"))
                save.connect("clicked", lambda _b, n=i, p=r.package: self.show_report(p, n, save=True))
                line.append(show)
                line.append(save)
                self.details.append(line)
        else:
            self.details.append(label(_("None yet. Start the app with log or with debug to record one."),
                                      "dim-label", wrap=True))

        self.details.append(Gtk.Separator(margin_top=6))
        danger = Gtk.Box(spacing=8)
        danger.append(Gtk.Button(label=_("Delete data…"), action_name="win.clear-data", action_target=target))
        uninstall = Gtk.Button(label=_("Uninstall…"), action_name="win.uninstall", action_target=target)
        uninstall.add_css_class("danger-text")         # calm until the question, which is red
        danger.append(uninstall)
        self.details.append(danger)

    # -- actions -------------------------------------------------------------------------------------------
    def start(self, pkg: str, level: str | None) -> None:
        label_ = self.rows[pkg].label if pkg in self.rows else pkg
        try:
            proc = Gio.Subprocess.new(start_argv(self.launcher, pkg, level), Gio.SubprocessFlags.NONE)
        except GLib.Error as e:
            self.alert(_("Cannot start {app}").format(app=label_), e.message)
            return

        def ended(p: Gio.Subprocess, res) -> None:
            p.wait_finish(res)
            hint = _("Look at “Show report”.") if level else _("Try “Start with log”, then “Show report”.")
            if p.get_if_exited() and p.get_exit_status() != 0:
                self.alert(_("{app} ended with exit code {code}").format(app=label_, code=p.get_exit_status()), hint)
            elif p.get_if_signaled():
                self.alert(_("{app} was killed by signal {signal}").format(app=label_, signal=p.get_term_sig()), hint)
            if pkg in self.rows:
                self.refresh()                      # «last started», recordings
        proc.wait_async(None, ended)
        if level == "log":
            self.toast(_("{app} starts with a log").format(app=label_))
        elif level == "debug":
            self.toast(_("{app} starts with debug; it runs much slower").format(app=label_))
        else:
            self.toast(_("{app} starts").format(app=label_))

    def show_report(self, pkg: str, index: int = 0, save: bool = False) -> None:
        label_ = self.rows[pkg].label if pkg in self.rows else pkg
        report = ReportWindow(self, pkg, label_, index, save)
        report.present()

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
        self.drop_zone.set_reveal_child(False)
        paths = [f.get_path() for f in files.get_files()]
        for p in paths:
            if p:
                self.install(Path(p))
        return any(paths)

    def install(self, apk: Path) -> None:
        warnings: list[str] = []

        def done(info) -> None:
            self.selected = info.package
            self.search.set_text("")
            if warnings:
                self.alert(_("{app} installed").format(app=info.label), "\n".join(warnings))
            else:
                self.toast(_("{app} installed").format(app=info.label))
        self.progress(_("Installing {file}…").format(file=apk.name))
        self.background(lambda: install_apk(self.paths, apk, self.lang, self.env, self.progress, warnings.append),
                        done, _("Cannot install {file}").format(file=apk.name))

    def _confirm(self, heading: str, body: str, verb: str, then: Callable[[], None],
                 extra: Gtk.Widget | None = None) -> None:
        Dialog(self, heading, body, [("cancel", _("Cancel")), ("ok", verb)],
               lambda resp: then() if resp == "ok" else None, extra=extra, destructive="ok").present()

    def ask_clear_data(self, pkg: str) -> None:
        label_ = self.rows[pkg].label if pkg in self.rows else pkg
        self._confirm(_("Delete the data of {app}?").format(app=label_),
                      _("The app stays installed. Its settings, accounts and files are deleted. "
                        "Close the app first."), _("Delete Data"),
                      lambda: self.background(lambda: clear_data(self.paths, pkg),
                                              lambda _r: self.toast(_("Data of {app} deleted").format(app=label_)),
                                              _("Cannot delete the data of {app}").format(app=label_)))

    def ask_uninstall(self, pkg: str) -> None:
        label_ = self.rows[pkg].label if pkg in self.rows else pkg
        purge = Gtk.CheckButton(label=_("Also delete app data"))

        def removed(warnings: list[str]) -> None:
            if self.selected == pkg:
                self.selected = None
                self.page = "list"
            if warnings:
                self.alert(_("{app} uninstalled").format(app=label_), "\n".join(warnings))
            else:
                self.toast(_("{app} uninstalled").format(app=label_))
        self._confirm(_("Uninstall {app}?").format(app=label_),
                      _("The app and its menu entry are removed. Close the app first."), _("Uninstall"),
                      lambda: self.background(lambda: remove_app(self.paths, pkg, purge.get_active()), removed,
                                              _("Cannot uninstall {app}").format(app=label_)),
                      extra=purge)


class ReportWindow(Gtk.Window):
    """«Show report»: the recordings of one app (newest first) and atl-doctor's report of the chosen one."""

    def __init__(self, win: Window, pkg: str, app_label: str, index: int = 0, save: bool = False):
        super().__init__(transient_for=win, title=_("Report: {app}").format(app=app_label), default_width=760,
                         default_height=640)
        self.win, self.app_label, self.save_after_load = win, app_label, save
        self.recordings = diag.recordings(win.paths, pkg)
        self.report = None
        self.generation = 0
        win.report_window = self
        kinds = {"log": _("Log"), "debug": _("Debug")}
        names = [f"{r.started:%Y-%m-%d %H:%M:%S} · {kinds[r.kind]}" for r in self.recordings]
        self.choice = Gtk.DropDown.new_from_strings(names or [""])
        self.choice.set_tooltip_text(_("Recording"))
        self.choice.set_visible(bool(names))
        self.save = Gtk.Button(label=_("Save Report…"), sensitive=False)
        self.save.connect("clicked", lambda _b: self.ask_save())
        top = Gtk.Box(spacing=8, margin_top=10, margin_bottom=10, margin_start=12, margin_end=12)
        top.append(self.choice)
        top.append(Gtk.Box(hexpand=True))
        top.append(self.save)

        self.sections = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18, margin_top=18, margin_bottom=18,
                                margin_start=24, margin_end=24)
        self.stack = Gtk.Stack(vexpand=True)
        none = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, valign=Gtk.Align.CENTER)
        none.append(label(_("No recordings"), "welcome-title", xalign=0.5))
        none.append(label(_("Start the app with log or with debug first."), "dim-label", xalign=0.5))
        self.stack.add_named(none, "none")
        self.stack.add_named(Gtk.Spinner(spinning=True, halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER),
                             "loading")
        self.stack.add_named(Gtk.ScrolledWindow(child=self.sections, hscrollbar_policy=Gtk.PolicyType.NEVER),
                             "report")
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.append(top)
        box.append(Gtk.Separator())
        box.append(self.stack)
        self.set_child(box)
        if self.recordings:
            self.choice.set_selected(min(index, len(self.recordings) - 1))
            self.load(self.choice.get_selected())
        else:
            self.stack.set_visible_child_name("none")
        self.choice.connect("notify::selected", lambda d, _p: self.load(d.get_selected()))

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
                if self.save_after_load:
                    self.save_after_load = False
                    self.ask_save()
        self.win.background(work, done, _("Cannot read the report"), job=False)

    def show(self, view: list[diag.ReportSection]) -> None:
        while (child := self.sections.get_first_child()) is not None:
            self.sections.remove(child)
        for section in view:
            group = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            group.title = section.title
            group.append(label(section.title, "section-title"))
            items = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            items.add_css_class("facts")
            for item in section.items:
                if item.details:
                    text = label(item.details, wrap=True, selectable=True, margin_top=6, margin_start=18)
                    text.add_css_class("monospace")
                    row = Gtk.Expander(child=text)
                    row.set_label_widget(label(item.title, wrap=True))
                else:
                    row = label(item.title, wrap=True, selectable=True)
                row.title = item.title
                items.append(row)
            group.append(items)
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
        dialog.save(self, None, chosen)

    def save_to(self, path: Path) -> None:
        """atl-doctor's text report, the same as `apk-starter --report`."""
        try:
            path.write_text(diag.report_text(self.report), encoding="utf-8")
        except OSError as e:
            self.win.alert(_("Cannot save the report"), describe_error(e))
            return
        self.win.toast(_("Report saved"))


class ThemeWatcher:
    """Colour scheme, accent and font of the desktop from the settings portal, followed live (Spec 13)."""

    def __init__(self, display: Gdk.Display):
        self.display = display
        self.css = Gtk.CssProvider()
        Gtk.StyleContext.add_provider_for_display(display, self.css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1)
        self.values: dict[tuple[str, str], object] = {}
        self.proxy = None
        try:
            self.proxy = Gio.DBusProxy.new_for_bus_sync(
                Gio.BusType.SESSION, Gio.DBusProxyFlags.DO_NOT_AUTO_START_AT_CONSTRUCTION, None,
                "org.freedesktop.portal.Desktop", "/org/freedesktop/portal/desktop",
                "org.freedesktop.portal.Settings", None)
            all_ = self.proxy.call_sync("ReadAll", GLib.Variant("(as)", (list(theme.NAMESPACES),)),
                                        Gio.DBusCallFlags.NONE, 2000, None).unpack()[0]
            self.values = {(ns, k): v for ns, keys in all_.items() for k, v in keys.items()}
            self.proxy.connect("g-signal", self.on_signal)
        except GLib.Error:
            pass                                    # no portal: GTK's own defaults
        self.apply()

    def on_signal(self, _proxy, _sender, name: str, params: GLib.Variant) -> None:
        if name == "SettingChanged":
            ns, key, value = params.unpack()
            self.values[(ns, key)] = value
            self.apply()

    def apply(self, t: theme.Theme | None = None) -> None:
        t = t or theme.from_settings(self.values)
        settings = Gtk.Settings.get_for_display(self.display)
        if settings.find_property("gtk-interface-color-scheme") is not None:     # GTK 4.20+
            scheme = {True: Gtk.InterfaceColorScheme.DARK, False: Gtk.InterfaceColorScheme.LIGHT}
            settings.set_property("gtk-interface-color-scheme",
                                  scheme.get(t.dark, Gtk.InterfaceColorScheme.DEFAULT))
        else:
            settings.set_property("gtk-application-prefer-dark-theme", bool(t.dark))
        if t.font:
            settings.set_property("gtk-font-name", t.font)
        danger = "#ff7b63" if t.dark else "#c01c28"           # readable red on either background
        self.css.load_from_string(theme.accent_css(t.accent) + f".danger-text {{ color: {danger}; }}")


def install_styles(display: Gdk.Display) -> None:
    css = Gtk.CssProvider()
    css.load_from_string(CSS)
    Gtk.StyleContext.add_provider_for_display(display, css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)


def default_launcher() -> list[str]:
    """How to start an app: run this very program again (`/app/bin/apk-starter` inside the Flatpak)."""
    return [sys.executable, os.path.realpath(sys.argv[0])]


def run(paths: Paths, env: Mapping[str, str], launcher: list[str] | None = None) -> int:
    app = Gtk.Application(application_id=APP_ID)
    lang = lang_from_env(env)

    def startup(_a: Gtk.Application) -> None:
        display = Gdk.Display.get_default()
        install_styles(display)
        app.theme = ThemeWatcher(display)

    def activate(a: Gtk.Application) -> None:
        win = a.get_active_window() or Window(a, paths, lang, launcher or default_launcher(), env)
        win.present()
    app.connect("startup", startup)
    app.connect("activate", activate)
    return app.run([sys.argv[0]])
