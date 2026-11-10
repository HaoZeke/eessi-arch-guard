# What the guard emits

Prolog mode and `--shell` carry the same decision. The bytes on stdout differ, and the human message uses a different stream, because Slurm has a `print` directive and no stderr form of it.

## Prolog mode

Stdout is a series of lines. Each line is `export NAME=value`, `unset NAME`, or `print message`. Names match `^[A-Za-z_][A-Za-z0-9_]*$`. Values must not contain CR, LF, or NUL. A value that cannot be carried aborts the whole restore: the task keeps the environment it arrived with, and the prolog sets `EESSI_ARCH_GUARD_FAILED` to a token ending in `->unsafe-environment`.

`PS1`, `PS2`, `PS3`, and `PROMPT_COMMAND` are the exception. An unsafe new value for one of those names is left unchanged, and the rest of the delta is still emitted.

Each line stays within 4094 bytes (`PROLOG_LINE_MAX`). That is the Slurm 17.11 read in `src/slurmd/slurmstepd/task.c`: `fgets(buf, sizeof(buf) - 1)` on `char buf[4096]`. A trim of a message stops on a character boundary. Export values keep the bytes they already had. A failed write is dropped and the process still exits 0.

The `print` line is emitted when `SLURM_LOCALID` is `0` or unset. Slurm writes it to the task's stdout. A batch script that reads its own stdout will see that line. Other local ranks get the `export` and `unset` lines and no `print` line.

## Shell mode

`--shell` prints `export NAME=<shlex quote>` and `unset NAME` on stdout, for `eval "$(eessi-arch-guard --shell)"`. The message goes to stderr. The [batch-script how-to](../howto/shell_batch.md) runs that eval.

## Variables the delta never includes

These names are omitted from the delta. `TMPDIR` is created by `slurmstepd` after the prolog; exporting it would change the directory Slurm creates.

`_`, `SHLVL`, `PWD`, `OLDPWD`, `TMPDIR`, `BASH_ENV`, `ENV`, `SHELLOPTS`, `BASHOPTS`, `BASH_XTRACEFD`, `PS4`, `BASH_ARGC`, `BASH_ARGV`, `BASH_LINENO`, `BASH_SOURCE`, `BASH_VERSINFO`, `BASH_VERSION`, `DIRSTACK`, `EPOCHREALTIME`, `EPOCHSECONDS`, `FUNCNAME`, `GROUPS`, `HOSTNAME`, `HOSTTYPE`, `IFS`, `MACHTYPE`, `OPTERR`, `OPTIND`, `OSTYPE`, `PIPESTATUS`, `PPID`.

Any name starting with `BASH_FUNC_` or `SLURM_` is omitted as well.

## Markers

`EESSI_ARCH_GUARD_RELOADED` is `was->now` after a reload that was applied. When the accelerator differs, each side is `<cpu> <accel>` or `<cpu> no accel`.

`EESSI_ARCH_GUARD_FAILED` uses the same shape. The token after `->` is fixed when there is no selected target: `no-software-dir`, `archdetect-failed`, `unsafe-module-name`, `unsafe-environment`, `lmod-unavailable`, `restore-failed`, `timeout`, `terminated`. When a target was selected and the module load failed, both sides are the target labels. A successful reload clears a previous `EESSI_ARCH_GUARD_FAILED` by emitting `unset` for it.

`EESSI_ARCH_GUARD_VERBOSE=1` writes the skip or match reason to stderr. The reason strings are the `detail` field of the decision, for example `target already matches this node`.

## One decision per step

Within one job step the first task on a node computes the result and the others read it. The cache file is mode `0600` under `TMPDIR` when that directory exists and is absolute, otherwise under `/tmp`. The name is:

```text
.eessi-arch-guard.<uid>.<node>.<job>.<step>.<digest>
```

`<step>` is `SLURM_STEP_ID`, or `batch` when that variable is unset. There is no cache file when `SLURM_JOB_ID` is unset. `<node>`, `<job>`, and `<step>` are used as-is when they match `^[A-Za-z0-9._-]{1,64}$`; otherwise each is a 16-character SHA-256 prefix. `<digest>` is the first 16 hex characters of the SHA-256 of these values joined by NUL:

`EESSI_SOFTWARE_SUBDIR`, `EESSI_ACCEL_SUBDIR`, `LOADEDMODULES`, `MODULEPATH`, `EESSI_INIT_PREFIX`, `EESSI_PROC_CPUINFO`, `PATH`.

`EESSI_ARCH_GUARD` is omitted from the key. Two tasks in one step that differ only by that variable share a result.

The cache file and the lock are opened with `O_NOFOLLOW`. A symlink at either path is not followed. The cache file is created with `O_EXCL` and published with `rename`. `flock` is assumed to work on the directory. The lock wait shares the decision's deadline. A result already in the cache is returned as stored, including when the wait used the whole deadline. If the cache cannot be used safely, the task computes its own result and does not write through the link.

A timeout or `SIGTERM` is not written to the cache. A decision that has finished is still published if `SIGTERM` arrives while the file is written.

## Deadline

One deadline covers the lock wait, both archdetect runs, and the restore helper. The default is 60 seconds. Children run in their own sessions. `SIGTERM` is held until the child process group is known, then released. At the deadline, or on `SIGTERM`, the guard sends `SIGKILL` to that group. A child that leaves the group is outside this kill. After the kill the guard still waits up to 5 seconds (`POST_KILL_WAIT_SECONDS`) for the pipe to close, and may wait that long in `communicate` and twice more in `wait`, so the worst case after the kill is 15 seconds.

The parent removes the helper's work directory when the helper exits, including when the deadline kills it.

An unexpected exception becomes one skip line (`eessi-arch-guard: skipped, ...`) and exit 0. In prolog mode that line is a `print` directive. In `--shell` it goes to stderr.
