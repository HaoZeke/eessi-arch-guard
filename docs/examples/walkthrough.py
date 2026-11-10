#!/usr/bin/env python3
"""Reload a stack that was loaded for zen4 while the node is zen2.

Run from any directory, in this order:

    python3 docs/examples/walkthrough.py mismatch
    python3 docs/examples/walkthrough.py again
    python3 docs/examples/walkthrough.py clean

``mismatch`` writes ``docs/examples/_work``. ``again`` runs the guard on the
environment produced by applying that prolog stdout. ``$PREFIX`` in the
transcript stands for the temporary prefix.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import support

WORK = Path(__file__).resolve().parent / "_work"
ENV_PATH = WORK / "env.json"
ROOT_PATH = WORK / "root.txt"


def mismatch() -> int:
    if WORK.exists():
        shutil.rmtree(WORK)
    WORK.mkdir(parents=True)
    site, env = support.cpu_site(directory=str(WORK))
    # Keep the prefix alive for the follow-up run. Site.close() deletes it.
    ROOT_PATH.write_text(site.root, encoding="ascii")
    proc = support.run(env)
    updated = support.apply_stdout(env, proc.stdout)
    ENV_PATH.write_text(json.dumps(updated), encoding="ascii")
    print("case: mismatch")
    print("loaded: %s" % support.ZEN4)
    print("node cpuinfo: zen2")
    print("command: python3 -I src/eessi_arch_guard.py")
    support.show(proc, site.root)
    if proc.returncode != 0:
        return proc.returncode
    needle = (
        "print eessi-arch-guard: reloaded modules for "
        "x86_64/amd/zen2 (loaded for x86_64/amd/zen4)\n"
    )
    if needle not in proc.stdout:
        print("missing print line", file=sys.stderr)
        return 1
    if "EESSI_ARCH_GUARD_RELOADED=x86_64/amd/zen4->x86_64/amd/zen2" not in proc.stdout:
        print("missing reload marker", file=sys.stderr)
        return 1
    if "EESSI_SOFTWARE_SUBDIR=x86_64/amd/zen2" not in proc.stdout:
        print("missing software subdir", file=sys.stderr)
        return 1
    return 0


def again() -> int:
    if not ENV_PATH.is_file() or not ROOT_PATH.is_file():
        print("run mismatch first", file=sys.stderr)
        return 2
    env = json.loads(ENV_PATH.read_text(encoding="ascii"))
    prefix = ROOT_PATH.read_text(encoding="ascii")
    proc = support.run(env)
    print("case: again")
    print("command: python3 -I src/eessi_arch_guard.py")
    support.show(proc, prefix)
    if proc.returncode != 0 or proc.stdout or proc.stderr:
        print("expected a silent match", file=sys.stderr)
        return 1
    return 0


def clean() -> int:
    shutil.rmtree(WORK, ignore_errors=True)
    print("case: clean")
    print("removed: docs/examples/_work")
    return 0


def main() -> int:
    commands = {"mismatch": mismatch, "again": again, "clean": clean}
    if len(sys.argv) != 2 or sys.argv[1] not in commands:
        print("usage: walkthrough.py mismatch|again|clean", file=sys.stderr)
        return 2
    return commands[sys.argv[1]]()


if __name__ == "__main__":
    sys.exit(main())
