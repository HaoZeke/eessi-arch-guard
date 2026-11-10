# Getting started

The documentation is split the way the [Diataxis framework](https://diataxis.fr/) describes.

* The [tutorial](../tutorial/index.md) is one path. It reloads a stack from a checkout, with no Slurm daemon and no EESSI mount.
* The [how-to guides](../howto/index.md) are procedures: install the prolog, configure it, run it from a batch script, and correct a failure.
* The [topics](../topics/index.md) explain detection, matching, and the reload.
* The [reference](../reference/index.md) lists the CLI, the variables, the config keys, the failure tokens, and the generated Python API.

A new user runs the tutorial. A site admin continues with [installation](installation.md) and the [install](../howto/install_prolog.md) and [config](../howto/configure.md) how-tos. Someone who already sees a `print` line starts at [correct a guard message](../howto/read_failure.md).

```{toctree}
:maxdepth: 1

installation
```
