"""Shared setup for the documentation examples.

The examples build a temporary prefix with the archdetect stub and the
LMOD_CMD stand-in from the test suite, then run ``src/eessi_arch_guard.py``.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "src"))

import harness  # noqa: E402
import slurm_model  # noqa: E402

GUARD = ROOT / "src" / "eessi_arch_guard.py"
ZEN2 = "x86_64/amd/zen2"
ZEN4 = "x86_64/amd/zen4"
CC90 = "accel/nvidia/cc90"
CC120 = "accel/nvidia/cc120"


def rewrite(text: str, prefix: str) -> str:
    """Replace the temporary prefix and the repository root with stable tokens."""
    if prefix:
        text = text.replace(prefix, "$PREFIX")
    return text.replace(str(ROOT), "$ROOT")


def show(proc, prefix: str) -> None:
    stdout = rewrite(proc.stdout, prefix)
    stderr = rewrite(proc.stderr, prefix)
    print("exit: %s" % proc.returncode)
    print("--- stdout ---")
    sys.stdout.write(stdout if stdout else "(empty)\n")
    if stdout and not stdout.endswith("\n"):
        print()
    print("--- stderr ---")
    sys.stdout.write(stderr if stderr else "(empty)\n")
    if stderr and not stderr.endswith("\n"):
        print()


def apply_stdout(env, stdout: str):
    changes, _printed = slurm_model.apply_task_prolog(stdout)
    updated = dict(env)
    for key, value in changes.items():
        if value is None:
            updated.pop(key, None)
        else:
            updated[key] = value
    return updated


def cpu_site(
    modules=("R/4.4.2",),
    node="zen2",
    loaded=ZEN4,
    both_modules=True,
    directory="",
):
    """Prefix with zen2 and zen4 trees. ``node`` is the cpuinfo kind."""
    site = harness.Site(directory=directory)
    site.add_cpu(ZEN2)
    site.add_cpu(ZEN4)
    if both_modules:
        for cpu in (ZEN2, ZEN4):
            for name in modules:
                site.add_module(cpu, name)
    else:
        for name in modules:
            site.add_module(loaded, name)
    site.write_cpuinfo(node)
    env = harness.base_env(site, loaded, list(modules), gpu="none")
    return site, env


def run(env, args=()):
    return harness.run_guard(env, args)
