## [0.2.0] - 2026-11-14

### Features

- Add opt-in strict shell and task-prolog status for user job wrappers while retaining the default decision failure policy.
- Accept four-digit ASCII year frontends alongside validated versioned module names during a restore.
- A static Zig build of the guard, `zig build -Drelease=true`, runs the same decision as the Python script and passes the same test suite (`TOOL_BIN=zig-out/bin/eessi-arch-guard python -m unittest discover -s tests`). It is not yet the default prolog; it replaces the Python guard only after the release gate in the port plan (shadow comparison, real Lmod, a second review of the validators).

### Bugfixes

- The decision cache is kept per output mode. A batch script that runs `--shell` after the task prolog no longer gets the prolog's cached result, such as a failure for a value with a newline that `--shell` can carry, and a prolog no longer reuses a `--shell` result.
- The cache and its lock are trusted only when the open file is a regular file owned by the caller, with one link and no group or other bits. A file planted at the cache name in a shared `/tmp`, such as a FIFO or another user's file, no longer feeds the decision or blocks the prolog, and a cached entry that changes `LD_*`, `SLURM_*`, `BASH_FUNC_*` or another denylisted name is ignored.
- Honor existing private job workspaces for restore temporaries and provide a strict-mode state-directory fallback without creating or widening configured bases.
- The Zig build exits 0 with the one skip line on every failure path it can reach: out of memory, a cache lock error other than would-block (reported instead of waited out), and a crash, whose handlers now run on an alternate stack; an emergency stops the chained prolog's child first. It builds on zig-common 0.8.1 and targets the baseline CPU by default.

### Docs

- This release ships the Zig build without the shadow comparison beside the Python guard that the port plan's release gate names; the maintainer waived it on 2026-11-14. The Python guard stays the default prolog, and a site switching to the binary should run the comparison itself first (`RELEASING.md`, item 5).


## [0.1.2] - 2026-11-12

### Features

- Opt-in strict mode returns status 78 for failed decisions and unexpected exceptions.
- Restore numeric year-module frontends alongside EESSI module names.
- Strict restores and caches use caller-owned private job or state directories.

### Docs

- Describe user-space Slurm task prologs without changing the site TaskProlog.
- Verify the supported Python 3.9 and 3.12 interfaces and documented examples.

## [0.1.1] - 2026-11-08

### Docs

- The WAITPKG march list is checked against GCC 16 (releases/gcc-16): the same set GCC 15.2.0 carried plus novalake, and znver6, the other new mask, has no WAITPKG; the comment names that source.

## [0.1.0] - 2026-11-05

### Features

- The decision stops at one deadline, a killed helper loses its temporary directory, and config `timeout` applies with `--chain` and `--shell`.
- The root-owned config file is read as a map of settings.

### Bugfixes

- A GCC march that includes waitpkg is skipped when the CPU flag is missing, and the next installed archdetect candidate is loaded.
- A config value that is not UTF-8 is kept with surrogateescape and the other keys still apply.
- An interrupted cache write removes its temporary file.
- Export values keep their bytes, a failed write exits 0, and each prolog line stays within 4094 bytes.
- The fake Lmod writes each script line as the environment's own bytes.
- The package metadata keeps the license expression and omits the license classifier that current setuptools rejects.
- The restore helper is `python3 -I -X utf8` and carries each name and value as those same bytes.
- The sdist includes CHANGELOG.md, DESIGN.md, and the test files the default manifest left out.

### Docs

- An Intel A100 node uses the first archdetect candidate whose software directory exists.
