# Design

The repository file `DESIGN.md` is the design record. This page is the part a user or an admin needs in order to see why the prolog prints a delta.

## What runs

1. Decide, in Python, whether this node is the target the loaded modules were built for. The CPU and accelerator walks are [Detection and matching](detection.md).
2. On a mismatch, restore modules in a helper: the same file, exec'd as `python3 -I -X utf8 <file> --restore`. The task environment is JSON on stdin. Each name and value crosses as one Latin-1 character per environment byte, and `-X utf8` turns that text back into those bytes for Lmod, so a prefix that is not valid UTF-8 is the same path Lmod opens.
3. Emit the delta, and only lines Slurm can apply. The rules are [What the guard emits](output.md).
4. Refuse module names that fail the grammar in the [environment reference](../reference/environment.md). The loaded list has to contain exactly one `EESSI/<version>` name. A bad name is a failed restore.
5. Fail loud, and still exit 0. A missing software directory, a broken archdetect, an unusable `LMOD_CMD`, an unsafe name, or a module that will not load sets `EESSI_ARCH_GUARD_FAILED` and prints one line. Exit 0 is required: on current Slurm master, a non-zero TaskProlog status is `_exit(status)` and the task never runs.

## What was checked in Slurm

Checked on Slurm `master` `src/slurmd/slurmstepd/task.c` (public source) and, separately, on Ubuntu's slurm-wlm 23.11.4 started by hand in one VM. That VM did run this prolog for both an `srun` step and a batch step. The site's Slurm version is open-003, below. Master behaving this way is evidence about the public source.

* `exec_task` calls the configured TaskProlog with no `!step->batch` guard. The batch step is included. The `!step->batch` checks in that function are for the switch plugin and MPI.
* The prolog is started with `SLURM_SCRIPT_CONTEXT=prolog_task`.
* The value after `=` in an `export` line is passed to `setenv` as literal text. `$` and backticks are not shell syntax there. A newline is a new command.
* A non-zero wait status is returned after that parse, and `exec_task` then exits. Exports from a failing prolog are applied, and the task still dies. This program returns non-zero only when a chained prolog did.
* `_make_tmpdir` runs after the prologs, and only for local task 0. A per-job `TMPDIR` may not exist yet. The cache falls back to `/tmp`. The prolog omits `TMPDIR` from the delta.

`srun --export=ALL,X=y` was not re-read in this repository. The design treats an `X` already in the environment as the value that remains. Opt-out belongs in the submitting environment.

## Closed alternatives

| Approach | What happens |
| --- | --- |
| CVMFS variant symlink per node | Module roots and `DT_RPATH` are absolute per target. The task opens the submitting node's files. Confirmed from `init/modules/EESSI/2023.06.lua` at software-layer-scripts `26441ca`. |
| Re-check inside the EESSI module file | Lmod evaluates the file at load and returns. |
| Lmod hook on every `module` command | The hook runs for module commands. A bare exec never calls one. It would also run on every load for a check that happens once per task. |
| `BASH_ENV` as the carrier | A bare `srun` of a binary never sources it. Lmod already uses `BASH_ENV`. The reload shell removes it. |
| `--export=NONE` as a site default | Job scripts lose the environment they were written against. A user can still pass it on one command. |
| Job prolog | The script runs as root, once per job, outside the task cgroup. A GPU the cgroup hides from the task is visible there. |
| SPANK or a site C plugin | The guard is one Python file a site can chain from the TaskProlog it already runs. |
| Rewrite `LOADEDMODULES` and `MODULEPATH` in place | Inactive modules stay inactive until the modulefiles run. The purge-and-load sequence is what re-runs them. |

Re-emitting the child shell's whole environment was the earlier mechanism. It is how the reviewed bugs happen: `bash --noprofile --norc` still sources `BASH_ENV` (confirmed on bash 5.2.21); an unchecked module name can smuggle a second prolog directive; a missing software directory returned an empty result; a newline in one value dropped that key and kept the others; a write error could exit non-zero; the cache followed a symlink. The helper plus the whitelist is the path that closes those classes.

## Out of scope

open-003 is unanswered: whether this site already has a TaskProlog, which Slurm version it runs, the cost on a 192-task node with node-local `TMPDIR`, and a real cross-target job on production nodes. Those measurements are absent from this repository.

Binaries built with `-march=native` on a login node, virtualenvs with compiled wheels from that node, and a module that has no build for the target stay as they are. The guard reports the missing module and leaves the environment unchanged. `python3 tests/measure_noop.py` prints a same-target timing on the machine where it is run. Treat that timing as a reading from that machine.
