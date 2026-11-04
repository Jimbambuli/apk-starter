"""An app store installs an app (Aurora Store through ATL's PackageInstaller, see ATL_INSTALL_COMMAND): ask the
user like Android's PackageInstallerActivity, then install like --install.

The exit status is the PackageInstaller status ATL hands back to the app: 0 installed, 3 aborted by the user,
4 not a valid APK, 5 conflict (signed by another certificate than the installed app), 7 incompatible (no x86_64
code), 1 other failure; the last line on stderr is its message."""
import os
import sys
from collections.abc import Mapping
from pathlib import Path

from .apkinfo import ApkInfo
from .errors import ApkError, SignatureConflict, StarterError, describe_error
from .i18n import _
from .paths import Paths
from . import splits

STATUS_SUCCESS, STATUS_FAILURE, STATUS_ABORTED, STATUS_INVALID, STATUS_CONFLICT, STATUS_INCOMPATIBLE = 0, 1, 3, 4, 5, 7


def _say(text: str) -> None:
    print(f"apk-starter: {text}", file=sys.stderr, flush=True)


def question(info: ApkInfo, installer: str, update: bool) -> tuple[str, str, str, str]:
    """Heading, line, cancel and confirm text of the question; short, like Android's installer."""
    heading = (_("Update {app}?") if update else _("Install {app}?")).format(app=info.label)
    line = _("From {installer} · Version {version}").format(installer=installer, version=info.version_name or "–")
    return heading, line, _("Cancel"), _("Update") if update else _("Install")


def ask(info: ApkInfo, installer: str, update: bool, env: Mapping[str, str]) -> bool:
    """The question in a small window of its own (not the management window: that may be closed, or open in
    another instance). True: install."""
    if not any(env.get(v) for v in ("WAYLAND_DISPLAY", "DISPLAY")):
        raise StarterError(_("no display to ask whether to install {app}").format(app=info.label))
    import gi
    gi.require_version("Gtk", "4.0")
    from gi.repository import Gio, Gtk
    from .gui import Dialog, install_styles
    answer = []
    app = Gtk.Application(application_id="io.github.Jimbambuli.ApkStarter.Install",
                          flags=Gio.ApplicationFlags.NON_UNIQUE)

    def activate(a: Gtk.Application) -> None:
        install_styles(Gtk.Window().get_display())
        owner = Gtk.ApplicationWindow(application=a, title="APK Starter")    # stays hidden: the dialog's parent
        heading, line, cancel, verb = question(info, installer, update)
        d = Dialog(owner, heading, line, [("cancel", cancel), ("install", verb)],
                   then=lambda rid: (answer.append(rid == "install"), a.quit()))
        d.set_application(a)
        d.present()
    app.connect("activate", activate)
    app.run([sys.argv[0]])
    return bool(answer and answer[0])


def run(paths: Paths, env: Mapping[str, str], files: list[Path]) -> int:
    from .cli import check_signer, install_parts, lang_from_env, parts_dir, read_parts
    installer = env.get("ATL_INSTALLER_LABEL") or env.get("ATL_INSTALLER_PACKAGE") or _("An app")
    lang = lang_from_env(env)
    try:
        with parts_dir(paths) as tmp:
            info, parts = read_parts(paths, files, lang, Path(tmp))
            update = paths.meta(info.package).is_file()
            check_signer(paths, info)           # refused before asking, like Android's installer
            if not ask(info, installer, update, env):
                _say(_("Installation of {app} cancelled").format(app=info.label))
                return STATUS_ABORTED
            try:
                install_parts(paths, info, parts.splits, lang, env)
            except StarterError as e:
                if not paths.meta(info.package).is_file():
                    raise
                _say(describe_error(e))         # installed, only the menu entry is missing: the store may open it
        return STATUS_SUCCESS
    except SignatureConflict as e:
        _say(describe_error(e))
        return STATUS_CONFLICT
    except splits.IncompatibleError as e:
        _say(describe_error(e))
        return STATUS_INCOMPATIBLE
    except ApkError as e:
        _say(describe_error(e))
        return STATUS_INVALID
    except (StarterError, OSError) as e:
        _say(describe_error(e))
        return STATUS_FAILURE


def uninstall_question(label: str) -> tuple[str, str, str, str]:
    """Heading, data option, cancel and confirm text; as in the window."""
    return _("Uninstall {app}?").format(app=label), _("Also delete app data"), _("Cancel"), _("Uninstall")


def ask_uninstall(label: str, env: Mapping[str, str]) -> tuple[bool, bool]:
    """(uninstall, also the data), asked in a small window of its own like ask()."""
    if not any(env.get(v) for v in ("WAYLAND_DISPLAY", "DISPLAY")):
        raise StarterError(_("no display to ask whether to uninstall {app}").format(app=label))
    import gi
    gi.require_version("Gtk", "4.0")
    from gi.repository import Gio, Gtk
    from .gui import Dialog, install_styles
    answer: list[tuple[bool, bool]] = []
    app = Gtk.Application(application_id="io.github.Jimbambuli.ApkStarter.Uninstall",
                          flags=Gio.ApplicationFlags.NON_UNIQUE)

    def activate(a: Gtk.Application) -> None:
        install_styles(Gtk.Window().get_display())
        owner = Gtk.ApplicationWindow(application=a, title="APK Starter")
        heading, data, cancel, verb = uninstall_question(label)
        purge = Gtk.CheckButton(label=data)
        d = Dialog(owner, heading, "", [("cancel", cancel), ("ok", verb)], extra=purge, destructive="ok",
                   then=lambda rid: (answer.append((rid == "ok", purge.get_active())), a.quit()))
        d.set_application(a)
        d.present()
    app.connect("activate", activate)
    app.run([sys.argv[0]])
    return answer[0] if answer else (False, False)


def run_uninstall(paths: Paths, env: Mapping[str, str], pkg: str) -> int:
    """An app store uninstalls an app (ACTION_DELETE / PackageInstaller.uninstall() through ATL_UNINSTALL_COMMAND);
    exit status as for installs: 0 uninstalled, 3 aborted by the user, 1 failed."""
    from .cli import list_installed, remove_app
    try:
        meta = next((m for m in list_installed(paths) if m["package"] == pkg), None)
        if meta is None:
            _say(_("not installed: {package}").format(package=pkg))
            return STATUS_FAILURE
        label = str(meta.get("label") or pkg)
        yes, purge = ask_uninstall(label, env)
        if not yes:
            _say(_("Uninstalling {app} cancelled").format(app=label))
            return STATUS_ABORTED
        for warning in remove_app(paths, pkg, purge):
            _say(warning)                       # removed; only the menu entry may be left
        return STATUS_SUCCESS
    except (StarterError, OSError) as e:
        _say(describe_error(e))
        return STATUS_FAILURE
