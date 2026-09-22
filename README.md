# pyreg2exe

Convert `.reg` files into standalone `.exe` patches.

A modern, Python-based reimagining of **Reg2exe** — the classic
Visual Basic tool by Jan Vorel (2001–2008, GPLv2).

The resulting `.exe`:

- Silently applies the embedded `.reg` when launched.
- Handles `HKCU` and `HKLM` correctly — no admin prompts for
  user-only patches.
- Self-elevates via UAC only when the patch actually needs
  `HKLM` access.
- Requires **no Python** on the target machine.

---

## Why

Windows `.reg` files work fine for simple cases, but:

- They open in **regedit** with a scary confirmation dialog.
- SmartScreen and Defender may block `.reg` from unknown sources.
- There's no straightforward way to **bundle** registry data
  with other setup steps.
- `HKCU` and `HKLM` operations need different privilege levels.

`pyreg2exe` builds a **single, self-contained `.exe`** that does
exactly one thing: apply the registry patch — cleanly, silently,
with the right privileges.

---

## Quick start

```
py pyreg2exe.py patch.reg
```

That's it. `patch.exe` sits next to `patch.reg` and is ready to
distribute.

## How it works

### Build

```
py pyreg2exe.py patch.reg
```

Creates `patch.exe` next to `patch.reg`.

The output is a copy of `regapply.exe` (the template) with the
following **overlay** appended:
```
+-------------------------------------+
| PE image of regapply.exe            |
+-------------------------------------+
| .reg data as-is                     |
+-------------------------------------+
| length of .reg (8 bytes, LE)        |
+-------------------------------------+
| mode byte (0x01 / 0x02 / 0x03)      |
+-------------------------------------+
| marker "PYREGEND" (8 bytes)         |
+-------------------------------------+
```

### Mode byte

| Value  | Meaning                    |
|--------|----------------------------|
| `0x01` | HKLM only                  |
| `0x02` | HKCU only                  |
| `0x03` | Mixed (both HKCU and HKLM) |

The mode is determined at build time from the parsed `.reg`
content.

### Runtime

When `patch.exe` is launched:

**Single-root patches:**

- `HKCU` only → applied immediately under the current user.
  No UAC.
- `HKLM` only → triggers a UAC prompt; the elevated instance
  applies the patch.

**Mixed patches:**

1. `HKCU` operations are applied first, under the **current
   user**. No UAC.
2. `HKLM` operations trigger a **UAC prompt**. The elevated
   instance applies them.

This ensures `HKCU` writes always land in the **invoking user's**
profile, not the administrator's — a subtle but important detail
that most simple wrappers get wrong.


## Variable expansion

Two families of variables are expanded at runtime.

**Standard Windows environment variables:**

`%APPDATA%`, `%LOCALAPPDATA%`, `%USERPROFILE%`, `%TEMP%`,
`%WINDIR%`, `%SYSTEMROOT%`, `%PROGRAMFILES%`, `%USERNAME%`,
`%COMPUTERNAME%`, and others.

**Reg2exe-compatible markers:**

| Marker                       | Expands to                |
|------------------------------|---------------------------|
| `<reg2exepath>`              | Directory of the `.exe`   |
| `<reg2exewinpath>`           | `%WINDIR%`                |
| `<reg2exesyspath>`           | `%SYSTEMROOT%`            |
| `<reg2exetemppath>`          | `%TEMP%`                  |
| `<reg2exeappdatapath>`       | `%APPDATA%`               |
| `<reg2exelocalappdatapath>`  | `%LOCALAPPDATA%`          |
| `<reg2exeusername>`          | `%USERNAME%`              |
| `<reg2execomputername>`      | `%COMPUTERNAME%`          |
| `<reg2exeprofilespath>`      | `%USERPROFILE%`           |

`%CD%` (and `%cd%`) are also supported — they expand to the
directory where `patch.exe` resides, matching the classic
Reg2exe behaviour.

---

## Supported registry value types

- `REG_SZ`
- `REG_EXPAND_SZ`
- `REG_BINARY`
- `REG_DWORD`
- `REG_QWORD`
- `REG_MULTI_SZ`
- `REG_NONE`

Deletion of values (`"name"=-`) and keys (`[-HKEY_...]`) is
supported.

---

## Files

| File                | Purpose                                              |
|---------------------|------------------------------------------------------|
| `pyreg2exe.py`      | The build tool. Takes a `.reg` and produces a `.exe`.|
| `regapply.py`       | The template (loader). Compiled to `regapply.exe`.   |
| `regfile.py`        | Parser and applier. Shared by both.                  |
| `examples/cu.reg`   | HKCU-only patch (no UAC prompt).                     |
| `examples/lm.reg`   | HKLM-only patch (UAC prompt).                        |
| `examples/mixed.reg`| Mixed. HKCU applied first, then HKLM via UAC.        |


---

## Building from source

### Requirements

- Python **3.10+**
- **Nuitka** (for compiling `regapply.exe`)
- **MinGW** or **MSVC** (Nuitka can download MinGW
  automatically)

### Build the template
```
pip install nuitka zstandard

py -m nuitka --onefile --windows-disable-console ^
    --assume-yes-for-downloads ^
    --output-filename=regapply.exe ^
    --output-dir=dist ^
    regapply.py

copy dist\regapply.exe .\regapply.exe
```

### Build the tool itself (optional)

```
py -m nuitka --onefile --windows-disable-console ^
    --assume-yes-for-downloads ^
    --output-filename=pyreg2exe.exe ^
    --include-data-file=regapply.exe=regapply.exe ^
    pyreg2exe.py
```

Result: a single `pyreg2exe.exe` (~10 MB) with the template
embedded inside.

### Size comparison

| Build                      | Size     |
|----------------------------|----------|
| PyInstaller                | ~8.5 MB  |
| **Nuitka**                 | **~4.3 MB** |
| Python source (`regfile` + `regapply`) | ~40 KB |

---

## License

**GNU General Public License v2.0** (GPLv2).

See [LICENSE](LICENSE) for the full text.

---

## Credits

This project is **inspired by**
[Reg2exe](http://www.ctuser.net) by **Jan Vorel** (2001–2008),
written in **Visual Basic 6**, and released under **GPLv2**.

While no code was taken from the original (VB6 vs Python), the
**idea and overlay structure** are similar:

- Template `.exe` + appended registry data.
- A marker to identify the overlay.
- Self-contained, no installation required.

Released under the same license out of respect for the original
project and to avoid any licensing ambiguity.

---

## Changelog

### 1.0.0 — 2026

- Initial release.
- Overlay format with `PYREGEND` marker.
- `HKCU` / `HKLM` separation with self-elevation.
- All standard registry value types.
- Variable expansion (`%CD%`, `<reg2exe...>`, environment vars).
- Nuitka-based build (~4.3 MB `.exe`).
