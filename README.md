# eessi-arch-guard

eessi-arch-guard is a Slurm TaskProlog that reloads an EESSI module stack when the task runs on a CPU or GPU other than the target recorded at `module load`. The EESSI module chooses that target once. Lmod keeps the recorded `MODULEPATH` and the `EBROOT*` paths until the stack is purged and loaded again. The prolog does that purge and load on the compute node, before `execve`, and prints the environment delta for `slurmstepd` to apply.

The documentation is a Sphinx site under `docs/`: a tutorial, how-to guides, topics, and a reference, including the generated Python API.

```console
python3 -m pip install -r docs/requirements.txt
make -C docs html
```

The HTML is written to `docs/_build/html`.

## Install the prolog

The prolog target is `src/eessi_arch_guard.py` (shebang `#!/usr/bin/python3 -I`). Python 3.9 or newer, standard library only.

```bash
sudo install -m 755 src/eessi_arch_guard.py /usr/local/sbin/eessi-arch-guard
```

```
TaskProlog=/usr/local/sbin/eessi-arch-guard
```

`pip install .` also installs a console script for `eessi-arch-guard --shell` on a login node. That generated script is started without `-I`. Point `TaskProlog` at the file. The worked install, the config file, and the failure messages are in `docs/`.

## Develop

```console
python3 -m pip install ruff
ruff check src tests
python3 -m unittest discover -s tests -v
```

Copyright (c) eessi-arch-guard developers
