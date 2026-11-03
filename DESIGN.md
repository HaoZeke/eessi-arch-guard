# Design: eessi-arch-guard

The EESSI module picks a CPU target once, at load time, and an accelerator target the same way. Both are written into `MODULEPATH` and into every path later modules export. Slurm sends that environment to the job. A task that lands on another CPU or GPU then runs the submitting machine's binaries: illegal instruction, a silent slow path, or a missing GPU tree. `module load EESSI/<version>` inside the job does nothing, because Lmod does not re-read a module that is already loaded. `module reload`, unload/load, and swap leave the other modules inactive. The sequence that works is: list the loaded modules, `module --force purge`, `module load EESSI/<version>` by its full name, then load the others in that order.

This file records which program should do that, and which shapes were rejected.

## Decision

A Slurm **TaskProlog** is still the place that can fix the environment. It is the wrong place to re-derive a shell and print the whole result as `export` lines. The prolog stays. The mechanism in the appendix A prototype does not.

The program:

1. Decides, in Python, whether this node is the target the loaded modules were built for. CPU detection is `eessi_archdetect.sh -a cpupath` and then the first candidate that has `<EESSI_PREFIX>/software/<EESSI_OS_TYPE>/<candidate>/software`. Accelerator detection is `eessi_archdetect.sh accelpath`, compared raw with `EESSI_ACCEL_SUBDIR`. The `ccNN` → `ccN0` fallback stays inside the EESSI module, which is what a reload is for.
2. On a mismatch, restores modules in a helper: the same file, exec'd as `python3 -I <file> --restore`, with a process environment that contains only `PATH`, `HOME`, `LANG`, `LC_ALL`, and `TMPDIR`. The task environment is data on stdin, not the helper's process environment. Module names are argv to `LMOD_CMD`, after a whitelist check. Bash is started as `bash --noprofile --norc` and only after `BASH_ENV`, `ENV`, `SHELLOPTS`, `BASHOPTS`, `BASH_XTRACEFD`, and `PS4` have been removed. Its stdout is `env -0`, not a shell script the prolog will print.
3. Emits the **delta** of that restore, and only lines Slurm can apply safely. Names match `^[A-Za-z_][A-Za-z0-9_]*$`. In the default (prolog) mode, values must not contain CR, LF, or NUL. A value that cannot be carried aborts the whole restore: the task keeps the environment it arrived with, and the prolog sets `EESSI_ARCH_GUARD_FAILED`. Prompt variables (`PS1`, `PS2`, `PS3`, `PROMPT_COMMAND`) are the exception: an unsafe new value is left unchanged instead of failing the restore. `SLURM_*`, `BASH_FUNC_*`, `TMPDIR`, `BASH_ENV`, and shell bookkeeping (`_`, `SHLVL`, `PWD`, `OLDPWD`, `SHELLOPTS`, `BASHOPTS`) are never emitted.
4. Refuses module names that fail `^[A-Za-z0-9_][A-Za-z0-9._+-]*(/[A-Za-z0-9_][A-Za-z0-9._+-]*)+$` (length at most 256). The EESSI entry must be exactly one `EESSI/<version>` name. A bad name is a failed restore, not a string interpolated into a shell.
5. Fails loud, and still exits 0. A missing software directory, a broken archdetect, an unusable `LMOD_CMD`, an unsafe name, or a module that will not load sets `EESSI_ARCH_GUARD_FAILED` and prints one line. `EESSI_ARCH_GUARD=0` is the opt-out (silent). `EESSI_ARCH_GUARD_MISSING=ignore` is the narrower opt-out for a node that has no EESSI software directory. Exit 0 is required: on current Slurm master, a non-zero TaskProlog status is `_exit(status)` and the task never runs. Loud means the user and the environment can see the failure. It does not mean killing the task.
6. `--shell` prints `export`/`unset` with `shlex.quote` on stdout, for `eval "$(eessi-arch-guard --shell)"`. The human message goes to stderr, so command substitution does not capture it and does not need an `echo` inside the eval. Prolog mode uses Slurm's `print` line, which master writes to the task's stdout. Those channels differ because Slurm has no stderr form of `print`.

The helper command is `python3 -I -X utf8`. Each name and value crosses as one Latin-1 character per environment byte, and `-X utf8` turns that text back into those bytes for Lmod.

