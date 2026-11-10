# Config file

| Item | Value |
| --- | --- |
| First path | `/etc/eessi-arch-guard.conf` |
| Second path | `eessi-arch-guard.conf` beside the program, read only when the first path does not exist |
| Owner | root (`st_uid` 0) |
| Type | regular file |
| Mode | group-write and other-write bits clear (`mode & 0o022 == 0`) |
| Symlink | ignored |

| Key | Meaning |
| --- | --- |
| `chain` | Absolute path of an executable. Run first. Non-zero status is returned unchanged. |
| `timeout` | Positive seconds, at most `threading.TIMEOUT_MAX`. Default 60. Overridden by `--timeout`. |

Lines are `key = value`. Blank lines and lines whose first non-space byte is `#` are comments. A line with no `=` is skipped. Keys and values are stripped. A key that is not valid UTF-8 is skipped. A value that is not valid UTF-8 is stored with surrogateescape, and stderr receives `eessi-arch-guard: config value kept with its original bytes: <key>`.

The procedure and the transcripts are [Configure the guard](../howto/configure.md).
