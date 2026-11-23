# -*- coding: utf-8 -*-
"""
Exception hierarchy and the exit-code table.

Every numeric exit code of both binaries lives here and nowhere else
(PROJECT_CONTEXT.md §6.5).  Entry points are thin wrappers that translate a
``PyregError`` into ``SystemExit(exc.exit_code)``.

    builder (pyreg2exe.exe)        loader (patch.exe)
    0  patch built                 0  applied / no overlay
    2  bad command line            2  one or more registry operations failed
    3  input or template problem   3  overlay corrupt or unsupported
    4  output problem              5  embedded .reg invalid
    7  internal error              6  elevation declined or failed
                                   7  internal error
"""


class PyregError(Exception):
    """Base class of every expected failure; maps to an exit code."""

    exit_code = 7

    def __init__(self, message, line=None, details=None):
        super().__init__(message)
        self.message = message
        self.line = line
        self.details = details

    def __str__(self):
        if self.line:
            return "line {0}: {1}".format(self.line, self.message)
        return self.message

    @property
    def report(self):
        """Single-line text suitable for a log file or a console message."""
        return "{0} (exit {1})".format(self, self.exit_code)


class UsageError(PyregError):
    """Bad command line.  argparse already exits with 2 on its own."""

    exit_code = 2


class InputError(PyregError):
    """Unreadable or invalid .reg file, unusable template."""

    exit_code = 3


class BuildError(PyregError):
    """The output file cannot be produced or verified."""

    exit_code = 4


class OverlayError(PyregError):
    """Corrupt, unsupported or inconsistent patch overlay."""

    exit_code = 3


class EmbeddedRegError(PyregError):
    """The .reg data embedded in a patch does not parse."""

    exit_code = 5


class RegistryError(PyregError):
    """A registry operation failed (used for internal, non-fatal reporting)."""

    exit_code = 2


class ElevationError(PyregError):
    """The UAC prompt was declined or the elevated child could not start."""

    exit_code = 6
