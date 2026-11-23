# PROJECT_CONTEXT — pyreg2exe

**Audit date:** 2026-11-23
**Auditor:** Cline (AI coding agent), read-only audit plus isolated parser probes
**Snapshot:** repository without Git metadata, without a built `regapply.exe`
template, Python 3.13.15 (x64, CPython Official), Windows x64 host.

This document is the single source of truth for the **Stage 2 refactor** of the
Python implementation and for the **Stage 3 C11 port** of the registry engine.
It records:

1. the **as-is** architecture and behaviour (reverse-engineered and verified),
2. the **defect registry** (B1–B23) with file/line evidence,
3. the **to-be** architecture and the normative `.reg` semantics that the
   Python and the C implementation must share,
4. the **build recipe** (Nuitka) and the **C module specification**.

Everything here is written in English because the project is published on
GitHub; the same applies to all source comments, docstrings, `--help` texts and
log messages produced from now on.

No `.py` file was modified during the audit: all findings below were obtained by
reading the sources and by running the parser on synthetic inputs.

---

## 1. Purpose, scope and compatibility contract

| Item | Value |
|---|---|
| Project | `pyreg2exe` — converts a `.reg` file into a single self-applying `.exe` |
| Origin | modern re-implementation of *Reg2exe* (Jan Vorel, 2001–2008, VB6, GPLv2) |
| License | GPLv2 (`LICENSE`) |
| Distribution | public GitHub repository |
| Entry points | `pyreg2exe.py` (builder CLI), `regapply.py` (loader, compiled to `regapply.exe`) |
| Shared code | `regfile.py` (parser + applier, imported by both entry points) |
| Target OS | Windows (registry access is Windows-only; parser and overlay are portable) |

### 1.1 Frozen compatibility contract

Stage 2 and Stage 3 must not break the following surfaces:

| Surface | Frozen behaviour |
|---|---|
| Overlay format | `[PE image][.reg bytes as-is][size: 8 LE][mode: 1][b"PYREGEND"]`, tail length 17 bytes |
| Mode byte | `0x01` HKLM-only, `0x02` HKCU-only, `0x03` mixed (both hives) |
| Builder CLI | positional `regfile`; `-o/--output`; `-t/--template`; `--force` |
| Loader CLI | internal `--hklm-only` second pass; plain template without overlay exits `0` silently |
| Output naming | default output = input path with `.exe` extension, next to the `.reg` |
| Build tool | Nuitka onefile; `regapply.exe` is the template embedded into `pyreg2exe.exe` |
| Runtime UX | loader is silent on success, shows no console window |

Bug-for-bug compatibility is **not** part of the contract: silent data loss,
wrong value types and undetected failures are defects to be fixed, not
behaviour to preserve. Every intentional behaviour change must be recorded in
`README.md` (Changelog, version 1.1.0).

---

## 2. Project inventory

| File | Lines | Role |
|---|---|---|
| `pyreg2exe.py` | 260 | builder CLI: validates the `.reg`, locates the template, appends the overlay, writes the output `.exe` |
| `regapply.py` | 232 | loader: reads its own overlay, applies HKCU operations in-process, re-launches itself elevated for HKLM operations |
| `regfile.py` | 707 | `.reg` decoder/lexer/parser, root splitter, variable expansion, `winreg` applier |
| `README.md` | 243 | user documentation: overlay layout, mode byte, marker table, build recipe, changelog |
| `LICENSE` | — | GPLv2 full text |
| `.gitignore` | 218 | standard Python template; ignores `build/`, `dist/`, `__pycache__/`; **does not** ignore `*.exe` |
| `examples/cu.reg` | 5 | `HKCU\TestPyreg2exe_CU` — `Name`, `Path=%CD%\data` |
| `examples/lm.reg` | 5 | `HKLM\SOFTWARE\TestPyreg2exe_LM` — `Name`, `InstallDir=%CD%` |
| `examples/mixed.reg` | 9 | both hives: `HKCU\TestPyreg2exe_Mixed`, `HKLM\SOFTWARE\TestPyreg2exe_Mixed` |

Missing from the snapshot: `regapply.exe` (the builder cannot produce anything
until it is built), any test suite, any CI configuration, Git history.

---

## 3. Constraints, environment and toolchain

### 3.1 Host and runtimes

| Component | Version / path | Notes |
|---|---|---|
| OS | Windows x64 | host used for the audit and for all builds |
| Python | 3.13.15, CPython Official, x64 | `~\AppData\Local\Programs\Python\Python313\python.exe` |
| Nuitka | 4.2.1 (installed) | 4.3.5 available; upgrade **not** applied (not required, no consent) |
| `zstandard` | installed | required by Nuitka onefile compression |
| `ordered_set` | **absent** | not required by Nuitka 4.2.1 (verified: `--version` runs) |
| `pytest` / `coverage` | **absent** | the test suite must be stdlib `unittest`-based |
| C compiler | `C:\VSS\mingw64\bin\gcc.exe` — GCC 16.2.0, target `x86_64-w64-mingw32`, UCRT (WinLibs r3) | used for Stage 3 and for Nuitka via `CC`/`CXX` |
| Companion tools | `mingw32-make.exe`, `windres.exe`, `cmake.exe`, `gdb`, `cppcheck` (same `bin`) | available for Stage 3 |
| MSVC | not verified | not required |
| Git | repository has no `.git` | no history or CI to preserve |

### 3.2 Consequences for the design

* **No new Python dependencies.** Stdlib only: `winreg`, `ctypes`, `argparse`,
  `logging`, `dataclasses`, `enum`, `unittest`. Keeps Nuitka builds small and
  reproducible.
* **Tests must run without pytest** (`python -m unittest discover -s tests`),
  but must stay pytest-compatible (plain `unittest.TestCase`, no fixtures).
* **No admin rights assumed** for tests: registry tests are restricted to
  `HKCU\Software\__pyreg2exe_tests__\...`; admin code paths are covered with
  mocks, never by real elevation.
* **UCRT toolchain** means the produced executables require Windows 10 or
  newer; this comes from the compiler, not from the code, and must be stated in
  `README.md` (Requirements).
* **Windows-only code paths** (`winreg`, `ctypes.windll`) must be isolated in
  a few modules (`registry.py`, `elevate.py`, `selfpath.py`) so that the parser,
  encoder and overlay layers stay importable and testable on any platform.

---

## 4. As-is architecture (verified)

### 4.1 Modules and call graph

```
pyreg2exe.py
|-- resource_path() ---------------- sys._MEIPASS | dirname(abspath(__file__))
|-- find_template() ---------------- resource_path("regapply.exe")
|-- load_regfile() ----------------- import regfile | spec_from_file_location()
|-- validate_reg() ----------------- regfile._read_reg_file()
|                                    regfile._parse_reg_file()
|                                    regfile.split_operations_by_root()
`-- build_exe() -------------------- template bytes + .reg bytes + tail

regapply.py
|-- read_overlay() ----------------- tail scan of its own .exe (marker, mode, size)
|-- load_regfile() ----------------- duplicate of pyreg2exe.load_regfile()
|-- is_admin() --------------------- shell32.IsUserAnAdmin()
|-- run_as_admin() ----------------- ShellExecuteW("runas", ...)
`-- main() ------------------------- read -> decode -> parse -> split -> apply HKCU
                                     -> (elevate?) -> apply admin ops
```

### 4.2 Build flow (`pyreg2exe.py`)

1. `main()` parses the command line (`pyreg2exe.py:174-196`).
2. Input path is made absolute and checked for existence and `.reg` extension
   (`:199-206`).
3. `load_regfile()` imports `regfile`, falling back to
   `importlib.util.spec_from_file_location` (`:84-102`).
4. `validate_reg()` reads and parses the file **once for validation**
   (`:105-138`).
5. Operations are split by root; the mode byte is derived from
   `has_hkcu`/`has_hklm` (`:123-136`).
6. `find_template()` resolves `regapply.exe` next to the script (`:66-77`).
7. The output path is derived from `-o` or from the input path (`:237-241`), and
   the `--force` check runs (`:244-247`).
8. `build_exe()` re-reads the `.reg`, reads the template and writes
   `template + reg + size + mode + marker` into the output file in one
   non-atomic `open(..., "wb")` session (`:145-167`).
9. Exit code `0` on success, `1` on every failure.

### 4.3 Runtime flow (`regapply.py`)

1. `exe_path = os.path.abspath(sys.argv[0])` (`:170`).
2. `read_overlay()` seeks the last 17 bytes, validates the marker, reads the
   mode byte and the 8-byte size, then reads that many bytes before the tail
   (`:62-105`). No overlay -> silent `exit(0)` (`:174-176`).
3. `load_regfile()` imports the shared module (`:112-127`).
4. Embedded bytes are decoded and parsed; any exception -> `exit(1)` without a
   message (`:184-188`).
5. Operations are split into HKCU / HKLM / other; "other" roots are merged into
   the admin group (`:193-197`).
6. `install_dir = dirname(exe_path)` feeds the expansion variables `%CD%`,
   `%cd%`, `<reg2exepath>` (`:199-204`).
7. Second pass (`--hklm-only` in `sys.argv`): apply admin operations, `exit(0)`
   (`:207-213`).
8. First pass: apply HKCU operations, then admin operations either directly (if
   already elevated) or after `run_as_admin(["--hklm-only"])` (`:215-228`).
9. `exit(0)` unconditionally; return values of `apply_operations()` and
   `run_as_admin()` are discarded.
### 4.4 Overlay format (current and frozen)

| Offset from EOF | Size | Content |
|---|---|---|
| -8 | 8 | marker `b"PYREGEND"` |
| -9 | 1 | mode: `0x01` HKLM-only, `0x02` HKCU-only, `0x03` mixed |
| -17 | 8 | length of the embedded `.reg` data, little-endian, unsigned |
| -17 - N | N | `.reg` file bytes exactly as they were on disk |
| 0 .. -17 - N | - | unmodified PE image of `regapply.exe` |

Properties that must be preserved:

* The overlay is a pure appendage: the template's PE headers, section table and
  image contents are untouched, so a patched file still starts.
* Nuitka 4.2.1 onefile payload is a linker-embedded blob referenced by
  `getpayload_binData()` and `_NUITKA_ONEFILE_PAYLOAD_SIZE_INT`
  (`nuitka/build/static_src/OnefileBootstrap.c:177-192`), not a size-sensitive
  tail scan, therefore appending data does not break the runtime.
* The overlay invalidates any Authenticode signature of the template; signing
  must be applied to the finished patch, never to the template.

### 4.5 CLI surface

| Binary | Option | Meaning |
|---|---|---|
| `pyreg2exe.exe` | `regfile` (positional) | input `.reg` (required) |
| | `-o, --output PATH` | output `.exe`; default `<input>.exe` |
| | `-t, --template PATH` | template `regapply.exe`; default next to the script |
| | `--force` | overwrite an existing output file |
| `patch.exe` | `--hklm-only` | internal: elevated second pass, admin operations only |

### 4.6 What is already done well (keep it)

* The overlay is minimal, self-describing and resilient to `regapply.exe`
  changes; the marker plus length validation rejects truncated files.
* Root-based privilege split (HKCU first under the invoking user, then HKLM
  elevated) is the correct approach and matches the README promises.
* `winreg` handles are closed in `finally` blocks; there are no leaks.
* The parser covers all common `.reg` constructs and preserves operation order.

### 4.7 Behavioural verification

Every defect below was verified either by reading the code or by running the
parser on synthetic input; the actual probe output is reproduced in §5.1.

---

## 5. Defect registry

Priorities:

* **P0** — silent data corruption, silent failure or security-relevant
  misdirection. Must be fixed in Stage 2.
* **P1** — wrong semantics, missing validation, poor diagnosability, or code
  duplication that blocks the C port. Must be fixed in Stage 2.
* **P2** — build, documentation and tooling issues. Fixed in the final build
  step (§8).

Each fix must be covered by at least one test from §10.

### 5.1 Evidence: actual probe output

`probe.py` imports `regfile` from the repository and calls its private helpers
directly (no registry access, no files written). Results:

```
root_boundary    ('HKEY_LOCAL_MACHINE', 'X\\Soft')        # input HKEY_LOCAL_MACHINEX\Soft
root_suffix      ('HKEY_CURRENT_USER', 'S')               # input HKEY_CURRENT_USERS
root_empty       ('HKLM', '')
hex4             ('D', b'\x01\x00\x00\x00', 'REG_BINARY') # must be REG_DWORD
hex3             ('D', b'\x01\x00\x00\x00', 'REG_BINARY') # correct
dword_short      ('N', 1, 'REG_DWORD')
dword_overflow   ('N', 1311768467294899695, 'REG_DWORD') # > 0xFFFFFFFF
hexb_short       None                                     # silently dropped
hex_0x           ('X', b'\n\x0b', 'REG_BINARY')           # '0x0A' accepted
hex_single       ('X', b'\x01\x02', 'REG_BINARY')         # '1' accepted
escape_quote     ('V', 'a\\"b', 'REG_SZ')                  # \" not decoded
escape_backslash ('V', 'a\\b', 'REG_SZ')                   # correct
continuation     ['; note "A"="b"']                        # value line swallowed
unquoted_name    None                                     # silently dropped
norm_dyndata     None                                     # parses, cannot be written
norm_hkpd        None
expand_cd        'X:\\patch'
expand_marker    'X:\\patch'
split_hkcu       ([('create_key','HKCU','a')], [], [('create_key','HKCR','b')])
parsed_ops       [('create_key', 'HKEY_CURRENT_USER', 'Probe'),
                  ('set_value', 'HKEY_CURRENT_USER', 'Probe', 'Hex4',
                   b'\x01\x00\x00\x00', 'REG_BINARY'),
                  ('set_value', 'HKEY_CURRENT_USER', 'Probe', 'Ok',
                   'value', 'REG_SZ'),
                  ('create_key', 'HKEY_DYN_DATA', 'Legacy'),
                  ('set_value', 'HKEY_DYN_DATA', 'Legacy', 'Lost',
                   'x', 'REG_SZ')]
```

The `parsed_ops` probe is the most instructive one. Three different failure
modes appear in a nine-line input:

* `"Bad"=hex(b):01,02` (a malformed QWORD) vanished without any diagnostic;
* `"Hex4"=hex(4):01,00,00,00` was accepted but typed as `REG_BINARY`;
* the whole `[HKEY_DYN_DATA\Legacy]` section was accepted even though every one
  of its operations is doomed at apply time (`_normalize_root()` returns `None`
  for that root, so B1 turns the failure into a silent success).

