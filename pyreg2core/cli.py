# -*- coding: utf-8 -*-
"""
Command-line front ends (PROJECT_CONTEXT.md §6.5, §6.9).

Both entry points are thin: parse arguments, call the pipeline, translate
``PyregError`` into the documented exit code, write the log.  No entry point
owns a numeric literal.

    pyreg2exe.exe   0 built, 2 usage, 3 input/template, 4 output, 7 internal
    patch.exe       0 applied, 2 operations failed, 3 overlay, 5 embedded .reg,
                    6 elevation, 7 internal
"""

import argparse
import traceback

from . import __version__, builder, diag
from .applier import apply_patch
from .errors import PyregError

PROGRAM_BUILDER = "pyreg2exe"
PROGRAM_APPLIER = "regapply"


def _version_text(program):
    return "{0} {1}".format(program, __version__)


def builder_parser():
    """Argument parser of the build tool."""
    parser = argparse.ArgumentParser(
        prog=PROGRAM_BUILDER,
        description="Build a standalone .exe patch from a .reg file "
                    "(modern analogue of Reg2exe).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Exit codes: 0 ok, 2 usage, 3 input/template problem, "
               "4 output problem, 7 internal error.")
    parser.add_argument("regfile", help="path to the .reg file")
    parser.add_argument("-o", "--output",
                        help="path of the output .exe (default: next to "
                             "the .reg file)")
    parser.add_argument("-t", "--template",
                        help="path to the template regapply.exe "
                             "(default: next to pyreg2exe.py)")
    parser.add_argument("--force", action="store_true",
                        help="overwrite an existing output file")
    parser.add_argument("--dry-run", action="store_true",
                        help="parse, validate and report, but write nothing")
    parser.add_argument("--lenient", action="store_true",
                        help="downgrade parse errors to warnings "
                             "(legacy behaviour)")
    parser.add_argument("--encoding", metavar="NAME",
                        help="input encoding: auto (default), utf-8, "
                             "utf-16-le, utf-16-be, cp1251")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="print details to the console")
    parser.add_argument("--quiet", action="store_true",
                        help="print nothing on success")
    parser.add_argument("--version", action="version",
                        version=_version_text(PROGRAM_BUILDER))
    return parser


def applier_parser():
    """Argument parser of the loader (normally invoked without arguments)."""
    parser = argparse.ArgumentParser(
        prog=PROGRAM_APPLIER,
        description="Apply the registry patch embedded in this executable.",
        epilog="Exit codes: 0 applied, 2 operations failed, 3 damaged "
               "overlay, 5 invalid embedded .reg, 6 elevation failed, "
               "7 internal error.")
    parser.add_argument("--hklm-only", action="store_true",
                        help=argparse.SUPPRESS)
    parser.add_argument("--patch-data", metavar="FILE", default=None,
                        help=argparse.SUPPRESS)
    parser.add_argument("--patch-dir", metavar="DIR", default=None,
                        help=argparse.SUPPRESS)
    parser.add_argument("--view", choices=("default", "32", "64"),
                        default="default",
                        help="registry view to write to (default: %(default)s)")
    parser.add_argument("--no-expand-env", action="store_true",
                        help="do not expand %%VARIABLE%% from the environment")
    parser.add_argument("--lenient", action="store_true",
                        help="tolerate recoverable .reg defects")
    parser.add_argument("--dry-run", action="store_true",
                        help="log what would be applied, change nothing")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="echo diagnostics to the console and keep "
                             "debug details in the log")
    parser.add_argument("--quiet", action="store_true",
                        help="write the log file only")
    parser.add_argument("--version", action="version",
                        version=_version_text(PROGRAM_APPLIER))
    return parser


def main_builder(argv=None):
    """Entry point of pyreg2exe; returns the process exit code."""
    args = builder_parser().parse_args(argv)
    log = diag.Log(verbose=args.verbose, quiet=args.quiet, echo=True)
    try:
        return _run_builder(args, log)
    except PyregError as exc:
        log.error(exc.report)
        return exc.exit_code
    except KeyboardInterrupt:
        log.error("interrupted")
        return 7
    except Exception as exc:                       # pragma: no cover
        log.error("internal error: {0!r}".format(exc))
        log.debug(traceback.format_exc())
        return 7
    finally:
        log.close()


def _run_builder(args, log):
    plan = builder.build_patch(
        args.regfile,
        output_path=args.output,
        template_path=args.template,
        force=args.force,
        dry_run=args.dry_run,
        lenient=args.lenient,
        encoding_name=args.encoding,
        log=log,
    )

    for diagnostic in plan.feedback:
        log.warning(str(diagnostic))

    log.info(plan.summary())
    if plan.dry_run:
        log.info("dry run: nothing was written (mode byte 0x{0:02x})".format(
            int(plan.mode)))
        return 0
    log.info("wrote {0} ({1} bytes)".format(plan.output_path,
                                            plan.output_size))
    log.debug("sha256 of the embedded .reg: {0}".format(plan.digest))
    return 0


def main_applier(argv=None):
    """Entry point of regapply; returns the process exit code."""
    args = applier_parser().parse_args(argv)
    log = diag.open_patch_log(verbose=args.verbose, quiet=args.quiet)
    try:
        code = apply_patch(
            hklm_only=args.hklm_only,
            patch_data=args.patch_data,
            patch_dir=args.patch_dir,
            view=args.view,
            expand_env=not args.no_expand_env,
            lenient=args.lenient,
            dry_run=args.dry_run,
            log=log,
        )
    except PyregError as exc:
        log.error(exc.report)
        code = exc.exit_code
    except KeyboardInterrupt:
        log.error("interrupted")
        code = 7
    except Exception as exc:                       # pragma: no cover
        log.error("internal error: {0!r}".format(exc))
        log.debug(traceback.format_exc())
        code = 7
    finally:
        log.close()
    return code

