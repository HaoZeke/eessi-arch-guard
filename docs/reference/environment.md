# Environment

## Read from the task

| Variable | Role |
| --- | --- |
| `EESSI_SOFTWARE_SUBDIR` | CPU target recorded at load. Unset means EESSI is not loaded, and the guard returns silently. |
| `EESSI_ACCEL_SUBDIR` | Accelerator subdirectory recorded at load. Empty means no accelerator. |
| `EESSI_PREFIX` | Absolute EESSI prefix. The software walk is `<prefix>/software/<os>/<candidate>/software`. |
| `EESSI_OS_TYPE` | Relative OS subdirectory. An absolute path fails the check. |
| `EESSI_INIT_PREFIX` | Absolute directory that contains `eessi_archdetect.sh`. |
| `EESSI_VERSION` | Exported by the EESSI module. The reload name is the `EESSI/<version>` entry in `LOADEDMODULES`. |
| `EESSI_PROC_CPUINFO` | CPU info file. Unset selects `/proc/cpuinfo`. |
| `EESSI_ARCHDETECT_OPTIONS` | Dropped for a waitpkg-fallback reload so it cannot outrank the pin. |
| `LOADEDMODULES` | Colon-separated module names. Exactly one must match `EESSI/<version>`. |
| `MODULEPATH` | Part of the cache key. The reload replaces it. |
| `LMOD_CMD` | Executable Lmod. Required once the targets differ. |
| `PATH` | Part of the cache key. The helper sees `/usr/bin:/bin` only. |
| `SLURM_JOB_ID` | Required for the cache file. Unset means every task computes its own result. |
| `SLURM_STEP_ID` | Cache component. Unset is stored as `batch`. |
| `SLURM_LOCALID` | `0` or unset receives the `print` line. |
| `TMPDIR` | Cache directory when it is absolute and exists. In strict mode it must be private and owned by the invoking user, or an existing configured private state directory is selected. Omitted from the delta. |

## Controls

| Variable | Effect |
| --- | --- |
| `EESSI_ARCH_GUARD=0` | Silent opt-out. Place it in the submitting environment. |
| `EESSI_ARCH_GUARD_MISSING=ignore` | Silent when no candidate has a software directory. |
| `EESSI_ARCH_GUARD_VERBOSE=1` | Skip and match reasons go to stderr. |
| `EESSI_SOFTWARE_SUBDIR_OVERRIDE` | Set on entry: skip detection. Set by a waitpkg fallback during the reload. |
| `EESSI_ARCHDETECT_OPTIONS_OVERRIDE` | Set on entry: skip detection. |
| `EESSI_ACCELERATOR_TARGET_OVERRIDE` | Skip the accelerator comparison. The CPU walk still runs. |
| `EESSI_ARCH_GUARD_WORKDIR` | Existing private directory owned by the invoking user for restore temporaries. The parent creates a private child and removes that child. The configured base is never created or chmodded. |

## Written by the guard

| Variable | Value |
| --- | --- |
| `EESSI_ARCH_GUARD_RELOADED` | `was->now` after an applied reload. |
| `EESSI_ARCH_GUARD_FAILED` | `was->token` or `was->now` on a loud failure. Cleared with `unset` after a later success. |

Plus the restore delta: every changed name that passes the prolog rules. See [What the guard emits](../topics/output.md).

## Module names

A loaded name has to match:

```text
^[A-Za-z0-9_][A-Za-z0-9._+-]*(/[A-Za-z0-9_][A-Za-z0-9._+-]*)+$
```

or be a four-digit ASCII year alias such as `2026`, and be at most 256 characters. Ordinary unversioned package names remain rejected. The EESSI entry has to match `^EESSI/<segment>$` and appear once. Anything else is `->unsafe-module-name` (a name that fails the grammar) or `->restore-failed` (the list has no single `EESSI/<version>` entry). The restore does not start.

Prolog names match `^[A-Za-z_][A-Za-z0-9_]*$`.

## Accelerator stdout

```text
^accel/(?:nvidia/cc[0-9]+|amd/gfx[0-9a-f]+)$
```

## Helper process environment

`PATH=/usr/bin:/bin`, `LANG=C`, `LC_ALL=C`, and `HOME`, `TMPDIR` and `EESSI_ARCH_GUARD_WORKDIR` naming the fresh private helper directory. The task environment travels in the JSON body.

The configured work directory takes priority over `TMPDIR`. Strict mode accepts only an existing private owned directory, with fallback to an existing `eessi-arch-guard` directory below `XDG_STATE_HOME` or `HOME/.local/state`. It refuses when none is available; it does not create those base directories or use the system temporary fallback. Default TaskProlog mode retains the standard temporary-directory fallback when the scheduler has not created `TMPDIR`.