A second probe (`probe2.py`) covers text decoding; its results are quoted in B9.

### 5.2 P0 defects

#### B1 — Registry operation failures are silently discarded
**Where:** `regfile.py:538-589` (returns `list[str]` of errors),
`regapply.py:211-213`, `:217-218`, `:221-223` (return value ignored),
`regapply.py:228` (`sys.exit(0)` unconditionally).
**Symptom:** access denied, invalid data, missing privileges — the patch still
exits with code `0` and the user believes it was applied.
**Fix:** `apply_operations()` returns an `ApplyReport` (counters plus
diagnostics); the loader exits `2` if any operation failed and logs each
failure; `--quiet` only suppresses the log, never the exit code.

#### B2 — UAC escalation outcome is never verified
**Where:** `regapply.py:143-162` (`run_as_admin` returns a bool), `:226` (result
ignored), `:228` (parent exits `0` immediately, never waits for the child).
**Symptom:** the user cancels the UAC prompt (`ShellExecuteW` returns 5,
`GetLastError()` = `ERROR_CANCELLED` 1223) — the patch exits `0` while the HKLM
part was never applied; a failing elevated child is equally invisible.
**Fix:** `ShellExecuteExW` with `SEE_MASK_NOCLOSEPROCESS`,
`WaitForSingleObject`, `GetExitCodeProcess`, then relay the child's exit code;
cancellation maps to exit `6`.

#### B3 — Own executable path is derived from `sys.argv[0]` plus `abspath`
**Where:** `regapply.py:150`, `:170`, `:199`.
**Symptom:** starting the patch from another directory with a relative path, or
via `PATH`, resolves to `<cwd>\patch.exe`: the overlay is not found (silent
`exit(0)` "nothing to do") and `%CD%` points to the wrong directory.
**Fix:** `selfpath.get_own_exe_path()` — absolute `argv[0]` if it exists, else
`GetModuleFileNameW(NULL, ...)` which is authoritative; never trust the CWD.

#### B4 — Non-atomic build, no protection against self-overwrite
**Where:** `pyreg2exe.py:162-167` (`open(output_path, "wb")` truncates first),
`:236-247` (no check that the output differs from the template and from the
input), no `flush`/`os.fsync`, no post-write verification.
**Symptom:** a crash or AV kill mid-write leaves a truncated/0-byte `.exe`;
`-o patch.reg` destroys the source; `-o` equal to the template destroys the
template; a template that already contains an overlay is accepted and produces
a double overlay.
**Fix:** compose the whole file in memory, write to `output.tmp-<pid>` in the
destination directory, `flush` + `os.fsync`, `os.replace`; reject equal paths;
reject a template that already carries an overlay; verify the written tail.

