# Detection and matching

CPU selection stays inside `eessi_archdetect.sh -a cpupath` plus a directory walk. Subdirectory names come from that script's stdout and from the directories that are mounted. An Intel node with A100 GPUs gets whichever candidate has a software directory, which is how open-001 was closed. Naming `x86_64/intel/icelake` or `x86_64/intel/skylake_avx512` inside the program would pick the wrong tree on the other of those nodes, and on every AMD node.

```{figure} detection.svg
:alt: CPU candidate walk beside the accelerator comparison, and the waitpkg pin
:width: 100%

cpupath is filtered for waitpkg, then the first installed directory wins. accelpath is compared as a string.
```

The examples call the stub `tests/stubs/eessi_archdetect.sh`. It implements the argv contract the guard uses, `[-a] cpupath` or `accelpath`. The EESSI script is the one under `EESSI_INIT_PREFIX` on a real node.

## Requirements before archdetect runs

`EESSI_PREFIX` and `EESSI_INIT_PREFIX` have to be absolute paths with no `..` segment and no CR, LF, or NUL. `EESSI_OS_TYPE` has to be a relative subdirectory: no empty segment, no `.` or `..`, and each segment matching `[A-Za-z0-9_.+-]+`. A value that fails these checks is an archdetect failure, and the script is not started.

The script path is `<EESSI_INIT_PREFIX>/eessi_archdetect.sh`. It is run as `bash --noprofile --norc`.

## CPU

cpupath must print one line, at most 65536 bytes, with no NUL and no character below ASCII 32. The line is split on `:`. Empty fields are dropped. Each candidate has to pass the same relative-subdirectory check.

The guard then reads CPU flags. The path is `EESSI_PROC_CPUINFO` when that variable is set, otherwise `/proc/cpuinfo`. Every `flags` line is parsed. The set used for the decision is the intersection: one logical CPU missing a flag removes it. The read stops at 16 MiB.

(detection-waitpkg)=
## The waitpkg check

archdetect's x86 spec requires the flags that separate one EESSI target from the previous one. GCC `-march` for that target enables a larger set. `waitpkg` (`tpause`, `umwait`, `umonitor`) is in the march mask and absent from the spec, so a virtual machine that hides the flag is still offered `sapphirerapids`. A binary built with that march then dies with `SIGILL`.

The guard drops a candidate whose leaf is one of these marches when `waitpkg` is missing from the intersection. The leaf spells `-` as `_`. The set is GCC 16 `gcc/config/i386/i386.h` on branch `releases/gcc-16`: the marches GCC 15.2.0 carried, plus `novalake`. `znver6`, the other new mask in that header, has no `WAITPKG`.

`alderlake`, `arrowlake`, `arrowlake-s`, `clearwaterforest`, `diamondrapids`, `grandridge`, `graniterapids`, `graniterapids-d`, `novalake`, `pantherlake`, `sapphirerapids`, `sierraforest`, `tremont`.

The other march flags stay unchecked. They are listed in [CPU flags](../reference/cpu_flags.md).

## The installed directory

The walk keeps the first remaining candidate for which this path is a directory:

```text
<EESSI_PREFIX>/software/<EESSI_OS_TYPE>/<candidate>/software
```

That directory is the CPU target. When no candidate has a directory, the result is `->no-software-dir` unless `EESSI_ARCH_GUARD_MISSING=ignore`. See [Correct a guard message](../howto/read_failure.md).

archdetect's own consumer, the EESSI module, walks the unfiltered list. When the directory that survives the waitpkg filter is further down the list than the directory the unfiltered walk would install, the reload sets `EESSI_SOFTWARE_SUBDIR_OVERRIDE` to the filtered directory and removes `EESSI_ARCHDETECT_OPTIONS` for that reload. A login-node value of `EESSI_ARCHDETECT_OPTIONS` would outrank the pin inside the module. The module's archdetect wrapper then exports `EESSI_ARCHDETECT_OPTIONS` as that one subdirectory.

If the user already set `EESSI_SOFTWARE_SUBDIR_OVERRIDE` or `EESSI_ARCHDETECT_OPTIONS_OVERRIDE`, the guard skips detection entirely. The pin is the user's.

## Accelerator

`eessi_archdetect.sh accelpath` is compared as a raw string with `EESSI_ACCEL_SUBDIR`.

* A non-zero status is "no accelerator". Exit 2 (no `nvidia-smi`) and exit 3 (`nvidia-smi` failed) are the EESSI script's no-GPU paths. The EESSI wrapper stores that as an empty `EESSI_ACCEL_SUBDIR`.
* Empty stdout on a zero status is "no accelerator".
* Stdout has to match `accel/nvidia/cc` plus digits, or `accel/amd/gfx` plus hexadecimal digits. Any other stdout, including a second line, is `->archdetect-failed`. An A100 is `accel/nvidia/cc80` only when the script prints that string.

The `ccNN` to `ccN0` directory fallback stays inside the EESSI module. The guard compares the string the script printed.

`EESSI_ACCELERATOR_TARGET_OVERRIDE` skips this comparison. The CPU walk still runs, and the loaded accelerator value is kept.

## What a match is

The CPU target equals `EESSI_SOFTWARE_SUBDIR`, and, when the accelerator is being compared, the accelpath result equals `EESSI_ACCEL_SUBDIR`. Both sides empty counts as a match. The prolog then prints nothing.

When the accelerator differs, the reload label is `<cpu> <accel>` or `<cpu> no accel` on each side. The [tutorial](../tutorial/index.md) is the CPU-only case: zen4 loaded, zen2 detected, label `x86_64/amd/zen4->x86_64/amd/zen2`.

## A GPU that changed

`docs/examples/accelerator.py mismatch` keeps the CPU at zen4, loads `accel/nvidia/cc90`, and points the stub `nvidia-smi` at compute capability 12.0. `CUDA/12.6.0` exists on both accelerator trees, so the reload completes:

```text
export EESSI_ACCEL_SUBDIR=accel/nvidia/cc120
export EESSI_ARCH_GUARD_RELOADED=x86_64/amd/zen4 accel/nvidia/cc90->x86_64/amd/zen4 accel/nvidia/cc120
print eessi-arch-guard: reloaded modules for x86_64/amd/zen4 accel/nvidia/cc120 (loaded for x86_64/amd/zen4 accel/nvidia/cc90)
```

`MODULEPATH` in that run gains the `cc120` modules directory ahead of the zen4 CPU modules. The full transcript also rewrites `PATH` under `$PREFIX`.

When `CUDA/12.6.0` exists only on `cc90`, the same node prints a failure and leaves the loaded accelerator in place:

```console
$ python3 docs/examples/accelerator.py missing-module
```

```text
export EESSI_ARCH_GUARD_FAILED=x86_64/amd/zen4 accel/nvidia/cc90->x86_64/amd/zen4 accel/nvidia/cc120
print eessi-arch-guard: this node is x86_64/amd/zen4 accel/nvidia/cc120, the modules were loaded for x86_64/amd/zen4 accel/nvidia/cc90; not available for this target: CUDA/12.6.0; environment left as it was
```

Load a CUDA build that exists for `cc120`, or drop `CUDA/12.6.0` from the submitting environment.
