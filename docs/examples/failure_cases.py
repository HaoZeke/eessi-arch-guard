#!/usr/bin/env python3
"""Print the guard transcript for one failure or skip.

    python3 docs/examples/failure_cases.py <case>

``$PREFIX`` stands for the temporary prefix. Each case removes that prefix
before it returns.
"""

from __future__ import annotations

import sys

import support

CASES = (
    "no-software-dir",
    "ignore-missing",
    "missing-module",
    "opt-out",
    "archdetect",
    "lmod",
    "verbose",
    "bad-timeout",
)


def _finish(site, proc, checks) -> int:
    print("command: python3 -I src/eessi_arch_guard.py")
    support.show(proc, site.root if site is not None else "")
    failed = False
    for label, ok in checks:
        if not ok:
            print("check failed: %s" % label, file=sys.stderr)
            failed = True
    if site is not None:
        site.close()
    if proc.returncode != 0 or failed:
        return 1
    return 0


def no_software_dir() -> int:
    site = support.harness.Site()
    site.write_cpuinfo("zen2")
    env = support.harness.base_env(site, support.ZEN4, [], gpu="none")
    proc = support.run(env)
    text = proc.stdout
    return _finish(
        site,
        proc,
        [
            ("token", "->no-software-dir" in text),
            ("words", "no EESSI software directory" in text),
            ("unchanged", "export EESSI_SOFTWARE_SUBDIR=" not in text),
        ],
    )


def ignore_missing() -> int:
    site = support.harness.Site()
    site.write_cpuinfo("zen2")
    env = support.harness.base_env(site, support.ZEN4, [], gpu="none")
    env["EESSI_ARCH_GUARD_MISSING"] = "ignore"
    proc = support.run(env)
    return _finish(
        site,
        proc,
        [
            ("silent stdout", proc.stdout == ""),
            ("silent stderr", proc.stderr == ""),
        ],
    )


def missing_module() -> int:
    site, env = support.cpu_site(modules=("R/4.4.2",), both_modules=False)
    proc = support.run(env)
    text = proc.stdout
    return _finish(
        site,
        proc,
        [
            ("names R", "R/4.4.2" in text),
            ("left as it was", "environment left as it was" in text),
            ("keeps subdir", "export EESSI_SOFTWARE_SUBDIR=" not in text),
            ("failed pair", "x86_64/amd/zen4->x86_64/amd/zen2" in text),
        ],
    )


def opt_out() -> int:
    site, env = support.cpu_site()
    env["EESSI_ARCH_GUARD"] = "0"
    proc = support.run(env)
    return _finish(
        site,
        proc,
        [
            ("silent stdout", proc.stdout == ""),
            ("silent stderr", proc.stderr == ""),
        ],
    )


def archdetect() -> int:
    site, env = support.cpu_site()
    site.write_cpuinfo("zen2", fail=True)
    proc = support.run(env)
    text = proc.stdout
    return _finish(
        site,
        proc,
        [
            ("token", "->archdetect-failed" in text),
            ("detail", "archdetect forced failure" in text),
        ],
    )


def lmod() -> int:
    site, env = support.cpu_site()
    env["LMOD_CMD"] = "/no/such/lmod"
    proc = support.run(env)
    text = proc.stdout
    return _finish(
        site,
        proc,
        [
            ("token", "->lmod-unavailable" in text),
            ("words", "LMOD_CMD is missing or not executable" in text),
        ],
    )


def verbose() -> int:
    site, env = support.cpu_site(node="zen4", loaded=support.ZEN4)
    env["EESSI_ARCH_GUARD_VERBOSE"] = "1"
    proc = support.run(env)
    return _finish(
        site,
        proc,
        [
            ("silent stdout", proc.stdout == ""),
            ("detail", proc.stderr.strip() == "target already matches this node"),
        ],
    )


def bad_timeout() -> int:
    proc = support.harness.run_guard(
        {"PATH": "/usr/bin:/bin", "LC_ALL": "C"},
        ("--timeout", "0"),
    )
    print("command: python3 -I src/eessi_arch_guard.py --timeout 0")
    support.show(proc, "")
    warning = (
        "eessi-arch-guard: --timeout is not a positive number of seconds "
        "at most 9223372036.0\n"
    )
    if proc.returncode != 0 or proc.stdout or proc.stderr != warning:
        print("unexpected timeout transcript", file=sys.stderr)
        return 1
    return 0


def main() -> int:
    dispatch = {
        "no-software-dir": no_software_dir,
        "ignore-missing": ignore_missing,
        "missing-module": missing_module,
        "opt-out": opt_out,
        "archdetect": archdetect,
        "lmod": lmod,
        "verbose": verbose,
        "bad-timeout": bad_timeout,
    }
    if len(sys.argv) != 2 or sys.argv[1] not in dispatch:
        print("usage: failure_cases.py " + "|".join(CASES), file=sys.stderr)
        return 2
    print("case: %s" % sys.argv[1])
    return dispatch[sys.argv[1]]()


if __name__ == "__main__":
    sys.exit(main())