#### B5 — Silent data loss in the parser and in the loader
**Where:** `regfile.py:126-131`, `:138-143` (unknown root -> `continue`),
`:154-156` (`_parse_value_line` -> `None` -> `continue`), `:272-273`, `:278-279`,
`:288-300`, `:328-329`, `:337` (malformed value -> `None` -> dropped),
`:60`, `:64`, `:68`, `:84` (`errors="replace"`), `regapply.py:184-188`
(`except Exception: sys.exit(1)` without a message), `:190-191`
(`if not operations: sys.exit(0)`).
**Symptom:** partially applied patch with exit code `0`; corrupted Cyrillic text
in the registry instead of an error; no way to find out what was skipped.
**Fix:** diagnostics carry a 1-based line number; strict mode turns them into
`InputError` (exit `3` in the builder, `5` in the loader); decoding uses
`errors="strict"`; `--lenient` downgrades diagnostics to warnings.
#### B6 — Root prefix is matched without a token boundary
**Where:** `regfile.py:216-221` (`upper.startswith(root)`).
**Verified:** `HKEY_LOCAL_MACHINEX\Soft` -> root `HKEY_LOCAL_MACHINE`, path
`X\Soft`; `HKEY_CURRENT_USERS` -> root `HKEY_CURRENT_USER`, path `S`.
**Symptom:** the patch writes to a different key than the author intended —
silent misdirection, potentially destructive.
**Fix:** `RegRoot.parse()` requires the exact root token followed by `\` or the
end of the section string; anything else is `InputError`.

#### B7 — Root tables disagree with each other
**Where:** `regfile.py:205-214` accepts `HKEY_DYN_DATA`,
`HKEY_PERFORMANCE_DATA`, `HKDD`, `HKPD`; `:519-535` (`_normalize_root`) has no
mapping for them and returns `None`, which makes `:596-598`, `:610-612`,
`:645-647`, `:666-668` raise `ValueError` for every operation.
**Verified:** `_normalize_root("HKEY_DYN_DATA")` -> `None`,
`_normalize_root("HKPD")` -> `None`, while the parser happily emitted
`('create_key', 'HKEY_DYN_DATA', 'Legacy')` and
`('set_value', 'HKEY_DYN_DATA', 'Legacy', 'Lost', 'x', 'REG_SZ')`.
**Symptom:** the builder accepts the file and computes a mode, the loader fails
every such operation at runtime (and, because of B1, still reports success).
**Fix:** one `RegRoot` enum in `reg_types.py`; unsupported roots are rejected at
parse time with a line number.

#### B8 — Empty key path is accepted
**Where:** `regfile.py:599-600` (`_winreg_create_key` returns silently),
`:669-670` (`_winreg_delete_key` returns silently).
**Verified:** `_split_reg_path("HKLM")` -> `("HKLM", "")`, so
`[HKEY_LOCAL_MACHINE]` and `[-HKEY_CURRENT_USER]` are accepted and then do
nothing; a future "non-silent" implementation of the latter would recursively
delete a whole hive.
**Fix:** empty path is `InputError` at parse time (`empty key path`, with line
number).

#### B9 — Encoding detection is lossy and silently mangles text
**Where:** `regfile.py:59-68` (`errors="replace"` in all three BOM branches),
`:71-75` (BOM-less UTF-16LE detected only when bytes 1 and 3 are `0x00`),
`:78-84` (UTF-8 strict, then `cp1251` with `errors="replace"`).
**Correction of an earlier assumption:** UTF-16BE **is** supported
(`regfile.py:63`); the defect is the lossy fallback chain, not a missing branch.
**Verified (probe 2):**

| Input | Result |
|---|---|
| UTF-16LE ASCII text with an odd byte count (`b"W\0i\0n\0d\0o\0w\0s"`, 13 bytes) | `'W\x00i\x00n\x00d\x00o\x00w\x00s'` — decoded as UTF-8, text full of NULs, no error |
| UTF-16LE Cyrillic without BOM | `'\x1f\x04@\x048\x042\x045\x04B\x04'` — control-character garbage, no error |
| UTF-16LE with BOM and a lone high surrogate | `'\ufffd'` — silent replacement |
| cp1251 Cyrillic without BOM | `'Привет'` (fallback happens to work) |
| UTF-8 with BOM, UTF-16BE with BOM | `'Привет'` (correct) |

**Symptom:** mojibake and NUL-laden strings written into the registry with no
diagnostic; values silently truncated by the replacement character.
**Fix:** `encoding.py` with an explicit BOM table, strict decoding, an
odd-length check that raises, a stricter BOM-less heuristic (NUL share in
`data[1::2]` vs `data[0::2]`), and cp1251 available only through an explicit
`--encoding=cp1251` option.

#### B10 — Line continuation is not hex-aware
**Where:** `regfile.py:171-200`.
**Verified:** `["; note \\", '"A"="b"']` -> `['; note "A"="b"']`: the value line
is swallowed by the comment and lost. A quoted value that ends with a literal
backslash is glued to the next line in the same way.
**Symptom:** the line following any comment that ends with a backslash
disappears without a trace; string values are corrupted.
**Fix:** a line-assembler state machine: continuation is allowed only while
reading a `hex:` or `hex(n):` payload; inside a quoted string a backslash is
part of an escape sequence, never a continuation.
### 5.3 P1 defects

#### B11 — `hex(4)` (REG_DWORD in hex form) is written with the wrong type
**Where:** `regfile.py:284-323`; subtype `4` has no branch and falls through to
`:323` (`return name, raw_bytes, "REG_BINARY"`).
**Verified:** `"D"=hex(4):01,00,00,00` -> `('D', b'\x01\x00\x00\x00', 'REG_BINARY')`.
**Symptom:** the value type changes silently; consumers expect a DWORD and read
binary data; comparisons in policy or installer logic break.
**Fix:** complete subtype map `{0: NONE, 1: SZ, 2: EXPAND_SZ, 3: BINARY,
4: DWORD, 7: MULTI_SZ, b: QWORD}` with length validation (4 bytes for `hex(4)`,
8 for `hex(b)`) and an explicit error for unknown subtypes.

#### B12 — Missing length and range validation
**Where:** `regfile.py:268-273` (DWORD value parsed as an unbounded
`int(x, 16)`), `:276-281` (`hex(b)` with `len != 8` -> `None`),
`:302-312`, `:360-370` (odd-length UTF-16 payload -> `None`).
**Verified:** `"N"=dword:1234567890abcdef` -> `1311768467294899695` typed as
`REG_DWORD`; `"B"=hex(b):01,02` -> `None` (dropped).
**Symptom:** an out-of-range DWORD reaches `winreg.SetValueEx` and raises a
generic error, or an unrelated oversized integer is accepted; malformed QWORDs
vanish without a trace.
**Fix:** validators with diagnostics: DWORD `0 .. 0xFFFFFFFF` (1-8 hex digits
accepted, regedit emits 8), QWORD exactly 8 bytes, `hex(n)` payload even length.

#### B13 — Hex token parsing is lenient and inconsistent
**Where:** `regfile.py:340-357`: `int(part, 16)` accepts `0x0A` and `+1`, a
negative token raises on `bytearray.append` and discards the whole value,
`replace(" ", "")` leaves tabs, tokens are not required to be two digits, empty
tokens are skipped silently.
**Verified:** `"X"=hex:0x0A,0B` -> `b"\n\x0b"`; `"X"=hex:1,2` -> `b"\x01\x02"`.
**Symptom:** malformed input is accepted and reinterpreted instead of rejected;
tabs inside hex data cause a failure with no line information.
**Fix:** tokenizer requiring `^[0-9A-Fa-f]{2}$`, optional whitespace around the
separator, and an error with line number and byte offset otherwise.

#### B14 — Escapes and expansion semantics
**Where:** `regfile.py:260-263`, `:332-335` (only `\\` is unescaped; `\"` stays
literal), `:555-575` (expansion applied to value *names* and to every string
value, including plain `REG_SZ`; consequently `REG_EXPAND_SZ` is stored
pre-expanded and Windows will never re-expand it later — and in the elevated
pass `%APPDATA%` resolves to the administrator's profile, not the user's).
**Verified:** `"V"="a\"b"` -> `('V', 'a\\"b', 'REG_SZ')`.
**Fix:** correct unescaping (`\\` -> `\`, `\"` -> `"`); value names are never
expanded; the expansion policy is explicit and documented in §7.9, with
`--no-expand-env` for a fully portable `REG_EXPAND_SZ`.
#### B15 — `%CD%` resolves to the current directory, not to the patch directory
**Where:** `regfile.py:461-473`: `os.environ` is consulted before the `CD`
special case, so a `%CD%` inherited from the parent process wins;
`README.md:128-130` promises "the directory where `patch.exe` resides".
**Symptom:** values written by the patch depend on how it was launched; started
from `cmd.exe`, `%CD%` becomes the shell's working directory.
**Fix:** `expand.py` takes an explicit `base_dir`, resolves
`%CD%`/`<reg2exepath>` from it first, then falls back to the environment, and
the priority order is documented.

#### B16 — Mode byte is read but never used; mode derivation is duplicated
**Where:** `regapply.py:86` (mode read), `:173` (bound, then never referenced),
`:193-197` (the admin split is re-derived from the parsed operations),
`pyreg2exe.py:105-113` (docstring promises a 2-tuple, the function returns a
3-tuple), `:127` (`other_ops` are counted as HKLM for the mode decision).
**Symptom:** two independent sources of truth for one decision; changing one
silently desynchronises the other; the overlay carries a byte nobody reads.
**Fix:** `RegRoot.requires_admin` is the only source of truth; the mode byte is
kept for format compatibility and used as a cross-check (mismatch -> warning),
and `--hklm-only` remains the internal second-pass switch.

#### B17 — Constants and helpers are duplicated three times
**Where:** `pyreg2exe.py:46-51` and `regapply.py:45-55` (marker, sizes, mode
constants), `pyreg2exe.py:84-102` and `regapply.py:112-127` (identical
`load_regfile`), root lists in `regfile.py:205-214`, `:415-418`, `:523-534`,
overlay layout repeated in three docstrings.
**Symptom:** drift risk — a format change needs three coordinated edits; the
root lists already disagree (B7).
**Fix:** `overlay.py` owns the constants and the codec, `reg_types.py` owns the
roots; `load_regfile()` disappears because all imports become static.

#### B18 — No diagnostics, no log, degenerate exit codes
**Where:** `regapply.py` has no logging at all; `README.md:176` builds the
loader with `--windows-disable-console`, so `print` output and tracebacks are
invisible; `pyreg2exe.py:201-254` writes to `stderr` without an encoding guard
(when output is redirected, the ANSI code page raises `UnicodeEncodeError` on
non-ASCII paths); every failure uses exit code `1`.
**Symptom:** a misbehaving patch cannot be diagnosed in the field.
**Fix:** `logging` to `%TEMP%\pyreg2exe\<name>-<pid>.log` (always on, silent),
a console writer with `errors="backslashreplace"`, and distinct exit codes
(§6.5).

#### B19 — `resource_path()` only understands PyInstaller
**Where:** `pyreg2exe.py:58-63` (`sys._MEIPASS`, fallback
`dirname(abspath(__file__))`).
**Symptom:** under Nuitka onefile the template is looked up in the unpack
directory; this works only because Nuitka's default
`--file-reference-choice=runtime` makes `__file__` point at the unpacked copy —
an untested assumption (see B23).
**Fix:** `selfpath.py` resolves resources from `__compiled__.containing_dir`
(Nuitka 4.x), then `sys._MEIPASS`, then `dirname(__file__)`; verified by an
end-to-end builder test with a synthetic template (§10) and by the post-build
checklist (§8.4).

#### B20 — The input `.reg` is read twice
**Where:** `pyreg2exe.py:115-116` (`_read_reg_file`) and `:159-160` (second
`open` inside `build_exe`).
**Symptom:** TOCTOU — the bytes that were validated need not be the bytes that
are embedded (the file can be replaced in between); doubled I/O.
**Fix:** read once, hash it, embed exactly those bytes, log the hash and size.

### 5.4 P2 defects (build, documentation, tooling)

#### B21 — The CLI tool is built without a console
**Where:** `README.md:188` (`--windows-disable-console` for `pyreg2exe.exe`).
**Symptom:** `--help`, usage errors and `File not found` messages are invisible;
the builder appears to do nothing when it fails.
**Fix:** `--windows-console-mode=force` for the builder, `=disable` only for the
loader (§8.3).

#### B22 — The build recipe is not reproducible
**Where:** `README.md:172-196`: no `--include-module=regfile` (it relies on
Nuitka discovering the lazy `import regfile` inside a function), no compiler
pinning (`CC`/`CXX` or `--mingw64`), deprecated `--include-data-file`, a manual
`copy` step for the template, and `.gitignore` does not ignore the produced
`\regapply.exe`.
**Symptom:** builds depend on the machine: a different MinGW, or a missing
`regfile` module, produces a patch that fails only at runtime.
**Fix:** exact commands with compiler pinning (§8.3), `--include-data-files`, and
a `.gitignore` entry for the built template.

#### B23 — No automated tests; the self-path assumption is unverified
**Where:** the whole repository (no `tests/`, no CI, no smoke script).
**Symptom:** every defect above reached a release; nothing prevents a
regression; the Nuitka assumption behind B19 has never been executed.
**Fix:** a `tests/` `unittest` suite (§10), sandboxed HKCU registry tests, an
end-to-end builder test with a synthetic `MZ` template, and a documented
post-build checklist (§8.4).
### 5.5 Verified as correct (must not regress)

* `\\` unescaping in quoted values works for the common case.
* Multi-line hex values are joined correctly when no comments are involved.
* `hex(7)` with a double-NUL terminator decodes to the expected list.
* `@="..."` (default value), `"name"=-` (value deletion) and `[-HKEY_...]`
  (recursive key deletion) are parsed.
* `read_overlay()` rejects a wrong marker or an impossible size and never reads
  past EOF (`regapply.py:70-105`).
* No `winreg` handle leaks: every `CreateKeyEx`/`OpenKey` result is closed.
* Appending the overlay is safe with Nuitka 4.2.1 onefile (the payload is a
  linker-embedded blob, `OnefileBootstrap.c:177-192`).
* Mode computation (`0x01`/`0x02`/`0x03`) matches the documented semantics, even
  though the loader ignores the byte (B16).
* UTF-16BE with BOM, UTF-8 with BOM and cp1251 without BOM are decoded
  correctly by the current fallback chain.

### 5.6 Observations outside the defect list

* **O1 — Registry view.** There is no support for `KEY_WOW64_32KEY` or
  `KEY_WOW64_64KEY`: a patch always writes to the view of the calling process.
  Stage 2 adds an opt-in `--view={default,32,64}`; Stage 3 exposes
  `RegOptions.wow64_view`.
* **O2 — Single-pass expansion.** A value produced by expansion is not expanded
  again. `ExpandEnvironmentStringsW` behaves the same way, so this is documented
  as-is rather than changed.
* **O3 — Value names are not expanded by Windows.** The current expansion of
  names is removed as part of B14.
* **O4 — Documentation drift.** `_REG2EXE_MARKERS` contains
  `<reg2exeprogspath>` (`regfile.py:436`, verified: expands to
  `C:\Program Files`) but the README table (`README.md:116-126`) omits it.
* **O5 — Signing.** Any appended overlay invalidates Authenticode; signing must
  be applied to the finished patch, never to the template.
* **O6 — Legacy virtual hives.** `HKEY_PERFORMANCE_DATA` and `HKEY_DYN_DATA`
  have no meaningful write support and are rejected by design (B7).
* **O7 — Non-str values.** Expansion only touches `str` data; `REG_MULTI_SZ`
  list items are handled (`regfile.py:562-567`) but `bytes` payloads cannot
  contain variables — correct, and worth stating in the README.

---

## 6. Target architecture (proposed — awaiting approval)

### 6.1 Principles

1. **One implementation of every rule.** Constants, roots, subtype maps, the
   overlay codec and the expansion policy live in exactly one module each.
2. **Pure core, thin edges.** `parser`, `encoding`, `expand`, `overlay` and
   `reg_types` must not import `winreg` or `ctypes`: they are testable anywhere
   and are the direct reference for the C port.
3. **Static imports only.** No `importlib.util.spec_from_file_location` tricks,
   so Nuitka can resolve the whole graph by itself.
4. **Every failure is locatable and typed.** Diagnostics carry a line number,
   exceptions map to distinct exit codes, and nothing is swallowed.
5. **Frozen surfaces stay frozen.** Overlay format, CLI keys, entry file names
   and the Nuitka build keep working (§1.1).

### 6.2 Package layout

```
pyreg2exe/                      repository root
|-- pyreg2exe.py                entry point: builder CLI (thin wrapper, ~40 lines)
|-- regapply.py                 entry point: loader (thin wrapper, ~40 lines)
|-- regfile.py                  DEPRECATED compatibility shim (legacy tuple API)
|-- pyreg2core/                 implementation package
|   |-- __init__.py             version, public re-exports
|   |-- errors.py               exception hierarchy + exit codes
|   |-- reg_types.py            RegRoot, RegType, OpKind, Operation, Diagnostic
|   |-- encoding.py             bytes -> text: BOM table, strict, heuristic
|   |-- parser.py               text -> ParseResult (operations + diagnostics)
|   |-- expand.py               VariableExpander (%VAR%, %CD%, <reg2exe...>)
|   |-- registry.py             RegistryApplier (the only module using winreg)
|   |-- overlay.py              Mode, Overlay, read/verify/encode
|   |-- selfpath.py             own executable path + resource directory
|   |-- elevate.py              is_admin(), run_elevated_and_wait()
|   |-- diag.py                 logging setup, safe console writer
|   |-- builder.py              build_patch() pipeline
|   `-- applier.py              apply_patch() pipeline (route, elevate, aggregate)
|-- tests/                      unittest suite (§10)
|-- examples/                   unchanged
|-- c_registry_module/          Stage 3 (§9)
`-- README.md, LICENSE, PROJECT_CONTEXT.md, .gitignore
```

**Why `pyreg2core` and not a `pyreg2exe/` package:** a package directory named
`pyreg2exe` sitting next to `pyreg2exe.py` shadows the module (directory finders
win over module finders), which makes `import pyreg2exe` ambiguous for anyone who
imported the project as a library, and confuses static analysers and Nuitka. A
distinct package name removes the hazard while keeping both entry files exactly
where they are today.

Entry scripts stay tiny on purpose — they remain the Nuitka build targets and
must never contain logic that the test suite cannot import:

```python
# pyreg2exe.py (target shape)
from pyreg2core.cli import main_builder
raise SystemExit(main_builder())

# regapply.py (target shape)
from pyreg2core.cli import main_applier
raise SystemExit(main_applier())
```
### 6.3 Layers and allowed dependencies

```
entry scripts  ->  cli
cli            ->  builder, applier, errors, diag
builder        ->  encoding, parser, expand, overlay, errors, diag
applier        ->  overlay, parser, registry, elevate, selfpath, expand, errors, diag
registry       ->  reg_types, expand, errors          (winreg)
elevate        ->  selfpath, errors                   (ctypes, shell32)
selfpath       ->  errors                             (ctypes, kernel32)
parser         ->  reg_types, encoding, errors
overlay        ->  reg_types, errors                  (no winreg, no ctypes)
```

Dependencies point downward only; there are no cycles and no library module
imports an entry script. The lower six modules are platform-independent, which
is what the test suite relies on (§10.2).

### 6.4 Data model

```python
class RegRoot(enum.Enum):
    HKCR = ("HKEY_CLASSES_ROOT",   False)
    HKCU = ("HKEY_CURRENT_USER",   False)
    HKLM = ("HKEY_LOCAL_MACHINE",  True)
    HKU  = ("HKEY_USERS",          True)
    HKCC = ("HKEY_CURRENT_CONFIG", True)

    @property
    def requires_admin(self) -> bool: ...
    @classmethod
    def parse(cls, token: str, line: int) -> "RegRoot": ...   # exact token only

class RegType(enum.Enum):
    NONE, SZ, EXPAND_SZ, BINARY, DWORD, QWORD, MULTI_SZ
    # .subtype -> int (the number used in hex(N)); .reg_name -> "REG_*"

class OpKind(enum.Enum):
    CREATE_KEY, SET_VALUE, DELETE_VALUE, DELETE_KEY

@dataclasses.dataclass(frozen=True, slots=True)
class Operation:
    kind: OpKind
    root: RegRoot
    path: str
    name: str | None = None
    value: str | bytes | int | list[str] | None = None
    vtype: RegType | None = None
    line: int = 0                       # 1-based source line

@dataclasses.dataclass(frozen=True, slots=True)
class Diagnostic:
    severity: str                       # "error" | "warning"
    line: int
    message: str

@dataclasses.dataclass
class ParseResult:
    operations: list[Operation]
    diagnostics: list[Diagnostic]
    mode: int                           # 0x01 | 0x02 | 0x03
    has_errors: bool

@dataclasses.dataclass
class ApplyReport:
    keys_created: int = 0
    keys_deleted: int = 0
    values_written: int = 0
    values_deleted: int = 0
    diagnostics: list[Diagnostic] = field(default_factory=list)

    @property
    def failed(self) -> bool: ...
```

The legacy tuple format `("set_value", root_str, path, name, value, type_str)`
survives only inside the `regfile.py` shim (§6.8).
### 6.5 Error model and exit codes

```python
class PyregError(Exception):
    exit_code = 7
class UsageError(PyregError):      exit_code = 2   # argparse already uses 2
class InputError(PyregError):      exit_code = 3   # unreadable/invalid .reg, bad template
class BuildError(PyregError):      exit_code = 4   # cannot write the output
class OverlayError(PyregError):    exit_code = 3   # corrupt/unsupported overlay
class RegistryError(PyregError):   exit_code = 2   # operation failed
class ElevationError(PyregError):  exit_code = 6   # UAC declined or failed
```

| Binary | Code | Meaning |
|---|---|---|
| `pyreg2exe.exe` | 0 | patch built |
| | 2 | bad command line (argparse) |
| | 3 | input/template problem: missing file, invalid `.reg`, template already patched |
| | 4 | output problem: exists without `--force`, same path as input or template, write error |
| | 7 | unexpected internal error |
| `patch.exe` | 0 | applied (also: no overlay, i.e. plain template) |
| | 2 | one or more registry operations failed |
| | 3 | overlay corrupt or unsupported |
| | 5 | embedded `.reg` invalid |
| | 6 | elevation declined or failed |
| | 7 | internal error |

Backward compatibility: the loader currently returns only `0`/`1`, so any
`if errorlevel 1` check keeps working. The builder's generic `1` becomes `3`/`4`;
this is an intentional, documented change (README Changelog 1.1.0).

Exit codes are defined once, in `errors.py`, and every entry point is a thin
`try/except PyregError` wrapper that maps the exception to
`raise SystemExit(exc.exit_code)` after writing the log — no entry point owns a
numeric literal.
### 6.6 Pipeline: builder

1. Read the input **once** (`read_bytes`), hash it, decode, parse.
2. Strict mode: any `error` diagnostic -> `InputError(3)`.
3. Compute `mode` from `RegRoot.requires_admin` over the operation list.
4. Read the template once; validate `MZ` and `PE\0\0`; reject a template that
   already ends with a `PYREGEND` overlay; reject `output == input` and
   `output == template` (compared as resolved absolute paths,
   case-insensitively on Windows).
5. Compose the output in memory and write it atomically:
   `output.tmp-<pid>` -> `flush` -> `os.fsync` -> `os.replace`.
6. Re-open the output and verify the tail marker, the mode byte, the size, and
   that the embedded bytes equal the hashed input.
7. `--dry-run` stops after step 3 and prints the plan; exit `0`.

### 6.7 Pipeline: applier (loader)

1. `selfpath.get_own_exe_path()`; `overlay.read_and_verify(path)`; no overlay ->
   `exit(0)` (plain template, behaviour unchanged).
2. Decode and parse the embedded bytes; errors -> `exit(5)`.
3. Route by `root.requires_admin`; cross-check against the mode byte and log a
   warning on mismatch (B16).
4. Apply non-admin operations in-process.
5. Admin operations: if `is_admin()` -> apply; otherwise
   `elevate.run_elevated_and_wait(["--hklm-only"])` and relay the child's exit
   code; UAC cancelled -> `exit(6)`.
6. Aggregate: `0` if everything was applied, `2` if anything failed; the log is
   always written to `%TEMP%\pyreg2exe\patch-<pid>.log`.

### 6.8 Compatibility shim `regfile.py`

The file stays in the repository (~80 lines) and maps the legacy API onto the new
core, so external scripts and downstream packaging keep working:

| Legacy symbol | New implementation |
|---|---|
| `_read_reg_file`, `_read_reg_content_from_bytes` | `encoding.decode_bytes` |
| `_parse_reg_file` | `parser.parse_reg_text`, converted back to legacy tuples |
| `_parse_value_line`, `_parse_hex_bytes`, `_decode_utf16_le*` | wrappers over `parser` internals |
| `_split_reg_path` | `RegRoot.parse`, same `ValueError` contract |
| `split_operations_by_root` | tuple-based helper implemented via `RegRoot` |
| `_expand_variables` | `expand.VariableExpander` |
| `apply_operations`, `_winreg_*`, `_normalize_root`, `_get_type_map` | wrappers over `registry.RegistryApplier` |

Deprecation is signalled by `__deprecated__ = True`, a module docstring note and
the README changelog — not by a `DeprecationWarning`, which would break users
running with `-W error`.

### 6.9 Public behaviour changes (README 1.1.0 changelog)

* Invalid `.reg` constructs fail loudly with a line number; `--lenient`
  downgrades them to warnings.
* `hex(4)` produces `REG_DWORD`; malformed `hex(n)`/`hex(b)` payloads are errors.
* `%CD%` always resolves to the patch directory.
* `\"` escapes are decoded; value names are never expanded.
* The loader writes a log file and returns distinct exit codes.
* `pyreg2exe.exe` keeps its console; the loader stays windowless.
* New options: `-v/--verbose`, `--quiet`, `--dry-run`, `--lenient`,
  `--no-expand-env`, `--version`, `--view={default,32,64}`.

---

## 7. Normative `.reg` semantics (shared by the Python core and the C module)

This section is the contract. Where the current implementation differs, the
current behaviour is a defect (§5).

### 7.1 Conformance language

* **MUST** — a conforming implementation rejects the input.
* **MUST NOT** — never performed.
* **SHOULD** — may be relaxed only by an explicit, documented option.

Diagnostics are 1-based line numbers referring to the decoded text, counting
physical lines as written in the file (a joined continuation reports the line
where the value started).

### 7.2 Byte-level decoding (MUST)

Detection order, first match wins:

| Condition | Action |
|---|---|
| starts with `FF FE` | UTF-16LE, skip BOM |
| starts with `FE FF` | UTF-16BE, skip BOM |
| starts with `EF BB BF` | UTF-8, skip BOM |
| empty input | empty text, zero operations, exit 0 |
| no BOM, byte length odd | **error** `unable to determine encoding (odd byte length)` |
| no BOM, ≥ 4 bytes, `data[1::2]` are all `0x00` | UTF-16LE |
| no BOM, otherwise | UTF-8, `errors="strict"`; on failure **error** |

* `errors="replace"` and any other lossy decoding are **MUST NOT**.
* Windows-1251 is available only via the explicit `--encoding=cp1251` option
  (and its C counterpart `RegOptions.encoding`); without it such a file is an
  error, not silently transcoded.
* Decoded text is normalised for line splitting only; value data is **not**
  re-encoded except for UTF-16LE types, which are written as UTF-16LE.

C parity: `reg_decode_bytes(data, size, options, &text, result)` returns
`REG_ERR_ENCODING` with `result->line_number == 0` on the two error paths.

### 7.3 Line structure and continuations (MUST)

1. Lines end with `CRLF`, `LF` or `CR`; all three are accepted.
2. Leading and trailing whitespace is insignificant outside quoted strings.
3. Empty lines are ignored.
4. A line whose first non-blank character is `;` is a comment and is ignored.
   A comment never continues onto the next line, even if it ends with `\`.
5. Continuation is allowed **only** inside a `hex:`/`hex(N):` payload: a line
   whose last non-blank character is `\` is concatenated with the next line,
   the backslash removed, and the next line is stripped before concatenation.
6. Inside a quoted string, `\` starts an escape sequence (§7.7); a trailing
   backslash that is the last character before the closing quote is an escape
   of the quote and **MUST NOT** be treated as a continuation.
7. An unterminated quoted string is an error.
8. A backslash at the end of a non-hex line is an error in strict mode
   (`unexpected line continuation`), a warning in lenient mode.

### 7.4 Sections (MUST)

| Form | Meaning |
|---|---|
| `[ROOT\sub\key]` | create the key and make it current |
| `[-ROOT\sub\key]` | delete the key recursively; no key becomes current |
| `[ROOT]` or `[ROOT\]` | **error** `empty key path` |
| `[UNKNOWN\...]` | **error** `unknown registry root` |
| `[...]` without a trailing `]` | **error** `unterminated section header` |
| value line before any section | **error** `value outside of a section` |

Root tokens (case-insensitive, exact match, followed by `\` or end):

`HKEY_CLASSES_ROOT`/`HKCR`, `HKEY_CURRENT_USER`/`HKCU`,
`HKEY_LOCAL_MACHINE`/`HKLM`, `HKEY_USERS`/`HKU`,
`HKEY_CURRENT_CONFIG`/`HKCC`.

Rejected roots: `HKEY_DYN_DATA`, `HKEY_PERFORMANCE_DATA`, `HKDD`, `HKPD` and any
other token (B7, O6). `HKEY_CURRENT_USERX` is a rejected root, not `HKEU` plus a
path (B6).

The header lines `Windows Registry Editor Version 5.00` and `REGEDIT4`
(case-insensitive) are accepted before the first section and ignored. A header
appearing after the first section is a warning.

### 7.5 Value lines and types (MUST)

| Form | Type | Stored data |
|---|---|---|
| `"name"="text"` | `REG_SZ` | text as decoded |
| `@="text"` | `REG_SZ` | default value (empty name) |
| `"name"=-` | — | delete the value |
| `@=-` | — | delete the default value |
| `"name"=dword:XXXXXXXX` | `REG_DWORD` | unsigned 32-bit |
| `"name"=hex:01,02` | `REG_BINARY` | raw bytes |
| `"name"=hex(0):01,02` | `REG_NONE` | raw bytes |
| `"name"=hex(1):..` | `REG_SZ` | UTF-16LE text |
| `"name"=hex(2):..` | `REG_EXPAND_SZ` | UTF-16LE text, stored literal |
| `"name"=hex(3):..` | `REG_BINARY` | raw bytes |
| `"name"=hex(4):..` | `REG_DWORD` | exactly 4 bytes, little-endian |
| `"name"=hex(7):..` | `REG_MULTI_SZ` | UTF-16LE, NUL-separated, double-NUL terminated |
| `"name"=hex(b):..` | `REG_QWORD` | exactly 8 bytes, little-endian |
| `"name"=hex(N):..`, other `N` | **error** | `unsupported value type hex(N)` |
| `name=...` (unquoted) | **error** | `value name must be quoted` |
| line without `=` | **error** | `expected a value assignment` |

Rules:

* `dword:` accepts 1–8 hexadecimal digits and **MUST** be `<= 0xFFFFFFFF`
  (`dword:100000000` is an error, B12); the canonical output form is 8 digits.
* `hex(4)` **MUST** have exactly 4 bytes, `hex(b)` exactly 8, `hex(1)`, `hex(2)`,
  `hex(7)` an even number of bytes (two-byte code units), B11/B12.
* Trailing NULs of `hex(1)`/`hex(2)` are stripped when decoding to text;
  `hex(7)` strips exactly one terminating `\0\0` (or a single trailing `\0` in
  files written by older tools) and preserves empty strings inside the list.
* `REG_MULTI_SZ` with an empty payload is an empty list, written back as `\0\0`.
* Value names may not contain `\` (it is reserved for paths) — error.
* Duplicate assignment to the same name in the same key is allowed and the last
  one wins (file order, §7.8), matching `regedit`.
### 7.6 Hex payload grammar (MUST)

```
payload  := [ byte { "," byte } ]        ; empty payload allowed -> zero-length data
byte     := hexdigit hexdigit
hexdigit := "0".."9" | "A".."F" | "a".."f"
```

* Whitespace (space, tab) around a separator is allowed and ignored.
* Whitespace **inside** a byte token (`0 A`) is an error.
* `0x0A`, `+1`, `1` and `-1` are errors (B13).
* A trailing comma before a continuation is allowed: `...,0A,\`.
* A trailing comma at the end of the payload is an error
  (`trailing comma in hex data`).
* A token longer than two digits, or a value above `0xFF`, is an error, not a
  silent `None` (B5).

### 7.7 String escapes (MUST)

Inside quoted names and quoted `REG_SZ` data:

| Sequence | Result |
|---|---|
| `\\` | `\` |
| `\"` | `"` |
| `\` followed by anything else | **error** `invalid escape sequence` (lenient: kept verbatim) |

No other escapes exist in the format (no `\n`, `\t`, no `\uXXXX`). The current
implementation decodes only `\\` and leaves `\"` in the data (B14).

