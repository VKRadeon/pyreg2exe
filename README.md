# pyreg2exe

Convert `.reg` files into standalone `.exe` patches.

A modern, Python-based reimagining of **Reg2exe** — the classic
Visual Basic tool by Jan Vorel (2001–2008, GPLv2).

The resulting `.exe`:

- Silently applies the embedded `.reg` when launched.
- Handles `HKCU` and `HKLM` correctly — no admin prompts for
  user-only patches.
- Self-elevates via UAC only when the patch actually needs
  machine-wide access.
- Requires **no Python** on the target machine.
- Writes a log file, so a silent run is still diagnosable.

As of 1.1.0 the implementation lives in the `pyreg2core` package;
`pyreg2exe.py` and `regapply.py` are thin entry points. See
[Changelog](#changelog).

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

### Options of the builder

| Option | Meaning |
|---|---|
| `-o`, `--output PATH` | output file (default: next to the `.reg`) |
| `-t`, `--template PATH` | template `regapply.exe` (default: next to `pyreg2exe.py`) |
| `--force` | overwrite an existing output file |
| `--dry-run` | parse, validate and report — write nothing |
| `--lenient` | downgrade parse errors to warnings (pre-1.1 behaviour) |
| `--encoding NAME` | `auto` (default), `utf-8`, `utf-16-le`, `utf-16-be`, `cp1251` |
| `-v`, `--verbose` | print details, write debug lines to the console |
| `--quiet` | print nothing on success |
| `--version` | print the version and exit |

---

## How it works

### Build

```
py pyreg2exe.py patch.reg
```

Creates `patch.exe` next to `patch.reg`.

The builder:

1. reads the `.reg` **once** (bytes, then decoded text) and parses it;
2. rejects any parse error with a **line number** (unless `--lenient`);
3. computes the mode byte from the roots it saw;
4. reads the template once and validates it (`MZ`, `PE\0\0`, not already
   patched, output path distinct from input and template);
5. composes the result in memory and writes it **atomically**
   (`*.tmp-<pid>` → `fsync` → `os.replace`);
6. re-opens the output and verifies marker, mode, size and payload
   before reporting success.

The output is a copy of `regapply.exe` (the template) with the
following **overlay** appended:

```
+-------------------------------------+
| PE image of regapply.exe            |
+-------------------------------------+
| .reg data as-is (original bytes)    |
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
| `0x01` | HKLM (machine-wide) only   |
| `0x02` | HKCU (current user) only   |
| `0x03` | Mixed (both)               |

The mode is derived from the parsed operations: roots that need
administrator rights (`HKEY_LOCAL_MACHINE`, `HKEY_USERS`,
`HKEY_CURRENT_CONFIG`) versus roots that do not (`HKEY_CURRENT_USER`,
`HKEY_CLASSES_ROOT`).

### Runtime

When `patch.exe` is launched, the loader:

1. locates its own overlay (the running image, or the path it was started
   from — both are checked, so shortcuts, `PATH` launches and Nuitka
   onefile bootstraps all work);
2. no overlay → exits `0` silently (this is a plain template);
3. decodes and parses the embedded `.reg`; invalid data → exit `5`;
4. applies the **user** operations in-process;
5. applies the **machine-wide** operations in-process when already
   elevated, otherwise restarts itself elevated (`--hklm-only`), **waits**
   for the child and relays its exit code;
6. writes `%TEMP%\pyreg2exe\patch-<pid>.log` and exits.

**Mixed patches:** `HKCU` operations are applied first, by the current
user, without UAC; only then does the elevated child apply the
machine-wide ones. This guarantees `HKCU` writes land in the
**invoking user's** profile, not the administrator's — a subtle but
important detail that most simple wrappers get wrong.

### Exit codes

| Binary | Code | Meaning |
|---|---|---|
| `pyreg2exe.exe` | `0` | patch built |
| | `2` | bad command line |
| | `3` | input/template problem: missing file, invalid `.reg`, template already patched |
| | `4` | output problem: exists without `--force`, same path as input/template, write error |
| | `7` | unexpected internal error |
| `patch.exe` | `0` | applied (also: no overlay, i.e. a plain template) |
| | `2` | one or more registry operations failed |
| | `3` | overlay corrupt or unsupported |
| | `5` | the embedded `.reg` is invalid |
| | `6` | elevation declined or failed |
| | `7` | unexpected internal error |

Any `if errorlevel 1` check keeps working. The builder's generic `1` from
1.0.0 became `3`/`4` — an intentional, documented change.

### Log

The loader is windowless, so it never shows a dialog. Every run appends to
`%TEMP%\pyreg2exe\patch-<pid>.log`; the directory is created on demand.
`--verbose` also echoes the same lines to the console (useful for source
runs), `--quiet` keeps the file only.

---

## Variable expansion

Two families of variables are expanded at runtime, in the **data** of
string values only — never in value names, never in binary payloads.

**Standard Windows environment variables** (`%VAR%`): `APPDATA`,
`LOCALAPPDATA`, `USERPROFILE`, `TEMP`, `WINDIR`, `SYSTEMROOT`,
`PROGRAMFILES`, `USERNAME`, `COMPUTERNAME`, and anything else present in
the environment of the applying process. `--no-expand-env` turns this off.

**Reg2exe-compatible markers** (case-insensitive):

| Marker | Expands to |
|---|---|
| `<reg2exepath>` | directory of the patch `.exe` |
| `<reg2exewinpath>` | `%WINDIR%` |
| `<reg2exesyspath>` | `%SYSTEMROOT%` |
| `<reg2exetemppath>` | `%TEMP%` |
| `<reg2exeprogspath>` | `%PROGRAMFILES%` |
| `<reg2exeappdatapath>` | `%APPDATA%` |
| `<reg2exelocalappdatapath>` | `%LOCALAPPDATA%` |
| `<reg2exeusername>` | `%USERNAME%` |
| `<reg2execomputername>` | `%COMPUTERNAME%` |
| `<reg2exeprofilespath>` | `%USERPROFILE%` |

`%CD%` (and `%cd%`) always expands to the directory where the patch `.exe`
resides, matching the classic Reg2exe behaviour — regardless of the
process working directory and even if `%CD%` happens to exist in the
environment.

Rules:

- unresolved tokens are kept verbatim (no error, no empty replacement);
- expansion is single-pass: an expanded value is not expanded again;
- `REG_EXPAND_SZ` data keeps environment variables literal (`%VAR%`
  survives, `%CD%`/markers are resolved), so Windows expands them per user
  at read time.

---

## Supported registry value types

| Written as | Stored as | Data |
|---|---|---|
| `"name"="text"` | `REG_SZ` | text, `\\` and `\"` decoded |
| `@="text"` | `REG_SZ` | default value |
| `"name"=dword:XXXXXXXX` | `REG_DWORD` | 1–8 hex digits, ≤ `0xFFFFFFFF` |
| `"name"=hex:01,02` | `REG_BINARY` | raw bytes |
| `"name"=hex(0):01,02` | `REG_NONE` | raw bytes |
| `"name"=hex(1):..` | `REG_SZ` | UTF-16LE text |
| `"name"=hex(2):..` | `REG_EXPAND_SZ` | UTF-16LE text |
| `"name"=hex(3):..` | `REG_BINARY` | raw bytes |
| `"name"=hex(4):..` | `REG_DWORD` | exactly 4 bytes, little-endian |
| `"name"=hex(7):..` | `REG_MULTI_SZ` | UTF-16LE, NUL-separated |
| `"name"=hex(b):..` | `REG_QWORD` | exactly 8 bytes, little-endian |
| `"name"=-` / `@=-` | — | delete the value |
| `[-HKEY_...\key]` | — | delete the key recursively |

### Strict parsing

Since 1.1.0 the parser follows the format instead of guessing. These are
**rejected** (with a line number) unless `--lenient` is used:

- unknown roots: `HKEY_DYN_DATA`, `HKEY_PERFORMANCE_DATA`, `HKDD`, `HKPD`,
  `HKEY_CURRENT_USERX`, XML `.reg` files;
- empty key paths (`[HKLM]`, `[HKLM\]`);
- unsupported subtypes: `hex(5)`, `hex(6)`, `hex(8)`, `hex(a)`, …;
- malformed payloads: `hex(4)` without exactly 4 bytes, `hex(b)` without
  exactly 8, odd-length `hex(1)`/`hex(2)`/`hex(7)`;
- non-hex byte tokens: `0x0A`, `1`, `+1`, `-1`, a trailing comma;
- `dword:` values above `0xFFFFFFFF`;
- unquoted value names, unterminated quoted strings, undefined escapes
  (`\n`, `\t`, …);
- line continuation outside a `hex:`/`hex(N):` payload, and a value line
  outside any section.

Also decoded/relaxed correctly:

- `\"` inside quoted data and names becomes `"` (`\\` becomes `\`);
- `%CD%` resolves to the patch directory, not the working directory;
- value names are never expanded;
- `hex(4)`/`hex(b)` are type codes, not four-byte data;
- a `;` comment never continues onto the next line, even if it ends
  with `\`;
- duplicate assignments to the same value are allowed, the last one wins.

`--lenient` restores the pre-1.1 behaviour for legacy files: an unknown
construct becomes a warning and is skipped, unquoted data is kept as
`REG_SZ`.

---

## Files

| File / directory | Purpose |
|---|---|
| `pyreg2exe.py` | entry point of the builder (thin wrapper) |
| `regapply.py` | entry point of the loader; compiled to `regapply.exe` and used as the template |
| `pyreg2core/` | implementation package (see below) |
| `regfile.py` | **deprecated** compatibility shim with the 1.0 tuple API |
| `tests/` | `unittest` suite (stdlib only) |
| `examples/cu.reg` | HKCU-only patch (no UAC prompt) |
| `examples/lm.reg` | HKLM-only patch (UAC prompt) |
| `examples/mixed.reg` | mixed: HKCU first, then HKLM via UAC |
| `PROJECT_CONTEXT.md` | full refactor/port specification |

Inside `pyreg2core/` the layering is strict — imports point downwards only:

| Module | Responsibility |
|---|---|
| `errors.py` | exception hierarchy and the exit-code table |
| `reg_types.py` | `RegRoot`, `RegType`, `OpKind`, `Operation`, diagnostics |
| `encoding.py` | bytes → text: BOM table, UTF-16LE heuristic, explicit codecs |
| `parser.py` | text → operations + located diagnostics |
| `expand.py` | `%VAR%`, `%CD%` and `<reg2exe...>` expansion |
| `overlay.py` | mode byte, overlay read/verify/encode (no `winreg`) |
| `registry.py` | the only module that uses `winreg` |
| `selfpath.py`, `elevate.py` | locating our own image, UAC restart |
| `diag.py` | log file, crash-proof console writer |
| `builder.py`, `applier.py` | the two pipelines |
| `cli.py` | argument parsing and exit-code mapping |

`errors`, `reg_types`, `encoding`, `parser`, `expand` and `overlay` are free
of `winreg`/`ctypes`, so they are portable and directly testable.

---

## Tests

The suite uses the standard library only (no pytest required):

```
py -m unittest discover -s tests -v
```

It creates `HKCU\Software\__pyreg2exe_tests__\<random>` keys and removes
them again; after a run the sandbox key must not exist. Elevation is
mocked, and no test writes to `HKLM` (the one test that needs an access
denied is skipped when the process is elevated).

---

## Building from source

### Requirements

- Python **3.10+**
- **Nuitka** plus `zstandard` (for `--onefile`)
- A C compiler: either MinGW-w64 (tested with GCC 16.2.0, UCRT) or the
  WinLibs download Nuitka performs with `--assume-yes-for-downloads`
- `regapply.ico` is optional; drop `--windows-icon-from-ico` if the icon is
  absent or rejected

`CC`/`CXX` is the supported way to pin the compiler: Nuitka reads both
variables, prepends the compiler directory to `PATH` and switches to
`mingw_mode`, so the build does not depend on `PATH` order. `--mingw64` is
a boolean flag, not a path, and **Nuitka 4.2.1 rejects it on Python 3.13+**
(`FATAL: cannot use '--mingw64' on Python version 3.13 or higher`) — pin the
compiler with `CC`/`CXX` instead, or use `--msvc=latest`.

Verified on Python 3.13 + Nuitka 4.2.1: Nuitka reported the external MinGW
as *"is being ignored, Nuitka is very dependent on the precise one"* and
compiled with its bundled **zig** backend instead. Both artefacts build and
run correctly that way; use Nuitka 4.3.x or newer (or `--msvc=latest`) if a
MinGW/MSVC-built binary is required.

### Step 1 — template (windowless loader)

```powershell
$env:CC  = 'C:\VSS\mingw64\bin\gcc.exe'
$env:CXX = 'C:\VSS\mingw64\bin\g++.exe'

python -m nuitka `
  --onefile `
  --assume-yes-for-downloads `
  --windows-console-mode=disable `
  --windows-icon-from-ico=regapply.ico `
  --output-filename=regapply.exe `
  --output-dir=dist `
  regapply.py

Copy-Item -LiteralPath .\dist\regapply.exe -Destination .\regapply.exe -Force
```

### Step 2 — builder (console kept)

```powershell
python -m nuitka `
  --onefile `
  --assume-yes-for-downloads `
  --windows-console-mode=force `
  --windows-icon-from-ico=regapply.ico `
  --include-data-files=regapply.exe=regapply.exe `
  --output-filename=pyreg2exe.exe `
  --output-dir=dist `
  pyreg2exe.py
```

Notes:

- `--windows-console-mode={force,disable,attach,hide}` replaces the removed
  `--windows-disable-console`; `disable` is correct **only** for the loader.
- `--include-data-files=SRC=DEST` is the current spelling;
  `--include-data-file` is deprecated.
- `SRC` is resolved relative to the build directory, so run step 2 from the
  repository root **after** step 1.
- No `--include-module=regfile` is needed: the entry points import
  `pyreg2core` statically, so Nuitka resolves the whole graph.
- The first build downloads and extracts WinLibs into
  `%LOCALAPPDATA%\Nuitka` and can take 5–15 minutes; incremental builds take
  1–3 minutes.

### Release checklist

1. `Get-ChildItem .\dist\regapply.exe, .\dist\pyreg2exe.exe | Select-Object Name, Length`
   — onefile builds with CPython 3.13 are roughly 7–12 MB each.
2. `.\dist\pyreg2exe.exe --help` prints help (the console must stay).
3. Build the examples and check the mode bytes (`2`, `1`, `3`):

   ```powershell
   .\dist\pyreg2exe.exe .\examples\cu.reg    -o "$env:TEMP\cu_patch.exe"    --force
   .\dist\pyreg2exe.exe .\examples\lm.reg    -o "$env:TEMP\lm_patch.exe"    --force
   .\dist\pyreg2exe.exe .\examples\mixed.reg -o "$env:TEMP\mixed_patch.exe" --force

   python -c "import sys;d=open(sys.argv[1],'rb').read()[-17:];print(sys.argv[1],'size',int.from_bytes(d[:8],'little'),'mode',d[8],'marker',d[9:])" "$env:TEMP\cu_patch.exe"
   ```

4. `& "$env:TEMP\cu_patch.exe"`, then `reg query "HKCU\TestPyreg2exe_CU"`:
   `Name` must be `VKRadeon` and `Path` must be the directory of
   `cu_patch.exe` followed by `\data` (the `%CD%` check). Clean up with
   `reg delete "HKCU\TestPyreg2exe_CU" /f`.
5. Running the builder again on an existing output without `--force` must fail
   with exit code `4` and a visible message.
6. `lm_patch.exe` / `mixed_patch.exe` require an elevated shell; they must
   apply successfully or exit with `6`/`2`, never silently with `0`.
7. `Remove-Item "$env:TEMP\*_patch.exe"` and inspect `%TEMP%\pyreg2exe\*.log`.

### Troubleshooting

| Symptom | Cause | Action |
|---|---|---|
| `Scons: *** Unsupported compiler` | GCC older than 11.2, or a 32-bit compiler with x64 Python | unset `CC`/`CXX` and let Nuitka download WinLibs, or use `--msvc=latest` |
| `FATAL: cannot use '--mingw64' on Python version 3.13 or higher` | `--mingw64` is obsolete on new Python versions | drop `--mingw64`; select MinGW with `CC`/`CXX` (or use `--msvc=latest`) |
| `--onefile` output larger than 40 MB | leftovers in `dist\`/`build\`, extra modules | clean both directories and rebuild |
| Patch starts but applies nothing | overlay not found (started from a copy without the overlay, or an old template) | rebuild from the fresh template, start it by absolute path, read `%TEMP%\pyreg2exe\patch-*.log` |
| Builder prints garbled characters | console code page does not cover the text | the safe writer escapes instead of failing; set `PYTHONIOENCODING=utf-8` or use a UTF-8 console |

---

## Known limitations

| ID | Limitation | Status |
|---|---|---|
| L1 | Executables built with the UCRT MinGW toolchain require Windows 10 or newer | documented here |
| L2 | Appending the overlay invalidates Authenticode; sign the finished patch | documented here |
| L3 | Only an x64 build is produced | documented here |
| L4 | The registry view is the caller's view unless `--view` is used | opt-in option since 1.1.0 |
| L5 | Variable expansion is single-pass | documented here |
| L6 | `HKEY_USERS`/`HKEY_CLASSES_ROOT` writes need admin and target the current user's hive; other users' hives are not loaded | documented here |
| L7 | No transactions: operations run in order, failures are reported, there is no rollback | by design |
| L8 | Console output depends on the code page (non-representable characters are escaped) | mitigated by the safe writer |
| L9 | `REG_EXPAND_SZ` in an `HKLM` patch is expanded in the elevated context when environment expansion is on | use `--no-expand-env` for portable values |

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

### 1.1.0 — 2026

**Architecture**

- The implementation moved into the `pyreg2core` package; `pyreg2exe.py`
  and `regapply.py` are thin entry points.
- `regfile.py` is now a deprecated shim that keeps the 1.0 tuple API
  (`__deprecated__`); no `DeprecationWarning` is raised, so callers running
  with `-W error` keep working.
- A `unittest` suite (`tests/`) covers the parser, the overlay codec, the
  builder, the loader, the registry layer and the shim.

**Correctness**

- Strict parsing with line numbers; `--lenient` restores the old
  skip-and-continue behaviour.
- `hex(4)` is `REG_DWORD` and `hex(b)` is `REG_QWORD`; malformed payloads are
  reported instead of being dropped.
- `hex(0)`, `hex(1)`, `hex(2)`, `hex(3)` are handled explicitly;
  `hex(5)`/`hex(6)`/`hex(8)` and other subtypes are rejected.
- `\"` escapes are decoded; value names are never expanded.
- `%CD%`/`<reg2exepath>` always resolve to the patch directory.
- `REG_EXPAND_SZ` is stored literal, so Windows expands it per user.
- Rejected: `dword:` values above `0xFFFFFFFF`, hex tokens such as `0x0A`,
  empty key paths, `HKEY_DYN_DATA`/`HKDD`/`HKPD`/`HKEY_PERFORMANCE_DATA`,
  XML `.reg` files.
- The loader reports failed operations instead of exiting `0`, waits for the
  elevated child and relays its exit code, and finds its overlay even from a
  onefile bootstrap or a `PATH` launch.
- The builder validates the template (`MZ`, `PE\0\0`, not already patched),
  refuses to overwrite the input or the template, writes atomically and
  verifies the result.

**Interface**

- Builder: `--dry-run`, `--lenient`, `--encoding`, `-v/--verbose`,
  `--quiet`, `--version`.
- Loader: `--view={default,32,64}`, `--no-expand-env`, `--lenient`,
  `--dry-run`, `-v/--verbose`, `--quiet`, `--version`.
- Distinct exit codes (see above); the builder's generic `1` became `3`/`4`.
- The loader always writes `%TEMP%\pyreg2exe\patch-<pid>.log`.
- `pyreg2exe.exe` keeps its console; only the loader is windowless.

### 1.0.0 — 2026

- Initial release.
- Overlay format with `PYREGEND` marker.
- `HKCU` / `HKLM` separation with self-elevation.
- All standard registry value types.
- Variable expansion (`%CD%`, `<reg2exe...>`, environment vars).
- Nuitka-based build (~4.3 MB `.exe`).






