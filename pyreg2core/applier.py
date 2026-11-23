# -*- coding: utf-8 -*-
"""
Apply pipeline: patch .exe -> registry (PROJECT_CONTEXT.md §6.7).

1. find the overlay (own image, another candidate path, or ``--patch-data``);
   no overlay means a plain template and exit code 0;
2. decode and parse the embedded bytes; invalid data is exit code 5;
3. route by ``RegRoot.requires_admin`` and cross-check the mode byte;
4. apply the user operations in-process;
5. apply the admin operations in-process when elevated, otherwise restart
   elevated with ``--hklm-only`` and relay the child's exit code;
6. return 0 when everything was applied, 2 when anything failed.
"""

import dataclasses
import os
import tempfile

from . import diag, overlay, parser, selfpath
from .elevate import is_admin, run_elevated_and_wait
from .errors import EmbeddedRegError, InputError, OverlayError
from .expand import VariableExpander
from .reg_types import ApplyReport, compute_mode
from .registry import RegistryApplier

HKLM_ONLY_FLAG = "--hklm-only"
PATCH_DATA_FLAG = "--patch-data"
PATCH_DIR_FLAG = "--patch-dir"

_RELAYED_CODES = (2, 3, 5, 6, 7)


@dataclasses.dataclass(slots=True)
class PatchSource:
    """Where the embedded .reg payload came from."""

    overlay: object
    path: str = ""              # executable carrying the overlay, if any

    @property
    def base_dir(self):
        return os.path.dirname(self.path) if self.path else ""


def find_patch(patch_data=None):
    """
    Locate the overlay.

    Returns a ``PatchSource`` or None (plain template).  A damaged overlay
    raises ``OverlayError`` instead of being silently ignored.
    """
    if patch_data:
        recovered = overlay.read_file(patch_data)
        if recovered is None:
            raise OverlayError(
                "{0} does not contain a PYREGEND overlay".format(patch_data))
        return PatchSource(overlay=recovered, path="")

    for candidate in selfpath.existing_candidates():
        recovered = overlay.read_file(candidate)
        if recovered is not None:
            return PatchSource(overlay=recovered, path=candidate)
    return None


def apply_patch(hklm_only=False, patch_data=None, patch_dir=None,
                view="default", expand_env=True, lenient=False,
                dry_run=False, log=None):
    """Apply the embedded patch; returns the process exit code."""
    log = log or diag.Log()
    log.debug("candidate paths: {0}".format(
        " | ".join(selfpath.candidate_paths()) or "(none)"))

    source = find_patch(patch_data)
    if source is None or source.overlay is None:
        log.info("no patch overlay found: this is a plain template, "
                 "nothing to do")
        return 0

    log.info("overlay: {0} byte(s), mode 0x{1:02x} ({2}) from {3}".format(
        source.overlay.size, int(source.overlay.mode), source.overlay.mode,
        source.path or patch_data))

    try:
        result = parser.parse_reg_bytes(source.overlay.data, lenient=lenient)
    except InputError as exc:
        raise EmbeddedRegError(
            "the embedded .reg file cannot be decoded: {0}".format(exc))

    log.diagnostics(result.diagnostics)
    if result.has_errors:
        raise EmbeddedRegError(
            "the embedded .reg file is invalid ({0} error(s), first: {1})"
            .format(len(result.errors), result.errors[0]))

    if not result.operations:
        log.info("the patch contains no operations")
        return 0

    actual_mode = int(compute_mode(result.operations))
    if actual_mode != int(source.overlay.mode):
        log.warning(
            "mode byte 0x{0:02x} does not match the embedded operations "
            "(0x{1:02x}); routing by operations".format(
                int(source.overlay.mode), actual_mode))

    base_dir = patch_dir or source.base_dir or selfpath.own_directory()
    base_dir = os.path.abspath(base_dir)
    log.info("patch directory: {0}".format(base_dir))

    user_ops = [op for op in result.operations if not op.root.requires_admin]
    admin_ops = [op for op in result.operations if op.root.requires_admin]

    if dry_run:
        for operation in user_ops:
            log.info("would apply (user): {0}".format(operation.describe()))
        for operation in admin_ops:
            log.info("would apply (admin): {0}".format(operation.describe()))
        return 0

    expander = VariableExpander(base_dir=base_dir, expand_env=expand_env)
    applier = RegistryApplier(expander=expander, view=view)
    report = ApplyReport()

    if hklm_only:
        # Second pass: only the machine-wide operations, only by the elevated
        # copy, so that HKCU writes never land in the administrator's profile.
        log.info("elevated pass: applying {0} machine-wide operation(s)"
                 .format(len(admin_ops)))
        if admin_ops:
            report.merge(applier.apply(admin_ops))
        _log_report(log, report)
        return 2 if report.failed else 0

    if user_ops:
        log.info("applying {0} user operation(s)".format(len(user_ops)))
        report.merge(applier.apply(user_ops))
    else:
        log.info("no user operations")

    if not admin_ops:
        log.info("no machine-wide operations")
        _log_report(log, report)
        return 2 if report.failed else 0

    if is_admin():
        log.info("applying {0} machine-wide operation(s) in-process".format(
            len(admin_ops)))
        report.merge(applier.apply(admin_ops))
        _log_report(log, report)
        return 2 if report.failed else 0

    child_code = _elevate(source, base_dir, view, expand_env, lenient, log)
    _log_report(log, report)
    if child_code:
        log.warning("the elevated copy exited with code {0}".format(
            child_code))
        return child_code if child_code in _RELAYED_CODES else 2
    return 2 if report.failed else 0


def _log_report(log, report):
    log.info("result: {0}".format(report.summary()))
    log.diagnostics(report.diagnostics)


def _elevate(source, base_dir, view, expand_env, lenient, log):
    """Relay the machine-wide operations to an elevated copy of ourselves."""
    image = selfpath.running_image_path() or source.path
    arguments = [HKLM_ONLY_FLAG, "{0}={1}".format(PATCH_DIR_FLAG, base_dir)]

    temporary = None
    if not (image and overlay.has_overlay(image)):
        diag.ensure_log_directory()
        directory = diag.log_directory() or tempfile.gettempdir()
        temporary = os.path.join(directory,
                                 "patch-{0}.regpatch".format(os.getpid()))
        source.overlay.write_to(temporary)
        arguments.append("{0}={1}".format(PATCH_DATA_FLAG, temporary))

    if view and view != "default":
        arguments.append("--view={0}".format(view))
    if not expand_env:
        arguments.append("--no-expand-env")
    if lenient:
        arguments.append("--lenient")

    log.info("requesting elevation: {0} {1}".format(
        image, " ".join(arguments)))
    try:
        return run_elevated_and_wait(arguments, exe_path=image)
    finally:
        if temporary:
            try:
                os.remove(temporary)
            except OSError:
                pass

