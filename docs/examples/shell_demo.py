#!/usr/bin/env python3
"""Run ``--shell`` and eval the stdout in bash.

``$PREFIX`` in the transcript stands for the temporary prefix. The eval
result is read back from the shell.
"""

from __future__ import annotations

import subprocess
import sys

import support


def main() -> int:
    site, env = support.cpu_site()
    try:
        proc = support.run(env, ("--shell",))
        print("case: shell")
        print("command: python3 -I src/eessi_arch_guard.py --shell")
        support.show(proc, site.root)
        if proc.returncode != 0 or "print " in proc.stdout:
            print("shell mode printed a prolog line", file=sys.stderr)
            return 1
        if "reloaded modules for x86_64/amd/zen2" not in proc.stderr:
            print("message missing from stderr", file=sys.stderr)
            return 1
        script = (
            'eval "$1"\n'
            'printf "%s\\n" "$EESSI_SOFTWARE_SUBDIR" "$EESSI_ARCH_GUARD_RELOADED"\n'
        )
        baked = subprocess.run(
            ["bash", "--noprofile", "--norc", "-c", script, "bash", proc.stdout],
            capture_output=True,
            text=True,
            env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"},
            check=False,
        )
        print("--- eval ---")
        sys.stdout.write(baked.stdout)
        if baked.returncode != 0:
            sys.stderr.write(baked.stderr)
            return baked.returncode
        lines = baked.stdout.splitlines()
        if lines != [
            "x86_64/amd/zen2",
            "x86_64/amd/zen4->x86_64/amd/zen2",
        ]:
            print("eval result mismatch", file=sys.stderr)
            return 1
        return 0
    finally:
        site.close()


if __name__ == "__main__":
    sys.exit(main())
