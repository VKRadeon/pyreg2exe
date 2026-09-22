# -*- coding: utf-8 -*-
"""
regapply — loader for pyreg2exe patches.

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

Behavior:
    - Reads its own overlay (PE image + .reg + length + mode + marker).
    - Applies HKCU operations under the current user (no UAC).
    - Applies HKLM (and other) operations under admin (via runas restart).
    - Exits silently.

Overlay layout (must match pyreg2exe.py):
    [PE image]
    [.reg data as-is]
    [length of .reg: 8 bytes LE]
    [mode: 1 byte]
    [marker b"PYREGEND": 8 bytes]
"""

import os
import sys


# ═══════════════════════════════════════════════════════════════
#  CONSTANTS — must match pyreg2exe.py
# ═══════════════════════════════════════════════════════════════

MARKER = b"PYREGEND"
SIZE_LEN = 8
MODE_LEN = 1
TAIL_LEN = SIZE_LEN + MODE_LEN + len(MARKER)   # 17 bytes

MODE_HKLM = 0x01
MODE_HKCU = 0x02
MODE_MIXED = 0x03

# CLI flag for the second pass (admin only)
FLAG_HKLM_ONLY = "--hklm-only"


# ═══════════════════════════════════════════════════════════════
#  OVERLAY READING
# ═══════════════════════════════════════════════════════════════

def read_overlay(exe_path):
    """
    Read the overlay from our own .exe.

    Returns:
        (reg_data, mode) on success
        (None, None) if this is a plain template (no overlay)
    """
    try:
        with open(exe_path, "rb") as f:
            f.seek(0, 2)
            file_size = f.tell()

            if file_size < TAIL_LEN:
                return None, None

            # Marker
            f.seek(file_size - len(MARKER))
            marker = f.read(len(MARKER))
            if marker != MARKER:
                return None, None

            # Mode (1 byte before marker)
            f.seek(file_size - len(MARKER) - MODE_LEN)
            mode = f.read(MODE_LEN)[0]

            # Length of .reg (8 bytes before mode)
            f.seek(file_size - len(MARKER) - MODE_LEN - SIZE_LEN)
            size_bytes = f.read(SIZE_LEN)
            reg_size = int.from_bytes(size_bytes, "little")

            if reg_size <= 0:
                return None, None
            if reg_size > file_size - TAIL_LEN:
                return None, None

            # .reg data (reg_size bytes before length)
            f.seek(file_size - TAIL_LEN - reg_size)
            reg_data = f.read(reg_size)

            return reg_data, mode

    except OSError:
        return None, None


# ═══════════════════════════════════════════════════════════════
#  MODULE LOADING
# ═══════════════════════════════════════════════════════════════

def load_regfile():
    """Import regfile — bundled or next to us."""
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


# ═══════════════════════════════════════════════════════════════
#  ADMIN PRIVILEGES
# ═══════════════════════════════════════════════════════════════

def is_admin():
    """Check if the current process is elevated."""
    import ctypes
    try:
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        return False


def run_as_admin(args):
    """
    Restart ourselves with admin privileges (UAC prompt).
    Return True on success (ShellExecuteW returned > 32).
    """
    import ctypes

    exe_path = os.path.abspath(sys.argv[0])
    params = " ".join(f'"{a}"' for a in args)

    result = ctypes.windll.shell32.ShellExecuteW(
        None,
        "runas",
        exe_path,
        params,
        os.path.dirname(exe_path),
        1,  # SW_SHOWNORMAL
    )

    return result > 32


# ═══════════════════════════════════════════════════════════════
#  MAIN
# ═══════════════════════════════════════════════════════════════

def main():
    exe_path = os.path.abspath(sys.argv[0])

    # 1. Read overlay
    reg_data, mode = read_overlay(exe_path)
    if reg_data is None:
        # Plain template (no overlay) — exit silently
        sys.exit(0)

    # 2. Load regfile
    regfile = load_regfile()
    if regfile is None:
        sys.exit(1)

    # 3. Decode and parse .reg
    try:
        content = regfile._read_reg_content_from_bytes(reg_data)
        operations = regfile._parse_reg_file(content)
    except Exception:
        sys.exit(1)

    if not operations:
        sys.exit(0)

    hkcu_ops, hklm_ops, other_ops = \
        regfile.split_operations_by_root(operations)

    # Combine HKLM with "other" roots (HKCR, HKU, HKCC — need admin too)
    admin_ops = hklm_ops + other_ops

    install_dir = os.path.dirname(exe_path)
    variables = {
        "%CD%": install_dir,
        "%cd%": install_dir,
        "<reg2exepath>": install_dir,
    }

    # 4. Second pass? (--hklm-only, already elevated)
    is_second_pass = FLAG_HKLM_ONLY in sys.argv

    if is_second_pass:
        # Only apply admin ops
        if admin_ops:
            regfile.apply_operations(admin_ops, variables)
        sys.exit(0)

    # 5. First pass
    # Apply HKCU under current user
    if hkcu_ops:
        regfile.apply_operations(hkcu_ops, variables)

    # Apply admin ops if we are already admin
    if admin_ops:
        if is_admin():
            regfile.apply_operations(admin_ops, variables)
        else:
            # Restart ourselves with UAC
            run_as_admin([FLAG_HKLM_ONLY])

    sys.exit(0)


if __name__ == "__main__":
    main()