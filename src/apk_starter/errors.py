"""Error classes; the exit code of the command line is the class attribute `code`."""
from .i18n import _


class StarterError(Exception):
    code = 1


class UsageError(StarterError):
    code = 2


class ApkError(StarterError):
    code = 3


def describe_error(e: BaseException) -> str:
    """One line for the user (command line and window), never a traceback."""
    if isinstance(e, StarterError):
        return str(e)
    if isinstance(e, OSError):
        return f"{e.strerror or e}" + (f": {e.filename}" if e.filename else "")
    return _("unexpected error: {error}").format(error=repr(e))
