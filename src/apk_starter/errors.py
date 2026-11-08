"""Error classes; the exit code of the command line is the class attribute `code`."""
from .i18n import _


class StarterError(Exception):
    code = 1


class UsageError(StarterError):
    code = 2


class ApkError(StarterError):
    code = 3


class SignatureConflict(StarterError):
    """An update signed by another certificate than the installed app (INSTALL_FAILED_UPDATE_INCOMPATIBLE)."""


class NoMenuEntry(StarterError):
    """The app is installed, only its menu entry could not be made (no DynamicLauncher portal, or the dialog refused)."""


def describe_error(e: BaseException) -> str:
    """One line for the user (command line and window), never a traceback."""
    if isinstance(e, StarterError):
        return str(e)
    if isinstance(e, OSError):
        return f"{e.strerror or e}" + (f": {e.filename}" if e.filename else "")
    return _("unexpected error: {error}").format(error=repr(e))