### 7.8 Deletion and ordering (MUST)

1. Operations are applied strictly in file order.
2. A section emits exactly one `create_key` operation for its own path, before
   the values of that section.
3. `[-KEY]` emits `delete_key` (recursive) and does **not** become the current
   section; value lines that follow it are an error
   (`value outside of a section`).
4. Deleting a missing value or a missing key is a success (idempotent).
5. Setting a value creates the key chain as needed.
6. Writing a value with `@` (empty name) writes the default value.
7. A later `create_key` under a previously deleted subtree recreates the whole
   chain.

### 7.9 Variable expansion (SHOULD, tool-specific extension)

Expansion is a `pyreg2exe` feature, not part of the `.reg` format, and applies to
the **data** of string values only (`REG_SZ`, `REG_EXPAND_SZ`, each item of
`REG_MULTI_SZ`); never to names, never to `bytes` payloads.

Priority order for a token:

1. Tool markers `<reg2exe...>` and `%CD%` resolve from the explicit
   `base_dir` (the directory of the patch executable), §7.9.1.
2. Otherwise `%VAR%` resolves from the process environment (by default on,
   `--no-expand-env` disables).
3. Unresolved tokens are kept verbatim (no error, no empty replacement).

`REG_EXPAND_SZ` data is written **literal** after step 1: environment variables
that survived step 2 stay in the value so that Windows expands them per user at
read time. Pre-expanding them (current behaviour, B14) makes the value point at
the applying account's profile and breaks the type's purpose.

`%CD%` and `<reg2exepath>` **MUST** resolve to the patch directory regardless of
the process working directory or of a `%CD%` variable in the environment (B15).

#### 7.9.1 Marker table

| Marker | Value |
|---|---|
| `<reg2exepath>` | directory of the patch executable (`%CD%` in old documentation) |
| `<reg2exewinpath>` | `%WINDIR%` |
| `<reg2exesyspath>` | `%SYSTEMROOT%` |
| `<reg2exetemppath>` | `%TEMP%` |
| `<reg2exeprogspath>` | `%PROGRAMFILES%` |
| `<reg2exeappdatapath>` | `%APPDATA%` |
| `<reg2exelocalappdatapath>` | `%LOCALAPPDATA%` |
| `<reg2exeusername>` | `%USERNAME%` |
| `<reg2execomputername>` | `%COMPUTERNAME%` |
| `<reg2exeprofilespath>` | `%USERPROFILE%` |

Markers are case-insensitive. `<reg2exeprogspath>` is missing from the current
README table (O4) and must be added.

### 7.10 Applying operations (MUST)

* Value type codes are passed through unchanged (`REG_SZ` 1, `REG_EXPAND_SZ` 2,
  `REG_BINARY` 3, `REG_DWORD` 4, `REG_MULTI_SZ` 7, `REG_QWORD` 11, `REG_NONE` 0).
* Keys are opened with the minimum required access:
  `KEY_WRITE` for create/set, `KEY_SET_VALUE` for value deletion,
  `KEY_READ|KEY_WRITE` for recursive deletion (enumerate then delete).
* Handles are closed on every path, including errors.
* A failing operation does not abort the run: it is recorded, the remaining
  operations continue, and the aggregate result is a failure (exit `2`).
  `--stop-on-error` aborts at the first failure (Stage 2 addition, optional).
* `ERROR_FILE_NOT_FOUND` / `ERROR_PATH_NOT_FOUND` during deletion are successes.
* `ERROR_ACCESS_DENIED` is reported as an access error, never silently ignored.
* An unknown root or value type at apply time is an internal error
  (the parser must have rejected it already).

### 7.11 Summary of constructs accepted and rejected

