# eessi-arch-guard

A Slurm TaskProlog that reloads an EESSI module stack when the task is not on the CPU or GPU the stack was loaded for.

On current Slurm master, `exec_task` runs the TaskProlog for the batch step as well as for `srun`, a non-zero status kills the task, `TMPDIR` is created after the prolog, and the prolog is started with `SLURM_SCRIPT_CONTEXT=prolog_task`. `srun --export=ALL,X=y` is treated here as not overriding an `X` that is already set; that line was not re-read in this repo. Ubuntu's slurm-wlm 23.11.4, started by hand in one VM, did run this prolog for both an `srun` step and a batch step. The site's Slurm version, and whether a TaskProlog is already configured, are still open (open-003). Do not treat master or that one 23.11.4 VM as the site's daemon.

The EESSI module chooses its target once, at load. Lmod will not evaluate the file again while EESSI stays loaded, and reload, swap, and unload/load leave the other modules inactive. The prolog lists `LOADEDMODULES`, runs `module --force purge`, loads `EESSI/<version>` by that full name, then loads the other names in order. It prints `export` and `unset` lines. slurmstepd applies those to the task before `execve`, including when the task is a bare binary.

## Install

The prolog is one file. Runtime dependency is Python 3.9 or newer, standard library only, started as `python3 -I`.

```sh
cp src/eessi_arch_guard.py /usr/local/sbin/eessi-arch-guard
chmod 755 /usr/local/sbin/eessi-arch-guard
```

In `slurm.conf`:

```
TaskProlog=/usr/local/sbin/eessi-arch-guard
```

`pip install .` also installs a console script for `eessi-arch-guard --shell` on a login node. That generated script does not pass `-I`. Point `TaskProlog` at the file itself.

## Flags

```
eessi-arch-guard [--shell] [--chain PATH] [--restore] [-h|--help] [--version]
```

`--shell` prints bash assignments for `eval "$(eessi-arch-guard --shell)"`. `--chain PATH` runs an existing task prolog first. The path has to be absolute and executable. `--restore` is the helper the prolog execs (`python3 -I <this file> --restore`). It reads one JSON request on stdin and writes one JSON result on stdout. The task environment is that request, not the helper's process environment. `-h` and `--help` print the usage text. `--version` prints the version.

```sh
python3 src/eessi_arch_guard.py --version
python3 src/eessi_arch_guard.py --help
```

## Chaining an existing TaskProlog

Slurm execs the prolog with no arguments, so a user cannot pass `--chain`. Two site-controlled ways:

Wrapper, if the existing prolog must keep the `TaskProlog` path:

```sh
#!/bin/sh
# /usr/local/sbin/task_prolog
if /etc/slurm/task_prolog.site; then
    exec /usr/bin/python3 -I /usr/local/sbin/eessi-arch-guard
fi
exit $?
```

Or leave `TaskProlog` on this file and add a root-owned config. `/etc/eessi-arch-guard.conf` is read first; a file named `eessi-arch-guard.conf` beside the program is read only if `/etc` has none. The file must be a regular file, owned by root, and not writable by group or others. Symlinks are ignored. A file that exists but fails those checks is not used, and a one-line warning goes to stderr.

```
# /etc/eessi-arch-guard.conf
chain = /etc/slurm/task_prolog.site
```

The chained program runs first, as the user, with the task environment. Its stdout is forwarded. If it exits non-zero, this program exits with that status and does not restore modules. Slurm then kills the task, which is what that prolog already did. `--chain PATH` is the same hook for a wrapper and for tests.

Which path a site should chain is open-003.

## Users without the prolog

At the top of a batch script:

```sh
eval "$(eessi-arch-guard --shell)"
```

Stdout is only `export` and `unset`, with values quoted for bash. The message is on stderr, so command substitution does not capture it. This does nothing for `srun ./a.out` unless that command's environment was already fixed. A shell wrapper cannot see a bare exec. That is why the prolog exists.

`EESSI_ARCH_GUARD=0` in the submitting environment turns the guard off. Putting it only in `srun --export=ALL,EESSI_ARCH_GUARD=0` does not replace a value that is already set.

## What it compares

