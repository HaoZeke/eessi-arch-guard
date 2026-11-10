#!/usr/bin/env python3
"""Reload or refuse a stack when the GPU target changes.

    python3 docs/examples/accelerator.py mismatch
    python3 docs/examples/accelerator.py missing-module

The CPU target stays zen4. The loaded accelerator is ``accel/nvidia/cc90``
and the node reports compute capability 12.0. ``$PREFIX`` stands for the
temporary prefix.
"""

from __future__ import annotations

import sys

import support


def _site(with_cc120_module: bool):
    site = support.harness.Site()
    site.add_cpu(support.ZEN4)
    site.add_module(support.ZEN4, "R/4.4.2")
    site.add_accel(support.ZEN4, support.CC90)
    site.add_accel(support.ZEN4, support.CC120)
    site.add_module(support.ZEN4, "CUDA/12.6.0", accel=support.CC90)
    if with_cc120_module:
        site.add_module(support.ZEN4, "CUDA/12.6.0", accel=support.CC120)
    site.write_cpuinfo("zen4")
    env = support.harness.base_env(
        site,
        support.ZEN4,
        ["R/4.4.2", "CUDA/12.6.0"],
        accel=support.CC90,
        gpu="1",
        gpu_cc="12.0",
    )
    return site, env


def mismatch() -> int:
    site, env = _site(True)
    try:
        proc = support.run(env)
        print("case: accelerator-mismatch")
        print("command: python3 -I src/eessi_arch_guard.py")
        support.show(proc, site.root)
        marker = (
            "EESSI_ARCH_GUARD_RELOADED="
            "x86_64/amd/zen4 accel/nvidia/cc90->"
            "x86_64/amd/zen4 accel/nvidia/cc120"
        )
        if proc.returncode != 0 or marker not in proc.stdout:
            print("missing accelerator reload marker", file=sys.stderr)
            return 1
        if "export EESSI_ACCEL_SUBDIR=accel/nvidia/cc120" not in proc.stdout:
            print("missing accel subdir", file=sys.stderr)
            return 1
        return 0
    finally:
        site.close()


def missing_module() -> int:
    site, env = _site(False)
    try:
        proc = support.run(env)
        print("case: accelerator-missing-module")
        print("command: python3 -I src/eessi_arch_guard.py")
        support.show(proc, site.root)
        text = proc.stdout
        if proc.returncode != 0:
            return proc.returncode
        if "CUDA/12.6.0" not in text or "environment left as it was" not in text:
            print("missing CUDA failure line", file=sys.stderr)
            return 1
        if "export EESSI_ACCEL_SUBDIR=" in text:
            print("accelerator was updated on a failed reload", file=sys.stderr)
            return 1
        return 0
    finally:
        site.close()


def main() -> int:
    commands = {"mismatch": mismatch, "missing-module": missing_module}
    if len(sys.argv) != 2 or sys.argv[1] not in commands:
        print("usage: accelerator.py mismatch|missing-module", file=sys.stderr)
        return 2
    return commands[sys.argv[1]]()


if __name__ == "__main__":
    sys.exit(main())