| Construct | Status |
|---|---|
| `; comment`, `Windows Registry Editor Version 5.00`, `REGEDIT4` | accepted, ignored |
| `[HKLM\...]`, `[-HKCU\...]`, `@=`, `"n"=-` | accepted |
| `hex:`, `hex(0..4)`, `hex(7)`, `hex(b)` | accepted |
| `hex(5)`, `hex(6)`, `hex(8)`, `hex(a)`, ... | rejected |
| `HKEY_DYN_DATA`, `HKEY_PERFORMANCE_DATA`, `HKDD`, `HKPD` | rejected |
| `HKLM`, `HKEY_LOCAL_MACHINE\` (empty path) | rejected |
| unquoted value name, unquoted `=1` (non-string) | rejected |
| `0x0A` / `1` / `+1` hex tokens, odd-length UTF-16 payload | rejected |
| `dword:` above `0xFFFFFFFF`, `hex(4)` with 3 or 5 bytes | rejected |
| `\"` in quoted data/names | accepted, decoded to `"` |
| trailing `\` on a comment line | no continuation (B10) |

---

## 8. Build and release recipe (Nuitka)

This is the authoritative recipe. `README.md` must be brought in line with it
(B21, B22).

### 8.1 Prerequisites

| Requirement | Detail |
|---|---|
| Python | 3.13 x64 (any CPython 3.x supported by Nuitka works, must match the architecture of the compiler) |
| Nuitka | 4.2.1 or newer, `pip install nuitka` |
| `zstandard` | required for `--onefile` compression |
| C compiler | either `C:\VSS\mingw64` (GCC 16.2.0, UCRT) or Nuitka's own WinLibs download (`--assume-yes-for-downloads`) |
| Icon | `regapply.ico` (optional; drop the flag if the file is absent or rejected) |
| Order | `regapply.exe` **must** exist in the repository root before the builder is compiled, because it is embedded as a data file |

### 8.2 Why `CC`/`CXX` is the supported way to pin the compiler

* `nuitka/build/SconsUtils.py:166-210` (`_prepareFromEnvironmentVar`) reads
  `CC`/`CXX` (and the `NUITKA_CC` variants), prepends the compiler's directory
  to `PATH` and switches the backend into `mingw_mode`.
* `nuitka/build/SconsCompilerSettings.py:205` is the minimum GCC version check
  (11.2) and `:325-380` (`decideArchMismatch`) accepts a
  `x86_64-w64-mingw32`-prefixed GCC with an x64 CPython. GCC 16.2.0 satisfies
  both.
* Setting the variables is therefore reproducible and does not depend on
  `PATH` order, unlike naming `gcc.exe` in `--mingw64` (which is a boolean flag,
  not a path).

### 8.3 Commands

Run from the repository root (`C:\pyreg2exe`). PowerShell syntax.

**Step 1 — template (loader, windowless):**

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

**Step 2 — builder (console kept, B21):**

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

* `--include-data-files=SRC=DEST` is the current spelling;
  `--include-data-file` is deprecated and must not be used in the README.
* **Stage 2 finding — commands corrected:** `--mingw64` was removed from both
  commands above. Nuitka 4.2.1 refuses that switch on Python 3.13+
  (`FATAL: cannot use '--mingw64' on Python version 3.13 or higher`) and
  ignores an external MinGW GCC (`... is being ignored, Nuitka is very
  dependent on the precise one`), falling back to its bundled **zig** backend.
  `--include-module=regfile` was removed as well (§8.6): both entry points
  import `pyreg2core` statically. Both artefacts were built successfully with
  the commands above (loader 7,985,664 B, builder 14,128,640 B; SHA-256 in
  §13) and verified on this machine. For a MinGW- or MSVC-built binary, upgrade
  Nuitka (4.3.x) or pass `--msvc=latest`.
* `--windows-console-mode={force,disable,attach,hide}` replaces the removed
  `--windows-disable-console`; `disable` is correct **only** for the loader.
* `SRC` in `--include-data-files` is resolved relative to the build directory,
  so the command must be executed from the repository root after step 1.
* The first build downloads/extracts WinLibs into `%LOCALAPPDATA%\Nuitka` and can
  take 5–15 minutes; incremental builds take 1–3 minutes.

### 8.4 Verification checklist (must pass before a release)

1. Artefacts exist and have plausible sizes (onefile with CPython 3.13:
   roughly 7–12 MB for the loader; the builder is larger because it embeds the
   loader and its own code):

   ```powershell
   Get-ChildItem .\dist\regapply.exe, .\dist\pyreg2exe.exe | Select-Object Name, Length
   ```

2. The builder keeps its console and prints help:

   ```powershell
   .\dist\pyreg2exe.exe --help
   ```

3. Build all three examples and inspect the overlay tail
   (`[size:8LE][mode:1][b"PYREGEND"]`):

   ```powershell
   .\dist\pyreg2exe.exe .\examples\cu.reg    -o "$env:TEMP\cu_patch.exe"    --force
   .\dist\pyreg2exe.exe .\examples\lm.reg    -o "$env:TEMP\lm_patch.exe"    --force
   .\dist\pyreg2exe.exe .\examples\mixed.reg -o "$env:TEMP\mixed_patch.exe" --force

   python -c "import sys;d=open(sys.argv[1],'rb').read()[-17:];print(sys.argv[1],'size',int.from_bytes(d[:8],'little'),'mode',d[8],'marker',d[9:])" "$env:TEMP\cu_patch.exe"
   ```

   Expected mode bytes: `cu.reg` -> `2`, `lm.reg` -> `1`, `mixed.reg` -> `3`.

4. Apply the HKCU patch and verify the result:

   ```powershell
   & "$env:TEMP\cu_patch.exe"
   reg query "HKCU\TestPyreg2exe_CU"
   reg delete "HKCU\TestPyreg2exe_CU" /f
   ```

   `Name` must be `VKRadeon` and `Path` must be `<directory of cu_patch.exe>\data`
   (this is the `%CD%` check for B15).

5. Re-run the builder without `--force` on an existing output: it must fail with
   a non-zero code and a visible message (not silently).

6. `lm_patch.exe` and `mixed_patch.exe` may only be run in an elevated shell;
   they must either apply successfully or fail with exit code `6`/`2`, never
   with `0` (B1, B2).

7. Clean up: `Remove-Item "$env:TEMP\*_patch.exe"`.
### 8.5 Troubleshooting

| Symptom | Cause | Action |
|---|---|---|
| `Scons: *** Unsupported compiler` / version error | GCC older than 11.2 or a 32-bit compiler with x64 Python | unset `CC`/`CXX` and let Nuitka download WinLibs, or use `--msvc=latest` |
| GCC 16 rejected by a Nuitka version that predates it | compiler newer than the supported range | unset `CC`/`CXX` (WinLibs fallback) or upgrade Nuitka (needs consent) |
| `--onefile` output is unexpectedly large (>40 MB) | `--standalone` leftovers in `dist\` or an extra module pulled in | clean `dist\` and `build\`, rebuild |
| Built loader exits `2` with no console output | Nuitka bootstrap fatal error (`OnefileBootstrap.c:fatalError*`) | rebuild the loader with `--windows-console-mode=force` temporarily and read the message |
| Patch starts but never applies anything | overlay not found: wrong self path (B3/B19) | check that the patch was produced from the freshly built template, run it by absolute path, inspect the log file |
| Icon rejected | `.ico` contains unsupported sub-formats (1.45 MB source file) | drop `--windows-icon-from-ico` |
| `UnicodeEncodeError` from the builder | redirected stdout on a non-UTF-8 code page (B18) | fixed in Stage 2; meanwhile use `PYTHONIOENCODING=utf-8` |

### 8.6 Deltas required after Stage 2

* `--include-module=regfile` becomes unnecessary: entry points import
  `pyreg2core` statically, so Nuitka resolves everything itself. Add
  `--include-module=pyreg2core` only if a lazy import is reintroduced — the
  design forbids it (§6.1).
* `--include-data-files=regapply.exe=regapply.exe` stays unchanged.
* `README.md` must be rewritten to the commands above, with the note that
  `pyreg2exe.exe` keeps a console and only `regapply.exe` is windowless.
* `.gitignore` gains `regapply.exe` (the built template) in addition to
  `build/`, `dist/`, which are already ignored.
* Both artefacts are rebuilt once after Stage 2 and the §8.4 checklist is
  executed again; the results are recorded in the Stage 2 report.

---

## 9. C11 WinAPI registry module (`c_registry_module/`)

### 9.1 Scope

A drop-in, dependency-free Windows library that reimplements the
`pyreg2core` behaviour from §7 so that the registry engine can be embedded in a
native GUI or service. It is **not** a wrapper around the Python code and has no
Python dependency.

In scope: `.reg` decoding/parsing, operation planning, applying to the registry,
overlay encode/decode, elevation helper, self-test runner.

Out of scope: the CLI, `.ico` handling, logging sinks (the caller owns logging
through a callback), localization.

### 9.2 Files

```
c_registry_module/
|-- win_reg_core.h        public API (the only header a consumer includes)
|-- win_reg_parse.h       internal: plan structures, parser entry points
|-- win_reg_core.c        apply/create/delete, status mapping, registry view
|-- win_reg_parse.c       decoding, lexer, parser, plan lifetime
|-- win_reg_overlay.c     overlay read/verify/write (atomic temp + replace)
|-- win_reg_elevate.c     is_admin, ShellExecuteExW + wait + exit code
|-- test_runner.c         self-tests, wmain, sandboxed HKCU keys
|-- build.bat             gcc driver (mingw64)
|-- Makefile              optional, same flags
`-- MODULE_CONTEXT.md     module-level documentation and test report
```

Every `.c` includes `win_reg_core.h`; internal cross-references use
`win_reg_parse.h`. No translation unit defines a non-`static` symbol that is not
declared in a header.

### 9.3 Public API (target `win_reg_core.h`)

```c
#ifndef WIN_REG_CORE_H
#define WIN_REG_CORE_H

#include <windows.h>
#include <stddef.h>

#ifdef WIN_REG_CORE_BUILD_DLL
#define WRC_API __declspec(dllexport)
#else
#define WRC_API
#endif

#ifdef __cplusplus
extern "C" {
#endif

typedef enum RegStatus {
    REG_OK = 0,
    REG_ERR_ARGUMENT,      /* NULL pointer, bad size, unknown option        */
    REG_ERR_MEMORY,        /* allocation failure                            */
    REG_ERR_IO,            /* file open/read/write failure                  */
    REG_ERR_ENCODING,      /* undecodable text                              */
    REG_ERR_PARSE,         /* syntax error (see result->line_number)        */
    REG_ERR_ROOT,          /* unknown/unsupported root                      */
    REG_ERR_VALUE_TYPE,    /* unsupported hex(N) subtype                    */
    REG_ERR_VALUE_DATA,    /* malformed/invalid payload                     */
    REG_ERR_ACCESS,        /* ERROR_ACCESS_DENIED and friends               */
    REG_ERR_WIN32,         /* other Win32 failure (win32_error is set)      */
    REG_ERR_OVERLAY,       /* overlay missing/corrupt/already present       */
    REG_ERR_ELEVATION      /* UAC declined or the elevated child failed     */
} RegStatus;

typedef enum RegLogLevel {
    REG_LOG_ERROR = 1, REG_LOG_WARNING = 2, REG_LOG_INFO = 3, REG_LOG_DEBUG = 4
} RegLogLevel;

typedef void (__cdecl *RegLogFn)(RegLogLevel level, const wchar_t *message,
                                 void *user);

typedef struct RegOptions {
    size_t struct_size;        /* caller sets sizeof(RegOptions)        */
    const wchar_t *base_dir;   /* patch directory for %CD%              */
    RegLogFn log_fn;           /* optional                              */
    void *log_user;            /* passed back to log_fn                 */
    DWORD wow64_view;          /* 0 | KEY_WOW64_32KEY | KEY_WOW64_64KEY */
    BOOL dry_run;              /* count only, never touch the registry  */
    BOOL lenient;              /* downgrade diagnostics to warnings     */
    BOOL expand_env;           /* substitute %VAR% from the environment */
    int forced_encoding;       /* 0 = auto, else 1251/1200/1201/65001   */
} RegOptions;

typedef struct RegResult {
    RegStatus status;
    unsigned int line_number;  /* 1-based, 0 when not applicable       */
    DWORD win32_error;         /* GetLastError() at the failure        */
    unsigned int keys_created;
    unsigned int keys_deleted;
    unsigned int values_written;
    unsigned int values_deleted;
    unsigned int ops_skipped;  /* lenient mode only                    */
    unsigned int diagnostics;  /* number of reported diagnostics       */
    wchar_t message[512];      /* English, human-readable, may be empty */
} RegResult;

typedef struct RegPlan RegPlan;      /* opaque */
```

```c
WRC_API void reg_options_init(RegOptions *options);
WRC_API void reg_result_init(RegResult *result);
WRC_API const wchar_t *reg_status_name(RegStatus status);

/* Planning: decode + parse, no registry access. */
WRC_API RegStatus reg_plan_parse(const void *data, size_t size,
                                 const RegOptions *options,
                                 RegPlan **plan, RegResult *result);
WRC_API BOOL          reg_plan_needs_admin(const RegPlan *plan);
WRC_API unsigned char reg_plan_mode(const RegPlan *plan);   /* 1, 2, 3 */
WRC_API unsigned int  reg_plan_operation_count(const RegPlan *plan);
WRC_API void          reg_plan_free(RegPlan *plan);

/* Applying: admin_only == TRUE skips non-admin roots entirely. */
WRC_API RegStatus reg_plan_apply(const RegPlan *plan, BOOL admin_only,
                                 const RegOptions *options, RegResult *result);

/* Convenience wrappers. */
WRC_API RegStatus reg_apply_file(const wchar_t *path, BOOL admin_only,
                                 const RegOptions *options, RegResult *result);
WRC_API RegStatus reg_apply_memory(const void *data, size_t size,
                                   BOOL admin_only, const RegOptions *options,
                                   RegResult *result);

/* Overlay helpers (format per §4.4). */
WRC_API RegStatus reg_overlay_read(const wchar_t *exe_path,
                                   unsigned char **reg_data, size_t *reg_size,
                                   unsigned char *mode, RegResult *result);
WRC_API void      reg_overlay_free(unsigned char *buffer);
WRC_API RegStatus reg_overlay_write(const wchar_t *template_path,
                                    const void *reg_data, size_t reg_size,
                                    unsigned char mode, const wchar_t *out_path,
                                    BOOL force, RegResult *result);

/* Elevation helpers. */
WRC_API BOOL      reg_is_admin(void);
WRC_API RegStatus reg_run_elevated_and_wait(const wchar_t *exe_path,
                                            const wchar_t *params,
                                            DWORD *child_exit_code,
                                            RegResult *result);

#ifdef __cplusplus
}
#endif
#endif /* WIN_REG_CORE_H */
```

Design notes:

* Two-phase plan/apply mirrors the loader (§6.7): parse once, ask
  `reg_plan_needs_admin()`, then apply the user part and the admin part
  separately.
* The overlay and elevation helpers are separate entry points so a GUI can reuse
  the format and UAC logic without linking the parser.
* `reg_apply_file`/`reg_apply_memory` exist for tests and for consumers that do
  not need the plan.
* No function returns an allocated string: everything goes through the
  `RegResult` buffer or the log callback.
### 9.4 Ownership, threading and error rules

| Rule | Detail |
|---|---|
| Ownership | `RegResult` and `RegOptions` are caller-owned and must be initialised with the `*_init` helpers; `RegPlan` is owned by the library and freed with `reg_plan_free`; buffers from `reg_overlay_read` are freed with `reg_overlay_free` |
| ABI guard | `RegOptions.struct_size` is checked first; a mismatch returns `REG_ERR_ARGUMENT` |
| Threads | no global mutable state; a `RegPlan` is read-only after `reg_plan_parse` and may be applied from several threads |
| Reentrancy | `reg_plan_apply` and overlay functions are reentrant; the log callback may be invoked from the calling thread only |
| Errors | every public function returns `RegStatus`; on failure `result->status`, `win32_error`, `line_number` and `message` are filled; `result` may be `NULL` only if the caller accepts that |
| Partial failure | `reg_plan_apply` continues after a failed operation and returns `REG_ERR_ACCESS`/`REG_ERR_WIN32` if anything failed; counters stay meaningful |
| Dry run | no registry write at all; counters reflect the operations that would run |
| Text | `WCHAR`/`wchar_t` only, UTF-16LE; no ANSI fallback, no `TCHAR` |
| Memory | single allocator (`malloc`/`free` from the UCRT) used consistently; every early return frees what it allocated |
| Formatting | `StringCchPrintfW`/`StringCchCopyW` from `strsafe.h`; `FALSE`-returning calls are checked |

### 9.5 Win32 mapping

| Win32 error | `RegStatus` |
|---|---|
| `ERROR_SUCCESS` | `REG_OK` |
| `ERROR_ACCESS_DENIED`, `ERROR_PRIVILEGE_NOT_HELD`, `ERROR_SECURITY_DESCRIPTOR_NOT_IN` | `REG_ERR_ACCESS` |
| `ERROR_FILE_NOT_FOUND`, `ERROR_PATH_NOT_FOUND` during deletion | `REG_OK` (idempotent) |
| `ERROR_INVALID_PARAMETER`, `ERROR_MORE_DATA`, `ERROR_NO_MORE_ITEMS` | `REG_ERR_WIN32` |
| `ERROR_CANCELLED` (1223) from `ShellExecuteExW` | `REG_ERR_ELEVATION` |
| `ERROR_NOT_ENOUGH_MEMORY`, `ERROR_OUTOFMEMORY` | `REG_ERR_MEMORY` |

APIs used: `RegCreateKeyExW`, `RegOpenKeyExW`, `RegSetValueExW`, `RegDeleteValueW`,
`RegDeleteKeyW`, `RegEnumKeyExW`, `RegQueryInfoKeyW`, `RegCloseKey`,
`IsUserAnAdmin` (shell32), `ShellExecuteExW`, `WaitForSingleObject`,
`GetExitCodeProcess`, `CloseHandle`, `GetModuleFileNameW`, `CreateFileW`,
`ReadFile`, `WriteFile`, `MoveFileExW`, `SetFilePointerEx`, `GetFileSizeEx`.

