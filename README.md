# eessi-arch-guard

eessi-arch-guard is a Slurm TaskProlog that reloads an EESSI module stack when the task runs on a CPU or GPU other than the target recorded at `module load`. The EESSI module chooses that target once. Lmod keeps the recorded `MODULEPATH` and the `EBROOT*` paths until the stack is purged and loaded again. The prolog does that purge and load on the compute node, before `execve`, and prints the environment delta for `slurmstepd` to apply.

The documentation is a Sphinx site under `docs/`: a tutorial, how-to guides, topics, and a reference, including the generated Python API.

```console
python3 -m pip install -r docs/requirements.txt
make -C docs html
```

The HTML is written to `docs/_build/html`.

## Install the prolog

The prolog target is `src/eessi_arch_guard.py` (shebang `#!/usr/bin/python3 -I`). Python 3.9 or newer, standard library only.

```bash
sudo install -m 755 src/eessi_arch_guard.py /usr/local/sbin/eessi-arch-guard
```

```
TaskProlog=/usr/local/sbin/eessi-arch-guard
```

`pip install .` also installs a console script for `eessi-arch-guard --shell` on a login node. That generated script is started without `-I`. Point `TaskProlog` at the file. The worked install, the config file, and the failure messages are in `docs/`.

## Flags and environment

```
eessi-arch-guard [--shell] [--strict] [--chain PATH] [--timeout SEC] [--restore] [-h|--help] [--version]
```

`--shell` prints bash assignments. `--strict` returns status 78 for a failed decision or unexpected exception; successful reloads, no-ops and intentional skips return zero. The default TaskProlog failure policy is unchanged. `--chain PATH` runs an existing task prolog first. `--timeout SEC` bounds the decision. `--restore` is the helper the prolog execs. `-h` and `--help` print the usage text. `--version` prints the version.

The prolog compares `EESSI_SOFTWARE_SUBDIR` and `EESSI_ACCEL_SUBDIR` with `eessi_archdetect.sh`, walking `<EESSI_PREFIX>/software/<EESSI_OS_TYPE>/<candidate>/software`. It may set `EESSI_SOFTWARE_SUBDIR_OVERRIDE` and drop a stale `EESSI_ARCHDETECT_OPTIONS`. Opt-out and pins: `EESSI_ARCH_GUARD=0`, `EESSI_ARCHDETECT_OPTIONS_OVERRIDE`, `EESSI_ACCELERATOR_TARGET_OVERRIDE`. Loud results set `EESSI_ARCH_GUARD_FAILED`. `EESSI_ARCH_GUARD_MISSING=ignore` restores silence when no software directory exists. `EESSI_ARCH_GUARD_VERBOSE=1` writes skip reasons to stderr. `EESSI_ARCH_GUARD_RELOADED` is `was->now`. The cache sits under `TMPDIR` if that directory exists, otherwise `/tmp`, and is opened with `O_NOFOLLOW`. The cache key includes `LOADEDMODULES`, `MODULEPATH`, `EESSI_INIT_PREFIX`, `EESSI_PROC_CPUINFO`, and `PATH`. `LMOD_CMD` is the module command the helper execs. `SLURM_JOB_ID`, `SLURM_STEP_ID`, and `SLURM_LOCALID` (local task 0) scope the cache and the `print` line. The helper process itself is `python3 -I -X utf8` with `PATH`, `HOME`, `LANG`, `LC_ALL`, `TMPDIR`, and `EESSI_ARCH_GUARD_WORKDIR` in its environment.

Strict restores and caches use an existing private directory owned by the caller: `EESSI_ARCH_GUARD_WORKDIR`, then `TMPDIR`, then `XDG_STATE_HOME/eessi-arch-guard` (or `HOME/.local/state/eessi-arch-guard`). The base must exist and is never created or widened by the guard. Strict mode has no shared temporary-directory fallback.

Outside strict mode the cache falls back to `/tmp`, which other users can write. The guard trusts a cache or lock file only when it is a regular file owned by the caller, with one link and no group or other permission bits, checked on the open descriptor; a FIFO at the name does not block it. A cached entry that changes `LD_*`, `SLURM_*`, `BASH_FUNC_*` or any other denylisted name is ignored and the decision is made again.

The helper never emits `SLURM_*`, `BASH_FUNC_*`, `TMPDIR`, `BASH_ENV`, or shell bookkeeping from the task environment. The full denylist is `_`, `SHLVL`, `PWD`, `OLDPWD`, `TMPDIR`, `BASH_ENV`, `ENV`, `SHELLOPTS`, `BASHOPTS`, `BASH_XTRACEFD`, `PS4`, `BASH_ARGC`, `BASH_ARGV`, `BASH_LINENO`, `BASH_SOURCE`, `BASH_VERSINFO`, `BASH_VERSION`, `DIRSTACK`, `EPOCHREALTIME`, `EPOCHSECONDS`, `FUNCNAME`, `GROUPS`, `HOSTNAME`, `HOSTTYPE`, `IFS`, `MACHTYPE`, `OPTERR`, `OPTIND`, `OSTYPE`, `PIPESTATUS`, `PPID`, plus any `BASH_FUNC_*` or `SLURM_*` name. `PS1`, `PS2`, `PS3`, and `PROMPT_COMMAND` are the exception: an unsafe new value is left unchanged. Bash for the restore is `bash --noprofile --norc` with `BASH_ENV`, `ENV`, `SHELLOPTS`, `BASHOPTS`, `BASH_XTRACEFD`, and `PS4` removed.

The worked install, the config file, and the failure messages are in `docs/`.

## Develop

```console
python3 -m pip install ruff
ruff check src tests
python3 -m unittest discover -s tests -v
```

Copyright (c) eessi-arch-guard developers
