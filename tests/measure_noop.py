#!/usr/bin/python3
"""Time a same-target eessi-arch-guard run. Prints the method and the numbers.

This is the cost of one TaskProlog process on this machine, with the test
stub for eessi_archdetect.sh and a nvidia-smi stub that fails (no GPU).
It is not a measurement from a production node.
"""

from __future__ import annotations

import statistics
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import harness  # noqa: E402

ZEN4 = "x86_64/amd/zen4"
WARMUP = 3
N = 50


def percentile_report(samples):
    ordered = sorted(samples)
    return {
        "n": len(ordered),
        "min_ms": ordered[0] * 1e3,
        "median_ms": statistics.median(ordered) * 1e3,
        "max_ms": ordered[-1] * 1e3,
    }


def time_command(argv, env, repeats):
    samples = []
    for _ in range(repeats):
        start = time.perf_counter()
        proc = subprocess.run(argv, env=env, capture_output=True, check=False)
        samples.append(time.perf_counter() - start)
        if proc.returncode != 0:
            raise SystemExit(proc.stderr.decode())
    return samples


def main():
    site = harness.Site()
    try:
        site.add_cpu(ZEN4)
        site.add_module(ZEN4, "R/4.4.2")
        site.write_cpuinfo("zen4")
        env = harness.base_env(site, ZEN4, ["R/4.4.2"], gpu="none")
        argv = [sys.executable, "-I", str(harness.GUARD)]
        time_command(argv, env, WARMUP)
        cold = time_command(argv, env, N)

        env["TMPDIR"] = site.root
        env["SLURM_JOB_ID"] = "1"
        env["SLURM_STEP_ID"] = "batch"
        time_command(argv, env, 1)
        warm = time_command(argv, env, N)

        baseline_argv = [sys.executable, "-I", "-c", "pass"]
        time_command(baseline_argv, {"PATH": "/usr/bin:/bin"}, WARMUP)
        baseline = time_command(baseline_argv, {"PATH": "/usr/bin:/bin"}, N)
    finally:
        site.close()

    uname = subprocess.run(["uname", "-srvm"], capture_output=True, text=True, check=False)
    print("host:", uname.stdout.strip())
    print("python:", sys.version.split()[0], sys.executable)
    print("method: time.perf_counter around subprocess.run of the command below")
    print("command:", " ".join(argv))
    print("case: CPU already matches; accel unset; nvidia-smi stub exits non-zero")
    print("includes: Python startup, one cpupath, one accelpath, no module reload")
    print("cold (no SLURM_JOB_ID, archdetect every time):", percentile_report(cold))
    print("warm (cache hit after one prime, same job id):", percentile_report(warm))
    print("baseline python3 -I -c pass:", percentile_report(baseline))


if __name__ == "__main__":
    main()
