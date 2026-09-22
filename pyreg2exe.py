# -*- coding: utf-8 -*-
"""
pyreg2exe — build a standalone .exe patch from a .reg file.

Copyright (C) 2026 Deepseek + VKRadeon

This program is free software; you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation; either version 2 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program; if not, write to the Free Software
Foundation, Inc., 59 Temple Place, Suite 330, Boston, MA  02111-1307  USA

Inspired by Reg2exe (Jan Vorel, 2001-2008, Visual Basic 6, GPLv2).

Overlay layout (appended after the regapply.exe PE image):

    [PE image of regapply.exe]
    [.reg data as-is (bytes)]
    [length of .reg: 8 bytes, little-endian]
    [mode: 1 byte]
    [marker b"PYREGEND": 8 bytes]

Mode values:
    0x01 — HKLM only
    0x02 — HKCU only
    0x03 — mixed (both)
"""

import os
import sys
import argparse


# ═══════════════════════════════════════════════════════════════
#  CONSTANTS — must match regapply.py
# ═══════════════════════════════════════════════════════════════

MARKER = b"PYREGEND"
SIZE_LEN = 8

MODE_HKLM = 0x01
MODE_HKCU = 0x02
MODE_MIXED = 0x03


# ═══════════════════════════════════════════════════════════════
#  RESOURCE / TEMPLATE LOCATION
# ═══════════════════════════════════════════════════════════════

def resource_path(relative_path):
    """Return the absolute path to a resource, working in .exe too."""
    if hasattr(sys, "_MEIPASS"):
        return os.path.join(sys._MEIPASS, relative_path)
    return os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        relative_path)


def find_template(explicit_path=None):
    """Locate regapply.exe (template). Default: next to pyreg2exe.py."""
    if explicit_path:
        if os.path.isfile(explicit_path):
            return explicit_path
        return None

    candidate = resource_path("regapply.exe")
    if os.path.isfile(candidate):
        return candidate

    return None


# ═══════════════════════════════════════════════════════════════
#  VALIDATION
# ═══════════════════════════════════════════════════════════════

def load_regfile():
    """
    Import regfile from next to pyreg2exe.py (or bundled).
    Return module or None on failure.
    """
    try:
        import regfile
        return regfile
    except ImportError:
        base_dir = os.path.dirname(os.path.abspath(__file__))
        candidate = os.path.join(base_dir, "regfile.py")
        if os.path.isfile(candidate):
            import importlib.util
            spec = importlib.util.spec_from_file_location("regfile",
                                                          candidate)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return mod
    return None


def validate_reg(regfile_mod, reg_path):
    """
    Parse .reg and return (operations, mode) or (None, error_text).

    mode:
        0x01 — HKLM only
        0x02 — HKCU only
        0x03 — mixed
    """
    try:
        content = regfile_mod._read_reg_file(reg_path)
        operations = regfile_mod._parse_reg_file(content)
    except Exception as e:
        return None, None, f"Parse error: {e}"

    if not operations:
        return None, None, "No operations found in .reg"

    hkcu_ops, hklm_ops, other_ops = \
        regfile_mod.split_operations_by_root(operations)

    has_hkcu = bool(hkcu_ops)
    has_hklm = bool(hklm_ops) or bool(other_ops)  # other roots go with admin

    if has_hkcu and has_hklm:
        mode = MODE_MIXED
    elif has_hklm:
        mode = MODE_HKLM
    elif has_hkcu:
        mode = MODE_HKCU
    else:
        return None, None, "No operations to apply"

    return operations, mode, None


# ═══════════════════════════════════════════════════════════════
#  BUILD
# ═══════════════════════════════════════════════════════════════

def build_exe(reg_path, template_path, output_path, mode):
    """
    Build the patched .exe.

    Layout:
        [template bytes]
        [.reg bytes as-is]
        [len(.reg): 8 bytes LE]
        [mode: 1 byte]
        [MARKER: 8 bytes]
    """
    with open(template_path, "rb") as f:
        template_data = f.read()

    with open(reg_path, "rb") as f:
        reg_data = f.read()

    with open(output_path, "wb") as f:
        f.write(template_data)
        f.write(reg_data)
        f.write(len(reg_data).to_bytes(SIZE_LEN, "little"))
        f.write(bytes([mode]))
        f.write(MARKER)


# ═══════════════════════════════════════════════════════════════
#  CLI
# ═══════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="Build a standalone .exe patch from a .reg file "
                    "(modern analogue of Reg2exe).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("regfile", help="Path to the .reg file")
    parser.add_argument(
        "-o", "--output",
        help="Path to the output .exe (default: next to .reg)"
    )
    parser.add_argument(
        "-t", "--template",
        help="Path to the template regapply.exe "
             "(default: next to pyreg2exe.py)"
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite the existing output file"
    )

    args = parser.parse_args()

    # 1. Validate .reg
    reg_path = os.path.abspath(args.regfile)
    if not os.path.isfile(reg_path):
        print(f"File not found: {reg_path}", file=sys.stderr)
        sys.exit(1)

    if not reg_path.lower().endswith(".reg"):
        print(f"Not a .reg file: {reg_path}", file=sys.stderr)
        sys.exit(1)

    regfile_mod = load_regfile()
    if regfile_mod is None:
        print("regfile.py not found next to pyreg2exe.py", file=sys.stderr)
        sys.exit(1)

    operations, mode, error = validate_reg(regfile_mod, reg_path)
    if error:
        print(f"Invalid .reg: {error}", file=sys.stderr)
        sys.exit(1)

    mode_names = {
        MODE_HKLM: "HKLM only",
        MODE_HKCU: "HKCU only",
        MODE_MIXED: "mixed (HKLM + HKCU)",
    }
    # Uncomment for verbose mode:
    # print(f"Mode: {mode_names.get(mode, '?')}", file=sys.stderr)

    # 2. Find template
    template_path = find_template(args.template)
    if template_path is None:
        if args.template:
            print(f"Template not found: {args.template}", file=sys.stderr)
        else:
            print("Template regapply.exe not found next to pyreg2exe.py",
                  file=sys.stderr)
        sys.exit(1)

    # 3. Determine output path
    if args.output:
        output_path = os.path.abspath(args.output)
    else:
        base = os.path.splitext(reg_path)[0]
        output_path = base + ".exe"

    # 4. Check overwrite
    if os.path.isfile(output_path) and not args.force:
        print(f"File already exists: {output_path}", file=sys.stderr)
        print("Use --force to overwrite", file=sys.stderr)
        sys.exit(1)

    # 5. Build
    try:
        build_exe(reg_path, template_path, output_path, mode)
    except Exception as e:
        print(f"Build error: {e}", file=sys.stderr)
        sys.exit(1)

    sys.exit(0)


if __name__ == "__main__":
    main()