### 9.6 Build (mingw-w64, `-Werror` clean)

```bat

rem c_registry_module\build.bat  (extract)
@echo off
setlocal
if not defined MINGW_BIN (
  if exist "C:\VSS\mingw64\bin\gcc.exe" set "MINGW_BIN=C:\VSS\mingw64\bin"
)
if not defined MINGW_BIN (
  echo ERROR: mingw-w64 not found. Set MINGW_BIN to the folder containing gcc.exe.
  exit /b 1
)
set "CC=%MINGW_BIN%\gcc.exe"
set "AR=%MINGW_BIN%\ar.exe"
set "CFLAGS=-std=c11 -Wall -Wextra -Werror -pedantic -O2 -DUNICODE -D_UNICODE -D_WIN32_WINNT=0x0601 -DWIN32_LEAN_AND_MEAN -I."
if not exist build mkdir build
%CC% %CFLAGS% -c win_reg_core.c    -o build\win_reg_core.o    || exit /b 1
%CC% %CFLAGS% -c win_reg_parse.c   -o build\win_reg_parse.o   || exit /b 1
%CC% %CFLAGS% -c win_reg_overlay.c -o build\win_reg_overlay.o || exit /b 1
%CC% %CFLAGS% -c win_reg_elevate.c -o build\win_reg_elevate.o || exit /b 1
%AR% rcs build\libwin_reg_core.a build\win_reg_core.o build\win_reg_parse.o ^
      build\win_reg_overlay.o build\win_reg_elevate.o || exit /b 1
%CC% %CFLAGS% -municode test_runner.c build\libwin_reg_core.a ^
      -ladvapi32 -lshell32 -o build\test_runner.exe || exit /b 1
build\test_runner.exe
exit /b %ERRORLEVEL%
```

* `-municode` is required for `wmain`; without it the linker cannot resolve the
  entry point.
* `-pedantic` plus `-Werror` enforces C11 conformance and rejects MSVC-only
  extensions; `__declspec` is guarded by the `WRC_API` macro.
* `-DWIN32_LEAN_AND_MEAN` keeps the WinAPI surface small; `_WIN32_WINNT=0x0601`
  matches the minimum supported OS for the APIs used (no `RegDeleteTreeW`).
* An optional DLL target adds `-DWIN_REG_CORE_BUILD_DLL -shared` and links with
  `-Wl,--out-implib,build\libwin_reg_core.dll.a`.
* The build must produce **zero warnings**; `cppcheck --enable=warning,style
  --error-exitcode=1` is part of the acceptance criteria.
### 9.7 Behaviour parity matrix (Python core <-> C module)

| Rule (§7) | Python | C | Test |
|---|---|---|---|
| decoding (§7.2) | `encoding.decode_bytes` | `reg_decode_bytes` | `test_encoding.py` / `test_decode` |
| line assembly (§7.3) | `parser._LineAssembler` | `reg_parse_line` | `test_parser.py` / `test_continuation` |
| sections (§7.4) | `RegRoot.parse` | `reg_parse_section` | `test_parser.py` / `test_roots` |
| value types (§7.5) | `parser._parse_value` | `reg_parse_value` | `test_parser.py` / `test_value_types` |
| hex grammar (§7.6) | `parser._parse_hex` | `reg_parse_hex` | `test_parser.py` / `test_hex_tokens` |
| escapes (§7.7) | `parser._unescape` | `reg_unescape` | `test_parser.py` / `test_escapes` |
| expansion (§7.9) | `expand.VariableExpander` | `reg_expand_value` | `test_expand.py` / `test_expand` |
| apply (§7.10) | `registry.RegistryApplier` | `reg_plan_apply` | `test_registry.py` / `test_apply_*` |
| overlay (§4.4) | `overlay.Overlay` | `reg_overlay_read/write` | `test_overlay.py` / `test_overlay_*` |
| plan/mode | `ParseResult.mode` | `reg_plan_mode` | `test_build.py` / `test_mode_matrix` |
| elevation | `elevate.run_elevated_and_wait` | `reg_run_elevated_and_wait` | manual (§8.4) |

**Implementation note (Stage 3).** The C entry points are named
`reg_decode_bytes`, `wrc_assemble` (line assembly), `reg_parse_section`,
`reg_parse_value`, `reg_parse_hex`, `reg_unescape`, `reg_expand_value`,
`reg_plan_apply_part` (the applier), `reg_overlay_read`/`reg_overlay_write`
and `reg_run_elevated_and_wait`; the document driver is `reg_parse_document`
(§9.7 called it `reg_parse_line`); the parity table above refers to the
Python names on the left. The concrete mapping, the additive API
(`reg_plan_apply_part`, `reg_apply_buffer`, `reg_overlay_has`,
`reg_text_free`) and the eleven documented deviations are listed in
`c_registry_module/MODULE_CONTEXT.md` §3 and §9.

**Golden corpus:** `examples/*.reg` plus `tests/data/*.reg` are the shared input
set. Both implementations must produce the same operation count, the same mode
byte and the same final registry state for every corpus file. The C self-test
embeds the corpus as UTF-16LE literals to stay self-contained; a Python test
(`tests/test_parity.py`) asserts that the corpus files decode to the same
operations as the embedded expectations (`tests/data/expected.json`, generated
once by the Python core and reviewed by hand).

### 9.8 C self-tests (`test_runner.c`)

Each test prints `PASS <name>` / `FAIL <name>: <why>` and increments a counter;
the process exits with the number of failures (0 = success).

Registry tests use `HKCU\Software\__win_reg_core_selftest__\<pid>` and delete it
in a cleanup handler (`SetConsoleCtrlHandler` + normal path).

