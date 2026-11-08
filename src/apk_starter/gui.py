"""Management window (plain GTK 4, no libadwaita: the compositor draws its own title bar, e.g. Breeze under KWin;
GTK draws a simple one where the compositor wants none). List of apps on the left, details on the right.

All work is done by the same functions as the command line (cli.py); this module only shows and asks."""
import dataclasses
import os
import sys
import threading
from collections.abc import Callable, Mapping
from pathlib import Path

from . import aurora, dexopt, diag, microg, permissions, theme
from .cli import (APP_ID, ENTRY_PREFIX, clear_data, install_files, lang_from_env, microg_hint, prepare_all, remove_app,
                  setup_microg, VERSION)
from .apkinfo import read_apk_info
from .launch import cached_apk_info
from .errors import StarterError, describe_error
from .i18n import _
from .gui_model import (AppRow, WindowState, crash_meaning, details_layout, filter_rows, group_parts, is_narrow,
                        key_action, load_rows, load_state, save_state, sort_rows, start_argv)
from .paths import Paths
from .settings import TITLEBARS, load_settings, save_settings

try:
    import gi
    gi.require_version("Gtk", "4.0")
    gi.require_version("Gdk", "4.0")
    from gi.repository import Gdk, Gio, GLib, Gtk, Pango
except (ImportError, ValueError) as e:      # pragma: no cover - depends on the system
    raise StarterError(_("the window needs GTK 4: {reason}").format(reason=e)) from None

APK_MIME = "application/vnd.android.package-archive"
RECORDINGS_SHOWN = 5
NOTE_SECONDS = 6                # a short message at the bottom stays this long
SIDE_WIDTH = 300
DETAILS_MARGIN = 28
TEXT_CHARS = 64                 # readable line length of the details' texts

