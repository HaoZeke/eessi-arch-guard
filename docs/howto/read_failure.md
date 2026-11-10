# Correct a guard message

On a mismatch the guard exports the reloaded environment and one `print` line, as in the [tutorial](../tutorial/index.md). On a failure it still exits 0. It exports `EESSI_ARCH_GUARD_FAILED` and prints one line. Every other variable stays as the task arrived with it. Slurm would kill the task if this program exited non-zero, so a failure is reported in the environment and the task still starts.

The token after `->` is the case to correct. The full list is the [failure reference](../reference/failures.md). The examples below run the real guard against the stubs in `tests/`. `$PREFIX` is the temporary prefix.

## No software directory

The CPU walk found candidates and none of them has `prefix/software/<os>/<candidate>/software`.

```console
$ python3 docs/examples/failure_cases.py no-software-dir
```

```text
case: no-software-dir
command: python3 -I src/eessi_arch_guard.py
exit: 0
--- stdout ---
export EESSI_ARCH_GUARD_FAILED=x86_64/amd/zen4->no-software-dir
print eessi-arch-guard: no EESSI software directory for this node under $PREFIX/prefix (candidates x86_64/amd/zen2 x86_64/generic); modules were loaded for x86_64/amd/zen4; environment left as it was
--- stderr ---
(empty)
```

Mount or install a software tree for one of the candidates in the message. The loaded stack stays in place until that directory exists.

A node that is expected to have no EESSI tree can set `EESSI_ARCH_GUARD_MISSING=ignore`. The same inputs then produce an empty transcript:

```console
$ python3 docs/examples/failure_cases.py ignore-missing
case: ignore-missing
command: python3 -I src/eessi_arch_guard.py
exit: 0
--- stdout ---
(empty)
--- stderr ---
(empty)
```

## A module has no build for this target

The node has a software directory, and one of the loaded modules has no file on the reloaded `MODULEPATH`. The message names each missing module, and stops after 32.

```console
$ python3 docs/examples/failure_cases.py missing-module
```

```text
case: missing-module
command: python3 -I src/eessi_arch_guard.py
exit: 0
--- stdout ---
export EESSI_ARCH_GUARD_FAILED=x86_64/amd/zen4->x86_64/amd/zen2
print eessi-arch-guard: this node is x86_64/amd/zen2, the modules were loaded for x86_64/amd/zen4; not available for this target: R/4.4.2; environment left as it was
--- stderr ---
(empty)
```

Here `EESSI_ARCH_GUARD_FAILED` is `loaded-target->node-target`, because a target was selected and the reload then failed. Load a version that exists for the node, or unload the named module before submitting. The same shape is used when the accelerator changes and the module exists only on the old GPU tree; see [Detection and matching](../topics/detection.md).

## archdetect failed

`eessi_archdetect.sh` is missing, exits non-zero, prints an empty cpupath, or prints a candidate the guard rejects as unsafe. Accelerator stdout that is non-empty and fails the subdirectory grammar is the same token.

```console
$ python3 docs/examples/failure_cases.py archdetect
```

```text
case: archdetect
command: python3 -I src/eessi_arch_guard.py
exit: 0
--- stdout ---
export EESSI_ARCH_GUARD_FAILED=x86_64/amd/zen4->archdetect-failed
print eessi-arch-guard: archdetect failed (archdetect forced failure); environment left as it was
--- stderr ---
(empty)
```

The parenthetical text is the first line of the script's stderr, or a fixed reason such as `cpupath output is not a single line`. Restore `EESSI_INIT_PREFIX/eessi_archdetect.sh`. `EESSI_PREFIX` and `EESSI_INIT_PREFIX` have to be absolute. `EESSI_OS_TYPE` has to be a relative subdirectory with no empty, `.`, or `..` segment.

## LMOD_CMD is missing

The targets differ, and `LMOD_CMD` is missing or not executable. The restore never starts.

```console
$ python3 docs/examples/failure_cases.py lmod
```

```text
case: lmod
command: python3 -I src/eessi_arch_guard.py
exit: 0
--- stdout ---
export EESSI_ARCH_GUARD_FAILED=x86_64/amd/zen4->lmod-unavailable
print eessi-arch-guard: LMOD_CMD is missing or not executable; this node is x86_64/amd/zen2, the modules were loaded for x86_64/amd/zen4; environment left as it was
--- stderr ---
(empty)
```

Point `LMOD_CMD` at the Lmod executable the EESSI module normally exports.

## Turn the guard off

`EESSI_ARCH_GUARD=0` in the submitting environment skips the decision with no output, including on a mismatch:

```console
$ python3 docs/examples/failure_cases.py opt-out
case: opt-out
command: python3 -I src/eessi_arch_guard.py
exit: 0
--- stdout ---
(empty)
--- stderr ---
(empty)
```

Put the variable in the environment that Slurm copies. An `EESSI_ARCH_GUARD` value that is already set is the one the task receives. The design treats `srun --export=ALL,EESSI_ARCH_GUARD=0` as leaving that existing value in place. That Slurm line was not re-read in this repository.

`EESSI_SOFTWARE_SUBDIR_OVERRIDE` or `EESSI_ARCHDETECT_OPTIONS_OVERRIDE` also skips the whole decision: detection is pinned. `EESSI_ACCELERATOR_TARGET_OVERRIDE` skips only the accelerator comparison. The CPU walk still runs.

## See why a run was silent

`EESSI_ARCH_GUARD_VERBOSE=1` sends the skip or match reason to stderr. A matching node prints:

```console
$ python3 docs/examples/failure_cases.py verbose
case: verbose
command: python3 -I src/eessi_arch_guard.py
exit: 0
--- stdout ---
(empty)
--- stderr ---
target already matches this node
```

## The decision ran out of time

`--timeout 0` is rejected. The guard warns, keeps the default of 60 seconds, and exits 0:

```console
$ python3 docs/examples/failure_cases.py bad-timeout
case: bad-timeout
command: python3 -I src/eessi_arch_guard.py --timeout 0
exit: 0
--- stdout ---
(empty)
--- stderr ---
eessi-arch-guard: --timeout is not a positive number of seconds at most 9223372036.0
```

A decision that is still running at the deadline sets `EESSI_ARCH_GUARD_FAILED` to a value ending in `->timeout`, kills the process group it is waiting on, and exits 0. That result is not cached. Raise `timeout` in the [config file](configure.md), or pass `--timeout SEC` from a wrapper. `SEC` has to be greater than 0 and at most `threading.TIMEOUT_MAX` (the number in the warning). `SIGTERM` during the decision uses the token `->terminated` and the same kill.
