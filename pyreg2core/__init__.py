# -*- coding: utf-8 -*-
"""
pyreg2core — implementation package for pyreg2exe and regapply.

Copyright (C) 2026 Deepseek + VKRadeon

This program is free software; you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation; either version 2 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

Layering (imports point downwards only):

    cli -> builder, applier, errors, diag
    builder -> encoding, parser, expand, overlay, errors, diag
    applier -> overlay, parser, registry, elevate, selfpath, expand, errors, diag
    registry -> reg_types, expand, errors      (winreg)
    elevate -> selfpath, errors                (ctypes)
    selfpath -> errors                         (ctypes)
    parser -> reg_types, encoding, errors
    overlay -> reg_types, errors
"""

from .errors import (
    BuildError,
    ElevationError,
    EmbeddedRegError,
    InputError,
    OverlayError,
    PyregError,
    RegistryError,
    UsageError,
)
from .reg_types import (
    ApplyReport,
    Diagnostic,
    Mode,
    OpKind,
    Operation,
    ParseResult,
    RegRoot,
    RegType,
    Severity,
    compute_mode,
)

__version__ = "1.1.0"

__all__ = [
    "__version__",
    "ApplyReport",
    "BuildError",
    "Diagnostic",
    "ElevationError",
    "EmbeddedRegError",
    "InputError",
    "Mode",
    "OpKind",
    "Operation",
    "OverlayError",
    "ParseResult",
    "PyregError",
    "RegRoot",
    "RegType",
    "RegistryError",
    "Severity",
    "UsageError",
    "compute_mode",
]