One decision is cached per uid, node, job, and step, under `TMPDIR` if that directory exists and otherwise `/tmp`. The cache key is the one from the original design: `EESSI_SOFTWARE_SUBDIR`, `EESSI_ACCEL_SUBDIR`, `LOADEDMODULES`, `MODULEPATH`, `EESSI_INIT_PREFIX`, `EESSI_PROC_CPUINFO`, `PATH`. `EESSI_ARCH_GUARD` is not part of the key. Adding it would change the contract; two tasks in one step that differ only by that variable share a result. The cache file is mode `0600`, created with `O_NOFOLLOW` and `O_EXCL`, and published with `rename`. The lock file is opened the same way. A symlink is not followed. If the cache cannot be used safely, the task computes its own result and does not write through the link.

Chaining does not trust the job environment. Slurm execs a TaskProlog with no arguments, so a user cannot pass `--chain`. A site either wraps the existing prolog, or writes `chain = /absolute/path` in `/etc/eessi-arch-guard.conf` (or a file of that name beside the program). The file is used only when it is a regular file, owned by root, and not writable by group or others. Symlinks are ignored. Each key is decoded on its own. A value that is not UTF-8 is kept with surrogateescape, and the other keys still apply. `--chain PATH` exists for a root-owned wrapper and for tests.

## Why a TaskProlog at all

The bug appears when the process image is already chosen. A batch script can purge and reload. `srun ./a.out` never runs a shell. The prolog runs on the compute node, as the user, with the task environment, before `execve`, including the batch step. Nothing earlier in the job has both the executing node's CPU and the task's cgroup view of the GPU.

Checked on Slurm `master` `src/slurmd/slurmstepd/task.c` (public source, not a site install):

- `exec_task` calls the configured TaskProlog with no `!step->batch` guard. The batch step is included. The `!step->batch` checks in that function are for the switch plugin and MPI, not for the prolog.
- `_run_script_and_set_env` sets `SLURM_SCRIPT_CONTEXT=prolog_task`.
- `_proc_stdout` splits on `\n` and handles `export`, `unset`, and `print`. The value after `=` is passed to `setenv` as literal text. `$` and backticks are not shell syntax there. A newline is a new command, which is the injection.
- A non-zero wait status is returned after that parse, and `exec_task` then `_exit`s. Exports from a failing prolog are applied, and the task still dies.
- `_make_tmpdir` runs after the prologs, and only for local task 0. A per-job `TMPDIR` may not exist yet. Falling back to `/tmp` is the right cache location. The prolog must not itself export `TMPDIR`: that would change the directory Slurm creates.

`srun --export=ALL,X=y` was not re-read in this repo. The design treats the existing statement as true: an `X` already in the environment is not overridden. Opt-out has to be in the submitting environment. It cannot be smuggled in as `--export=ALL,EESSI_ARCH_GUARD=0` when the variable is already set.

The site's Slurm version is open-003. Master behaving this way is not evidence about the installed daemon.

## What was rejected

### Re-emitting the child shell's environment (the prototype)

Rejected as the mechanism, not as the idea of a prolog. It is how the reviewed bugs happen:

- `bash --noprofile --norc` still sources `BASH_ENV`. Confirmed on bash 5.2.21: a `BASH_ENV` file runs before `-c` and before a script file, and its stdout is prefixed to the script. Stripping the variable is necessary. It is not sufficient if the next step trusts a NUL-delimited dump as prolog text.
- `module load $loaded` without quotes, and `export NAME=value` with an unchecked name, turn a newline in a module name into another directive. Slurm will apply that directive.
- A node with no software directory returned an empty result and printed nothing. The job kept the foreign binaries.
- Values that contain a newline were omitted while other keys were still exported, so `MODULEPATH` could move and `_ModuleTable*` or `PS1` could stay behind.
- The `try` covered the decision and not the printer, so a write error could exit non-zero and kill the task.
- The cache was created with the process umask, and `open(path + ".lock")` follows a symlink.

A helper plus a whitelist removes those classes. Patching each one inside the dump-and-print loop would leave the next parser bug somewhere in the same path.

### "Emit a flag and let the job reload"

Rejected as the only mechanism. It does not run for a bare `srun`. The helper runs inside the prolog so the corrected variables are in the task before `execve`. `--shell` is the fallback for a user whose site has not installed the prolog, and it has the same hole as any shell wrapper. The README says so.

### Minimal set meaning "only PATH and MODULEPATH"

Rejected. Modules publish `EBROOT*`, `EBVERSION*`, and names they invent. Rewriting the paths we know about and leaving the rest on the submitting node is a partial environment, which is the failure mode the newline filter already had. The emitted set is the restore delta, after the denylist above, not a hand-picked subset. "Minimal" means we do not print the helper's process environment, shell noise, or anything the prolog protocol cannot carry.

