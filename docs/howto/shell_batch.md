# Run the guard from a batch script

Use this when the site has not installed the TaskProlog. For `srun` steps, see [Use the guard without changing the site TaskProlog](user_task_prolog.md). At the top of the batch script:

```bash
eval "$(eessi-arch-guard --shell)"
```

Stdout is `export` and `unset` with values quoted for bash. The human message goes to stderr, so the command substitution captures only the assignments.

`docs/examples/shell_demo.py` builds the same zen4-loaded, zen2-node stack as the [tutorial](../tutorial/index.md), runs `--shell`, and evals the stdout in `bash --noprofile --norc`.

```console
$ python3 docs/examples/shell_demo.py
```

```text
case: shell
command: python3 -I src/eessi_arch_guard.py --shell
exit: 0
--- stdout ---
export EBROOTR=$PREFIX/prefix/software/linux/x86_64/amd/zen2/software/R/4.4.2
export EESSI_ACCEL_SUBDIR=''
export EESSI_ARCH_GUARD_RELOADED='x86_64/amd/zen4->x86_64/amd/zen2'
export EESSI_SOFTWARE_SUBDIR=x86_64/amd/zen2
export MODULEPATH=$PREFIX/prefix/software/linux/x86_64/amd/zen2/modules/all
export PATH=$PREFIX/prefix/software/linux/x86_64/amd/zen2/software/R/4.4.2/bin:$PREFIX/prefix/software/linux/x86_64/amd/zen2/bin:$PREFIX/bin:/usr/bin:/bin
export _LMFILES_=''
export _ModuleTable001_=table-for-x86_64-amd-zen2
--- stderr ---
eessi-arch-guard: reloaded modules for x86_64/amd/zen2 (loaded for x86_64/amd/zen4)
--- eval ---
x86_64/amd/zen2
x86_64/amd/zen4->x86_64/amd/zen2
```

The `eval` block is what the shell sees after applying stdout. `EESSI_SOFTWARE_SUBDIR` is the node, and `EESSI_ARCH_GUARD_RELOADED` records the change. There is no `print` line: that directive belongs to the prolog protocol.

This eval fixes the shell that runs it. `srun ./a.out` from that shell inherits the corrected environment only when the `srun` is started after the eval and the export reaches the step. A bare exec that never passed through the shell still has the submitting environment. The [TaskProlog install](install_prolog.md) is the path that runs before `execve`.

## Refuse a failed restore before the payload

An opt-in job wrapper can use strict status instead of interpreting stderr:

```bash
if delta="$(/usr/bin/python3 -I /absolute/path/eessi_arch_guard.py --strict --shell)"; then
    eval "$delta"
else
    exit "$?"
fi
```

The source path names the installed guard file. The native Python interpreter starts before any inherited EESSI Python. A nonzero guard status prevents the payload from running. This does not alter the configured site TaskProlog.
