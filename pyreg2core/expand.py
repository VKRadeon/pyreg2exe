# -*- coding: utf-8 -*-
"""
Variable expansion (PROJECT_CONTEXT.md §7.9).

Expansion is a pyreg2exe feature, not part of the .reg format:

* it applies to the **data** of string values only — never to value names and
  never to binary payloads (B14);
* ``%CD%`` and ``<reg2exepath>`` resolve to the directory of the patch
  executable, ignoring any ``%CD%`` in the process environment (B15);
* ``REG_EXPAND_SZ`` keeps environment variables literal so that Windows expands
  them per user at read time;
* unresolved tokens are kept verbatim.
"""

import os
import re

from .reg_types import RegType

# Reg2exe-compatible markers and the environment variable behind each one.
MARKERS = {
    "<reg2exepath>": None,             # the patch directory, special-cased
    "<reg2exewinpath>": "WINDIR",
    "<reg2exesyspath>": "SYSTEMROOT",
    "<reg2exetemppath>": "TEMP",
    "<reg2exeprogspath>": "PROGRAMFILES",
    "<reg2exeappdatapath>": "APPDATA",
    "<reg2exelocalappdatapath>": "LOCALAPPDATA",
    "<reg2exeusername>": "USERNAME",
    "<reg2execomputername>": "COMPUTERNAME",
    "<reg2exeprofilespath>": "USERPROFILE",
}

_PERCENT_RE = re.compile(r"%([A-Za-z_][A-Za-z0-9_]*)%")
_MARKER_RE = re.compile(r"<reg2exe[a-z]*>", re.IGNORECASE)
_PATCH_PATH_MARKER = "<reg2exepath>"


class VariableExpander:
    """Expands %VAR% and <reg2exe...> markers in value data."""

    def __init__(self, base_dir=None, environ=None, expand_env=True,
                 extra=None):
        self._environ = dict(os.environ if environ is None else environ)
        self._extra = {}
        for key, value in (extra or {}).items():
            self._extra[str(key).lower()] = value
        self.base_dir = base_dir if base_dir is not None else \
            self._extra.get("%cd%")
        self.expand_env = bool(expand_env)

    def expand(self, text):
        """Expand markers, ``%CD%`` and (optionally) environment variables."""
        return self._expand(text, self.expand_env)

    def expand_value(self, value, vtype):
        """Expand a value according to its registry type."""
        if vtype is None:
            return value
        if vtype is RegType.MULTI_SZ:
            if not isinstance(value, list):
                return value
            return [self._expand(item, self.expand_env)
                    if isinstance(item, str) else item for item in value]
        if vtype is RegType.EXPAND_SZ:
            return self._expand(value, False) if isinstance(value, str) else value
        if vtype is RegType.SZ:
            return self._expand(value, self.expand_env) \
                if isinstance(value, str) else value
        return value

    def _expand(self, text, use_env):
        if not text or not isinstance(text, str):
            return text
        if "%" not in text and "<" not in text.lower():
            return text
        expanded = _PERCENT_RE.sub(
            lambda match: self._percent(match, use_env), text)
        return _MARKER_RE.sub(
            lambda match: self._marker(match, use_env), expanded)

    def _percent(self, match, use_env):
        token = match.group(1)
        upper = token.upper()
        if upper == "CD" and self.base_dir:
            return self.base_dir
        key = "%" + token.lower() + "%"
        if key in self._extra:
            return self._extra[key]
        if use_env and upper in self._environ:
            return self._environ[upper]
        return match.group(0)

    def _marker(self, match, use_env):
        marker = match.group(0).lower()
        if marker == _PATCH_PATH_MARKER and self.base_dir:
            return self.base_dir
        if marker in self._extra:
            return self._extra[marker]
        env_name = MARKERS.get(marker)
        if env_name and use_env and env_name in self._environ:
            return self._environ[env_name]
        return match.group(0)


_MARKER_RE = re.compile(r"<reg2exe[a-z]*>", re.IGNORECASE)


def make_expander(base_dir=None, expand_env=True, environ=None, extra=None):
    """Convenience constructor used by the applier and the shim."""
    return VariableExpander(base_dir=base_dir, environ=environ,
                            expand_env=expand_env, extra=extra)
