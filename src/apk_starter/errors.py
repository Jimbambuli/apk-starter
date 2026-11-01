"""Error classes; the exit code of the command line is the class attribute `code`."""


class StarterError(Exception):
    code = 1


class UsageError(StarterError):
    code = 2


class ApkError(StarterError):
    code = 3
