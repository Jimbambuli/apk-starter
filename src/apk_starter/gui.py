"""Management window (plain GTK 4, no libadwaita: the compositor draws its own title bar, e.g. Breeze under KWin;
GTK draws a simple one where the compositor wants none). List of apps on the left, details on the right.

All work is done by the same functions as the command line (cli.py); this module only shows and asks."""
import os
import sys
import threading
from collections.abc import Callable, Mapping
from pathlib import Path

from . import dexopt, diag, microg, theme
from .cli import (APP_ID, ENTRY_PREFIX, clear_data, install_apk, lang_from_env, microg_hint, prepare_all, remove_app,
                  setup_microg)
from .errors import StarterError, describe_error
from .i18n import _
from .gui_model import (AppRow, WindowState, crash_meaning, details_layout, filter_rows, is_narrow, key_action,
                        load_rows, load_state, save_state, sort_rows, start_argv)
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
SIDE_WIDTH = 300
DETAILS_MARGIN = 28
TEXT_CHARS = 64                 # readable line length of the details' texts

# Only spacing and type sizes: the theme draws buttons, frames and every other shape (Marcel 2.11.2026).
CSS = """
.app-name { font-size: 1.7em; font-weight: 700; }
.welcome-title { font-size: 2em; font-weight: 700; }
.section-title { font-weight: 700; }
.row-name { font-weight: 600; }
.caption { font-size: 0.85em; }
.drop-zone { margin: 12px; background-color: @theme_bg_color; }
.progress-bar { padding: 6px 12px; }
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
        self.generation = 0                         # newest list load; older results are dropped
        self.dialog: Dialog | None = None
        self.narrow = False
        self.page = "list"                          # narrow window: "list" or "details"
        self.layout = 0                             # extra right margin of the details
        self.debug = False                          # «Debug» ticked; never remembered (Spec 14)
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
        install = Gtk.Button(icon_name="list-add-symbolic", tooltip_text=_("Install APK…"))
        install.connect("clicked", lambda _b: self.choose_apk())
        # test build with microG: once, until «microG settings» is installed
        self.microg_button = Gtk.Button(label="microG", tooltip_text=_("Set up microG"),
                                        visible=bool(microg.bundled(paths)) and not paths.apk(microg.GMS).is_file())
        self.microg_button.connect("clicked", lambda _b: self.setup_microg())
        self.spinner = Gtk.Spinner(visible=False, spinning=True, tooltip_text=_("Working…"))
        top = Gtk.Box(spacing=6, margin_top=10, margin_bottom=8, margin_start=10, margin_end=10)
        for w in (self.search, sort, install, self.microg_button, self.spinner):
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
        self.stack.add_named(self.make_welcome(), "empty")
        self.stack.add_named(apps, "list")

        self.progress_label = label("", hexpand=True, ellipsize=Pango.EllipsizeMode.END)
        bar = Gtk.Box(spacing=10)
        bar.add_css_class("progress-bar")
        bar.append(Gtk.Spinner(spinning=True))
        bar.append(self.progress_label)
        banner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        banner.append(bar)
        banner.append(Gtk.Separator())
        self.banner = Gtk.Revealer(child=banner, reveal_child=False)
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        body.append(self.banner)
        body.append(self.stack)

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
        self.set_child(overlay)

        drop = Gtk.DropTarget.new(Gdk.FileList, Gdk.DragAction.COPY)
        drop.connect("drop", self.on_drop)
        drop.connect("enter", lambda *_a: self.drop_zone.set_reveal_child(True) or Gdk.DragAction.COPY)
        drop.connect("leave", lambda *_a: self.drop_zone.set_reveal_child(False))
        self.add_controller(drop)
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self.on_key)
        self.add_controller(keys)

        for name, handler in (("start", lambda p: self.start(p, "debug" if self.debug else None)),
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
        choose.connect("clicked", lambda _b: self.choose_apk())
        # test build with microG: the same as the microG button above the list, which this page does not show
        self.microg_welcome = Gtk.Button(label=_("Set up microG"), halign=Gtk.Align.CENTER,
                                         visible=self.microg_button.get_visible())
        self.microg_welcome.connect("clicked", lambda _b: self.setup_microg())
        for w in (self.welcome_title, self.welcome_text, choose, self.microg_welcome):
            box.append(w)
        return box

    # -- helpers -----------------------------------------------------------------------------------------
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

    def save_app_setting(self, pkg: str, **changes) -> bool:
        """Per app, for every start of it (also from its menu entry)."""
        try:
            save_settings(self.paths.settings(), load_settings(self.paths.settings()).with_app(pkg, **changes))
        except OSError as e:
            self.alert(_("Could not save the setting"), describe_error(e))
            return False
        return True

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
        while (child := self.details.get_first_child()) is not None:
            self.details.remove(child)
        r = self.rows.get(self.selected or "")
        if r is None:
            self.debug = False
            return
        if getattr(self, "debug_for", None) != r.package:
            self.debug = False                      # «Debug» is never remembered, also not across apps
        self.debug_for = r.package
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

        # one row: Start, Delete data, Uninstall, ⚙ (window bar, Log, Debug, recordings) (Marcel 2.11.2026)
        start = Gtk.Button(label=_("Start"), action_name="win.start", action_target=target)
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
                              visible=app.log or self.debug, tooltip_text=_("Log or Debug is on"))
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
                 (_("Status"), _("Prepared for this computer") if r.prepared
                  else _("Not prepared yet (done at the next start)")))
        for i, (key, value) in enumerate(lines):
            facts.attach(label(key, "dim-label"), 0, i, 1, 1)
            facts.attach(label(value, wrap=True, selectable=True, hexpand=True, focusable=False), 1, i, 1, 1)
        self.details.append(Gtk.Frame(child=facts))
        if refocus and (not self.narrow or self.page == "details"):
            start.grab_focus()                      # not the name (a focused label shows all of it selected)

    def make_options(self, r: AppRow, app) -> Gtk.Popover:
        """The ⚙ menu: window bar and Log (remembered per app), Debug (next start only), the recordings."""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin_top=8, margin_bottom=8, margin_start=8,
                      margin_end=8)
        bar = Gtk.Box(spacing=8)
        bar.append(label(_("Window bar:"), valign=Gtk.Align.CENTER))
        names_ = {"system": _("From the system"), "atl": _("With back button"),
                  "system-navigation": _("From the system + navigation bar")}
        order = ("system", "atl", "system-navigation")
        assert set(order) == set(TITLEBARS)
        choice = Gtk.DropDown.new_from_strings([names_[k] for k in order])
        choice.set_selected(order.index(app.titlebar))
        choice.connect("notify::selected",
                       lambda d, _p, p=r.package: self.save_app_setting(p, titlebar=order[d.get_selected()]))
        bar.append(choice)
        log = Gtk.CheckButton(label=_("Log"), active=app.log or self.debug, sensitive=not self.debug)
        debug = Gtk.CheckButton(label=_("Debug (next start only)"), active=self.debug)
        hint = label(_("Debug runs a little slower."), "dim-label", "caption", wrap=True, max_width_chars=40,
                     visible=self.debug, margin_start=26)

        def log_toggled(b: Gtk.CheckButton, pkg=r.package) -> None:
            if not self.debug and b.get_active() != load_settings(self.paths.settings()).app(pkg).log:
                self.save_app_setting(pkg, log=b.get_active())
            self.gear_dot.set_visible(b.get_active() or self.debug)

        def debug_toggled(b: Gtk.CheckButton, pkg=r.package) -> None:
            """Debug includes the log: ticks and locks «Log» while it is ticked."""
            self.debug = b.get_active()
            hint.set_visible(self.debug)
            log.set_sensitive(not self.debug)
            log.set_active(self.debug or load_settings(self.paths.settings()).app(pkg).log)
            self.gear_dot.set_visible(log.get_active())
        log.connect("toggled", log_toggled)
        debug.connect("toggled", debug_toggled)
        self.debug_check, self.log_check, self.titlebar_choice = debug, log, choice
        for w in (bar, log, debug, hint):
            box.append(w)

        recs = diag.recordings(self.paths, r.package)
        self.recordings_shown = len(recs[:RECORDINGS_SHOWN])
        if recs:
            box.append(Gtk.Separator(margin_top=4))
            box.append(label(_("Recordings"), "section-title"))
            kinds = {"log": _("Log"), "debug": _("Debug")}
            for rec in recs[:RECORDINGS_SHOWN]:
                box.append(label(f"{rec.started:%Y-%m-%d %H:%M} · {kinds[rec.kind]}"))
            box.append(Gtk.Button(label=_("Open log folder"), action_name="win.open-logs",
                                  action_target=GLib.Variant("s", r.package), halign=Gtk.Align.START))
        return Gtk.Popover(child=box)

    # -- actions -------------------------------------------------------------------------------------------
    def start(self, pkg: str, level: str | None) -> None:
        """level None: as from the menu entry (with a log if «Log» is ticked for the app), or "debug"."""
        label_ = self.rows[pkg].label if pkg in self.rows else pkg
        try:
            proc = Gio.Subprocess.new(start_argv(self.launcher, pkg, level), Gio.SubprocessFlags.NONE)
        except GLib.Error as e:
            self.alert(_("Cannot start {app}").format(app=label_), e.message)
            return
        before = {rec.path for rec in diag.recordings(self.paths, pkg)}

        def ended(p: Gio.Subprocess, res) -> None:
            p.wait_finish(res)
            exited = p.get_if_exited()
            meaning = crash_meaning(exited, p.get_exit_status() if exited else p.get_term_sig())
            if meaning is not None:
                new = [rec for rec in diag.recordings(self.paths, pkg) if rec.path not in before]
                self.crashed(label_, meaning, bool(new))
            if pkg in self.rows:
                self.refresh()                      # «last started», recordings
        proc.wait_async(None, ended)
        if level == "debug" and self.debug:
            self.debug = False                      # never remembered: off after the start
            if self.selected == pkg and getattr(self, "debug_check", None) is not None:
                self.debug_check.set_active(False)

    def crashed(self, label_: str, meaning: str, recorded: bool) -> None:
        """A real crash, not a normal end (exit 0, SIGTERM): what it means; atl-doctor reads the recordings."""
        if not recorded:
            meaning += "\n" + _("Tick “Log” under ⚙ and start the app again to record the error.")
        Dialog(self, _("{app} ended unexpectedly.").format(app=label_), "",
               [("close", _("Close")), ("logs", _("Open log folder"))],
               lambda resp: self.open_logs() if resp == "logs" else None,
               extra=label(meaning, "dim-label", "caption", wrap=True, max_width_chars=56)).present()

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
        self.progress(_("Installing {file}…").format(file=apk.name))
        self.background(lambda: install_apk(self.paths, apk, self.lang, self.env, self.progress, warnings.append),
                        done, _("Cannot install {file}").format(file=apk.name))

    def setup_microg(self) -> None:
        warnings: list[str] = []

        def done(info) -> None:
            self.microg_button.set_visible(False)
            self.microg_welcome.set_visible(False)
            self.selected = info.package
            self.alert(_("microG is set up"), "\n".join(warnings + [microg_hint()]))
        self.progress(_("Setting up microG…"))
        self.background(lambda: setup_microg(self.paths, self.lang, self.env, self.progress, warnings.append),
                        done, _("Cannot set up microG"))

    def _confirm(self, heading: str, body: str, verb: str, then: Callable[[], None],
                 extra: Gtk.Widget | None = None) -> None:
        Dialog(self, heading, body, [("cancel", _("Cancel")), ("ok", verb)],
               lambda resp: then() if resp == "ok" else None, extra=extra, destructive="ok").present()

    def ask_clear_data(self, pkg: str) -> None:
        label_ = self.rows[pkg].label if pkg in self.rows else pkg
        self._confirm(_("Delete the data of {app}?").format(app=label_),
                      _("The app stays installed. Its settings, accounts and files are deleted. "
                        "Close the app first."), _("Delete Data"),
                      lambda: self.background(lambda: clear_data(self.paths, pkg), None,
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
                      lambda: self.background(lambda: remove_app(self.paths, pkg, purge.get_active()), removed,
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


def run(paths: Paths, env: Mapping[str, str], launcher: list[str] | None = None) -> int:
    app = Gtk.Application(application_id=ENTRY_PREFIX)     # a test build's window beside the one of stable
    lang = lang_from_env(env)

    def startup(_a: Gtk.Application) -> None:
        display = Gdk.Display.get_default()
        install_styles(display)
        app.theme = ThemeWatcher(display, env)

    def activate(a: Gtk.Application) -> None:
        win = a.get_active_window() or Window(a, paths, lang, launcher or default_launcher(), env)
        win.present()
    app.connect("startup", startup)
    app.connect("activate", activate)
    return app.run([sys.argv[0]])
