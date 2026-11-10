# Install the TaskProlog

Install the single file on the prolog host and point Slurm at it. The file's shebang is `#!/usr/bin/python3 -I`. Runtime dependencies are Python 3.9 or newer and the standard library. Platform notes are in [Installation](../intro/installation.md).

From a checkout of this repository:

```bash
sudo install -m 755 src/eessi_arch_guard.py /usr/local/sbin/eessi-arch-guard
```

In `slurm.conf`:

```
TaskProlog=/usr/local/sbin/eessi-arch-guard
```

`docs/examples/check_install.sh` copies the file to that path, prints the shebang and the version, installs a throwaway virtualenv, and deletes both so the script can be run again. It refuses to replace an existing `/usr/local/sbin/eessi-arch-guard`. On this machine the prolog half of the transcript is:

```text
+ sudo install -m 755 src/eessi_arch_guard.py /usr/local/sbin/eessi-arch-guard
+ head -n 1 /usr/local/sbin/eessi-arch-guard
#!/usr/bin/python3 -I
+ /usr/local/sbin/eessi-arch-guard --version
0.1.1
```

Leave the file in place on a production host. The script removes it only so the example is repeatable.

## Login-node console script

The same script then creates a virtualenv, runs `pip install .`, and checks the generated console script:

```text
+ python3 -m venv <venv>
+ <venv>/bin/python -m pip install .
+ <venv>/bin/python -c 'import importlib.metadata; print(importlib.metadata.version("eessi-arch-guard"))'
0.1.1
console: <venv>/bin/eessi-arch-guard
console import: from eessi_arch_guard import main
console -I flag: absent
+ <venv>/bin/eessi-arch-guard --version
0.1.1
```

The console script is the right binary for [`--shell`](shell_batch.md) on a login node. It is started without `-I`. `TaskProlog` stays on the installed file, whose shebang passes `-I`.

## After the copy

Add the `TaskProlog` line, or chain this file from the prolog the site already runs. Both arrangements are in [Configure the guard](configure.md). Slurm reads `TaskProlog` with no arguments, so flags such as `--chain` and `--timeout` come from the config file or from a wrapper the site owns.