CPU: `eessi_archdetect.sh -a cpupath`, then the first candidate that has `<EESSI_PREFIX>/software/<EESSI_OS_TYPE>/<candidate>/software`. Accelerator: `eessi_archdetect.sh accelpath`, compared raw with `EESSI_ACCEL_SUBDIR`. A non-zero accelpath (the script's exit 2 and 3 are the no-GPU paths) is "no accelerator". The `ccNN` to `ccN0` directory fallback stays inside the EESSI module.

Module names must match `^[A-Za-z0-9_][A-Za-z0-9._+-]*(/[A-Za-z0-9_][A-Za-z0-9._+-]*)+$` and be at most 256 characters. Anything else is a failed restore, not a string passed to a shell. Prolog names match `^[A-Za-z_][A-Za-z0-9_]*$`. Prolog values must not contain CR, LF, or NUL. A value that cannot be carried aborts the whole restore. The task keeps the environment it arrived with. Export values are written as those same bytes, not re-encoded as text. Messages (`print`, and stderr in `--shell`) are encoded in the locale's codeset and kept inside `PROLOG_LINE_MAX` (4094 bytes). That is the Slurm 17.11 read in `src/slurmd/slurmstepd/task.c`: `fgets(buf, sizeof(buf) - 1)` on `char buf[4096]`. The same cap applies to each `export` line. A trim stops on a character boundary. A failed write is dropped and the process still exits 0. An interrupt is one skip line and exit 0.

`SLURM_*`, `BASH_FUNC_*`, `TMPDIR`, `BASH_ENV`, and shell bookkeeping are never emitted. The full denylist is `_`, `SHLVL`, `PWD`, `OLDPWD`, `TMPDIR`, `BASH_ENV`, `ENV`, `SHELLOPTS`, `BASHOPTS`, `BASH_XTRACEFD`, `PS4`, `BASH_ARGC`, `BASH_ARGV`, `BASH_LINENO`, `BASH_SOURCE`, `BASH_VERSINFO`, `BASH_VERSION`, `DIRSTACK`, `EPOCHREALTIME`, `EPOCHSECONDS`, `FUNCNAME`, `GROUPS`, `HOSTNAME`, `HOSTTYPE`, `IFS`, `MACHTYPE`, `OPTERR`, `OPTIND`, `OSTYPE`, `PIPESTATUS`, `PPID`, plus any `BASH_FUNC_*` or `SLURM_*` name. `PS1`, `PS2`, `PS3`, and `PROMPT_COMMAND` are the exception: an unsafe new value is left unchanged instead of failing the restore. Bash for the restore is `bash --noprofile --norc` with `BASH_ENV`, `ENV`, `SHELLOPTS`, `BASHOPTS`, `BASH_XTRACEFD`, and `PS4` removed. The helper process itself is `python3 -I` with only `PATH`, `HOME`, `LANG`, `LC_ALL`, and `TMPDIR` in its environment. The task environment is data on that helper's stdin. `LMOD_CMD` is the module command the helper execs.

## Failure modes

The program exits 0 except when a chained prolog exits non-zero. A non-zero status from this program would kill the task. Loud means a `print` line (prolog) or a stderr line (`--shell`), plus `EESSI_ARCH_GUARD_FAILED` where a variable is set. The module environment is otherwise unchanged.

| Situation | Result |
|---|---|
| `EESSI_SOFTWARE_SUBDIR` unset | Silent. EESSI is not loaded. |
| `EESSI_ARCH_GUARD=0` | Silent opt-out. |
| `EESSI_SOFTWARE_SUBDIR_OVERRIDE` or `EESSI_ARCHDETECT_OPTIONS_OVERRIDE` set | Silent. Detection is pinned. |
| `EESSI_ACCELERATOR_TARGET_OVERRIDE` set | Accelerator is not compared. CPU still is. |
| Same CPU and accelerator | Silent. No exports. |
| No software directory for any candidate | `EESSI_ARCH_GUARD_FAILED` ends in `->no-software-dir`, and a print line. `EESSI_ARCH_GUARD_MISSING=ignore` restores the old silence. |
| archdetect missing, non-zero, empty, or unsafe | Loud failure (`->archdetect-failed`). Unsafe accel stdout is a failure, not "no GPU". |
| A loaded name fails the grammar | Loud failure (`->unsafe-module-name`). Restore does not start. |
| A module has no build for this target | Loud failure. Nothing from the restore is applied. The message names each missing module, up to 32. |
| A changed value cannot be a prolog line | Loud failure (`->unsafe-environment`). No partial delta. |
| Unexpected exception | One skip line, exit 0. |
| `EESSI_ARCH_GUARD_VERBOSE=1` | Skip and no-op reasons go to stderr. |

`EESSI_ARCH_GUARD_RELOADED` is `was->now`. When the accelerator differs, each side is `<cpu> <accel>` or `<cpu> no accel`. `print` goes to the task's stdout, and only for `SLURM_LOCALID` 0 (or unset). A batch script that reads its own stdout will see that line. Slurm has no stderr form of `print`.

Within one job step the first task on a node computes the result and the others read it. The cache key is uid, node, `SLURM_JOB_ID`, `SLURM_STEP_ID`, and a sha256 prefix of `EESSI_SOFTWARE_SUBDIR`, `EESSI_ACCEL_SUBDIR`, `LOADEDMODULES`, `MODULEPATH`, `EESSI_INIT_PREFIX`, `EESSI_PROC_CPUINFO`, and `PATH`. `EESSI_ARCH_GUARD` is not part of the key. The file is mode `0600` under `TMPDIR` if that directory exists, otherwise `/tmp`. `flock` is assumed to work on that directory. The cache and the lock are opened with `O_NOFOLLOW`. A symlink at either path is not followed.

`EESSI_PREFIX`, `EESSI_OS_TYPE`, and `EESSI_INIT_PREFIX` have to be absolute, or a safe relative subdirectory for `EESSI_OS_TYPE`, before archdetect is run. `EESSI_VERSION` is whatever the loaded module exported. `SLURM_LOCALID` chooses who gets the `print` line: local task 0, or an unset id.

## Develop

```sh
python3 -m pip install ruff
ruff check src tests
python3 -m unittest discover -s tests -v
```

The integration tests build a fake prefix, a stub `eessi_archdetect.sh` that reads `EESSI_PROC_CPUINFO`, and a `nvidia-smi` stub that is first on `PATH`. They do not need CVMFS, a GPU, or Slurm. `tests/test_real_lmod.py` runs one reload through `/usr/share/lmod/lmod/libexec/lmod` when that file exists and is skipped otherwise. `python3 tests/measure_noop.py` prints a same-target timing on the machine where it is run.

## What this does not fix

Binaries built with `-march=native` on a login node, virtualenvs with compiled wheels from that node, and a module that has no build for the target. The guard refuses rather than loading half of the stack. A CVMFS variant symlink, an Lmod hook, `BASH_ENV`, and `--export=NONE` as a site default are the wrong tool. See `DESIGN.md`.
