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

Entry point only: reading the overlay, routing by privilege, elevation and
logging live in ``pyreg2core.applier`` (see PROJECT_CONTEXT.md §6.7).  The
file name stays because it is the Nuitka build target and the template of
every produced patch.
"""

from pyreg2core.cli import main_applier

if __name__ == "__main__":
    raise SystemExit(main_applier())
