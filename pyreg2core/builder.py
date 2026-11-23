# -*- coding: utf-8 -*-
"""
Build pipeline: .reg -> patch .exe (PROJECT_CONTEXT.md §6.6).

1. read the input once, hash it, decode, parse;
2. strict mode: any error diagnostic stops the build;
3. compute the mode byte from ``RegRoot.requires_admin``;
4. read the template once, validate it, reject a template that is already
   patched and an output path that would overwrite the input or the template;
5. compose in memory and write atomically (temp file + ``os.replace``);
6. re-open the result and verify marker, mode, size and payload;
7. ``--dry-run`` stops after step 3.
"""

import dataclasses
import hashlib
import os

from . import encoding, overlay, parser, selfpath
from .errors import BuildError, InputError
from .reg_types import Mode

TEMPLATE_NAME = "regapply.exe"
TEMP_SUFFIX = ".tmp-{0}".format(os.getpid())


@dataclasses.dataclass(slots=True)
class BuildPlan:
    """What the builder did (or would do, with ``--dry-run``)."""

    reg_path: str
    output_path: str
    template_path: str
    mode: Mode
    reg_size: int
    operation_count: int
    diagnostics: list
    feedback: list
    dry_run: bool = False
    output_size: int = 0
    digest: str = ""

    def summary(self):
        return ("{0} operation(s), mode 0x{1:02x} ({2}), payload {3} bytes"
                .format(self.operation_count, int(self.mode), self.mode,
                        self.reg_size))


def template_candidates(explicit=None):
    """Places where regapply.exe may live, most specific first."""
    candidates = []
    if explicit:
        candidates.append(os.path.abspath(explicit))
    package_dir = os.path.dirname(os.path.abspath(__file__))
    candidates.append(os.path.join(os.path.dirname(package_dir),
                                   TEMPLATE_NAME))
    compiled = selfpath.compiled_argv0()
    if compiled:
        candidates.append(os.path.join(os.path.dirname(compiled),
                                       TEMPLATE_NAME))
    candidates.append(os.path.join(os.getcwd(), TEMPLATE_NAME))
    candidates.append(os.path.join(selfpath.own_directory(), TEMPLATE_NAME))
    onefile_dir = os.environ.get("NUITKA_ONEFILE_DIRECTORY")
    if onefile_dir:
        candidates.append(os.path.join(onefile_dir, TEMPLATE_NAME))
    return candidates


def find_template(explicit=None):
    """Return the template path or raise ``InputError``."""
    if explicit:
        path = os.path.abspath(explicit)
        if os.path.isfile(path):
            return path
        raise InputError("template not found: {0}".format(path))
    for candidate in template_candidates():
        if os.path.isfile(candidate):
            return os.path.abspath(candidate)
    raise InputError(
        "template {0} not found (looked next to pyreg2exe.py, in the "
        "current directory and next to the running image); build it with "
        "Nuitka or pass --template".format(TEMPLATE_NAME))


def default_output_path(reg_path):
    return os.path.splitext(os.path.abspath(reg_path))[0] + ".exe"


def _same_path(first, second):
    return os.path.normcase(os.path.abspath(first)) == \
        os.path.normcase(os.path.abspath(second))


def _summarize_errors(result, limit=5):
    errors = result.errors
    lines = [str(item) for item in errors[:limit]]
    if len(errors) > limit:
        lines.append("... {0} more error(s)".format(len(errors) - limit))
    return "; ".join(lines)


def build_patch(reg_path, output_path=None, template_path=None, force=False,
                dry_run=False, lenient=False, encoding_name=None, log=None):
    """Build a patch executable; returns a ``BuildPlan``."""
    from .diag import Log

    log = log or Log()

    reg_path = os.path.abspath(reg_path)
    if not os.path.isfile(reg_path):
        raise InputError("file not found: {0}".format(reg_path))
    if not reg_path.lower().endswith(".reg"):
        raise InputError("not a .reg file: {0}".format(reg_path))

    raw = encoding.read_file_bytes(reg_path)
    text = encoding.decode_bytes(raw, encoding=encoding_name)
    log.debug("read {0} bytes from {1}".format(len(raw), reg_path))
    result = parser.parse_reg_text(text, lenient=lenient)
    log.debug("parsed {0} operation(s), {1} diagnostic(s)".format(
        len(result.operations), len(result.diagnostics)))

    if result.has_errors:
        first = result.errors[0]
        raise InputError("invalid .reg file: {0}".format(
            _summarize_errors(result)), line=first.line)
    if not result.operations:
        raise InputError("the .reg file contains no operations")
    mode = Mode(result.mode)

    template = find_template(template_path)
    template_data = encoding.read_file_bytes(template)
    overlay.validate_pe_image(template_data, template)
    if overlay.has_overlay_data(template_data):
        raise InputError(
            "template {0} already contains a PYREGEND overlay; build the "
            "template again from an unpatched regapply.py".format(template))
    log.debug("template {0}: {1} bytes".format(template,
                                               len(template_data)))

    output = (os.path.abspath(output_path) if output_path
              else default_output_path(reg_path))
    if _same_path(output, reg_path):
        raise BuildError(
            "the output path equals the input .reg file; pass a different "
            "-o/--output")
    if _same_path(output, template):
        raise BuildError(
            "the output path equals the template; pass a different "
            "-o/--output")
    if os.path.isfile(output) and not force:
        raise BuildError(
            "output already exists: {0} (use --force to overwrite)"
            .format(output))

    plan = BuildPlan(
        reg_path=reg_path,
        output_path=output,
        template_path=template,
        mode=mode,
        reg_size=len(raw),
        operation_count=len(result.operations),
        diagnostics=list(result.diagnostics),
        feedback=list(result.warnings),
        dry_run=bool(dry_run),
        digest=hashlib.sha256(raw).hexdigest(),
    )
    if dry_run:
        return plan

    blob = template_data + overlay.encode_blob(raw, mode)
    _write_atomic(output, blob)
    _verify(output, template_data, raw, mode)
    plan.output_size = len(blob)
    return plan


def _write_atomic(path, blob):
    """Write ``blob`` to ``path`` through a temporary file in the same dir."""
    temporary = path + TEMP_SUFFIX
    try:
        with open(temporary, "wb") as handle:
            handle.write(blob)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except OSError as exc:
        try:
            os.remove(temporary)
        except OSError:
            pass
        raise BuildError("cannot write {0}: {1}".format(path, exc))


def _verify(path, template_data, reg_data, mode):
    """Re-read the output and verify the whole overlay record."""
    from .errors import OverlayError

    try:
        with open(path, "rb") as handle:
            blob = handle.read()
    except OSError as exc:
        raise BuildError("cannot read back {0}: {1}".format(path, exc))

    if blob[:len(template_data)] != template_data:
        raise BuildError(
            "verification failed: the template image was not copied "
            "verbatim into {0}".format(path))
    try:
        recovered = overlay.parse_tail(blob)
    except OverlayError as exc:
        raise BuildError("verification failed: {0}".format(exc))
    if recovered is None:
        raise BuildError(
            "verification failed: {0} has no overlay marker".format(path))
    if recovered.mode != mode:
        raise BuildError(
            "verification failed: mode byte 0x{0:02x} instead of 0x{1:02x}"
            .format(int(recovered.mode), int(mode)))
    if recovered.data != reg_data:
        raise BuildError(
            "verification failed: the embedded .reg data does not match the "
            "input")

