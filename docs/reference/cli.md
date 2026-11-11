# Command line

```text
eessi-arch-guard [--shell] [--strict] [--chain PATH] [--timeout SEC] [--restore] [-h|--help] [--version]
```

`python3 src/eessi_arch_guard.py --help` prints:

```text
usage: eessi-arch-guard [--shell] [--chain PATH] [--timeout SEC] [--restore] [-h|--help] [--version]

Slurm TaskProlog: when the loaded EESSI CPU or accelerator target is not this
node's, print export/unset lines for the reloaded module environment.
--shell prints bash for: eval "$(eessi-arch-guard --shell)"
--chain PATH runs an existing task prolog first. Slurm does not pass arguments,
so a site sets this in a wrapper or in a root-owned eessi-arch-guard.conf.
--timeout SEC bounds the decision. The default is 60 seconds.
--restore reads a JSON restore request on stdin and writes the JSON result.
-h, --help print this text. --version prints the version.
```

`python3 src/eessi_arch_guard.py --version` prints `0.1.1`.

| Flag | Effect |
| --- | --- |
| `--shell` | Bash assignments on stdout, message on stderr. See [Run the guard from a batch script](../howto/shell_batch.md). |
| `--chain PATH` | Run this absolute executable first. A user job cannot pass it; Slurm supplies no arguments. See [Configure the guard](../howto/configure.md). |
| `--chain=PATH` | Same as `--chain PATH`. |
| `--timeout SEC` | Deadline in seconds. Overrides the config `timeout` key. `SEC` has to be greater than 0 and at most `threading.TIMEOUT_MAX`. Any other value is ignored, a warning goes to stderr, and the default of 60 is used. |
| `--timeout=SEC` | Same as `--timeout SEC`. |
| `--restore` | Helper mode. Read one JSON request on stdin and write one JSON object on stdout. The prolog execs this itself. |
| `-h`, `--help` | Print the usage text and exit 0. |
| `--version` | Print the version and exit 0. |

Flags other than these are skipped. The process exits 0 unless a chained prolog exits non-zero, or `--restore` hits an exception (that path exits 1 and writes the reason to stderr). The TaskProlog path catches exceptions and exits 0.

`--restore` is the helper described in [Concepts](../topics/concepts.md). The JSON request has keys `v` (1), `env`, `eessi`, `modules`, and `lmod`. Names and values are the Latin-1 wire form from {py:func}`eessi_arch_guard.to_wire`.

## Strict user job and step status

`--strict` returns 78 when the decision fails or an unexpected exception interrupts it.
The same flag works with `--shell` and with task-prolog output.
A successful reload, a same-target no-op or an intentional skip returns zero.
A chained prolog failure retains its own nonzero status.
Without `--strict`, the default site TaskProlog keeps its fail-open decision policy.
