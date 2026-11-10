# Concepts

The EESSI module picks a CPU target once, at load time, and an accelerator target the same way. Both are written into `MODULEPATH` and into the paths later modules export. Slurm sends that environment to the job. A task that lands on another CPU or GPU then runs the submitting machine's binaries.

`module load EESSI/<version>` inside the job leaves the record in place, because Lmod evaluates a loaded module file once. `module reload`, unload/load, and swap leave the other modules inactive. The sequence that changes the stack is: list `LOADEDMODULES`, `module --force purge`, `module load EESSI/<version>` by that full name, then load the other names in the same order.

```{figure} concepts.svg
:alt: Submit host, TaskProlog, and the match or reload branches
:width: 100%

The prolog compares the loaded target with the node, then either returns or reloads.
```

The comparison is local to the task. CPU flags come from `EESSI_PROC_CPUINFO` or `/proc/cpuinfo`. The accelerator string comes from `eessi_archdetect.sh accelpath`, which sees the GPUs visible to the task. A job prolog runs outside the task cgroup, so a GPU hidden from the task is still visible there. The TaskProlog runs as the user, with the task environment, before `execve`.

On a mismatch the prolog runs a helper, `python3 -I -X utf8 <this file> --restore`, whose stdin is a JSON request carrying the task environment. The helper's own process environment is `PATH`, `HOME`, `LANG`, `LC_ALL`, `TMPDIR`, and `EESSI_ARCH_GUARD_WORKDIR`. Module names are arguments to `LMOD_CMD` after a grammar check. Bash inside the helper is `bash --noprofile --norc`, with `BASH_ENV`, `ENV`, `SHELLOPTS`, `BASHOPTS`, `BASH_XTRACEFD`, and `PS4` removed, and its last command is `env -0`. The prolog prints the delta of that environment: `export` and `unset` lines Slurm can apply, plus one `print` line for local task 0.

The [detection](detection.md) topic is the CPU and accelerator walk. [What the guard emits](output.md) is the delta, the cache, and the deadline. [Design](design.md) records why this shape was kept.