| Group | Cases |
|---|---|
| decode | UTF-8 BOM, UTF-16LE BOM, UTF-16BE BOM, BOM-less UTF-16LE, odd length (error), cp1251 forced, empty buffer |
| parse | each value type, `hex(0/1/2/3/4/7/b)`, `hex(5)` (error), `dword:` bounds, `\"`/`\\`, comments with trailing `\`, `[-KEY]`, empty path (error), unknown root (error), `HKEY_CURRENT_MACHINEX` (error), value outside section (error) |
| plan | operation count, mode 1/2/3, needs_admin, plan reuse across two applies |
| apply | create key/value, overwrite, delete value (existing/missing), delete key recursive, `REG_MULTI_SZ` round-trip via `RegQueryValueExW`, `REG_EXPAND_SZ` stored literal, `REG_QWORD`, `REG_NONE`, dry run leaves no key behind, access denied on `HKLM` when not elevated (skipped when elevated) |
| view | `KEY_WOW64_32KEY` vs `KEY_WOW64_64KEY` write into different views (skipped when unavailable) |
| overlay | encode/decode round-trip in `%TEMP%`, wrong marker, truncated tail, size larger than the file, mode 0/4 rejection, second overlay rejected, atomic replace leaves no `.tmp` |
| elevation | `reg_is_admin()` returns a stable value; `reg_run_elevated_and_wait` on a dummy child relays its exit code (manual, requires UAC) |

### 9.9 Coding standard (C)

* C11 only, no compiler extensions, no C++, no `goto` except a single cleanup
  label per function.
* `-Wall -Wextra -Werror -pedantic` clean; `cppcheck --enable=warning,style`
  clean.
* All strings are `const wchar_t *`; all buffers are length-checked
  (`StringCch*` family, `ARRAYSIZE`).
* One allocation per logical object; every failure path frees what was
  allocated; ownership is documented next to each declaration.
* No `printf` in library code; logging only through `RegLogFn`.
* Every public function validates its arguments before doing anything else.
* `static` helpers are `static` in exactly one translation unit; shared helpers
  live in `win_reg_parse.h` as `static inline` only when they are two lines long.
* Line length 100, 4-space indent, `/* */` comments, English only.

---

## 10. Test plan (Stage 2, `tests/`)

### 10.1 Strategy

* Stdlib `unittest` only (`pytest` is not installed and must not become a
  dependency). Every test class is a plain `unittest.TestCase`, so the suite
  also runs under `pytest` if the user installs it later.
* Two commands must both work from the repository root:

  ```powershell
  python -m unittest discover -s tests -v
  python -m unittest discover -s tests -p "test_*.py" -v
  ```

* Unit tests never touch the real registry except inside the sandbox key (§10.3)
  and never require elevation.
* Tests are written against the new package (`pyreg2core`); a small
  `test_shim.py` guards the deprecated legacy shim.

### 10.2 Coverage matrix

| Module | Test file | Focus |
|---|---|---|
| `encoding.py` | `test_encoding.py` | BOM table, odd length, heuristic, strict failure, forced cp1251 |
| `reg_types.py` | `test_types.py` | `RegRoot.parse` boundaries (B6), `requires_admin`, subtype map (B11) |
| `parser.py` | `test_parser_sections.py`, `test_parser_values.py` | sections, roots, routing, delete forms (B7, B8); value forms, hex grammar, escapes, continuations (B5, B10-B14), line numbers |
| `expand.py` | `test_expand.py` | `%VAR%`, `%CD%` priority (B15), markers, case-insensitivity, no expansion of names (B14), `expand_env=False` |
| `overlay.py` | `test_overlay.py` | round-trip, marker mismatch, truncation, impossible size, existing overlay, mode validation |
| `registry.py` | `test_registry.py` | create/set/delete, recursive delete, type round-trip, access-denied mapping, `--view` (O1), report counters (B1) |
| `builder.py` | `test_builder.py` | end-to-end with a synthetic `MZ` template, atomicity, `--force`, path guards (B4), single read (B20), mode matrix |
| `applier.py` | `test_applier.py` | routing (B16), elevation relay mocked (B2), exit codes, missing overlay -> 0 |
| `selfpath.py` | `test_selfpath.py` | `argv[0]` absolute/relative/`PATH` resolution (B3), resource dir fallbacks (B19) |
| `cli.py` | `test_cli.py` | `--help`, `--version`, argparse errors -> 2, exit code table (§6.5) |
| shim | `test_shim.py` | legacy tuple API and return types |

### 10.3 Test data and sandbox policy

* `tests/data/*.reg` — small, purpose-built files, UTF-8 with BOM, one file per
  construct group; `tests/data/broken/*.reg` — one file per rejected construct
  (the file name states the expected error).
* **Stage 2 note:** `tests/data/` was deliberately **not** created. Every
  fixture is built in memory by `tests/support.py` (see below), which keeps the
  suite self-contained and the diff readable; the end-to-end checks use
  `examples/*.reg` through the built binaries (§8.4). If a future case needs a
  file on disk, `support.write_reg_file()` already provides the helper.
* Generated fixtures are built in memory (`bytes` literals) whenever a file is
  not required, so the corpus stays readable in the diff.
* Registry sandbox:
  `HKCU\Software\__pyreg2exe_tests__\<uuid4>`; created in `setUp`, removed with
  a recursive delete in `addCleanup`, so a failing test never leaves keys behind.
* Tests that need Windows-only APIs are decorated with
  `@unittest.skipUnless(sys.platform == "win32", "Windows only")`.
* No test writes outside `%TEMP%` and the sandbox key.

### 10.4 Case inventory

**encoding** — UTF-8/UTF-16LE/UTF-16BE with BOM; BOM-less UTF-16LE ASCII and
Cyrillic; odd-length buffer (error); invalid UTF-8 (error); cp1251 forced;
empty file; 1-byte file; surrogate pair; `\ufeff` in the middle of the text
(kept as U+FEFF, documented).

**parser** — each row of the §7.5 table; `hex(5)` (error); `hex(4)` with 3/5
bytes (error); `dword:100000000` (error); `dword:0`; `dword:FFFFFFFF`;
`@`; `@=-`; `"n"=-`; duplicate names (last wins); comment lines including
`; comment \` followed by a value (B10); `hex:` continued over three lines with
`\`; `\"` and `\\`; unterminated string; value outside section; `[-KEY]` then a
value (error); section without `]`; root boundaries (`HKEY_LOCAL_MACHINEX`,
`HKEY_CURRENT_USERS`); empty path; `REGEDIT4` header; header after a section
(warning); CRLF/LF/CR; trailing whitespace; UTF-8 BOM stripped.

**expand** — `%WINDIR%`, undefined `%NOPE%` (kept), `%CD%` with the environment
variable set to a different directory (B15), `<reg2exepath>` lower-case,
`<reg2exeprogspath>`, mixed markers and `%VAR%` in one string, `REG_MULTI_SZ`
items, a value containing `%%` (kept), `expand_env=False`.

**overlay** — encode/decode round-trip with 0, 1 and 1 MiB payloads; marker
wrong; tail truncated by one byte; size `2**64-1` (rejected); size larger than
the file (rejected); mode 0/4 (rejected); template that already has an overlay
(rejected); pre-existing output without `--force` (rejected).

**registry** — `REG_SZ`, `REG_EXPAND_SZ` (stored literal, verified with
`QueryValueEx`), `REG_BINARY`, `REG_DWORD`, `REG_QWORD`, `REG_MULTI_SZ`
(including `[""]` and `[]`), `REG_NONE`; create nested keys; delete value that
does not exist; delete key recursively with children; report counters; simulated
`PermissionError` (patched `winreg`) -> `failed == True`.

**builder/applier/cli** — the §6.6 and §6.7 step sequences, argument parsing,
defaults, `-o`/`-t` handling, exit codes for each documented failure, and the
absence of any write to the registry during `--dry-run`.
### 10.5 Acceptance criteria for Stage 2

1. `python -m unittest discover -s tests -v` passes with **0 failures, 0 errors,
   0 skips** on this Windows host (skips allowed only for the elevation and
   registry-view groups, and must be listed in the report).
2. Every defect B1–B23 has at least one test that fails on the old code and
   passes on the new one; the mapping is written into the Stage 2 report.
3. `python -m unittest` runtime stays under 10 seconds (no sleeps, no real
   elevation).
4. `python -m compileall -q pyreg2core pyreg2exe.py regapply.py regfile.py`
   reports nothing.
5. `python -m pyflakes`-equivalent cleanliness is **not** required (the tool is
   not installed); unused imports and dead code are checked by review instead.
6. `python pyreg2exe.py --help` and `python regapply.py` (no overlay) both exit
   `0`; the loader must still do nothing on a plain source run.
7. Registry access during the suite is confined to
   `HKCU\Software\__pyreg2exe_tests__`; after the run that key does not exist.

### 10.6 Notes on tooling

* `pytest`/`coverage` are absent, and installing packages is outside the current
  scope. Coverage is therefore reasoned from the case inventory (§10.4), not
  measured. If the user later approves `pip install pytest coverage`, the same
  suite runs unchanged and `coverage run -m pytest` gives the numbers.
* No CI configuration exists and none is added in Stage 2; the acceptance run is
  a local command executed by the agent and reported with its full output.

---

## 11. Known limitations (documented, not defects)

| ID | Limitation | Status |
|---|---|---|
| L1 | Executables produced with the UCRT MinGW toolchain require Windows 10 or newer | documented in README |
| L2 | Any appended overlay invalidates Authenticode; sign the finished patch | documented (O5) |
| L3 | No 32-bit build is produced; the payload is x64 only | documented |
| L4 | Registry view is the caller's view unless `--view` is used (O1) | opt-in option in Stage 2 |
| L5 | Variable expansion is single-pass (O2) | documented |
| L6 | `HKEY_USERS` and `HKEY_CLASSES_ROOT` writes require admin and target the
current user's hive; the patch does not load other users' hives | documented |
| L7 | No transaction support: a partially applied patch is possible by design
(operations are applied in order, failures are reported, no rollback) | documented |
| L8 | Unicode file names are supported, but the console output of the builder
depends on the code page (mitigated by the safe writer) | mitigated in Stage 2 |
| L9 | `REG_EXPAND_SZ` inside an `HKLM` patch is expanded in the elevated context
when `expand_env` is on; use `--no-expand-env` for portable values | documented (§7.9) |

---

## 12. Roadmap and acceptance criteria

### 12.1 Stage 2 — Python refactor (next)

Deliverables:

* `pyreg2core/` package as specified in §6.2 with all modules listed there.
* `pyreg2exe.py` and `regapply.py` reduced to thin wrappers.
* `regfile.py` compatibility shim (§6.8).
* `tests/` suite (§10).
* `README.md` rewritten: build recipe from §8, marker table completed (O4),
  Changelog 1.1.0 (§6.9), known limitations from §11.
* `.gitignore` additions: `regapply.exe`, `*.reg.exe`.
* Nuitka rebuild of both artefacts and a §8.4 checklist report.

Definition of done: §10.5 criteria plus the §8.4 checklist.

### 12.2 Stage 3 — C11 module

Deliverables: `c_registry_module/` exactly as §9.2, zero-warning build with the
flags from §9.6, `test_runner.exe` green, `MODULE_CONTEXT.md` with the parity
matrix (§9.7) and the test report.

Definition of done: `build.bat` returns `0`, `cppcheck` clean, the golden corpus
produces identical operation counts and modes to the Python core, and the
sandbox keys are cleaned up after the run.

### 12.3 Decisions requested from the maintainer

| # | Decision | Recommendation |
|---|---|---|
| D1 | Default strictness: reject invalid constructs (new) or keep silent skipping (`--lenient`) | reject by default, `--lenient` available |
| D2 | Exit code change for the builder (`1` -> `3`/`4`) | accept, document in changelog |
| D3 | Expansion policy: `REG_EXPAND_SZ` stored literal, `%VAR%` still expanded for `REG_SZ` | accept as in §7.9 |
| D4 | Keep `regfile.py` as a deprecated shim | keep for one release |
| D5 | Add `--view={default,32,64}` in Stage 2 or defer to Stage 3 | add in Stage 2 (cheap, closes O1) |
| D6 | Show a `MessageBoxW` when a patch fails, or stay silent + log only | stay silent, always write the log |
| D7 | Package name `pyreg2core` vs `pyreg2exe/` | `pyreg2core` (avoids module/package shadowing) |

---

## 13. Stage 2 report

Status: **done** — Python refactor, test suite and rebuilt binaries are complete
and verified. Stage 3 (§9) has **not** started: `c_registry_module/` and
`MODULE_CONTEXT.md` do not exist.

Host: Windows x64, CPython 3.13, Nuitka 4.2.1 (bundled zig backend), PowerShell.
`git` is not installed on this host, so this report carries no commit id; the
artefact hashes in §13.2 identify the verified build.

### 13.1 Scope delivered

| Area | Result |
|---|---|
| `pyreg2core/` | 14 modules as in §6.2: `__init__`, `errors`, `diag`, `encoding`, `reg_types`, `parser`, `expand`, `overlay`, `registry`, `builder`, `applier`, `selfpath`, `cli`, `elevate` |
| Entry points | `pyreg2exe.py` and `regapply.py` are thin wrappers; all logic lives in the package |
| Legacy API | `regfile.py` is a deprecated shim (§6.8) that re-exports the tuple API |
| Tests | 12 modules, 269 tests, stdlib `unittest` only (§10) |
| Docs | `README.md` rewritten to 1.1.0 (L1–L9, marker table, exit codes); §8.3 and §10.2 corrected to the commands and file names that actually work |
| Packaging | Both artefacts rebuilt with Nuitka; `.gitignore` ignores `regapply.exe` and `*.reg.exe` |

### 13.2 Verified artefacts

| File | Size (bytes) | Built (local) | SHA-256 |
|---|---|---|---|
| `dist\regapply.exe` | 7,985,664 | 12:34:04 | `04C596CBACCE7C2BE94380F65092D2725C1BCCCBB2516A8B0E5EB4DD274BB1ED` |
| `dist\pyreg2exe.exe` | 14,128,640 | 12:35:42 | `9636C7672CC19D5C6733E3E19496724E120ED2108F28F46705A2B12882EE81F7` |

Both are `--onefile`. The loader is windowless; the builder keeps its console
(B21). The builder is larger than the loader because it embeds
`regapply.exe` as a data file, so the 7–12 MB band in §8.4 step 1 applies to the
loader only.

### 13.3 Test suite

| Command | Result |
|---|---|
| `python -m unittest discover -s tests -v` | **269 tests, OK**, 0 failures / 0 errors / 0 skips, exit 0 |
| `python -m unittest discover -s tests -p "test_*.py" -v` | same 269 tests, OK, exit 0 |
| `python -m compileall -q pyreg2core pyreg2exe.py regapply.py regfile.py` | no output, exit 0 |
| `python pyreg2exe.py --help` | exit 0 |
| `python regapply.py` (no overlay) | exit 0, "nothing to do" |

Runtime 0.576–0.695 s across runs, far below the 10 s budget (§10.5 p.3). There
are no skips: the elevation paths are exercised with a mocked
`run_elevated_and_wait`, so UAC is never shown during the suite.

| Test module | Tests | Focus |
|---|---|---|
| `test_applier.py` | 16 | routing, elevation relay mocked, exit codes, missing overlay → 0 |
| `test_builder.py` | 22 | end-to-end with a synthetic `MZ` template, atomicity, `--force`, path guards, single read, mode matrix |
| `test_cli.py` | 15 | `--help`, `--version`, argparse errors → 2, exit-code table |
| `test_encoding.py` | 15 | BOM table, odd length, heuristics, forced encodings |
| `test_expand.py` | 20 | `%VAR%`, `%CD%` priority, markers, case-insensitivity, `expand_env=False` |
| `test_overlay.py` | 19 | round-trip, marker mismatch, truncation, impossible size, mode validation |
| `test_parser_sections.py` | 20 | sections, roots, routing, delete forms, ordering |
| `test_parser_values.py` | 48 | value forms, hex grammar, escapes, continuations, line numbers |
| `test_registry.py` | 25 | create/set/delete, recursive delete, type round-trip, access denied, report counters |
| `test_selfpath.py` | 9 | `argv[0]` forms, resource directories, template discovery |
| `test_shim.py` | 17 | deprecated tuple API of `regfile.py` |
| `test_types.py` | 43 | `RegRoot.parse` boundaries, `from_section`, `Mode`, `RegType`, subtype map, `compute_mode`, `Operation.describe` |

### 13.4 Acceptance criteria §10.5

| # | Criterion | Result | Evidence |
|---|---|---|---|
| 1 | Full suite green, 0 failures / 0 errors / 0 skips | **pass** | 269 tests in 0.695 s, `OK` (§13.3) |
| 2 | Every defect B1–B23 has at least one test | **pass, with one documented exception** | mapping in §13.6; B22 is build-level (no unit test can cover a recipe, §13.7 p.7), B17 and B23 are closed structurally |
| 3 | Runtime under 10 s | **pass** | 0.588 s on the final run, 0.576–0.695 s across five runs |
| 4 | `compileall` reports nothing | **pass** | exit 0, zero lines of output |
| 5 | Pyflakes not required, review instead | **n/a** | tool absent; unused imports were reviewed manually |
| 6 | `pyreg2exe.py --help` and bare `regapply.py` exit 0 | **pass** | both exit 0; the loader logs "nothing to do" |
| 7 | Registry access confined to `HKCU\Software\__pyreg2exe_tests__`, key gone afterwards | **pass** | `SANDBOX_KEY=GONE` checked after the suite and after §8.4 |

### 13.5 §8.4 checklist on the rebuilt binaries

| # | Step | Expected | Observed |
|---|---|---|---|
| 1 | Artefacts exist with plausible sizes | 7–12 MB loader, larger builder | `regapply.exe` 7,985,664 B, `pyreg2exe.exe` 14,128,640 B |
| 2 | Builder keeps its console and prints help | usage text, exit 0 | full usage printed, exit 0 |
| 3 | Build `cu.reg`, `lm.reg`, `mixed.reg`, inspect the tail | modes 2 / 1 / 3, marker `PYREGEND` | `size 112 mode 2`, `size 122 mode 1`, `size 230 mode 3`, marker `b'PYREGEND'`; all builds exit 0 |
| 4 | Run `cu_patch.exe`, then `reg query` | `Name=VKRadeon`, `Path=<patch dir>\data` | exit 0; `Name REG_SZ VKRadeon`, `Path REG_SZ C:\Users\User\AppData\Local\Temp\data` — the patch directory, not the current directory (B15) |
| 5 | Rebuild without `--force` on an existing output | non-zero exit, visible message | exit **4**, stderr `[error] output already exists: ... (use --force to overwrite)` |
| 6 | `lm_patch.exe` / `mixed_patch.exe` apply or fail with 6/2, never 0 | 6 or 2 when refused | **deviated:** run with `--dry-run -v` instead (see below). Exit 0, log shows the three user operations as `would apply (user)` and the three machine-wide ones as `would apply (admin)` — routing by `RegRoot.requires_admin` is correct |
| 7 | Cleanup | no `*_patch.exe`, no test keys | `remaining *_patch.exe: 0`, `TEST_KEY_CU=GONE`, `SANDBOX_KEY=GONE` |

Deviation for step 6: a real UAC round-trip needs an interactive prompt on this
machine, so the elevated path was verified in three safer ways instead —
`test_applier.ElevationTests` (mocked relay: child exit code relayed, cancelled
UAC raises `ElevationError`, unexpected child code becomes 2, `--hklm-only`
applies only admin operations), `test_cli` (elevation error maps to exit 6) and
the dry-run above on the real binary. A manual elevated run of `lm_patch.exe`
remains the one item to repeat before shipping; nothing else in the checklist
depends on it.

Full §8.4 transcript (normalised paths): builds report `3 operation(s), mode
0x02 … payload 112 bytes`, `3 … 0x01 … 122`, `6 … 0x03 … 230`; the dry run
reports `overlay: 230 byte(s), mode 0x03 (mixed (HKLM + HKCU))` and echoes every
set operation with its declared type (`REG_SZ`), so the payload survived the
build/apply round-trip byte for byte.

### 13.6 Defect → test mapping (B1–B23)

Class names are given when the module holds several groups. Rows that name a
test point at real cases in the suite of §13.3; B17, B22 and B23 are closed
structurally or at build level, and say so in the row.

| Defect | Test(s) that fail on the old code and pass now |
|---|---|
| B1 — operation failures silently discarded | `test_registry.FailureTests` (`test_a_failing_operation_does_not_abort_the_run`, `test_out_of_range_dword_is_reported`, `test_stop_on_error_aborts`), report counters in `test_registry.ValueTests`/`DeletionTests`, `test_applier.test_invalid_embedded_reg_exits_with_five` |
| B2 — UAC outcome never verified | `test_applier.ElevationTests` (`test_child_exit_code_is_relayed`, `test_uac_cancelled_raises_elevation_error`, `test_unexpected_child_code_becomes_two`), `test_cli.test_cli_maps_elevation_error_to_six` |
| B3 — own path from `sys.argv[0]` + `abspath` | `test_selfpath.SelfPathTests` (absolute, relative and `PATH`-resolved `argv[0]`) |
| B4 — non-atomic build, self-overwrite | `test_builder.BuilderTests` (`test_no_temporary_file_is_left_behind`, `test_output_equal_to_input_is_rejected`, `test_output_equal_to_template_is_rejected`, `test_existing_output_requires_force`) |
| B5 — silent data loss in parser and loader | `test_parser_values` (all value/hex/escape groups), `test_encoding.DecodingTests`, `test_applier.test_invalid_embedded_reg_exits_with_five` |
| B6 — root prefix matched without a boundary | `test_types.RegRootParseTests.test_prefix_matches_are_rejected`, `test_parser_sections.RootTests.test_unsupported_roots_are_rejected` |
| B7 — root tables disagree | `test_types.RegRootParseTests.test_long_forms` / `test_short_forms`, `test_parser_sections.RootTests.test_long_and_short_forms` |
| B8 — empty key path accepted | `test_types.RegRootSectionTests.test_empty_path_is_rejected`, `test_parser_sections.RootTests.test_empty_path_is_rejected` |
| B9 — lossy encoding detection | `test_encoding.DecodingTests` (15 cases: BOMs, odd length, heuristic, strict UTF-8, forced cp1251) |
| B10 — continuation is not hex-aware | `test_parser_values.ContinuationTests` (6 cases), `test_parser_sections` routing of continued sections |
| B11 — `hex(4)` written with the wrong type | `test_types.SubtypeParsingTests`, `test_parser_values.HexPayloadTests.test_hex_dword`, `test_registry.ValueTests` (type round-trip through `winreg`) |
| B12 — missing length and range validation | `test_parser_values.DwordTests.test_dword_overflow_is_an_error`, `HexPayloadTests.test_hex_dword_wrong_length`, `test_types.RegTypeTests.test_exact_byte_length`, `test_registry.FailureTests.test_out_of_range_dword_is_reported` |
| B13 — lenient, inconsistent hex token parsing | `test_types.SubtypeParsingTests.test_non_hex_tokens_are_rejected`, `test_parser_values.HexPayloadTests.test_invalid_byte_tokens` / `test_malformed_subtype_payload` |
| B14 — escapes and expansion semantics | `test_parser_values.StringValueTests` (escapes, unterminated string, unknown escape), `test_expand.ValueTypeTests` (names are not expanded, binary untouched) |
| B15 — `%CD%` resolves to the current directory | `test_expand.PercentTests.test_cd_uses_the_patch_directory` / `test_cd_lowercase`; live check in §8.4 step 4 (`Path` = patch directory) |
| B16 — mode byte unused, derivation duplicated | `test_types.ModeTests`, `test_types.ComputeModeTests`, `test_applier.test_mode_mismatch_is_only_a_warning`, `test_builder.test_mixed_mode`, `test_overlay.EncodeTests.test_invalid_mode_is_rejected` |
| B17 — constants and helpers duplicated three times | structural: one `reg_types` module imports the tables everywhere (`test_types` pins them), `test_shim` proves the legacy API re-exports the same objects instead of a copy |
| B18 — no diagnostics, degenerate exit codes | `test_cli.ApplierCliTests` / `BuilderCliTests` (exit-code table), `test_parser_values.StringValueTests.test_line_numbers_are_reported`, `test_builder.InputValidationTests.test_invalid_reg_reports_the_line` |
| B19 — `resource_path()` only understands PyInstaller | `test_selfpath.TemplateDiscoveryTests`, `test_overlay.TemplateValidationTests` |
| B20 — the input `.reg` is read twice | `test_builder.BuilderTests.test_digest_matches_the_input` (embedded bytes == parsed bytes, one read) |
| B21 — CLI built without a console | `test_cli.BuilderCliTests.test_help`; build flag `--windows-console-mode=force` (§8.3) proven on the real binary by §8.4 step 2 |
| B22 — build recipe not reproducible | build-level: both binaries were rebuilt exactly from §8.3 as written, `.gitignore` covers `regapply.exe` and `*.reg.exe`, `test_builder` proves the embedded template is found (`--include-data-files`), `test_shim` proves no lazy `import regfile` is required |
| B23 — no automated tests, self-path assumption unverified | this suite: 269 tests, including `test_selfpath` and the end-to-end build; §8.4 exercises the packaged self-path |

### 13.7 Deviations, corrections and what is left

1. **§8.4 step 6 was run as a dry run.** A real UAC prompt cannot be driven from
   a non-interactive shell, so `mixed_patch.exe --dry-run -v` was used on the
   built binary and the elevated relay itself is covered by mocked unit tests.
   A single manual elevated run of `lm_patch.exe` (exit 0, key created) is the
   only remaining manual check; it does not gate the rest of the stage.
2. **`--mingw64` and `--include-module=regfile` are gone from §8.3.** Nuitka
   4.2.1 rejects the first on Python 3.13 and the second is unnecessary after
   the refactor (§8.6). Consequence for B22: the "compiler pinning" part of its
   fix cannot be expressed with this Nuitka version — `CC`/`CXX` are ignored and
   the bundled zig backend is used. The recipe is still deterministic and is the
   one that produced the hashes in §13.2.
3. **`tests/data/` was not created.** Fixtures are generated in memory by
   `tests/support.py` (§10.3); the on-disk corpus would have duplicated
   `examples/*.reg` without adding coverage.
4. **`tests/test_types.py` was missing and has now been added.** §10.2 referenced
   it, the suite did not contain it. It contributes 43 of the 269 tests and is
   the only place that pins `RegRoot.parse`, `Mode`, `RegType`,
   `parse_subtype_text`, `compute_mode` and `Operation.describe` directly. Test
   count therefore grew from 226 to 269 between the first and the final run.
5. **Doc/test references corrected.** §10.1 and §10.2 now name
   `test_parser_sections.py` / `test_parser_values.py` / `test_shim.py`;
   `test_shim.py` cited B22 in its docstring (a build-level defect) and now cites
   B17/B23, which is what the shim actually guards.
6. **Module-form invocation needs the documented command.** `python -m unittest
   tests.test_types` fails with `ModuleNotFoundError: support`, because
   `tests/support.py` is importable only when `tests/` itself is on `sys.path` —
   which is what `python -m unittest discover -s tests` does. Both commands
   documented in §10.1 work; the module form was never documented.
7. **B22 has no unit test by nature.** It is a build-recipe defect; its evidence
   is a successful rebuild from the documented commands plus the `.gitignore`
   entries, as recorded in §13.6.
8. **Stage 3 was untouched at that point.** `c_registry_module/` and
   `MODULE_CONTEXT.md` did not exist when Stage 2 closed; §9 was the plan of
   record for the next iteration. Stage 3 has since been implemented — see
   §14 for the report and `c_registry_module/MODULE_CONTEXT.md` for the module
   documentation.

---

## 14. Stage 3 report

### 14.1 Result

Stage 3 (§12, item 3) is complete. `c_registry_module/` contains a
self-contained C11/WinAPI registry engine that mirrors `pyreg2core` and is
independent of the console: no `printf`/`exit`/`abort` in the library, every
diagnostic travelling through `RegResult` and the optional `RegLogFn`, no
mutable global state, one allocator (`malloc`/`free`), every early return
freeing what it allocated.

* Compiler: mingw-w64 GCC 16.2.0 (`x86_64-w64-mingw32`, UCRT),
  `-std=c11 -Wall -Wextra -Werror -pedantic -O2 -DUNICODE -D_UNICODE`.
* Build: four objects → `build/libwin_reg_core.a` → `build/test_runner.exe`,
  **zero warnings, zero errors**.
* Self-tests: **55 passed, 0 failed, 1 skipped** (exit code 0); the skipped
  case is the UAC relay, which needs `--elevation` and a human at the prompt.
* The Python suite is unaffected: `python -m unittest discover -s tests`
  → **269 tests, OK** in 0.676 s.

### 14.2 Files

| File | Role |
|---|---|
| `win_reg_core.h` | Public API: §9.3 plus four additive entry points (`reg_plan_apply_part`, `reg_apply_buffer`, `reg_overlay_has`, `reg_text_free`). |
| `win_reg_parse.h` | Internal structures (`RegPlan`, `RegOperation`, `RegValue`, `WrcBuf`) and parser entry points. |
| `win_reg_parse.c` | §7.2 decoding, §7.3 line assembly, §7.4 sections, §7.5 value types, §7.6 hex grammar, §7.7 escapes, §7.9 expansion, plan lifetime, `reg_plan_parse`. |
| `win_reg_core.c` | Lifecycle helpers, `reg_status_name`, §9.5 status mapping, §7.10 applier, `reg_apply_file`/`reg_apply_memory`/`reg_apply_buffer`. |
| `win_reg_overlay.c` | §4.4 overlay read/verify/write, atomic temp + `MoveFileExW`, `reg_overlay_has`. |
| `win_reg_elevate.c` | `reg_is_admin` (token elevation), `reg_run_elevated_and_wait` (`ShellExecuteExW` + wait + exit code). |
| `test_runner.c` | 56 self-tests, sandbox under `HKCU\Software\__win_reg_core_selftest__\<pid>`, `wmain`, exit code = failures. |
| `build.bat` | mingw-w64 driver: compile → archive → link → run (`--no-run` to skip the run). |
| `Makefile` | Same flags and targets for `make`. |
| `MODULE_CONTEXT.md` | English module documentation: API, ownership, parity matrix, status mapping, overlay and elevation details, test report, deviations, embedding checklist. |

### 14.3 Build and test commands

```bat
cd c_registry_module
build.bat                    rem auto-detects C:\VSS\mingw64\bin\gcc.exe
build.bat --no-run           rem build only
build\test_runner.exe --elevation   rem adds the UAC relay test
```

`build.bat` and `Makefile` use exactly the flags recorded in §14.1 and the
link line `-municode test_runner.c build\libwin_reg_core.a -ladvapi32
-lshell32`. `-municode` is required for `wmain`; `-Werror` makes any warning
fatal, and the build reproducibly reports none.

### 14.4 What the self-tests cover

| Group | Cases | Proves |
|---|---|---|
| decode | 8 | BOM order (`FF FE`, `FE FF`, `EF BB BF`), BOM-less UTF-16LE detection, odd-length rejection, forced cp1251, empty input, invalid UTF-8 |
| parse | 18 | header/comment handling, every value type (`hex(0/1/2/3/4/7/b)`, `dword:`, `hex:`), `hex(5)` rejection, `dword:` bounds, comment with a trailing backslash, `[-KEY]`, empty path, unknown roots (`HKEY_DYN_DATA`, `HKEY_CURRENT_USERX`), value outside a section, unterminated header, continuations, trailing comma, hex token shapes, name/data errors, lenient mode, escapes, duplicate assignment, late header |
| plan | 2 | plan reuse across two applies (counters reset, no state), `REG_PART_USER` filtering with `ops_skipped` |
| apply | 15 | create/set, overwrite, delete value (present and missing), recursive subtree deletion, the full type matrix read back with `RegQueryValueExW`, `\"`/`\\`, `%VAR%`/`%CD%`/marker expansion, `REG_EXPAND_SZ` stored literal, `expand_env = FALSE`, dry run leaving no key, `ERROR_ACCESS_DENIED` on HKLM, joined hex payload, default-value deletion, `reg_apply_file`, `reg_apply_buffer` |
| view | 1 | `KEY_WOW64_32KEY` / `KEY_WOW64_64KEY` pass-through |
| overlay | 6 | round-trip, missing marker, truncated tail, mode 0/4, already patched template, existing output with/without `force`, no `.tmp` left behind |
| elevation | 2 | `reg_is_admin` stability, exit-code relay (opt-in) |
| api/corpus | 4 | NULL pointers, `struct_size`, `wow64_view`, `NULL` result, and the three `examples/*.reg` documents (3 ops/0x02, 3 ops/0x01, 6 ops/0x03, `needs_admin` false/true/true) |

The corpus parity of §9.7 is asserted directly from the embedded literals
instead of a Python-side `tests/test_parity.py` plus `tests/data/expected.json`:
the Python suite must stay runnable without a compiled artifact, and the C
side compares the same numbers (operation count, mode byte, `needs_admin`)
against the values the Python core produces for those files.

### 14.5 Deviations from §9

`c_registry_module/MODULE_CONTEXT.md` §9 lists them in full; the ones a
consumer must know:

1. `reg_is_admin` uses `GetTokenInformation(TokenElevation)` because mingw-w64
   does not declare `IsUserAnAdmin`.
2. `ERROR_INVALID_SECURITY_DESCR` (1338) is the name used in the status
   mapping; `ERROR_SECURITY_DESCRIPTOR_NOT_IN` does not exist.
3. Four additive entry points (`reg_plan_apply_part`, `reg_apply_buffer`,
   `reg_overlay_has`, `reg_text_free`) — needed for the two-pass UAC flow and
   for cheap overlay probing; no §9.3 signature changed.
4. `reg_plan_apply(plan, FALSE, …)` runs all operations, `TRUE` runs only the
   elevated roots; the loader uses `reg_plan_apply_part` so the unelevated pass
   never attempts HKLM.
5. `hex(N)` with more than eight hexadecimal digits reports
   `unsupported value type hex(<token>)` (still `REG_ERR_VALUE_TYPE`).
6. `reg_overlay_write` compares the output and template paths with
   `_wcsicmp`, not canonical full paths; `reg_overlay_read` treats a file
   shorter than 17 bytes as "no overlay" (`REG_ERR_OVERLAY`, `win32_error` 0).
7. BOM-less input of odd byte length is `REG_ERR_ENCODING`, exactly as §7.2
   requires; hand-written ASCII fixtures therefore need an even byte count or a
   BOM (the self-tests use a UTF-8 BOM).
8. `HKCU\Software` is shared between the 32- and 64-bit views on current
   Windows, so `wow64_view` cannot produce two HKCU datasets; the view test
   asserts write/read through the same view.
9. No `stop_on_error` (a Stage 2 CLI option) and no extra `%TOKEN%` map in
   `RegOptions`.

### 14.6 Follow-ups (non-gating)

1. Run `build\test_runner.exe --elevation` once interactively to exercise the
   real UAC consent path (`PASS elevation/relay_exit_code`, child exit 7).
2. `cppcheck` is not installed in this environment, so the
   `cppcheck --enable=warning,style --error-exitcode=1` gate of §9.9 has not
   been executed; the compiler gate (`-Wall -Wextra -Werror -pedantic`) is
   green.
3. The optional DLL target is documented in `MODULE_CONTEXT.md` §2 but not
   built; static linking is the default for an installer.
4. `tests/test_parity.py` / `tests/data/expected.json` (§9.7) were not created
   (see §14.4). Adding them later only requires comparing the same literals
   against `pyreg2core` output.
5. `git` is still unavailable, so this report carries no commit id.

### 14.7 Verification log

| Check | Result |
|---|---|
| `gcc -Werror -pedantic` on the four library units | no output (clean) |
| `ar rcs build\libwin_reg_core.a …` | exit 0 |
| link `test_runner.exe` with `-municode … -ladvapi32 -lshell32` | exit 0 |
| `build\test_runner.exe` | `55 passed, 0 failed, 1 skipped`, exit 0 |
| `python -m unittest discover -s tests` | `269 tests, OK` (0.676 s) |
| `HKCU\Software\__win_reg_core_selftest__` after the run | absent |
| `HKCU\Software\__win_reg_core_view__` after the run | absent |
| `HKLM\SOFTWARE\__win_reg_core_selftest__` | absent (never created) |
| `%TEMP%\wrc_selftest_<pid>` | removed |

Two problems surfaced during the first full run and were fixed:

* 15 parse tests failed because their ASCII fixtures had an odd byte length
  and no BOM, which §7.2 correctly rejects. The library was right; the fixtures
  now carry a UTF-8 BOM (matching how a real editor saves a `.reg` file).
* `reg_plan_parse` did not validate `RegOptions.struct_size` before using the
  structure (the §9.4 ABI guard was only applied by the applier). The check is
  now shared by `reg_decode_bytes`, `reg_plan_parse` and the applier.

A third observation changed a test, not the library: `HKLM\SOFTWARE` widens the
view split, but `HKCU\Software` is shared between the 32- and 64-bit views, so
the `view/wow64` test asserts read-back through the same view instead of two
distinct HKCU datasets.
