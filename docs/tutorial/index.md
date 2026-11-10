# Reload a mismatched stack

This tutorial runs the guard from a checkout. The prefix, `eessi_archdetect.sh`, and `LMOD_CMD` are the stubs in `tests/`. The guard itself is `src/eessi_arch_guard.py`. You need Python 3.9 or newer and `bash`.

The loaded stack is EESSI 2025.06 plus `R/4.4.2`, recorded for `x86_64/amd/zen4`. The fake cpuinfo is a zen2 node, and both CPU trees exist in the prefix. `$PREFIX` in the transcript is the temporary directory the script created.

## Check the version

```console
$ python3 src/eessi_arch_guard.py --version
0.1.1
```

## Run the guard on the mismatched stack

```console
$ python3 docs/examples/walkthrough.py mismatch
```

The script prints:

```text
case: mismatch
loaded: x86_64/amd/zen4
node cpuinfo: zen2
command: python3 -I src/eessi_arch_guard.py
exit: 0
--- stdout ---
export EBROOTR=$PREFIX/prefix/software/linux/x86_64/amd/zen2/software/R/4.4.2
export EESSI_ACCEL_SUBDIR=
export EESSI_ARCH_GUARD_RELOADED=x86_64/amd/zen4->x86_64/amd/zen2
export EESSI_SOFTWARE_SUBDIR=x86_64/amd/zen2
export MODULEPATH=$PREFIX/prefix/software/linux/x86_64/amd/zen2/modules/all
export PATH=$PREFIX/prefix/software/linux/x86_64/amd/zen2/software/R/4.4.2/bin:$PREFIX/prefix/software/linux/x86_64/amd/zen2/bin:$PREFIX/bin:/usr/bin:/bin
export _LMFILES_=
export _ModuleTable001_=table-for-x86_64-amd-zen2
print eessi-arch-guard: reloaded modules for x86_64/amd/zen2 (loaded for x86_64/amd/zen4)
--- stderr ---
(empty)
```

Stdout is the TaskProlog protocol. `export` lines are the environment delta. The `print` line is the message `slurmstepd` writes to the task's stdout when `SLURM_LOCALID` is 0 or unset. `EESSI_ARCH_GUARD_RELOADED` is `was->now`. `EBROOTR` and `MODULEPATH` now point at the zen2 tree. The process exits 0, so Slurm continues with the task.

How the CPU walk chooses zen2 is covered in [Detection and matching](../topics/detection.md).

## Run it again on the corrected environment

The example applies those `export` lines the way `tests/slurm_model.py` reads a TaskProlog, then runs the guard on the result.

```console
$ python3 docs/examples/walkthrough.py again
case: again
command: python3 -I src/eessi_arch_guard.py
exit: 0
--- stdout ---
(empty)
--- stderr ---
(empty)
```

The loaded subdirectory is the node the second run detects. A match prints nothing.

Remove the temporary prefix when you are done:

```console
$ python3 docs/examples/walkthrough.py clean
case: clean
removed: docs/examples/_work
```

## Where to go next

A real job shows the same `print` line. The paths in that line use the site prefix. When the line says the environment was left as it was, follow [Correct a guard message](../howto/read_failure.md). A site that has yet to install the prolog can run the same decision from a batch script with [`--shell`](../howto/shell_batch.md).
