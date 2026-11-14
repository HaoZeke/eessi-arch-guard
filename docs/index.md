# eessi-arch-guard

```{image} _static/logo-light.svg
:alt: eessi-arch-guard
:width: 420px
:class: light-only
```

```{image} _static/logo-dark.svg
:alt: eessi-arch-guard
:width: 420px
:class: dark-only
```

eessi-arch-guard is a Slurm TaskProlog that reloads an EESSI module stack when the task runs on a CPU or GPU other than the target recorded at `module load`. The EESSI module chooses that target once. Lmod keeps the recorded `MODULEPATH` and the `EBROOT*` paths until the stack is purged and loaded again. The prolog does that purge and load on the compute node, before `execve`, and prints the environment delta for `slurmstepd` to apply.

Version {{ version }}.

## Why

A job submitted from a login node carries that node's EESSI paths. Slurm delivers the same environment to the allocated node. A binary from the login-node tree then executes on a different microarchitecture: the process can die with `SIGILL`, take a slow generic path, or open a GPU tree that is absent on the node. Running `module load EESSI/<version>` again leaves the stack in place, because Lmod evaluates a loaded module file once.

The prolog is the step that still has the task environment and the executing node's view of the CPU and the GPU. A batch script can purge and reload in the shell. `srun ./a.out` starts the binary directly, so the corrected variables have to be in the environment before `execve`.

::::{grid} 1 2 2 2
:gutter: 2

:::{grid-item-card} Installation
:link: intro/installation
:link-type: doc

Python 3.9 or newer on the prolog host, and a virtual environment on a login node.
:::

:::{grid-item-card} Tutorial
:link: tutorial/index
:link-type: doc

Reload one stack that was loaded for a different CPU, and read the prolog lines.
:::

:::{grid-item-card} How-to guides
:link: howto/index
:link-type: doc

Install the TaskProlog, write its config file, and correct a failed reload.
:::

:::{grid-item-card} Topics
:link: topics/index
:link-type: doc

How detection, the waitpkg check, and the reload fit together.
:::

:::{grid-item-card} Reference
:link: reference/index
:link-type: doc

CLI, environment variables, config keys, failure tokens, and the Python API.
:::
::::

```{toctree}
:hidden:
:maxdepth: 2
:caption: Documentation

intro/index
tutorial/index
howto/index
topics/index
reference/index
development
```

Copyright (c) eessi-arch-guard developers
