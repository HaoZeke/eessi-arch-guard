# eessi-arch-guard 0.1.2: the interface a port keeps

Source: `src/eessi_arch_guard.py`, 1817 lines, `__version__ = "0.1.2"` (line 32).
All "L" numbers are lines of that file unless a test or doc file is named. Python semantics that a
port must reproduce are called out in the Notes column.

Entry points: `if __name__ == "__main__": sys.exit(main())` (L1816-1817); `main` L1800-1813; `_main` L1734-1797.
Slurm runs the file as `TaskProlog` with no arguments, so the argument-less path is the production path.

## 1. Command-line options

| Flag | Argument grammar | Default | Effect | Lines |
|---|---|---|---|---|
| `--shell` | none | off | Selects shell mode: stdout gets `export K='q'` / `unset K` only; the message goes to stderr; no `print` line; the chain prolog is never run; multi-line values are allowed in the delta. | parse L1684-1685; chain skipped L1753; stderr message L1790-1791; `announce` false L1709-1710; `multiline=shell` L1195; no `print` L379 |
| `--strict` | none | off | Exit 78 when the outcome kind is `failed`, when the decision/render raises, or when the outer wrapper catches an exception. Also changes workspace selection (see section 6). | parse L1686-1687; L1788, L1795-1796, L1811; `STRICT_FAILURE_EXIT = 78` L116 |
| `--restore` | none | off | Helper mode: read one JSON request on stdin, write one JSON object on stdout. Checked before `--help` and `--version`. | parse L1688-1689; dispatch L1736-1737; `restore_main` L957-964 |
| `-h`, `--help` | none | off | Print `HELP` to stdout, exit 0. | parse L1690-1691; L1738-1740; text L141-154 |
| `--version` | none | off | Print `0.1.2` plus newline to stdout, exit 0. | parse L1692-1693; L1741-1743 |
| `--chain PATH` | Next argv element is consumed unconditionally as the value, even if it starts with `-`. A missing value yields `""`. | none (falls back to config key `chain`) | Run PATH as a prolog first (prolog mode only). An explicit value, even invalid or empty, suppresses the config `chain`. | parse L1694-1696; use L1753-1765 |
| `--chain=PATH` | Value is everything after the first `=`. | as above | Same. | L1697-1698 |
| `--timeout SEC` | Next argv element consumed unconditionally; missing yields `""`. Value parsed as a float. | 60 s | Overrides config key `timeout`. Invalid value: warning on stderr, previous value kept. | parse L1699-1701; `parse_timeout` L1507-1515; `resolve_timeout` L1518-1544; default L115 |
| `--timeout=SEC` | As `--chain=`. | as above | Same. | L1702-1703 |
| any other argv element | ignored silently (positional words, unknown flags, `--shell=x`, abbreviations). | n/a | No error, no effect. | docstring L1677; fall-through L1704 |
| later duplicate | `--chain` and `--timeout`: last occurrence wins. Booleans are idempotent. | n/a | n/a | L1694-1703 |
| strict detection in wrapper | `main` sets the global strict flag by exact membership `"--strict" in argv` over the raw argv, independent of `parse_args`. `--chain --strict` therefore leaves `args.strict` False but the strict workspace policy True. | n/a | n/a | L1806 |
| outer-handler shell detection | `main`'s exception handler picks the stderr/stdout channel with `"--shell" in argv` (raw membership). | n/a | n/a | L1810 |
| `--timeout` numeric grammar | `float(text)`: accepts leading/trailing whitespace, `+`, exponents, `_` digit separators, `inf`, `nan` (the last two rejected afterwards). Accepted only when finite-comparable, `value > 0` and `value <= threading.TIMEOUT_MAX` (9223372036.0 on this platform). | n/a | Rejected value gives the `--timeout is not a positive number...` warning. | L1507-1515, L118 |
| help text exact | See section 5 row "HELP". | n/a | n/a | L141-154 |

## 2. Exit codes

