# Installation

The TaskProlog runs on the compute node. That host is Linux, with Python 3.9 or newer and the standard library. The program is started as `python3 -I`. `requires-python` in `pyproject.toml` is `>=3.9`.

The test workflow runs the suite on Python 3.9 and 3.12. Its comment records that a Rocky 9 prolog host runs Python 3.9. On Ubuntu and Debian the `python3` package provides the interpreter. This checkout reports:

```console
$ python3 --version
Python 3.12.3
```

## From a git checkout

The prolog target is the file `src/eessi_arch_guard.py`. Its first line is `#!/usr/bin/python3 -I`. Copy that file to an absolute path and name it in `slurm.conf`:

```
TaskProlog=/usr/local/sbin/eessi-arch-guard
```

The steps, the shebang check, and a virtualenv install are in [Install the TaskProlog](../howto/install_prolog.md). `docs/examples/check_install.sh` performs them and then removes the copy so the script can be run again.

## Login node

`pip install` of this repository installs a console script named `eessi-arch-guard`. That script imports `eessi_arch_guard.main` and is started without `-I`. Use it for `eessi-arch-guard --shell` on a login node. Point `TaskProlog` at the file itself.

```console
$ python3 -m venv .venv
$ .venv/bin/python -m pip install .
$ .venv/bin/eessi-arch-guard --version
0.1.1
```

`docs/examples/check_install.sh` runs that sequence with the virtualenv under `/tmp`. The script's transcript on this machine ends with:

```text
console import: from eessi_arch_guard import main
console -I flag: absent
+ <venv>/bin/eessi-arch-guard --version
0.1.1
```

## What the host has to provide

`PATH` must contain `python3` and `bash`. The loaded EESSI environment supplies `EESSI_INIT_PREFIX/eessi_archdetect.sh` and `LMOD_CMD`. The guard reads CPU flags from `EESSI_PROC_CPUINFO` when that variable is set, and from `/proc/cpuinfo` otherwise. Accelerator detection is whatever `eessi_archdetect.sh accelpath` prints; the EESSI script uses `nvidia-smi` for NVIDIA GPUs.
