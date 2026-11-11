# Use the guard without changing the site TaskProlog

Use this when the site runs its own `TaskProlog` and you cannot, or should not, change `slurm.conf`.
Slurm has a second, per-job task prolog for exactly this, and it runs after the site's.

## For `srun` steps

`srun --task-prolog=PATH` runs `PATH` just before each task starts, after any `TaskProlog` from `slurm.conf`.
The environment variable `SLURM_TASK_PROLOG` is the same option, so a module or a wrapper can set it once for every `srun` that follows:

```bash
export SLURM_TASK_PROLOG=/path/to/eessi_arch_guard.py
srun -p genoa ./a.out
```

Give the full path from `/`; the file must be executable and readable on the compute node.
The file's own shebang, `#!/usr/bin/python3 -I`, starts it.

## For the batch script itself

`sbatch` has no task prolog.
The commands in a batch script run in the batch step, which runs no task prolog; `srun` steps do.
At the top of the script, use the [shell mode](shell_batch.md):

```bash
eval "$(/usr/bin/python3 -I /path/to/eessi_arch_guard.py --shell)"
```

`srun` steps started after that line inherit the corrected environment, and with `SLURM_TASK_PROLOG` set they are checked again on their own nodes.

## What a wrong tree looks like, and the fix

Measured on Snellius (Slurm 26.05.5) with EESSI 2026.06 and `GSL/2.8-GCC-15.2.0` loaded on a zen2 login node, then run on a zen4 (genoa) node:

| How the task started | `EESSI_SOFTWARE_SUBDIR` in the task | GSL tree |
|---|---|---|
| `srun -p genoa`, no guard | `x86_64/amd/zen2` | zen2 |
| `srun -p genoa` with `SLURM_TASK_PROLOG` | `x86_64/amd/zen4` | zen4 |
| `sbatch --export=ALL`, batch step, no guard | `x86_64/amd/zen2` | zen2 |
| same batch step after the `--shell` line | `x86_64/amd/zen4` | zen4 |

The guard's stderr line in the fixed cases was `eessi-arch-guard: reloaded modules for x86_64/amd/zen4 (loaded for x86_64/amd/zen2)`, and `EESSI_ARCH_GUARD_RELOADED` read `x86_64/amd/zen2->x86_64/amd/zen4`.

A plain `sbatch` without `--export=ALL` does not carry the login environment on that site, so the job loads EESSI on its own node and needs no guard.
The wrong tree comes from `--export=ALL` and from `srun` run in a shell that already loaded EESSI.

## Limits

- A job's own `--task-prolog` replaces `SLURM_TASK_PROLOG`: the job runs its own prolog and not the guard.
- The per-job prolog does not run for the batch step; the `--shell` line covers it.
- Once the site installs the [TaskProlog](install_prolog.md), the per-job prolog is redundant: the guard then finds the stack already matches and does nothing.