| Code | Condition | Lines |
|---|---|---|
| 0 (REQUIRED) | `--help`. | L1738-1740 |
| 0 (REQUIRED) | `--version`. | L1741-1743 |
| 0 (REQUIRED) | Normal prolog/shell run, any outcome kind (`skip`, `noop`, `reloaded`), and `failed` when `--strict` is absent. | L1797 |
| 0 (REQUIRED) | `failed` outcome without `--strict` (including `timeout` and `terminated` tokens). | L1795-1797 |
| 0 (REQUIRED) | Any `Exception` raised by `limited_decide` or `render_bytes` without `--strict`: chain output is flushed, one skip line is emitted, exit 0. | L1785-1788 |
| 0 (REQUIRED) | Any `Exception` or `KeyboardInterrupt` escaping `_main` (for example a failing `site_config`, `run_chain` raising `OSError`) without `--strict`: skip line, exit 0. | L1809-1811 |
| 0 (REQUIRED) | Failed stdout/stderr write (EPIPE, `/dev/full`, closed pipe): every `os.write` `OSError` and a non-positive write count are dropped. Python ignores SIGPIPE at start-up, so the process is not killed. | L337-349; tests `_assert_writes_exit_zero` (test_unit.py L702, called at L369; test_integration.py L555-577 inside test_non_utf8_path_entry_keeps_its_bytes L537 |
| 0 (REQUIRED) | Config file untrusted/unreadable/unparsable, invalid `chain` path, invalid `--chain`, invalid timeout: warnings only. | L1766-1767, L1776-1778 |
| 0 (REQUIRED) | Cache directory missing, lock file untrusted, cache entry invalid, cache write failure: decision proceeds without cache. | L1418-1439, L1383-1385, L1316-1346 |
| 0 (REQUIRED) | Deadline expiry or SIGTERM during the decision: converted to a `failed` outcome with token `timeout` / `terminated`. | L1483-1501 |
| 0 (REQUIRED) | Exception inside `_emergency` itself (encoding failure and so on) is swallowed. | L1730-1731 |
| `STRICT_FAILURE_EXIT` = 78 | `--strict` and outcome kind is `failed`. | L116, L1795-1796 |
| 78 | `--strict` and an `Exception` from `limited_decide` or `render_bytes`. | L1785-1788 |
| 78 | `--strict` (raw `"--strict" in argv`) and any `Exception` or `KeyboardInterrupt` escaping `_main`. | L1806, L1809-1811 |
| chain exit code N (1..255) | Chain prolog returned non-zero: its stdout is forwarded, then `return code`; no decision is made, no restore. A chain killed by a signal (negative `returncode`) is mapped to 1. Takes precedence over strict 78. | L1772-1774; mapping L1660; test_strict_status.py `test_chained_prolog_failure_preserves_its_status` L74 |
| 1 | `--restore` helper hits any `Exception` (bad JSON, non-dict payload, `AttributeError`, and so on); reason on stderr. | L962-964 |
| 0 | `--restore` success (JSON written to stdout; note this includes `{"ok": false, ...}` results). | L958-961 |
| signal death | SIGTERM before the handler is installed or after it is restored, SIGKILL: process dies by signal; no exit code contract. SIGTERM during the decision is handled (row above). | L474-475, L1479, L1503 |
| not a Python exit code | Interpreter start failure (`#!/usr/bin/python3 -I` missing) gives the shell's 126/127; not reproduced by a native binary. | L1 |

## 3. Environment variables read

All values come from `os.environ` copied at L1745 unless a row says the helper reads its own process environment. "Invalid" behaviour is as coded; nothing raises.

| Name | Controls | Accepted values | Behaviour on other value | Lines |
|---|---|---|---|---|
| `EESSI_ARCH_GUARD` | Opt-out | exactly `"0"` skips (`kind=skip`, detail `EESSI_ARCH_GUARD=0`). | Any other value, including empty, is ignored. Not in the cache key. | L1076-1077; key list L35-43 |
| `EESSI_SOFTWARE_SUBDIR` | CPU target recorded at load | non-empty string | Empty/unset: skip (`EESSI is not loaded`). Value also compared with probe result, used in `was` label, cache key, and failure tokens. | L1078-1080, L1053, L1462 |
| `EESSI_ACCEL_SUBDIR` | Accelerator recorded at load | string, empty means no accelerator | Compared to the `accelpath` result; empty renders `no accel`; cache key. | L1106, L1055, L1124-1127 |
| `EESSI_SOFTWARE_SUBDIR_OVERRIDE` | Pins detection | any non-empty value: skip. | Empty ignored. The guard itself sets it (in the reload environment only) when the waitpkg fallback chooses a directory. | L1081-1082, L1152-1159 |
| `EESSI_ARCHDETECT_OPTIONS_OVERRIDE` | Pins detection | any non-empty value: skip. | Empty ignored. | L1083-1084 |
| `EESSI_ARCHDETECT_OPTIONS` | Archdetect options | not interpreted | Removed from the reload environment when a CPU override is applied. | L1157 |
| `EESSI_ACCELERATOR_TARGET_OVERRIDE` | Pins accelerator | any non-empty value: accelerator not compared (`now_accel = was_accel`, `accelpath` not run). | Empty means compare. | L1105, L1117-1118 |
| `EESSI_ARCH_GUARD_MISSING` | No software dir behaviour | exactly `"ignore"` gives silent skip (`no EESSI software directory (ignored)`). | Any other value gives the loud failure. | L1094-1095 |
| `EESSI_ARCH_GUARD_VERBOSE` | Extra stderr | exactly `"1"` writes `outcome.detail` to stderr when non-empty. | Other values: nothing. | L1792-1793 |
| `EESSI_ARCH_GUARD_WORKDIR` | Private base for restore temporaries | absolute path without `..` segments, existing directory, owned by the invoking uid, mode with no group/other bits, not a symlink at the last component. Read from the process environment in the helper, from the task env elsewhere. | Set but invalid: `ValueError` "EESSI_ARCH_GUARD_WORKDIR is not an existing private owned directory" from `restore_work_base`; `call_restore` turns it into `restore workspace unavailable: ...`; `cache_directory` (strict only) returns no cache. Not consulted for the cache in non-strict mode. | L802-809, L830, L978-980, L1236-1240 |
| `TMPDIR` | Workspace/cache base | Non-strict cache: non-empty, `safe_absolute`, existing dir. Workspace: private owned dir. | Invalid: non-strict falls to `/tmp` (cache) or `tempfile.gettempdir()`/TMPDIR if `safe_absolute` and isdir (workspace); strict falls to state dir, else error. Never emitted in the delta. | L810-824, L1241-1246 |
| `XDG_STATE_HOME` | Strict-mode state workspace parent | directory path | Empty/unset: `$HOME/.local/state`. Leaf `eessi-arch-guard` must exist and be private. | L814-821 |
| `HOME` | Strict-mode fallback only | path | Missing gives `"" + .local/state` relative join. | L817 |
| `EESSI_PREFIX` | Software tree root | `safe_absolute` path | Missing/unsafe: `CpuProbe error` "EESSI_PREFIX, EESSI_OS_TYPE, or EESSI_INIT_PREFIX is missing or unsafe". | L675, L678-688 |
| `EESSI_OS_TYPE` | OS subdir | `safe_rel_subdir` | Same error. | L676, L678-688 |
| `EESSI_INIT_PREFIX` | Dir holding `eessi_archdetect.sh` | `safe_absolute` path | Same error (CPU) or `EESSI_INIT_PREFIX is missing or unsafe` (accel). Cache key. | L677, L733-735 |
| `EESSI_PROC_CPUINFO` | cpuinfo file | any path | Empty/unset: `/proc/cpuinfo`. Also passed through to archdetect (child inherits env). Cache key. | L710 |
| `LOADEDMODULES` | Loaded module list | colon list; empty entries dropped; see section 8 | See `parse_loaded_modules` errors in section 5. Cache key. | L419-429 |
| `MODULEPATH` | Cache key only | any | Not parsed. | L38, L1225 |
| `PATH` | Cache key; also resolves `bash`, `env`, Lmod's own commands in children | any | Not parsed. The helper gets `/usr/bin:/bin`. | L42, L982 |
| `LMOD_CMD` | Lmod executable | non-empty, not starting `-`, no `\n \r \0`, `isfile` and `X_OK` | Missing/invalid on mismatch: `lmod-unavailable` failure. Read only after a target mismatch is found. | L1142-1149, L755-758, L975 |
| `SLURM_JOB_ID` | Cache scope | any string | Empty/unset: no cache at all. Non-matching `^[A-Za-z0-9._-]{1,64}$` is replaced by a 16-hex SHA-256 prefix. | L1251-1253, L1229-1232 |
| `SLURM_STEP_ID` | Cache scope | any string | Unset: `batch`. Empty string stays empty and is hashed. | L1257, L1229-1232 |
| `SLURM_LOCALID` | Who prints | exactly `"0"` or unset prints the `print` line (prolog mode). | Any other value (including empty) suppresses `print`; exports still emitted. | L1708-1711 |
| locale (`LANG`, `LC_ALL`, `LC_CTYPE`) | Encoding for `print` text via `locale.getpreferredencoding(False)` | any | Unknown codec: falls back to ASCII with `errors="replace"`. Export values use `os.fsencode` (filesystem encoding, surrogateescape). | L324-334, L375 |
| `BASH_ENV`, `ENV`, `SHELLOPTS`, `BASHOPTS`, `BASH_XTRACEFD`, `PS4` | Read only to be removed from every child environment | n/a | Always stripped from archdetect, Lmod, and apply children. | L45-52, L253-257 |
| `SLURM_*`, `BASH_FUNC_*` etc. | Never read for decisions; excluded from delta | n/a | See section 4. | L268-273 |
| helper process env | The `--restore` child has only `PATH=/usr/bin:/bin`, `LC_ALL=C`, `LANG=C`, `HOME=<work>`, `TMPDIR=<work>`, `EESSI_ARCH_GUARD_WORKDIR=<work>`. The task env travels in the JSON body. | n/a | n/a | L981-988 |

## 4. Environment variables written, exported, unset; never-touch and deny lists

| Item | Content (quoted exactly) | Lines |
|---|---|---|
| Written by the guard: failure marker | `EESSI_ARCH_GUARD_FAILED` = `"<was>-><token-or-now>"` | L1038, L1035-1042 |
| Written by the guard: success marker | `EESSI_ARCH_GUARD_RELOADED` = `"%s->%s" % (_side(was_label), _side(now_label))`; added to every successful delta unconditionally | L1203 |
| Unset on success | `EESSI_ARCH_GUARD_FAILED` as `None` (rendered `unset`) when it was present in the input env and not otherwise in the delta | L1205-1206 |
| Delta | Every other variable whose value differs between input env and restored env, subject to the lists below; removed variables become `unset NAME`. Includes `EESSI_SOFTWARE_SUBDIR_OVERRIDE` when the waitpkg pin was applied and the module exported it, and `unset EESSI_ARCHDETECT_OPTIONS` when it was dropped for the reload and the module did not re-export it. | L1195-1203, L1152-1159 |
| Emission order | `sorted(changes)` (Python string order = codepoint order), `export`/`unset` lines first, then the `print` line | L360, L379-383 |
| `KEY_FIELDS` (cache key inputs, order matters) | `("EESSI_SOFTWARE_SUBDIR", "EESSI_ACCEL_SUBDIR", "LOADEDMODULES", "MODULEPATH", "EESSI_INIT_PREFIX", "EESSI_PROC_CPUINFO", "PATH")`; `EESSI_ARCH_GUARD` deliberately absent | L35-43 |
| `STARTUP_VARS` (stripped from child environments) | `("BASH_ENV", "ENV", "SHELLOPTS", "BASHOPTS", "BASH_XTRACEFD", "PS4")` | L45-52 |
| `NEVER_EMIT` (frozenset, never in a delta) | `"_"`, `"SHLVL"`, `"PWD"`, `"OLDPWD"`, `"TMPDIR"`, `"BASH_ENV"`, `"ENV"`, `"SHELLOPTS"`, `"BASHOPTS"`, `"BASH_XTRACEFD"`, `"PS4"`, `"BASH_ARGC"`, `"BASH_ARGV"`, `"BASH_LINENO"`, `"BASH_SOURCE"`, `"BASH_VERSINFO"`, `"BASH_VERSION"`, `"DIRSTACK"`, `"EPOCHREALTIME"`, `"EPOCHSECONDS"`, `"FUNCNAME"`, `"GROUPS"`, `"HOSTNAME"`, `"HOSTTYPE"`, `"IFS"`, `"MACHTYPE"`, `"OPTERR"`, `"OPTIND"`, `"OSTYPE"`, `"PIPESTATUS"`, `"PPID"` | L56-90 |
| Prefix rules (`_never_touch`) | `name in NEVER_EMIT` or `name.startswith("BASH_FUNC_")` or `name.startswith("SLURM_")` | L268-273 |
| Cache-only extra deny (`_never_cache`) | `_never_touch(name)` or `name.startswith("LD_")`. A cache entry whose `changes` carries such a name is rejected wholesale. `LD_*` is not denied in a live delta. | L1268-1270, L1297-1298 |
| `SOFT_DROP` | `{"PS1", "PS2", "PS3", "PROMPT_COMMAND"}`: an unsafe new value (CR/LF in prolog mode, NUL always) is skipped instead of aborting the restore. The branch at L292-293 (name fails `ENV_NAME_RE` and is in `SOFT_DROP`) is unreachable because all four names match `ENV_NAME_RE`. | L93, L291-303 |
| Abort rule | Name fails `ENV_NAME_RE`, or value has NUL, or (prolog mode) CR/LF, and not in `SOFT_DROP`: `environment_delta` returns `None`, outcome is failed token `unsafe-environment`, nothing partial is applied. Shell mode permits CR/LF (`multiline=True`) but never NUL. | L281-307, L260-265, L1195-1202 |
| Never touched keys are compared before anything else | The `_never_touch` check precedes the equality and validity checks, so an unsafe `SLURM_*` or `BASH_FUNC_*` value never aborts. | L285-286 |
| Prolog line limit | An export or print line over `PROLOG_LINE_MAX = 4094` bytes raises `ValueError`; at decision time this becomes failed `unsafe-environment`. | L112, L376-377, L381-382, L1211-1220 |

## 5. Output formats

### 5a. stdout and stderr line formats

| Channel / mode | Exact format | Lines |
|---|---|---|
| stdout, prolog, set | `"export " + NAME + "=" + value + "\n"`, bytes: name ASCII, value `os.fsencode(value)` verbatim (no quoting, no escaping). `$`, quotes and backticks are literal (Slurm reads literally). | L375, L373-374 |
| stdout, prolog, unset | `"unset " + NAME + "\n"` | L365 |
| stdout, prolog, message | `"print " + message_bytes(message) + "\n"`; emitted only when `message` is non-empty and `SLURM_LOCALID` is `"0"` or unset; never in shell mode. | L379-383, L1708-1711 |
| stdout, shell, set | `"export %s=%s" % (key, shlex.quote(value))` + `\n`, encoded `os.fsencode`. `shlex.quote` semantics: empty string gives `''`; a string made only of `[A-Za-z0-9_@%+=:,./-]` is left bare; otherwise wrapped in single quotes with each `'` replaced by `'"'"'`. | L369-371 |
| stdout, shell, unset | `"unset " + NAME + "\n"` | L365 |
| stdout, shell, message | None. The message is written to stderr as `message_bytes(outcome.message) + b"\n"` whenever the outcome has a message (reload or failure). | L1790-1791 |
| stdout, chain prefix | Chain stdout bytes are written first; if non-empty and not ending in `\n`, a `\n` is appended; then the guard's own text. | L1714-1719 |
| stdout, emergency (prolog) | `b"print " + message_bytes("eessi-arch-guard: skipped, %s: %s" % (type(exc).__name__, exc)) + b"\n"`. Ignores `SLURM_LOCALID` (always printed). Exception class names are Python-specific. | L1722-1731 |
| stderr, emergency (shell, per raw `--shell` in argv for the outer handler) | Same text and `\n`, to fd 2. | L1726-1727, L1810 |
| stderr, `--restore` failure | `"eessi-arch-guard: restore failed: %s\n" % one_line(str(exc), 300)` | L963 |
| stdout, `--restore` | `json.dumps(result)` with default separators (`", "`, `": "`), no trailing newline. | L960 |
| `HELP` (stdout, exit 0) | Exactly L141-154 with `%s` = `60`. Lines: `usage: eessi-arch-guard [--shell] [--strict] [--chain PATH] [--timeout SEC]` / `                        [--restore] [-h|--help] [--version]` / blank / `Slurm TaskProlog: when the loaded EESSI CPU or accelerator target is not this` / `node's, print export/unset lines for the reloaded module environment.` / `--shell prints bash for: eval "$(eessi-arch-guard --shell)"` / `--strict returns 78 on a failed decision or an unexpected exception.` / `--chain PATH runs an existing task prolog first. Slurm does not pass arguments,` / `so a site sets this in a wrapper or in a root-owned eessi-arch-guard.conf.` / `--timeout SEC bounds the decision. The default is 60 seconds.` / `--restore reads a JSON restore request on stdin and writes the JSON result.` / `-h, --help print this text. --version prints the version.` Each line ends `\n`; encoded ASCII. | L141-154, L135-138, L1739 |
| `--version` | `0.1.2\n` | L1742 |
| `one_line(text, limit=500)` | Replace `\r`, `\n`, `\0`, any char with ord < 32 or == 127 by a space; `strip()` (Unicode whitespace); if longer than `limit` characters, keep `limit - 3` characters and append `...`. | L211-222 |
| `message_bytes(text)` | `one_line(text)`, then encode in the locale's preferred encoding (`errors="replace"`; unknown codec falls back to ASCII), then cut to at most `4094 - len("print ") - 1 = 4087` bytes at a boundary that still decodes. | L324-334 |

### 5b. Messages and markers by decision (the `message`, `EESSI_ARCH_GUARD_FAILED` token, and `detail`)

`was` is `EESSI_SOFTWARE_SUBDIR`; labels are `"<cpu> <accel or 'no accel'>"` when the accelerator differs and the compare is active (L1124-1128). `_side(x)` is `x`, or `unrecognized` if empty or containing `\n \r \0` (L1029-1032). The token is `"%s->%s" % (_side(was), _side(now))` (L1036). Messages pass through `one_line` (L1041).

| Situation | Outcome / marker | Message (exact format string) | Lines |
|---|---|---|---|
| `EESSI_ARCH_GUARD=0` | skip, no output, detail `EESSI_ARCH_GUARD=0` | none | L1076-1077 |
| EESSI not loaded | skip, detail `EESSI is not loaded` | none | L1078-1080 |
| subdir override set | skip, detail `EESSI_SOFTWARE_SUBDIR_OVERRIDE is set` | none | L1081-1082 |
| options override set | skip, detail `EESSI_ARCHDETECT_OPTIONS_OVERRIDE is set` | none | L1083-1084 |
| CPU probe error | failed, token `<was>->archdetect-failed` | `"eessi-arch-guard: archdetect failed (%s); environment left as it was" % cpu.error` | L1087-1092 |
| no software dir, `MISSING=ignore` | skip, detail `no EESSI software directory (ignored)` | none | L1094-1095 |
| no software dir | failed, token `<was>->no-software-dir` | `"eessi-arch-guard: no EESSI software directory for this node under %s (candidates %s); modules were loaded for %s; environment left as it was" % (EESSI_PREFIX, shown, was)`; `shown` = candidates joined by space, or `(none)` | L1096-1103 |
| accel probe error | failed, token `<was>->archdetect-failed` | `"eessi-arch-guard: archdetect failed (%s); environment left as it was" % accel.error` | L1109-1115 |
| already matching | noop, detail `target already matches this node` | none | L1121-1122 |
| unsafe module name | failed, token `<was_label>->unsafe-module-name` | `"eessi-arch-guard: %s; environment left as it was"` with why = `a loaded module name is not safe to pass to Lmod` | L1130-1141 |
| no single `EESSI/<version>` / empty list | failed, token `<was_label>->restore-failed` | same template, why = `the loaded module list has no single EESSI/<version> entry` | L1130-1141, L418-429 |
| `LMOD_CMD` unusable | failed, token `<was_label>->lmod-unavailable` | `"eessi-arch-guard: LMOD_CMD is missing or not executable; this node is %s, the modules were loaded for %s; environment left as it was" % (now_label, was_label)` | L1142-1149 |
| restore raised | failed, token `<was_label>->restore-failed` | `"eessi-arch-guard: restore failed (%s); environment left as it was" % one_line(str(exc), 200)` | L1160-1168 |
| restore not ok | failed, token `<was_label>-><now_label>`, detail = restore error | `"eessi-arch-guard: this node is %s, the modules were loaded for %s; %s; environment left as it was" % (now_label, was_label, why)`; why = `"not available for this target: " + names` (names joined by space, plus `" (stopped after %s)" % 32` when `len(loaded.others) > 32`) or `module load failed` | L1169-1183 |
| reload mismatch | failed, token `<was_label>->restore-failed` | `"eessi-arch-guard: %s; environment left as it was" % mismatch`; mismatch strings: `reload did not select this node's CPU target`, `reload did not select this node's accelerator target`, `reload did not load the EESSI module`, `"reload did not load %s" % name` | L1185-1193, L1045-1063 |
| delta unsafe | failed, token `<was_label>->unsafe-environment` | `"eessi-arch-guard: reload produced a value this output mode cannot carry; environment left as it was"` | L1195-1202 |
| prolog line too long | failed, token `<was_label>->unsafe-environment` | `"eessi-arch-guard: reload produced a prolog line past the Slurm 17.11 read; environment left as it was"` | L1211-1220 |
| success | reloaded; `EESSI_ARCH_GUARD_RELOADED=<was_label>-><now_label>` | `"eessi-arch-guard: reloaded modules for %s (loaded for %s)" % (now_label, was_label)` | L1203-1210, L1221 |
| deadline | failed, token `<EESSI_SOFTWARE_SUBDIR>->timeout`; a cache entry (read without lock) wins if present | `"eessi-arch-guard: decision exceeded the time limit; environment left as it was"` | L1483-1495, L1461-1462 |
| SIGTERM | failed, token `<EESSI_SOFTWARE_SUBDIR>->terminated` | `"eessi-arch-guard: decision was terminated; environment left as it was"` | L1496-1501 |
| exception in decision/render | no env output; skip line | `"eessi-arch-guard: skipped, %s: %s" % (type(exc).__name__, exc)` | L1722-1731, L1785-1788 |
| `EESSI_ARCH_GUARD_VERBOSE=1` | stderr gets `message_bytes(outcome.detail) + b"\n"` when detail is truthy (skip/noop reasons, and restore error text on a `failed` outcome); never for `reloaded` (detail `None`). | none | L1792-1793, L1221 |

### 5c. Probe and helper error strings (these become `%s` parts of the messages above)

| Source | Text | Lines |
|---|---|---|
| `probe_cpu` unsafe env | `EESSI_PREFIX, EESSI_OS_TYPE, or EESSI_INIT_PREFIX is missing or unsafe` | L687 |
| `probe_cpu` no script | `eessi_archdetect.sh is not available` | L691 |
| `probe_cpu` spawn error | `"could not run archdetect: %s" % exc` | L695 |
| `probe_cpu` non-zero exit | first stderr line passed through `one_line(.., 200)`, or `"exit %s" % returncode` | L696-699 |
| `probe_cpu` parse | `cpupath output is not a single line`; `cpupath returned no candidates`; `cpupath returned an unsafe candidate` | L703, L706, L709 |
| `probe_accel` | `EESSI_INIT_PREFIX is missing or unsafe`; `eessi_archdetect.sh is not available`; `could not run archdetect: %s`; `accelpath output is not an accelerator subdirectory` | L735, L738, L742, L751 |
| `call_restore` | `"restore workspace unavailable: %s" % exc`; `"could not start restore helper: %s" % exc`; `one_line(stderr or "restore helper failed", 300)`; `restore helper returned invalid output`; `restore helper returned no environment` | L980, L1001, L1004, L1008, L1017, L1023 |
| `do_restore` | `restore request is malformed`; `LMOD_CMD is not executable`; `unsafe-module-name`; `one_line(err or "purge failed", 300)`; `one_line(err or "EESSI load failed", 300)`; `one_line(err or "module load failed", 300)` | L916, L925, L927, L929, L931, L933, L936, L942, L951 |
| `source_exports` | stderr text, else `"apply exited %s" % returncode` | L845-847 |
| config warnings | `"eessi-arch-guard: config ignored: " + reason` with reason `"%s (%s)" % (path, exc)`, `"%s is a symlink and is ignored"`, `"%s is not a regular file and is ignored"`, `"%s is not owned by root and is ignored"`, `"%s is writable by group or others and is ignored"`; `"eessi-arch-guard: config ignored: %s" % exc` on read error; `"eessi-arch-guard: config value kept with its original bytes: %s" % shown` (keys joined by space); `"eessi-arch-guard: config chain is not an absolute executable: %s" % one_line(chain, 200)`; `"eessi-arch-guard: --chain is not an absolute executable"`; `"eessi-arch-guard: config timeout is not a positive number of seconds at most %s" % TIMEOUT_MAX`; `"eessi-arch-guard: --timeout is not a positive number of seconds at most %s" % TIMEOUT_MAX` (`TIMEOUT_MAX` prints as `9223372036.0`); two timeout warnings are joined with one space. Each is written via `message_bytes(note) + b"\n"` to fd 2. | L1547-1560, L1618-1629, L1757-1767, L1527-1544, L1776-1778 |
| chain stderr | Chain's captured stderr is copied verbatim to fd 2 after the config notes, before the chain status is examined. | L1769-1771 |

### 5d. `--restore` JSON protocol (stdin to stdout, between guard and helper)

| Message | Shape | Lines |
|---|---|---|
| Request | `{"v": 1, "env": {name: value}, "eessi": str, "modules": [str], "lmod": str}`; every string is a "wire" string: one Latin-1 character per byte of the real value (`os.fsencode(x).decode("latin-1")`). `v` is written but not checked by the helper. | L967-976, L895-906, L909-927 |
| Reply, failure | `{"ok": false, "missing": [...], "error": str}` (no `env` key) | L916, L936-952 |
| Reply, success | `{"ok": true, "env": {wire name: wire value}, "missing": [], "error": ""}` | L954 |
| Parent-side validation of reply | `missing` entries kept only when `str` and `module_name_ok`; `ok` falsy gives failure with `one_line(str(error), 300)`; `env` must be a dict; empty stdout or non-zero exit gives failure with stderr text | L1002-1024 |
| Helper's lmod sequence | `--force purge`, then `load <eessi>`, then (if any) one `load <m1> <m2> ...`; on that last failing, per-module `_diagnose` replays purge, load EESSI, load module for each of the first 32 modules and lists the ones that fail | L934-953, L873-892 |

## 6. Files read or written

| Path | Direction | Format / rules | Trust checks | Lines |
|---|---|---|---|---|
| `/etc/eessi-arch-guard.conf` | read | first candidate | see next row | L1613 |
| `<dirname(abspath(script))>/eessi-arch-guard.conf` | read | second candidate, used only if the first does not `os.path.exists` (a dangling symlink counts as absent). The first existing candidate wins even if untrusted: no fall-through. | `lstat`: symlink rejected; non-regular rejected; `st_uid != 0` rejected; `st_mode & 0o022` rejected. Only the file itself is checked (not parent directories, not `S_ISUID`). | L1610-1631, L1547-1560 |
| config grammar | `blob.splitlines()` on bytes (`\n`, `\r`, `\r\n`); each line `strip()`ped (ASCII whitespace); skip if empty, starts with `#`, or has no `=`; split on the first `=`; key and value stripped; key must be valid UTF-8 and non-empty (otherwise skipped silently); a non-UTF-8 value is decoded `utf-8`/`surrogateescape` and its key is added to the "bad" list (warning, key kept); duplicate keys: last wins; no inline comments, quotes or continuation; no size cap (whole file read). Recognised keys: `chain` (empty counts as unset), `timeout`. Unknown keys ignored. | n/a | L1563-1591, L1598-1601 |
| `chain` validity | value must start with `/`, contain no `\n \r \0`, be a regular file (`isfile`), and `os.access(X_OK)`. No owner/mode check on the chain executable. | n/a | L1604-1607 |
| `/proc/cpuinfo` or `$EESSI_PROC_CPUINFO` | read | Up to 16 MiB (`_CPUINFO_READ_MAX = 16 * 1024 * 1024`), decoded UTF-8 `replace`; every line matching `(?im)^flags[ \t]*:[ \t]*(.*)$` is split on whitespace; the effective flag set is the intersection over all such lines; no match or unreadable: empty set (then every WAITPKG march is rejected). No trust check. | none | L636-658, L710 |
| `<EESSI_INIT_PREFIX>/eessi_archdetect.sh` | executed (through `bash`) | `os.path.isfile` (follows symlinks); executable bit not required. | none | L689-691, L736-738 |
| `<EESSI_PREFIX>/software/<EESSI_OS_TYPE>/<candidate>/software` | stat | `os.path.isdir`; first candidate in archdetect order that exists. | none | L713-718 |
| Cache directory | open | Non-strict: `$TMPDIR` if non-empty, `safe_absolute` and a directory, else `/tmp` if a directory, else no cache. Strict: `restore_work_base(env)` (below), else no cache. Opened `O_RDONLY\|O_DIRECTORY\|O_CLOEXEC` (a symlinked directory is followed; no owner/mode check on the directory). | n/a | L1235-1246, L1307, L1368, L1422 |
| Cache file `<dir>/.eessi-arch-guard.<uid>.<node>.<job>.<step>.<digest>` | read, write | `name = ".eessi-arch-guard.%s.%s.%s.%s.%s" % (os.getuid(), _safe_component(nodename), _safe_component(job), _safe_component(step), digest)`; `digest` = first 16 hex of SHA-256 over `"\0".join(env.get(k, "") for k in KEY_FIELDS)` UTF-8 with `surrogateescape`; `_safe_component`: kept if it matches `^[A-Za-z0-9._-]{1,64}$`, else first 16 hex of SHA-256 of the text; `nodename` is `os.uname().nodename`; no cache when `SLURM_JOB_ID` is empty. Content: JSON `{"v": 1, "kind": ..., "message": ..., "detail": ..., "changes": {...}}` (that key order, `ensure_ascii=True`, default separators) plus `\n`. | Read: opened relative to the dir fd with `O_RDONLY\|O_NOFOLLOW\|O_NONBLOCK\|O_CLOEXEC`; fstat must be a regular file, `st_uid == getuid()`, `st_nlink == 1`, `mode & 0o077 == 0`; at most 8 MiB read in one `os.read`; JSON must be a dict with `v == 1`, `message` null or a string without `\n \r \0`, `detail` null or string, `kind` in `skip/noop/reloaded/failed`, `changes` a dict whose keys are strings matching `ENV_NAME_RE` and not `_never_cache`, and whose values are `None` or strings that pass `value_emit_ok` with `multiline=shell`. Any failure: treated as no cache (silently). | L1249-1265, L1224-1226, L1306-1346, L1291-1303, L1356-1365 |
| Cache write | write | Temp file `.<name>.<pid>.<secrets.token_hex(6)>.tmp` created `O_WRONLY\|O_CREAT\|O_EXCL\|O_NOFOLLOW\|O_CLOEXEC` mode `0600` in the same directory, `fchmod 0600`, then `os.replace` onto the cache name; `OSError` is discarded (temp unlinked); other `BaseException` unlinks the temp and re-raises. Every outcome kind is cached, except that `timeout`/`terminated` outcomes are produced outside `cached_decide` and never reach `_write_cache`. The cache key does not include the shell/prolog mode. | n/a | L1356-1392, L1447-1454 |
| Lock file `<cache path>.lock` | open, `flock` | Opened `O_RDWR\|O_CREAT\|O_NOFOLLOW\|O_NONBLOCK\|O_CLOEXEC`, mode `0600`, relative to the dir fd; never deleted; must pass the same owned-private-regular-file test (else the decision runs without cache); `flock(LOCK_EX)`; under a deadline, `LOCK_EX\|LOCK_NB` polled every `0.05` s. | `_owned_private_file` | L1427-1440, L1273-1288, L1395-1407 |
| Strict/explicit workspace base (`restore_work_base`) | stat | Order: `EESSI_ARCH_GUARD_WORKDIR` (must be private or `ValueError`); `TMPDIR` if private; if strict: `$XDG_STATE_HOME/eessi-arch-guard` (default `$HOME/.local/state/eessi-arch-guard`) if private, else `ValueError("strict mode needs an existing private job or state workspace")`; non-strict: `TMPDIR` if `safe_absolute` and a directory, else `tempfile.gettempdir()`. Never created or chmodded. | `_private_directory`: `safe_absolute`; `os.open(O_RDONLY\|O_DIRECTORY\|O_NOFOLLOW\|O_CLOEXEC)`; `st_uid == getuid()`; `mode & 0o077 == 0`; returns `realpath`. | L785-824 |
| Restore work directory | create, remove | `tempfile.mkdtemp(prefix="eessi-arch-guard-", dir=base)` (0700); becomes the helper's `HOME`, `TMPDIR`, `EESSI_ARCH_GUARD_WORKDIR`; removed with `rmtree(ignore_errors=True)` in `finally`. | none beyond the base | L978-988, L1025-1026 |
| Helper scratch directory | create, remove | In the helper: `mkdtemp(prefix="eessi-arch-guard-", dir=base)`, `chmod 0700`; `fragment.sh` (Lmod `bash` output) and `apply.sh` written `O_WRONLY\|O_CREAT\|O_EXCL\|O_NOFOLLOW\|O_CLOEXEC` mode `0600`; `apply.sh` then `chmod 0700`; removed in `finally`. `apply.sh` content is exactly `source "$1" >/dev/null || exit 4` newline `exec env -0` newline. | `_write_private` | L830-850, L761-770, L129-132 |
| `nvidia-smi` | not run by the guard | `eessi_archdetect.sh accelpath` calls it (stub contract: `tests/stubs/nvidia-smi`, `tests/stubs/eessi_archdetect.sh`). Guard sees only the archdetect exit status and stdout. Exit 2 (no nvidia-smi) and 3 (nvidia-smi failed) are "no GPU"; any non-zero is "no GPU" to the guard. | none | L743-746 |
| stdout fd 1, stderr fd 2 | write | direct `os.write`, short writes continued, errors dropped. | none | L337-349 |
| Python source/README/pyproject | read by tests only | see section 10 | none | n/a |

## 7. External programs run

| Program | Resolution | Arguments | Environment | Timeouts / session | Output handling | Lines |
|---|---|---|---|---|---|---|
| `bash` (CPU detection) | Bare name; Python resolves it with the PATH of the `env` passed (task PATH in the parent) | `bash --noprofile --norc -- <INIT>/eessi_archdetect.sh -a cpupath` | task env minus `STARTUP_VARS` | Own session (`start_new_session=True`) while a decision limit is active; killed with SIGKILL to its process group at the deadline; `stdin` is `/dev/null`; stdout/stderr piped. | exit != 0: error text = first stderr line. stdout decoded UTF-8 `replace`; must contain `\0` free, at most 65536 chars; `splitlines()`, empty lines dropped, exactly one remaining; no char below ord 32; split on `:`; empty items dropped; each candidate must pass `safe_rel_subdir`. | L603-608, L693, L696-709, L432-441 |
| `bash` (accelerator detection) | as above | `bash --noprofile --norc -- <INIT>/eessi_archdetect.sh accelpath` (no `-a`) | as above | as above | any non-zero exit: status `none`; stdout `strip()`ped; empty: `none`; contains `\n`/`\r` or fails `ACCEL_RE`: error; else `ok` with the text. | L740-752 |
| helper (self) | `sys.executable`, absolute script path | `<python> -I -X utf8 <abspath(__file__)> --restore [--strict]` (`--strict` iff the global strict flag is set) | exactly `PATH=/usr/bin:/bin`, `LC_ALL=C`, `LANG=C`, `HOME=<work>`, `TMPDIR=<work>`, `EESSI_ARCH_GUARD_WORKDIR=<work>` | Own session, same deadline and kill as above; stdin is the JSON request; stdout/stderr piped. | See section 5d. | L989-1024 |
| `$LMOD_CMD` (inside helper) | Path used as given (must satisfy `lmod_invocable`) | `<lmod> bash --force purge`; `<lmod> bash load <EESSI/ver>`; `<lmod> bash load <m1> <m2> ...` (other modules, in `LOADEDMODULES` order, one invocation) | env of the current step (JSON env, then the previous step's result) minus `STARTUP_VARS`; `capture_output`; stdin inherited from the helper (consumed pipe) | No per-call timeout (bounded by the parent killing the helper session). | non-zero exit: stderr is the error; zero: stdout (shell code) goes to `apply.sh`. | L853-870, L934-953 |
| `bash` (apply, inside helper) | bare name via module-env PATH | `bash --noprofile --norc -- <tmp>/apply.sh <tmp>/fragment.sh`; script runs `source "$1" >/dev/null || exit 4` then `exec env -0` (resolved on the module-env PATH; requires GNU/uutils `env -0`) | env of the step (same input env as the Lmod call) minus `STARTUP_VARS` | none | stdout split on `\0`; parts without `=` skipped; each split at the first `=`; names and values decoded UTF-8 `surrogateescape`. non-zero: failure with stderr or `apply exited N`. | L839-848, L773-782 |
| chain prolog | the `PATH` string itself, executed directly (no shell, no arguments); must be an absolute path to an executable file | `[path]` | full `os.environ` copy (not stripped) | Not under the deadline, not in its own session; stdin inherited | stdout and stderr captured in full; stderr forwarded raw to fd 2; stdout forwarded to fd 1 before the guard's own text; exit code returned if non-zero (negative mapped to 1). Chain is never run in `--shell` mode. | L1652-1661, L1768-1774, L1753 |
| (diagnosis) | The `_diagnose` loop repeats Lmod/apply for up to `DIAGNOSE_LIMIT = 32` modules after a failed multi-module load. | see Lmod rows | see Lmod rows | see Lmod rows | missing list in the helper reply | L104, L873-892 |

## 8. Regular expressions and validators

| Name | Definition (exact) | Used with | Validates | Lines |
|---|---|---|---|---|
| `_MOD_SEGMENT` | `[A-Za-z0-9_][A-Za-z0-9._+-]*` | building block | one module path segment | L95 |
| `MODULE_NAME_RE` | `^{_MOD_SEGMENT}(?:/{_MOD_SEGMENT})+$` (at least two segments) | `fullmatch` | module names in `LOADEDMODULES` and restore requests. `fullmatch` rejects a trailing newline. | L96, L230 |
| `YEAR_MODULE_RE` | `^[0-9]{4}$` | `fullmatch` | four ASCII digit year frontends (for example `2026`) | L97, L231 |
| `module_name_ok` | `isinstance(name, str)`, `0 < len(name) <= 256` (`MAX_MODULE_NAME`, L105), and fullmatch of either regex above | direct | accepted module name | L225-233 |
| `EESSI_MODULE_RE` | `^EESSI/{_MOD_SEGMENT}$` | `fullmatch` | the one EESSI entry | L98, L426, L930 |
| `parse_loaded_modules` rules | split `LOADEDMODULES` on `:`, drop empty parts; none left: error `LOADEDMODULES is empty` (maps to `restore-failed`); any `module_name_ok` failure: error `unsafe-module-name`; entries starting `EESSI/` must number exactly one and match `EESSI_MODULE_RE`, else error `eessi-module-name` (maps to `restore-failed`); other names keep order | direct | loaded list | L418-429 |
| `ENV_NAME_RE` | `^[A-Za-z_][A-Za-z0-9_]*$` | `fullmatch` | environment variable names in delta, render, cache | L99, L291, L361, L396, L1295 |
| `ACCEL_RE` | `^accel/(?:nvidia/cc[0-9]+\|amd/gfx[0-9a-f]+)$` written as `^accel/(?:nvidia/cc[0-9]+|amd/gfx[0-9a-f]+)$` | `fullmatch` after `strip()` | accelpath output | L100, L750 |
| `_PATH_SEGMENT_RE` | `^[A-Za-z0-9_.+-]+$` | `fullmatch` per segment | segments of a relative subdirectory | L101, L242 |
| `safe_rel_subdir(value)` | false if empty, starts with `/`, or contains `\`; split on `/`; every segment must not be `.` or `..` and must match `_PATH_SEGMENT_RE` (so empty segments, `//` and trailing `/` fail) | direct | `EESSI_OS_TYPE`, each cpupath candidate | L236-244 |
| `safe_absolute(path)` | false if empty, not starting `/`, or containing `\n`, `\r`, `\0`; every `/`-split segment must differ from `..` (`.` and `//` allowed) | direct | `EESSI_PREFIX`, `EESSI_INIT_PREFIX`, `TMPDIR`, private-dir paths | L247-250 |
| `value_emit_ok(value, multiline)` | false if `\0` in value; false if not `multiline` and `\n` or `\r` in value | direct | delta values, cache values | L260-265 |
| `_SAFE_COMPONENT_RE` | `^[A-Za-z0-9._-]{1,64}$` | `fullmatch` | node, job, step in the cache name | L102, L1230 |
| `_CPUINFO_FLAGS_RE` | `(?im)^flags[ \t]*:[ \t]*(.*)$` | `finditer` over decoded text | flags lines of cpuinfo (case-insensitive key, multiline anchors; `.` does not cross `\n`) | L636, L651 |
| `lmod_invocable(path)` | non-empty, not starting `-`, no `\n \r \0`, `isfile`, `access(X_OK)` | direct | `LMOD_CMD` | L755-758 |
| `_chain_path_ok(path)` | non-empty, starts `/`, no `\n \r \0`, `isfile`, `access(X_OK)` | direct | chain path (CLI or config) | L1604-1607 |
| `parse_cpu_line` | see section 7; additionally `splitlines()` is Python's Unicode line splitter (also splits on `\v`, `\f`, `\x1c`-`\x1e`, `\x85`, ` `, ` `) | direct | cpupath stdout | L432-441 |
| `march_of(candidate)` | last `/` segment with `-` replaced by `_` | direct | waitpkg lookup | L661-663 |
| `WAITPKG_MARCHES` | `"alderlake"`, `"arrowlake"`, `"arrowlake_s"`, `"clearwaterforest"`, `"diamondrapids"`, `"grandridge"`, `"graniterapids"`, `"graniterapids_d"`, `"novalake"`, `"pantherlake"`, `"sapphirerapids"`, `"sierraforest"`, `"tremont"` | `candidate_supports_cpu`: if `march_of(candidate)` is in the set the flag `waitpkg` must be in the intersected flag set; otherwise true | CPU candidate filtering | L618-634, L666-670 |
| `_side` | empty, or contains `\n \r \0`: `unrecognized` | direct | marker sides | L1029-1032 |
| `parse_timeout` | see section 1 | direct | `--timeout`, config `timeout` | L1507-1515 |
| config key/value | see section 6 | direct | config file | L1563-1591 |

## 9. Signals and timeouts

| Item | Behaviour | Lines |
|---|---|---|
| Decision deadline | One `time.monotonic()` deadline of `DEFAULT_TIMEOUT = 60.0` s (or `--timeout`/config) covers: the lock wait, both archdetect runs, the restore helper. Checked at `decide` entry, before a cache miss decides, in the lock poll loop, and around each child. | L115, L455-472, L1074-1075, L1444-1445, L1395-1407 |
| Deadline expiry | `_Deadline` (a `BaseException` subclass, not caught by `except Exception`) is raised; `limited_decide` reads the cache once (no lock, silently ignoring a miss; an `OSError` from that read would escape to the generic handler) and returns it, else a failed outcome token `timeout`. Not cached. | L444-446, L1483-1495 |
| SIGTERM | Handler installed only for the duration of `limited_decide` (`signal.signal(SIGTERM, ...)`) and restored in `finally`. It sets `terminated`, and unless `committing`, `os.killpg(child_pgid, SIGKILL)`. `_Terminated` is raised at the next `check()` or after the child returns; result is a failed outcome token `terminated`. Not cached. | L452-498, L1477-1504, L1496-1501 |
| SIGTERM and child creation | `SIGTERM` is blocked with `pthread_sigmask` around `Popen` so the handler always knows the child's process group; then unblocked. If `terminated` was set meanwhile, the child group is killed before waiting. | L494-498, L559-580 |
| Commit window | While the cache write runs, `committing` is True: `check()` returns early, the handler does not kill, and a signal arriving then does not discard the finished decision. | L467-468, L486-487, L1447-1454 |
| Child kill | At the deadline or on SIGTERM: `os.killpg(pid, SIGKILL)` on the child's own session/process group (children started with `start_new_session=True`). A grandchild that called `setsid` escapes the kill. | L504-508, L563-570, L576-600 |
| Post-kill wait | `communicate(timeout=5.0)`; on timeout close the pipes, `killpg` again if still running, `wait(5.0)`, then `proc.kill()`, `wait(5.0)`. Worst case 3 x `POST_KILL_WAIT_SECONDS = 5.0` = 15 s. | L127, L511-540 |
| Lock polling | `flock(LOCK_EX\|LOCK_NB)` retry with `time.sleep(min(0.05, max(remaining, 0)))`; without a limit, a blocking `flock(LOCK_EX)`. | L122, L1395-1407 |
| Nested limit | If a limit is already active `limited_decide` just calls `cached_decide`. | L1475-1476 |
| Not under the deadline | The chain prolog (`run_chain`), config reading, and everything before `limited_decide`. | L1652-1661, L1768-1781 |
| Outside the decision | SIGTERM uses the default action (process dies); SIGINT becomes `KeyboardInterrupt`, which `main` catches (`(Exception, KeyboardInterrupt)`): skip line, exit 0 (78 under `--strict`). | L1809-1811 |
| SIGPIPE | Python ignores SIGPIPE at interpreter start; writes to a closed pipe return `EPIPE`, which `write_fd` swallows. A port must also ignore SIGPIPE (or treat `EPIPE` as ignorable). Python resets SIGPIPE to default in children it spawns. | L337-349 |
| `_ACTIVE` | Module-global holder of the current `DecisionLimit`; `None` means no deadline and no new session (`run_captured` degenerates to `subprocess.run`). | L501, L550-558, L1474-1504 |
| Other numeric limits | `PROLOG_LINE_MAX = 4094` (L112); `MAX_ARCH_STDOUT = 65536` (L106); `MAX_MODULE_NAME = 256` (L105); `DIAGNOSE_LIMIT = 32` (L104); cpuinfo read 16 MiB (L637); cache read 8 MiB (L1321); `one_line` limit 500 (L211). | as listed |

## 10. Python-only internals (tests import or inspect them)

All tests live under `eessi-arch-guard/tests/`. `harness.py` L14-18 supports `TOOL_BIN` to run the program-level tests against another binary; tests that import `eessi_arch_guard as guard`, mock it, or read its source test only the Python build and need separate equivalents. Program-level tests (subprocess via `harness.run_guard`) in `test_integration.py` are the cross-implementation contract tests. `test_unit.py` has a custom `load_tests` that wraps `UnitTests` methods (L751-757).

| Internal | Test file | Test name (line of `def`) |
|---|---|---|
| `module_name_ok` | test_unit.py | `test_module_name_grammar` (L74) |
| `module_name_ok`, `parse_loaded_modules` | test_fuzz.py | `test_hostile_module_names_are_rejected` (L45) |
| `module_name_ok` | test_year_modules.py | `test_only_four_ascii_digits_extend_unversioned_grammar` (L21) |
| `parse_loaded_modules` | test_year_modules.py | `test_year_keeps_loaded_module_order` (L39) |
| `safe_rel_subdir` | test_unit.py | `test_cpu_candidate_rejects_traversal` (L94), `test_select_first_directory` (L682) |
| `ACCEL_RE` | test_unit.py | `test_accel_grammar` (L102) |
| `ENV_NAME_RE` | test_integration.py (inside `_assert_prolog`, L45-53), test_fuzz.py (L57 test) | `test_hostile_exports_cannot_inject` (L57) |
| `render` (string renderer, not used by `main`) | test_unit.py | `test_prolog_value_is_literal` (L109), `test_shell_quote_round_trip` (L140), `test_print_newline_cannot_become_export` (L159), `test_unsafe_restore_value_publishes_nothing_else` (L237), `test_bad_module_name_does_not_restore` (L252) |
| `render` | test_fuzz.py | `test_hostile_exports_cannot_inject` (L57), `test_shell_mode_does_not_execute_payloads` (L90) |
| `render_bytes`, `PROLOG_LINE_MAX` | test_unit.py | `test_export_line_over_the_slurm_17_11_cap_is_refused` (L422) |
| `message_bytes`, `_fit_encoded`, `PROLOG_LINE_MAX` | test_unit.py | `test_message_trim_keeps_a_character_boundary` (L394) |
| `environment_delta` | test_unit.py | `test_newline_value_aborts_delta` (L116), `test_newline_name_aborts_delta` (L124), `test_soft_drop_keeps_the_rest` (L132), `test_slurm_never_emit` (L167) |
| `environment_delta` | test_fuzz.py | `test_hostile_exports_cannot_inject` (L57) |
| `cache_digest` | test_unit.py | `test_cache_key_omits_arch_guard` (L175) |
| `cache_directory` | test_unit.py | `test_cache_directory_falls_back` (L182) |
| `cache_directory`, `restore_work_base`, `_STRICT_MODE` (patched) | test_workspaces.py | `test_strict_restore_honors_existing_private_tmpdir` (L17), `test_strict_mode_without_private_workspace_has_no_cache_fallback` (L54) |
| `restore_work_base` | test_workspaces.py | `test_explicit_workspace_has_priority` (L22), `test_untrusted_explicit_workspace_is_refused_without_creation` (L27), `test_explicit_symlink_is_refused` (L38), `test_existing_state_workspace_handles_missing_job_tmpdir` (L45) |
| `call_restore`, `_wire_map`, `run_captured` (patched) | test_workspaces.py | `test_restore_helper_and_its_cleanup_stay_under_selected_parent` (L62) |
| `main`, `Outcome`, `site_config` (patched), `_STRICT_MODE` | test_workspaces.py | `test_strict_call_does_not_leak_policy_into_default_calls` (L87) |
| `main`, `Outcome`, `limited_decide`, `site_config`, `write_fd`, `_chain_path_ok`, `run_chain` (all patched) | test_strict_status.py | `test_failed_decision_has_strict_status_in_each_output_mode` (L24), `test_default_failed_decision_remains_fail_open` (L34), `test_unexpected_decision_exception_is_strict_in_each_output_mode` (L44), `test_unexpected_decision_exception_keeps_default_status` (L52), `test_outer_startup_exception_is_strict` (L56), `test_interrupted_decision_is_strict` (L64), `test_successful_and_intentional_noop_decisions_remain_zero` (L68), `test_chained_prolog_failure_preserves_its_status` (L74) |
| `decide`, `CpuProbe`, `AccelProbe`, `RestoreResult`, `Outcome` (injected `cpu_probe`/`accel_probe`/`restore`) | test_unit.py | `test_not_loaded_is_silent` (L188), `test_opt_out_and_overrides_are_silent` (L194), `test_same_target_does_not_restore` (L206), `test_cpu_mismatch_publishes_delta` (L223), `test_unsafe_restore_value_publishes_nothing_else` (L237), `test_bad_module_name_does_not_restore` (L252), `test_no_software_dir_is_loud_and_configurable` (L273), `test_archdetect_error_is_loud` (L291), `test_accel_difference_is_part_of_the_label` (L301), `test_accel_override_skips_accel_probe` (L314), `test_missing_lmod_is_loud_on_mismatch` (L326) |
| `cached_decide`, `main`, `Outcome` | test_unit.py | `test_main_exception_exits_zero` (L338), `test_interrupt_exits_zero` (L352), `test_untrusted_lock_bypasses_the_cache` (L508) |
| `_write_cache`, `_read_cache`, `cache_file_path` | test_unit.py | `test_interrupted_cache_write_leaves_no_temporary_file` (L433), `test_cache_read_needs_an_owned_private_regular_file` (L467), `test_cache_read_does_not_block_on_a_fifo` (L483), `test_cached_changes_obey_the_never_touch_list` (L498) |
| `cache_file_path`, `_write_cache`, `Outcome` | test_integration.py | `test_tmpdir_absent_uses_tmp_mode_0600` (L199), `test_cache_symlink_is_not_followed` (L226), `test_hung_gpu_query_is_stopped_and_not_cached` (L587), `test_lock_wait_ends_at_the_limit` (L636), `test_limit_does_not_undo_a_cached_decision` (L695) |
| `parse_config`, `parse_config_bytes`, `parse_chain_config`, `config_chain`, `_config_untrusted` | test_unit.py | `test_chain_config_parser_and_untrusted_file` (L371) |
| `configured_chain`, `parse_chain_config` (no test beyond the above; `configured_chain` is dead code in `main`) | test_unit.py | `test_chain_config_parser_and_untrusted_file` (L371) |
| `to_wire`, `from_wire` | test_unit.py | `test_wire_round_trip_keeps_the_byte` (L530) |
| `parse_timeout`, `resolve_timeout`, `parse_args`, `DEFAULT_TIMEOUT`, `TIMEOUT_MAX` | test_unit.py | `test_timeout_values` (L549) |
| `DecisionLimit`, `_ACTIVE`, `_Terminated`, `run_captured` | test_unit.py | `test_signal_while_a_child_starts_still_kills_it` (L569) |
| `limited_decide`, `_ACTIVE`, `CpuProbe` | test_unit.py | `test_limit_and_sigterm_end_the_decision` (L607) |
| `candidate_supports_cpu`, `read_cpu_flags`, `WAITPKG_MARCHES` | test_unit.py | `test_waitpkg_march_requires_the_flag` (L626) |
| `POST_KILL_WAIT_SECONDS` | test_integration.py | `_stop_limit` helper (L59-61) used by the hang/timeout tests (L587 onward) |
| `__version__` | test_integration.py | `test_help_version_and_shebang` (L926): also asserts the first source line is exactly `#!/usr/bin/python3 -I` |
| source scan (flags in `parse_args` body between `def parse_args` and `def _announce`, and every `"[A-Z][A-Z0-9_]+"` literal in the source must appear in README) | test_unit.py | `test_readme_lists_every_flag_and_env_name` (L664) |
| `pyproject.toml` license text | test_unit.py | `test_license_expression_has_no_license_classifier` (L677) |
| program-level, any build (no Python import): `harness.run_guard` with stubs in `tests/stubs/` and `tests/fake_lmod.py` | test_integration.py | all other tests in the file (L101-L938), including `test_chain_success_and_failure` (L442), `test_bad_config_byte_keeps_the_other_keys` (L465), `test_untrusted_config_is_ignored` (L487), `test_sigterm_stops_the_decision` (L658), `test_config_timeout_bounds_the_decision` (L785) |
| program-level | test_real_lmod.py | `test_zen4_loaded_env_reloads_on_zen2` (L46) |
| program-level | test_year_modules.py | `test_real_lmod_restores_year_and_versioned_module_on_new_cpu` (L46) |

## Appendix A. Discrepancies and open points found while reading

| Item | Detail | Lines |
|---|---|---|
| `docs/reference/cli.md` out of date | Shows usage without `--strict`, and says `--version` prints `0.1.1`; the source `HELP` includes `--strict` and `__version__` is `0.1.2`. The source is authoritative. | L32, L141-154; docs/reference/cli.md |
| VERBOSE wording | Docs say "skip and match reasons"; code writes only `outcome.detail` (skip, noop, and the restore error on failures). | L1792-1793 |
| Dead code | `configured_chain` (L1634-1649) and `parse_chain_config` (L1594-1595) are not called from `_main`; the live chain path is L1748-1765. Unreachable branch L292-293. `render` (L387-415) is not called from `main`; `render_bytes` is. | as listed |
| Emergency channel | `_emergency` always prints a `print` line in prolog mode regardless of `SLURM_LOCALID`. | L1729 |
| Cache key lacks mode | Prolog and shell mode share a cache file; a cached entry violating the current mode's `value_emit_ok` is silently recomputed. | L1224-1226, L1341 |
| `render_bytes` dry run | The decision runs `render_bytes` itself (prolog mode) to detect over-long lines; a cached outcome is not re-checked, so an over-long cached line raises `ValueError` in `_main` and becomes an emergency skip (exit 0, or 78 under strict). | L1211-1220, L1782-1788 |
| `TIMEOUT_MAX` is platform-dependent | `threading.TIMEOUT_MAX` is 9223372036.0 here; the number appears in warning text. | L118, L1529, L1538 |
| Not determined | The behaviour of the real `eessi_archdetect.sh` (only the stub in `tests/stubs/` was read); behaviour of real Lmod output; the docs for tutorial/howto pages under `docs/howto`, `docs/tutorial`, `docs/topics/{concepts,design,detection}.md` were not read line by line. | n/a |

## Where the Zig build differs on purpose

The rows above describe the Python guard at 0.1.2. The cache name gained the output mode in 0.2.0 for both implementations. The Zig build in `zig/` keeps everything above except the following.

| case | Python guard | Zig build |
|---|---|---|
| restore helper | `python3 -I -X utf8 eessi_arch_guard.py --restore` | the same binary with `--restore`; the request and reply keep their JSON shape |
| a request to `--restore` that is not UTF-8, not JSON or not an object | exit 1 with the Python exception text | exit 1 with a fixed text naming which of the three it was |
| the chained prolog's stdin | inherited | `/dev/null` |
| the config file | checked with `lstat`, then read by path | the same checks with `lstat`, then opened without following a link and checked again on the open file; a file swapped in between is ignored; at most 1 MiB is read |
| a config read error | the `OSError` text | `PATH: ErrorName` |
| the message encoding | the locale's preferred encoding, which Python's UTF-8 mode makes UTF-8 under the C, POSIX and UTF-8 locales | always UTF-8; a non-UTF-8 legacy locale gives different bytes for a non-ASCII message |
| a cache entry holding a lone surrogate outside U+DC80..U+DCFF | read, then failing when written out | refused, and the decision is made again |
| the skip line after an unexpected failure | `skipped, ExceptionName: text` | `skipped, panic: text`, `skipped, signal: SIGSEGV` and the like; a crash signal still exits 0 (78 with `--strict`) |
| archdetect or Lmod output past 64 MiB, stderr past 1 MiB | read whole | refused as a failed run; stderr is cut |
| a timeout written with non-ASCII digits, such as `５` | accepted, as `float()` takes any Unicode decimal digit | refused with the timeout warning; the default is kept |
| a cache file with a leading-zero number, NaN, Infinity, or nesting deeper than 32 | `NaN` and `Infinity` accepted, the rest refused | all refused, and the decision is made again |
| a cpuinfo line whose name is `flags` only under Unicode case folding (`flagſ`) | a flags line | not a flags line |