# Only spacing and type sizes: the theme draws buttons, frames and every other shape.
CSS = """
.app-name { font-size: 1.7em; font-weight: 700; }
.welcome-title { font-size: 2em; font-weight: 700; }
.section-title { font-weight: 700; }
.row-name { font-weight: 600; }
.caption { font-size: 0.85em; }
.drop-zone { margin: 12px; background-color: @theme_bg_color; }
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
        super().__init__(transient_for=parent, modal=True, resizable=False, title="APK Starter",
                         default_width=420)
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
        self.set_focus(row.get_first_child())                   # not the text (it would show as selected)
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
        # what runs for an app, shown as a spinner in its row and in «Status» (no bar):
        self.working: dict[str, str] = {}           # the rebuild after an update, per app
        self.jobs: dict[str, str] = {}              # delete data / uninstall
        self.pending: dict[int, list[str]] = {}     # installs and set-ups, before their app is in the list
        self.problems: dict[str, str] = {}          # the last failure of the rebuild, per app
        self.row_spinners: dict[str, Gtk.Spinner] = {}
        self.next_key = 0
        self.status_value: Gtk.Label | None = None
        self.generation = 0                         # newest list load; older results are dropped
        self.dialog: Dialog | None = None
        self.narrow = False
        self.page = "list"                          # narrow window: "list" or "details"
        self.layout = 0                             # extra right margin of the details
        self.start_button: Gtk.Button | None = None

        self.search = Gtk.SearchEntry(placeholder_text=_("Search apps"), hexpand=True)
        self.search.connect("search-changed", lambda _e: self.show_list())
        self.search.connect("activate", lambda _e: self.focus_list())
        self.search.set_key_capture_widget(self)
        sort_menu = Gio.Menu()
        for key, text in (("name", _("Name")), ("started", _("Last started")), ("installed", _("Last installed")),
                          ("size", _("Data size"))):
            sort_menu.append(text, f"win.sort::{key}")
        sort = Gtk.MenuButton(menu_model=sort_menu, tooltip_text=_("Sort"))
        # Breeze has no symbolic variant of this icon
        sort.set_child(Gtk.Image.new_from_gicon(Gio.ThemedIcon.new_from_names(
            ["view-sort-descending-symbolic", "view-sort-descending"])))
        self.add_menu = Gio.Menu()
        install = Gtk.MenuButton(menu_model=self.add_menu, icon_name="list-add-symbolic", tooltip_text=_("Add apps"))
        top = Gtk.Box(spacing=6, margin_top=10, margin_bottom=8, margin_start=10, margin_end=10)
        for w in (self.search, sort, install):
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
                               margin_start=DETAILS_MARGIN, margin_end=DETAILS_MARGIN, halign=Gtk.Align.FILL)
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
        self.stack.add_named(self.make_empty_page(), "empty")
        self.stack.add_named(apps, "list")

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        body.append(self.stack)
        self.toast_label = label("", wrap=True, max_width_chars=60)
        note = Gtk.Box()
        note.add_css_class("app-notification")
        note.append(self.toast_label)
        self.toast = Gtk.Revealer(child=note, reveal_child=False, halign=Gtk.Align.CENTER, valign=Gtk.Align.END,
                                  margin_bottom=18, can_target=False)
        self.toast_timer = 0

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
        overlay.add_overlay(self.toast)
        overlay.add_overlay(self.drop_zone)
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
                              ("open-logs", lambda _p: self.open_logs()),
                              ("clear-data", self.ask_clear_data),
                              ("uninstall", self.ask_uninstall)):
            action = Gio.SimpleAction.new(name, GLib.VariantType.new("s"))
            action.connect("activate", lambda _a, v, h=handler: h(v.get_string()))
            self.add_action(action)
        for name, handler in (("choose-apk", self.choose_apk), ("setup-aurora", self.ask_setup_aurora),
                              ("setup-microg", self.ask_setup_microg)):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", lambda _a, _v, h=handler: h())
            self.add_action(action)
        version = Gio.SimpleAction.new("version", None)
        version.set_enabled(False)
        self.add_action(version)
        sort_action = Gio.SimpleAction.new_stateful("sort", GLib.VariantType.new("s"), GLib.Variant("s", self.sort))
        sort_action.connect("activate", lambda a, v: (a.set_state(v), self.set_sort(v.get_string())))
        self.add_action(sort_action)
        self.connect("close-request", lambda _w: self.remember() or False)
        self.stack.set_visible_child_name("loading")
        self.refresh()
        # updates Aurora Store knows, as a notification: now and daily while the window is open (Spec 18 Phase 3)
        if app.lookup_action("aurora-updates") is None:
            updates = Gio.SimpleAction.new("aurora-updates", None)      # the notification's click
            updates.connect("activate", lambda *_a: self.start(aurora.PACKAGE, None))
            app.add_action(updates)
        self.check_updates()
        GLib.timeout_add_seconds(24 * 3600, self.check_updates)
        if not dexopt.runtime_ready(self.paths, self.env) or dexopt.stale_installed(self.paths):
            # after a Flatpak update: rebuild once in the background; apps started meanwhile wait for the runtime
            self.background(lambda: prepare_all(self.paths, self.env, lambda _t: None,
                                                lambda w: GLib.idle_add(self.set_working, w)),
                            self.prepared, None)

    def check_updates(self) -> bool:
        """Read Aurora Store's list of updates (see aurora.pending_updates) and tell new ones; never installs."""
        if load_settings(self.paths.settings()).aurora_updates and self.paths.meta(aurora.PACKAGE).is_file():
            self.background(lambda: aurora.updates_note(self.paths, aurora.pending_updates(self.paths)),
                            self.tell_updates, None, job=False)
        return GLib.SOURCE_CONTINUE

    def tell_updates(self, note: tuple[str, str] | None) -> None:
        if note is None:
            return
        n = Gio.Notification.new(note[0])
        n.set_body(note[1])
        n.set_default_action("app.aurora-updates")
        self.get_application().send_notification("aurora-updates", n)

    def make_empty_page(self) -> Gtk.Widget:
        """No app yet: only text, and the «+» button in the accent colour where it stands beside the list."""
        self.welcome_add = Gtk.MenuButton(menu_model=self.add_menu, icon_name="list-add-symbolic",
                                          tooltip_text=_("Add apps"), halign=Gtk.Align.START, margin_top=10,
                                          margin_start=10)
        # the theme colours the button inside a menu button, not the menu button itself
        self.welcome_add.get_first_child().add_css_class("suggested-action")
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        page.append(self.welcome_add)
        welcome = self.make_welcome()
        welcome.set_vexpand(True)
        page.append(welcome)
        return page

    def make_welcome(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14, valign=Gtk.Align.CENTER,
                      halign=Gtk.Align.CENTER, margin_start=24, margin_end=24)
        box.append(Gtk.Image(icon_name=APP_ID, pixel_size=128, margin_bottom=8))
        self.welcome_title = label(_("No apps installed"), "welcome-title", xalign=0.5, wrap=True)
        self.welcome_text = label(_("Add an app with + or drop an APK here."), "dim-label", xalign=0.5, wrap=True,
                                  justify=Gtk.Justification.CENTER)
        box.append(self.welcome_title)
        box.append(self.welcome_text)
        return box

    # -- helpers -----------------------------------------------------------------------------------------
    def alert(self, heading: str, body: str) -> None:
        """Errors and warnings stay until the user closes them."""
        Dialog(self, heading, body, [("close", _("Close"))]).present()

    def background(self, work: Callable[[], object], done: Callable[[object], None] | None,
                   failed: str | None, job: bool = True, end: Callable[[], None] | None = None) -> None:
        """Run file and portal work off the main loop; show errors as a dialog headed `failed` (None: a short
        note), then refresh. `end` runs on every way out, also after an error."""
        def thread() -> None:
            try:
                result, error = work(), None
            except Exception as e:                  # shown to the user, never a traceback
                result, error = None, e
            GLib.idle_add(finish, result, error)

        def finish(result, error) -> bool:
            try:
                if job:
                    self.busy -= 1
                if end is not None:
                    end()
                if error is not None:
                    if failed is None:
                        self.note(describe_error(error))
                    else:
                        self.alert(failed, describe_error(error))
                elif done is not None:
                    done(result)
            finally:
                if job:
                    self.refresh()
            return False

        if job:
            self.busy += 1
        threading.Thread(target=thread, daemon=True).start()

    def note(self, text: str) -> None:
        """A short message at the bottom of the window that goes by itself."""
        self.toast_label.set_text(text)
        self.toast.set_reveal_child(True)
        if self.toast_timer:
            GLib.source_remove(self.toast_timer)

        def hide() -> bool:
            self.toast_timer = 0
            self.toast.set_reveal_child(False)
            return False
        self.toast_timer = GLib.timeout_add_seconds(NOTE_SECONDS, hide)

    # -- what runs for an app --------------------------------------------------------------------------------
    def status_of(self, pkg: str) -> str | None:
        return self.jobs.get(pkg) or self.working.get(pkg)

    def set_working(self, working: dict[str, str]) -> bool:
        """From the rebuild after an update (worker thread via idle): the apps it works on now."""
        self.working = dict(working)
        self.update_busy()
        return False

    def prepared(self, problems: list[tuple[str | None, str]]) -> None:
        self.working = {}
        self.problems = {}
        for pkg, text in problems:
            for p in (self.rows if pkg is None else [pkg]):
                self.problems[p] = text
        if problems:
            self.note(problems[0][1])
        self.update_busy()

    def update_busy(self) -> None:
        for pkg, spinner in self.row_spinners.items():
            spinner.set_visible(self.status_of(pkg) is not None)
        if self.selected in self.rows and self.status_value is not None:
            if (self.status_of(self.selected) is None) != self.start_button.get_sensitive():
                self.show_details()                 # «Start» locked or free again
            else:
                self.status_value.set_text(self.status_text(self.rows[self.selected]))

    def status_text(self, r: AppRow) -> str:
        if (text := self.status_of(r.package)) is not None:
            return text
        if r.package in self.problems:
            return self.problems[r.package]
        return _("Prepared for this computer") if r.prepared else _("Not prepared yet (done at the next start)")

    def begin(self, title: str, text: str) -> tuple[Callable[[str], None], Callable[[], None]]:
        """A row with a spinner for an install or set-up, until its app is in the list. Returns the progress
        callback (worker threads) and what ends the row."""
        self.next_key += 1
        key = self.next_key
        self.pending[key] = [title, text]

        def progress(t: str) -> None:
            def show() -> bool:
                if key in self.pending:
                    self.pending[key][1] = t
                    self.show_list()
                return False
            GLib.idle_add(show)

        def end() -> None:
            self.pending.pop(key, None)
            self.update_stack()
            self.show_list()
        self.update_stack()
        self.show_list()
        return progress, end

    def run_for(self, pkg: str, text: str, work: Callable[[], object], done: Callable[[object], None] | None,
                failed: str) -> None:
        self.jobs[pkg] = text
        self.update_busy()

        def end() -> None:
            self.jobs.pop(pkg, None)
            self.update_busy()
        self.background(work, done, failed, end=end)

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
        layout = details_layout(width - (0 if is_narrow(width) else SIDE_WIDTH + 1) - 2 * DETAILS_MARGIN)
        if is_narrow(width) != self.narrow or layout != self.layout:
            GLib.idle_add(lambda: self.set_narrow(is_narrow(width), layout) or False)

    def set_narrow(self, narrow: bool, layout: int) -> None:
        """Details stand left, next to the list, and grow with the window up to a readable width."""
        if layout != self.layout:
            self.layout = layout
            self.details.set_margin_end(DETAILS_MARGIN + layout)
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
        self.update_add_menu()
        self.stack.set_visible_child_name("list" if rows or self.pending else "empty")
        if not rows and not self.pending:
            GLib.idle_add(self.focus_welcome_add)           # nothing else to do here but add an app
        self.show_list()

    def update_stack(self) -> None:
        """Installs and set-ups show their row also on the empty page; the first list load decides first."""
        if self.stack.get_visible_child_name() != "loading":
            self.stack.set_visible_child_name("list" if self.rows or self.pending else "empty")

    def focus_welcome_add(self) -> bool:
        """Focused with the focus ring showing (GTK shows it only after a key otherwise): Enter opens the menu."""
        self.welcome_add.grab_focus()
        self.set_focus_visible(True)
        return GLib.SOURCE_REMOVE

    def update_add_menu(self) -> None:
        """«Add apps» and the empty page offer a service only while it is not installed."""
        aurora_todo = not self.paths.meta(aurora.PACKAGE).is_file()
        microg_todo = not microg.installed(self.paths)
        self.add_menu.remove_all()
        self.add_menu.append(_("Install APK…"), "win.choose-apk")
        if aurora_todo:
            self.add_menu.append(_("Set up Aurora Store…"), "win.setup-aurora")
        if microg_todo:
            self.add_menu.append(_("Set up microG…"), "win.setup-microg")
        about = Gio.Menu()
        about.append(f"APK Starter {VERSION}", "win.version")       # greyed out: the action stays disabled
        self.add_menu.append_section(None, about)

    def visible_rows(self) -> list[AppRow]:
        return filter_rows(sort_rows(self.all_rows, self.sort), self.search.get_text())

    def show_list(self) -> None:
        rows = self.visible_rows()
        keep = self.selected
        self.filling = True
        self.listbox.remove_all()
        self.row_spinners = {}
        for title, text in self.pending.values():
            self.listbox.append(self.make_pending_row(title, text))
        chosen = None
        for r in rows:
            item = self.make_row(r)
            self.listbox.append(item)
            if r.package == keep:
                chosen = item
        if chosen is None and rows:
            chosen = self.listbox.get_row_at_index(len(self.pending))
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
        text.set_hexpand(True)
        box.append(text)
        spinner = Gtk.Spinner(spinning=True, visible=self.status_of(r.package) is not None, valign=Gtk.Align.CENTER)
        self.row_spinners[r.package] = spinner
        box.append(spinner)
        item = Gtk.ListBoxRow(child=box, tooltip_text=r.package)
        item.package = r.package
        item.title = r.label
        return item

    def make_pending_row(self, title: str, text: str) -> Gtk.ListBoxRow:
        box = Gtk.Box(spacing=12, margin_top=6, margin_bottom=6, margin_start=6, margin_end=6)
        box.append(app_image(None, 40))
        names = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER, spacing=2, hexpand=True)
        names.append(label(title, "row-name", ellipsize=Pango.EllipsizeMode.END))
        names.append(label(text, "dim-label", "caption", ellipsize=Pango.EllipsizeMode.END))
        box.append(names)
        box.append(Gtk.Spinner(spinning=True, valign=Gtk.Align.CENTER))
        item = Gtk.ListBoxRow(child=box, selectable=False, activatable=False, tooltip_text=text)
        item.package = None
        item.title = title
        return item

    def on_row_selected(self, _box, item) -> None:
        if getattr(self, "filling", False) or item is None or item.package is None:
            return
        self.selected = item.package
        self.show_details()

    def on_row_activated(self, _box, item) -> None:
        """Double click or Enter starts; in a narrow window a click opens the details page."""
        if item.package is None:
            return
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

    def save_app_setting(self, pkg: str, **changes) -> bool:
        """Per app, for every start of it (also from its menu entry)."""
        try:
            save_settings(self.paths.settings(), load_settings(self.paths.settings()).with_app(pkg, **changes))
        except OSError as e:
            self.alert(_("Could not save the setting"), describe_error(e))
            return False
        return True

    def show_package(self, pkg: str) -> None:
        """The details of one app (also before the list is read: it is kept selected)."""
        self.selected = pkg
        self.search.set_text("")
        if self.narrow:
            self.page = "details"
        if self.rows:                               # else the list, when read, keeps it selected
            self.show_list()

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
        focus = self.get_focus()
        refocus = focus is None or focus.is_ancestor(self.details)
        if refocus:
            self.set_focus(None)                    # never leave the focus on a widget that goes away
        self.start_button = None
        self.status_value = None
        while (child := self.details.get_first_child()) is not None:
            self.details.remove(child)
        r = self.rows.get(self.selected or "")
        if r is None:
            return
        target = GLib.Variant("s", r.package)
        app = load_settings(self.paths.settings()).app(r.package)

        head = Gtk.Box(spacing=20)
        head.append(app_image(r, 96))
        names = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, valign=Gtk.Align.CENTER)
        self.details_title = label(r.label, "app-name", wrap=True, max_width_chars=TEXT_CHARS)
        names.append(self.details_title)
        names.append(label(r.package, "dim-label", selectable=True, wrap=True, focusable=False))
        head.append(names)
        self.details.append(head)

        # one row: Start, Delete data, Uninstall, ⚙ (window bar, Debug, recordings)
        if self.status_of(r.package) is None:
            start = Gtk.Button(label=_("Start"), action_name="win.start", action_target=target)
        else:                                       # locked while it is being prepared (an action would free it)
            start = Gtk.Button(label=_("Start"), sensitive=False)
        start.add_css_class("suggested-action")         # the main action in the theme's accent (KDE/GNOME)
        self.start_button = start
        uninstall = Gtk.Button(label=_("Uninstall…"), action_name="win.uninstall", action_target=target)
        uninstall.add_css_class("danger-text")         # calm until the question, which is red
        # a square icon button drawn by the theme; the icon from the icon theme: KDE's «configure» (Breeze),
        # else the gear of Adwaita
        gear = Gtk.MenuButton(popover=self.make_options(r, app), tooltip_text=_("Options"))
        gear.get_first_child().add_css_class("image-button")    # the inner button, as set_icon_name() does
        icon = Gtk.Overlay(child=Gtk.Image.new_from_gicon(Gio.ThemedIcon.new_from_names(
            ["configure-symbolic", "configure", "emblem-system-symbolic"])))
        self.gear_dot = label("●", "caption", "accent", halign=Gtk.Align.END, valign=Gtk.Align.START,
                              visible=app.debug or app.debug_full, tooltip_text=_("Debug is on"))
        icon.add_overlay(self.gear_dot)
        gear.set_child(icon)
        self.gear = gear
        # one height and font for all (the box gives them its height), only the theme draws them
        actions = Gtk.Box(spacing=8, valign=Gtk.Align.CENTER)
        for w in (start, Gtk.Button(label=_("Delete data…"), action_name="win.clear-data", action_target=target),
                  uninstall, gear):
            actions.append(w)
        self.details.append(actions)

        facts = Gtk.Grid(column_spacing=24, row_spacing=8, margin_top=14, margin_bottom=14, margin_start=18,
                         margin_end=18)
        lines = ((_("Version"), r.version or "–"),
                 (_("Target SDK"), str(r.target_sdk) if r.target_sdk else "–"),
                 (_("Installed"), when(r.installed)),
                 (_("Last started"), when(r.started)),
                 (_("Data"), GLib.format_size(r.size) if r.size else _("no data")),
                 (_("Status"), self.status_text(r)))
        for i, (key, value) in enumerate(lines):
            facts.attach(label(key, "dim-label"), 0, i, 1, 1)
            value_label = label(value, wrap=True, selectable=True, hexpand=True, focusable=False)
            facts.attach(value_label, 1, i, 1, 1)
        self.status_value = value_label
        self.details.append(Gtk.Frame(child=facts))
        if refocus and (not self.narrow or self.page == "details"):
            start.grab_focus()                      # not the name (a focused label shows all of it selected)

    def make_options(self, r: AppRow, app) -> Gtk.Popover:
        """The ⚙ menu: window bar and Debug (both remembered per app), the Debug recordings.

        The window bar is a group of option buttons, not a drop-down: the menu keeps one size and nothing in it
        opens over the rest."""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin_top=8, margin_bottom=8, margin_start=8,
                      margin_end=8)
        box.append(label(_("Window bar"), "section-title"))
        names_ = {"atl": _("With back button"), "system": _("From the system"),
                  "system-navigation": _("From the system + navigation bar")}
        choice: dict[str, Gtk.CheckButton] = {}
        for key in TITLEBARS:                       # with back button (the default), from the system, + navigation
            b = Gtk.CheckButton(label=names_[key], active=key == app.titlebar,
                                group=next(iter(choice.values()), None))
            b.connect("toggled", lambda b, p=r.package, k=key: b.get_active() and self.save_app_setting(p, titlebar=k))
            choice[key] = b
            box.append(b)
        box.append(Gtk.Separator(margin_top=4))
        debug = Gtk.CheckButton(label=_("Debug"), active=app.debug and not app.debug_full)
        debug_full = Gtk.CheckButton(label=_("Debug full"), active=app.debug_full,
                                     tooltip_text=_("The archive contains everything in plain text, also passwords "
                                                    "and personal data – do not pass it on"))

        def debug_toggled(b: Gtk.CheckButton, pkg=r.package) -> None:
            """Remembered per app, for every start (also from its menu entry) until it is unticked. The two
            levels exclude each other."""
            full = b is debug_full
            if b.get_active():
                (debug if full else debug_full).set_active(False)
            now = load_settings(self.paths.settings()).app(pkg)
            want = {"debug_full": debug_full.get_active(), "debug": debug.get_active()}
            if (now.debug_full, now.debug) != (want["debug_full"], want["debug"]):
                self.save_app_setting(pkg, **want)
            on = debug.get_active() or debug_full.get_active()
            self.gear_dot.set_visible(on)
        debug.connect("toggled", debug_toggled)
        debug_full.connect("toggled", debug_toggled)
        self.debug_check, self.debug_full_check, self.titlebar_choice = debug, debug_full, choice
        for w in (debug, debug_full):
            box.append(w)

        self.aurora_updates_check = None
        if r.package == aurora.PACKAGE:             # Aurora Store: tell its updates (off by default)
            updates = Gtk.CheckButton(label=_("Report updates"), active=load_settings(self.paths.settings()).aurora_updates)

            def updates_toggled(b: Gtk.CheckButton) -> None:
                try:
                    save_settings(self.paths.settings(), dataclasses.replace(load_settings(self.paths.settings()),
                                                                             aurora_updates=b.get_active()))
                except OSError as e:
                    self.alert(_("Could not save the setting"), describe_error(e))
                    return
                if b.get_active():
                    self.check_updates()
            updates.connect("toggled", updates_toggled)
            box.append(updates)
            self.aurora_updates_check = updates

        # the dangerous permissions of the manifest, by group: off = ATL answers «denied» (default: all on)
        self.permission_switches = {}
        try:
            asked = cached_apk_info(self.paths, self.paths.apk(r.package), self.lang, read_apk_info).permissions
        except (StarterError, OSError):
            asked = ()
        keys = permissions.groups(asked)
        if keys:
            box.append(Gtk.Separator(margin_top=4))
            box.append(label(_("Permissions"), "section-title"))

        def switched(sw: Gtk.Switch, _param, pkg=r.package, key="") -> None:
            denied = set(load_settings(self.paths.settings()).app(pkg).denied)
            denied = denied - {key} if sw.get_active() else denied | {key}
            self.save_app_setting(pkg, denied=tuple(k for k, _l, _p in permissions.GROUPS if k in denied))
        for key in keys:
            row = Gtk.Box(spacing=12)
            row.append(label(_(permissions.LABELS[key]), hexpand=True))
            sw = Gtk.Switch(active=key not in app.denied, valign=Gtk.Align.CENTER)
            sw.connect("notify::active", switched, r.package, key)
            row.append(sw)
            box.append(row)
            self.permission_switches[key] = sw

        recs = diag.recordings(self.paths, r.package)
        debugs = [rec for rec in recs if rec.kind == "debug"]       # the plain logs are in the folder only
        self.recordings_shown = len(debugs[:RECORDINGS_SHOWN])
        if recs:
            box.append(Gtk.Separator(margin_top=4))
            if debugs:
                box.append(label(_("Recordings"), "section-title"))
            for rec in debugs[:RECORDINGS_SHOWN]:
                box.append(label(f"{rec.started:%Y-%m-%d %H:%M} · {_('Debug full') if rec.full else _('Debug')}"))
            box.append(Gtk.Button(label=_("Open log folder"), action_name="win.open-logs",
                                  action_target=GLib.Variant("s", r.package), halign=Gtk.Align.START))
        return Gtk.Popover(child=box)

    # -- actions -------------------------------------------------------------------------------------------
    def start(self, pkg: str, level: str | None) -> None:
        """level None: as from the menu entry (Debug if it is ticked for the app), or "debug" / "debug-full"."""
        label_ = self.rows[pkg].label if pkg in self.rows else pkg
        if self.status_of(pkg) is not None:
            return                                  # it is being prepared; the start waits for it
        before = {rec.path for rec in diag.recordings(self.paths, pkg)}
        try:
            proc = Gio.Subprocess.new(start_argv(self.launcher, pkg, level), Gio.SubprocessFlags.NONE)
        except GLib.Error as e:
            self.alert(_("Cannot start {app}").format(app=label_), e.message)
            return

        def ended(p: Gio.Subprocess, res) -> None:
            p.wait_finish(res)
            exited = p.get_if_exited()
            meaning = crash_meaning(exited, p.get_exit_status() if exited else p.get_term_sig())
            if meaning is not None:
                new = [rec for rec in diag.recordings(self.paths, pkg) if rec.path not in before]
                self.crashed(label_, meaning, new[0].path if new else None)
            if pkg in self.rows:
                self.refresh()                      # «last started», recordings
        proc.wait_async(None, ended)

    def crashed(self, label_: str, meaning: str, recording: Path | None) -> None:
        """A real crash, not a normal end (exit 0, SIGTERM): what it means; atl-doctor reads the recordings.
        «Open log folder» shows the recording of this start."""
        Dialog(self, _("{app} ended unexpectedly.").format(app=label_), "",
               [("close", _("Close")), ("logs", _("Open log folder"))],
               lambda resp: self.open_logs(recording) if resp == "logs" else None,
               extra=label(meaning, "dim-label", "caption", wrap=True, max_width_chars=56)).present()

    def open_logs(self, select: Path | None = None) -> None:
        """The log folder; with `select` the file manager shows that recording marked."""
        folder = self.paths.logs_dir()
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            self.alert(_("Cannot open the log folder"), describe_error(e))
            return
        marked = select is not None and select.exists()
        launcher = Gtk.FileLauncher.new(Gio.File.new_for_path(str(select if marked else folder)))

        def opened(lnc: Gtk.FileLauncher, res) -> None:
            try:
                lnc.open_containing_folder_finish(res) if marked else lnc.launch_finish(res)
            except GLib.Error as e:
                self.alert(_("Cannot open the log folder"), f"{folder}: {e.message}")
        (launcher.open_containing_folder if marked else launcher.launch)(self, None, opened)

    def choose_apk(self) -> None:
        apks = Gtk.FileFilter(name=_("Android apps"))
        for suffix in ("apk", "apks", "xapk"):
            apks.add_suffix(suffix)
        apks.add_mime_type(APK_MIME)
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(apks)
        dialog = Gtk.FileDialog(title=_("Install APK"), filters=filters, default_filter=apks)

        def chosen(d: Gtk.FileDialog, res) -> None:
            try:
                files = d.open_multiple_finish(res)
            except GLib.Error:                      # cancelled
                return
            self.install_dropped([f.get_path() for f in files if f.get_path()])
        dialog.open_multiple(self, None, chosen)    # several files: a split APK (base and its parts)

    def on_drop(self, _target, files, _x, _y) -> bool:
        self.drop_zone.set_reveal_child(False)
        paths = [f.get_path() for f in files.get_files() if f.get_path()]
        self.install_dropped(paths)
        return bool(paths)

    def install_dropped(self, names: list[str]) -> None:
        """One install per app: the parts of a split APK together, other APKs each on their own."""
        for group in group_parts([Path(n) for n in names]):
            self.install(group)

    def install(self, files: Path | list[Path]) -> None:
        files = [files] if isinstance(files, Path) else files
        warnings: list[str] = []
        name = files[0].name

        def done(info) -> None:
            self.selected = info.package
            self.search.set_text("")
            if warnings:
                self.alert(_("{app} installed").format(app=info.label), "\n".join(warnings))
        progress, end = self.begin(name, _("Installing…"))
        self.background(lambda: install_files(self.paths, files, self.lang, self.env, progress, warnings.append),
                        done, _("Cannot install {file}").format(file=name), end=end)

    def ask_setup_aurora(self) -> None:
        def setup() -> None:
            warnings: list[str] = []

            def done(info) -> None:
                self.selected = info.package
                self.search.set_text("")
                if warnings:
                    self.alert(_("{app} installed").format(app=info.label), "\n".join(warnings))
            progress, end = self.begin("Aurora Store", _("Downloading Aurora Store from F-Droid…"))
            self.background(lambda: aurora.setup(self.paths, self.env, progress, warnings.append), done,
                            _("Cannot set up Aurora Store"), end=end)
        Dialog(self, _("Set up Aurora Store?"),
               _("Free apps from Google Play. Downloaded from F-Droid and checked.\n\n"
                 "Sign in anonymously: Google can block your own account."),
               [("cancel", _("Cancel")), ("ok", _("Set up"))],
               lambda resp: setup() if resp == "ok" else None).present()

    def ask_setup_microg(self) -> None:
        def setup() -> None:
            warnings: list[str] = []

            def done(info) -> None:
                self.selected = info.package
                self.search.set_text("")
                self.alert(_("microG is set up"), "\n".join(warnings + [microg_hint()]))
            progress, end = self.begin("microG", _("Downloading microG…"))
            self.background(lambda: setup_microg(self.paths, self.lang, self.env, progress, warnings.append),
                            done, _("Cannot set up microG"), end=end)
        Dialog(self, _("Set up microG?"),
               _("Google services for apps (push messages, sign-in). Downloaded from the official microG "
                 "repository and checked."),
               [("cancel", _("Cancel")), ("ok", _("Set up"))],
               lambda resp: setup() if resp == "ok" else None).present()

    def _confirm(self, heading: str, body: str, verb: str, then: Callable[[], None],
                 extra: Gtk.Widget | None = None) -> None:
        Dialog(self, heading, body, [("cancel", _("Cancel")), ("ok", verb)],
               lambda resp: then() if resp == "ok" else None, extra=extra, destructive="ok").present()

    def ask_clear_data(self, pkg: str) -> None:
        label_ = self.rows[pkg].label if pkg in self.rows else pkg
        self._confirm(_("Delete the data of {app}?").format(app=label_),
                      _("The app stays installed. Its settings, accounts and files are deleted. "
                        "Close the app first."), _("Delete Data"),
                      lambda: self.run_for(pkg, _("Deleting data…"), lambda: clear_data(self.paths, pkg), None,
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
        self._confirm(_("Uninstall {app}?").format(app=label_),
                      _("The app and its menu entry are removed. Close the app first."), _("Uninstall"),
                      lambda: self.run_for(pkg, _("Uninstalling…"),
                                           lambda: remove_app(self.paths, pkg, purge.get_active()), removed,
                                           _("Cannot uninstall {app}").format(app=label_)),
                      extra=purge)


class ThemeWatcher:
    """The look of the desktop, followed live: the system's GTK theme and configuration (Spec 14, files) and colour
    scheme, accent and font from the settings portal (Spec 13)."""

    def __init__(self, display: Gdk.Display, env: Mapping[str, str] | None = None):
        self.display = display
        self.env = dict(os.environ if env is None else env)
        self.config_dir = theme.host_config_dir(self.env)
        self.css = Gtk.CssProvider()
        Gtk.StyleContext.add_provider_for_display(display, self.css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1)
        self.user_css = Gtk.CssProvider()               # the host's gtk.css, as GTK loads it outside the sandbox
        Gtk.StyleContext.add_provider_for_display(display, self.user_css, Gtk.STYLE_PROVIDER_PRIORITY_USER)
        self.system = theme.SystemTheme()
        self.values: dict[tuple[str, str], object] = {}
        self.proxy = None
        self.monitors = []
        self.reload_source = 0
        try:
            monitor = Gio.File.new_for_path(str(self.config_dir / "gtk-4.0")).monitor_directory(
                Gio.FileMonitorFlags.WATCH_MOVES, None)
            monitor.connect("changed", self.on_file_changed)
            self.monitors.append(monitor)
        except GLib.Error:
            pass
        try:
            self.proxy = Gio.DBusProxy.new_for_bus_sync(
                Gio.BusType.SESSION,
                Gio.DBusProxyFlags.DO_NOT_AUTO_START_AT_CONSTRUCTION | Gio.DBusProxyFlags.DO_NOT_LOAD_PROPERTIES, None,
                "org.freedesktop.portal.Desktop", "/org/freedesktop/portal/desktop",
                "org.freedesktop.portal.Settings", None)
            all_ = self.proxy.call_sync("ReadAll", GLib.Variant("(as)", (list(theme.NAMESPACES),)),
                                        Gio.DBusCallFlags.NONE, 2000, None).unpack()[0]
            self.values = {(ns, k): v for ns, keys in all_.items() for k, v in keys.items()}
            self.proxy.connect("g-signal", self.on_signal)
        except GLib.Error:
            pass                                    # no portal: GTK's own defaults
        self.load_system()
        self.apply()
        # GTK also takes the icon theme from the settings portal (org.gnome.desktop.interface), maybe one that is
        # not in the sandbox (KDE: breeze): keep an installed one
        Gtk.Settings.get_for_display(display).connect("notify::gtk-icon-theme-name", self.on_icon_theme)
        self.on_icon_theme()

    def on_icon_theme(self, *_args) -> None:
        settings = Gtk.Settings.get_for_display(self.display)
        if not theme.icon_theme_available(settings.props.gtk_icon_theme_name, theme.icon_theme_dirs(os.environ)):
            settings.props.gtk_icon_theme_name = "Adwaita"

    def on_signal(self, _proxy, _sender, name: str, params: GLib.Variant) -> None:
        if name == "SettingChanged":
            ns, key, value = params.unpack()
            self.values[(ns, key)] = value
            self.apply()

    def on_file_changed(self, _monitor, _file, _other, event: Gio.FileMonitorEvent) -> None:
        """The desktop writes several files for one change: apply them together."""
        if event == Gio.FileMonitorEvent.ATTRIBUTE_CHANGED:
            return
        if self.reload_source:
            GLib.source_remove(self.reload_source)
        self.reload_source = GLib.timeout_add(200, self.reload)

    def reload(self) -> bool:
        self.reload_source = 0
        self.load_system()
        self.apply()
        return False

    def load_system(self) -> None:
        # where GTK in this process looks for themes (the sandbox's own folders)
        self.system = theme.read_system(self.config_dir, theme.theme_dirs(os.environ))
        settings = Gtk.Settings.get_for_display(self.display)
        for prop, value in (("gtk-theme-name", self.system.theme_name), ("gtk-icon-theme-name", self.system.icon_theme),
                            ("gtk-cursor-theme-name", self.system.cursor_theme),
                            ("gtk-cursor-theme-size", self.system.cursor_size)):
            if value is None or (prop == "gtk-theme-name" and not self.system.found):
                continue
            if prop == "gtk-icon-theme-name" and not theme.icon_theme_available(value, theme.icon_theme_dirs(os.environ)):
                continue                            # not installed for the runtime: Adwaita's icons stay
            settings.set_property(prop, value)
        if self.system.gtk_css:
            self.user_css.load_from_path(str(self.system.gtk_css))
        else:
            self.user_css.load_from_string("")

    def apply(self, t: theme.Theme | None = None) -> None:
        t = t or theme.from_settings(self.values)
        settings = Gtk.Settings.get_for_display(self.display)
        if settings.find_property("gtk-interface-color-scheme") is not None:     # GTK 4.20+
            scheme = {True: Gtk.InterfaceColorScheme.DARK, False: Gtk.InterfaceColorScheme.LIGHT}
            settings.set_property("gtk-interface-color-scheme",
                                  scheme.get(t.dark, Gtk.InterfaceColorScheme.DEFAULT))
        else:
            settings.set_property("gtk-application-prefer-dark-theme", bool(t.dark))
        font = self.system.font or t.font
        if font:
            settings.set_property("gtk-font-name", font)
        self.css.load_from_string(theme.window_css(t, self.system.found))


def install_styles(display: Gdk.Display) -> None:
    css = Gtk.CssProvider()
    css.load_from_string(CSS)
    Gtk.StyleContext.add_provider_for_display(display, css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)


def default_launcher() -> list[str]:
    """How to start an app: run this very program again (`/app/bin/apk-starter` inside the Flatpak)."""
    return [sys.executable, os.path.realpath(sys.argv[0])]


def make_app() -> Gtk.Application:
    """The window's application, one per session. A running app (ATL) owns the Flatpak's bus name for notification
    clicks and lets it be replaced: the window takes it over (REPLACE), the app queues and gets it back. The window
    itself cannot be replaced: a second start goes to it."""
    return Gtk.Application(application_id=ENTRY_PREFIX,     # a test build's window beside the one of stable
                           flags=Gio.ApplicationFlags.REPLACE)


def run(paths: Paths, env: Mapping[str, str], launcher: list[str] | None = None, show: str | None = None) -> int:
    """show: open the details of this app (an app's «App info», Settings.ACTION_APPLICATION_DETAILS_SETTINGS);
    a window that is open already shows them."""
    app = make_app()
    lang = lang_from_env(env)

    def startup(_a: Gtk.Application) -> None:
        display = Gdk.Display.get_default()
        install_styles(display)
        app.theme = ThemeWatcher(display, env)

    def window(a: Gtk.Application) -> "Window":
        win = a.get_active_window() or next((w for w in a.get_windows() if isinstance(w, Window)), None)
        return win or Window(a, paths, lang, launcher or default_launcher(), env)

    def activate(a: Gtk.Application) -> None:
        window(a).present()

    def show_app(_action, value: GLib.Variant) -> None:
        win = window(app)
        win.show_package(value.get_string())
        win.present()
    action = Gio.SimpleAction.new("show", GLib.VariantType.new("s"))
    action.connect("activate", show_app)
    app.add_action(action)
    app.connect("startup", startup)
    app.connect("activate", activate)
    if show:
        app.register(None)
        app.activate_action("show", GLib.Variant("s", show))     # in the window that runs already, if there is one
        if app.get_is_remote():
            return 0
    return app.run([sys.argv[0]])