### Alternatives already rejected, with what is new

These were rejected before this repo existed. The new information does not revive them.

| Alternative | Still rejected? | What changed |
|---|---|---|
| CVMFS variant symlink per node | Yes | EESSI module roots and `DT_RPATH` are absolute per target. Confirmed by reading `init/modules/EESSI/2023.06.lua` at software-layer-scripts `26441ca` (later versions are pointers at that file). |
| Re-check inside the EESSI module file | Yes | Lmod does not re-evaluate a loaded module. The same lua file selects the target at load and returns. |
| Lmod hook on every `module` command | Yes | A hook does not run for a bare exec. It would also run on every load for a case that happens once per task. |
| `BASH_ENV` as the fix | Yes, more strongly | It misses `srun` of a binary, it runs for every bash, and Lmod already uses `BASH_ENV`. New: the prototype's own reload shell sources it. The guard strips it and does not use it as a carrier. |
| `--export=NONE` as a site default | Yes | It drops the inherited environment scripts depend on. It remains user advice. New, taken from the existing Slurm note and not re-verified here: `--export=ALL,X=y` does not override an `X` that is already set, so a one-variable override is not a fix either. |
| Job prolog (the root, once-per-job script) | Yes | It is not the task environment, and it does not see the task cgroup. A GPU hidden by the cgroup is "no accelerator" only from the task's point of view. |
| SPANK or a site C plugin | Yes | Not a single Python file a site can chain from the prolog it already has. |
| Rewriting `LOADEDMODULES` / `MODULEPATH` in place without `module load` | Yes | The inactive-module behavior is why the purge-and-load sequence exists. A path edit does not re-run modulefiles. |

## Failure policy

| Situation | Result |
|---|---|
| `EESSI_SOFTWARE_SUBDIR` unset | Silent. EESSI is not loaded. |
| `EESSI_ARCH_GUARD=0` | Silent opt-out. |
| `EESSI_SOFTWARE_SUBDIR_OVERRIDE` or `EESSI_ARCHDETECT_OPTIONS_OVERRIDE` set | Silent. The user pinned detection. |
| `EESSI_ACCELERATOR_TARGET_OVERRIDE` set | Accelerator is not compared. CPU still is. |
| Same CPU and accelerator | Silent success. No exports. |
| No software directory for any candidate | `EESSI_ARCH_GUARD_FAILED`, one `print` line, environment otherwise unchanged. `EESSI_ARCH_GUARD_MISSING=ignore` restores the old silence. |
| archdetect missing, non-zero, empty, or unsafe output | Same loud failure. Empty or non-zero **accelpath** is "no accelerator" (the EESSI wrapper exports that as an empty `EESSI_ACCEL_SUBDIR`; exit 2 and 3 are the script's no-GPU paths). Unsafe accel stdout is a failure, not "no GPU". |
| Module name fails the grammar | Loud failure. Restore is not started. |
| A module has no build for this target | Loud failure. Nothing from the restore is applied. Each module is probed so the message can name more than the first Lmod error. Probing stops after 32. |
| A changed variable cannot be expressed as a prolog line | Loud failure. No partial delta. |
| Any unexpected exception | One skip line, exit 0. |
| Chained prolog exits non-zero | That status is returned and this program does not restore. Slurm will kill the task, which is what that prolog already did. |

`EESSI_ARCH_GUARD_RELOADED` is `was->now`. When the accelerator differs, each side is `<cpu> <accel>` or `<cpu> no accel`. `EESSI_ARCH_GUARD_FAILED` uses the same shape, with a fixed token (`no-software-dir`, `archdetect-failed`, `unsafe-module-name`, `unsafe-environment`, `lmod-unavailable`, `restore-failed`) when there is no selected target.

## Install shape

Runtime dependency is Python 3.9+ standard library, executed as `python3 -I`. The TaskProlog target is the single file `src/eessi_arch_guard.py` (shebang `#!/usr/bin/python3 -I`). `pyproject.toml` packages that module for a login-node `--shell` install. The generated console script does not pass `-I`; the prolog should be the file itself.

## Out of scope

open-003 is unanswered: whether this site already has a TaskProlog, which Slurm version it runs, the cost on a 192-task node with node-local `TMPDIR`, and a real cross-target job on production nodes. This repository does not invent those measurements. Binaries built with `-march=native` on a login node, virtualenvs with compiled wheels, and a module that simply does not exist for the target are unchanged: the guard refuses rather than loading half of a stack.
