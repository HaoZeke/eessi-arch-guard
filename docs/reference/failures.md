# Failure tokens

The prolog exits 0 in every row. A chained prolog's non-zero status is the exception: it is returned as-is and the guard does not restore modules.

| Situation | `EESSI_ARCH_GUARD_FAILED` | Output |
| --- | --- | --- |
| `EESSI_SOFTWARE_SUBDIR` unset | unset | Silent. EESSI is not loaded. |
| `EESSI_ARCH_GUARD=0` | unset | Silent opt-out. |
| `EESSI_SOFTWARE_SUBDIR_OVERRIDE` or `EESSI_ARCHDETECT_OPTIONS_OVERRIDE` set | unset | Silent. Detection is pinned. |
| `EESSI_ACCELERATOR_TARGET_OVERRIDE` set | depends on the CPU | Accelerator is not compared. |
| Same CPU and accelerator | unset | Silent. No exports. |
| No software directory for any candidate | `<was>->no-software-dir` | One `print` line naming the prefix and the candidates. `EESSI_ARCH_GUARD_MISSING=ignore` restores the silence. |
| archdetect missing, non-zero, empty, or unsafe | `<was>->archdetect-failed` | One `print` line. Unsafe accel stdout is this failure. |
| `waitpkg` missing on a march that includes it | unset on success | That candidate is skipped. The next installed candidate is loaded, and `EESSI_SOFTWARE_SUBDIR_OVERRIDE` is set to it. |
| A loaded name fails the grammar | `<was>->unsafe-module-name` | Restore does not start. |
| The list has no single `EESSI/<version>` | `<was>->restore-failed` | Restore does not start. |
| `LMOD_CMD` missing or not executable | `<was>->lmod-unavailable` | Restore does not start. |
| A module has no build for this target | `<was>-><now>` | One `print` line naming the missing modules, up to 32. Nothing from the restore is applied. |
| A changed value cannot be a prolog line | `<was>->unsafe-environment` | No partial delta. |
| Unexpected exception | unchanged, plus a skip line | One `print` or stderr line, exit 0. |
| Deadline exhausted | `<subdir>->timeout` | The waited-on process group is killed. The result is not cached. |
| `SIGTERM` during the decision | `<subdir>->terminated` | The same kill. The result is not cached. |
| `EESSI_ARCH_GUARD_VERBOSE=1` | unchanged | Skip and match reasons go to stderr. |

`<was>` is `EESSI_SOFTWARE_SUBDIR`, or `<cpu> <accel>` / `<cpu> no accel` once both sides are known. `<subdir>` on the deadline rows is `EESSI_SOFTWARE_SUBDIR` alone.

How to act on a line is [Correct a guard message](../howto/read_failure.md).
