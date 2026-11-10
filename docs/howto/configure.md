# Configure the guard

Slurm execs the TaskProlog with no arguments, so a job cannot pass `--chain` or `--timeout`. A site sets them in a root-owned config file, or in a wrapper that the site owns. `--chain` on the command line is the same hook for that wrapper and for tests.

## Which file is read

`/etc/eessi-arch-guard.conf` is read first. A file named `eessi-arch-guard.conf` beside the program is read only when `/etc` has none. The file has to be a regular file, owned by root, and not writable by group or others. Symlinks are ignored. A file that exists and fails those checks is unused, and one warning goes to stderr.

`docs/examples/check_config.sh` copies the prolog into a temporary directory, writes each config below beside that copy, and deletes the directory on exit. `$WORK` in the transcript is that directory.

A config owned by the user who runs the prolog is ignored:

```text
case: user-owned
exit: 0
--- stdout ---
(empty)
--- stderr ---
eessi-arch-guard: config ignored: $WORK/eessi-arch-guard.conf is not owned by root and is ignored
```

A symlink at the config path is ignored even when the target is root-owned and mode `644`:

```text
case: symlink
exit: 0
--- stdout ---
(empty)
--- stderr ---
eessi-arch-guard: config ignored: $WORK/eessi-arch-guard.conf is a symlink and is ignored
```

A root-owned file with group write (`mode 664`) is ignored:

```text
case: group-writable
exit: 0
--- stdout ---
(empty)
--- stderr ---
eessi-arch-guard: config ignored: $WORK/eessi-arch-guard.conf is writable by group or others and is ignored
```

## Keys

```
# /etc/eessi-arch-guard.conf
chain = /etc/slurm/task_prolog.site
timeout = 60
```

`chain` is an absolute path of an executable. It runs first, as the user, with the task environment. Its stdout is forwarded. Its stderr is forwarded as raw bytes. A non-zero status is returned and this program does not restore modules. Slurm then kills the task, which is what that prolog already did.

`timeout` is a positive number of seconds, at most `threading.TIMEOUT_MAX`. It bounds the lock wait, both archdetect runs, and the restore helper. The default is 60 seconds, Slurm's `DEFAULT_UNKILLABLE_TIMEOUT` in `src/common/read_config.h`. `--timeout` overrides the config line. The same limit applies with `--shell` and with `--chain`. An unusable value keeps the default and writes a warning. See [the CLI](../reference/cli.md).

Each setting is decoded on its own. A value that is not UTF-8 is kept with surrogateescape, and the other keys still apply. A non-UTF-8 byte in a comment leaves `chain` and `timeout` in force.

A root-owned config whose first line is a comment containing the byte `0xff`, with `chain` pointing at a script that prints `export SITE_PROLOG=1` and `timeout = 15`, forwards the chain and stays silent on stderr:

```text
case: root-owned-chain
comment_has_0xff: yes
exit: 0
--- stdout ---
export SITE_PROLOG=1
--- stderr ---
(empty)
```

A non-UTF-8 value on another key is kept, and `chain` still runs:

```text
case: non-utf8-value
exit: 0
--- stdout ---
export SITE_PROLOG=1
--- stderr ---
eessi-arch-guard: config value kept with its original bytes: other
```

`timeout = 0` is rejected. The chain still runs, because it is started before the timeout value is checked, and the decision then uses 60 seconds:

```text
case: bad-timeout
exit: 0
--- stdout ---
export SITE_PROLOG=1
--- stderr ---
eessi-arch-guard: config timeout is not a positive number of seconds at most 9223372036.0
```

A chain that exits 7 is the prolog status. Stdout stays empty when that script writes only to stderr, and the guard does not continue into module restore:

```text
case: chain-fails
exit: 7
--- stdout ---
(empty)
--- stderr ---
fail-prolog
```

## Wrapper

Use a wrapper when the existing prolog has to remain the `TaskProlog` path. In POSIX sh, an `if` whose then-branch is skipped has status 0, so `if site; then exec guard; fi; exit $?` reports success when the site prolog failed. Test the site prolog with `|| exit $?` and exec the guard only after that:

```sh
#!/bin/sh
/etc/slurm/task_prolog.site || exit $?
exec /usr/bin/python3 -I /usr/local/sbin/eessi-arch-guard
```

The example runs that shape against a site script that prints `export FROM_SITE=1`. The guard then runs with no EESSI stack loaded, so it adds no further lines:

```text
case: wrapper
command: $WORK/wrapper/wrapper.sh
exit: 0
--- stdout ---
export FROM_SITE=1
--- stderr ---
(empty)
```

When the site script exits 9, the wrapper exits 9 and the guard is not exec'd:

```text
case: wrapper-site-fails
command: $WORK/wrapper-fails/wrapper.sh
exit: 9
--- stdout ---
(empty)
--- stderr ---
site-failed
```

Which path a site should chain is unanswered here. The [design topic](../topics/design.md) records that as part of open-003.
