"""Build a fake EESSI tree, archdetect stub, nvidia-smi, and LMOD_CMD."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GUARD = ROOT / "src" / "eessi_arch_guard.py"
STUBS = Path(__file__).resolve().parent / "stubs"
FAKE_LMOD = Path(__file__).resolve().parent / "fake_lmod.py"

CPU_MATCHES = {
    "zen2": "x86_64/amd/zen2:x86_64/generic",
    "zen4": "x86_64/amd/zen4:x86_64/amd/zen3:x86_64/amd/zen2:x86_64/generic",
    "zen5": "x86_64/amd/zen5:x86_64/amd/zen4:x86_64/amd/zen3:x86_64/generic",
    "generic": "x86_64/generic",
}


class Site:
    def __init__(self, directory: str = "") -> None:
        if directory:
            os.makedirs(directory, exist_ok=True)
            self.root = tempfile.mkdtemp(prefix="eessi-arch-guard-", dir=directory)
        else:
            self.root = tempfile.mkdtemp(prefix="eessi-arch-guard-")
        self.prefix = os.path.join(self.root, "prefix")
        self.init = os.path.join(self.prefix, "init")
        self.bindir = os.path.join(self.root, "bin")
        self.log = os.path.join(self.root, "lmod.log")
        self.arch_log = os.path.join(self.root, "arch.log")
        self.cpuinfo = os.path.join(self.root, "cpuinfo")
        self.bash_env_log = os.path.join(self.root, "bash-env.log")
        os.makedirs(self.init)
        os.makedirs(self.bindir)
        shutil.copy(STUBS / "eessi_archdetect.sh", os.path.join(self.init, "eessi_archdetect.sh"))
        os.chmod(os.path.join(self.init, "eessi_archdetect.sh"), 0o755)
        shutil.copy(STUBS / "nvidia-smi", os.path.join(self.bindir, "nvidia-smi"))
        os.chmod(os.path.join(self.bindir, "nvidia-smi"), 0o755)
        wrapper = os.path.join(self.bindir, "lmod")
        with open(wrapper, "w", encoding="utf-8") as handle:
            handle.write("#!/bin/bash\nexec /usr/bin/python3 -I %s \"$@\"\n" % FAKE_LMOD)
        os.chmod(wrapper, 0o755)
        self.lmod = wrapper

    def close(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def add_cpu(self, subdir: str) -> None:
        base = os.path.join(self.prefix, "software", "linux", subdir)
        os.makedirs(os.path.join(base, "software"), exist_ok=True)
        os.makedirs(os.path.join(base, "modules", "all"), exist_ok=True)
        os.makedirs(os.path.join(base, "bin"), exist_ok=True)

    def add_accel(self, cpu: str, accel: str) -> None:
        base = os.path.join(self.prefix, "software", "linux", cpu, accel, "modules", "all")
        os.makedirs(base, exist_ok=True)

    def add_module(self, cpu: str, name: str, accel: str = "") -> None:
        if accel:
            root = os.path.join(self.prefix, "software", "linux", cpu, accel, "modules", "all")
        else:
            root = os.path.join(self.prefix, "software", "linux", cpu, "modules", "all")
        path = os.path.join(root, name + ".lua")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("-- %s\n" % name)
        soft = os.path.join(
            self.prefix,
            "software",
            "linux",
            cpu,
            "software",
            name.split("/")[0],
            name.split("/")[1],
            "bin",
        )
        os.makedirs(soft, exist_ok=True)

    def write_cpuinfo(self, kind: str = "", matches: str = "", fail: bool = False) -> None:
        lines = ["vendor_id : AuthenticAMD", "flags : fpu avx2"]
        if kind == "zen2":
            lines.append("model name : AMD EPYC 7702 64-Core Processor")
        elif kind == "zen4":
            lines.append("model name : AMD EPYC 9654 96-Core Processor")
        elif kind == "zen5":
            lines.append("model name : AMD EPYC 9755 128-Core Processor")
        if matches or kind in CPU_MATCHES:
            lines.append("eessi_arch_matches: %s" % (matches or CPU_MATCHES[kind]))
        if fail:
            lines.append("eessi_arch_fail: 1")
        with open(self.cpuinfo, "w", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")


def base_env(
    site: Site, cpu: str, modules, accel: str = "", gpu: str = "none", gpu_cc: str = "9.0"
):
    names = ["EESSI/2025.06", *modules]
    modulepath = [os.path.join(site.prefix, "software", "linux", cpu, "modules", "all")]
    if accel:
        modulepath.insert(
            0, os.path.join(site.prefix, "software", "linux", cpu, accel, "modules", "all")
        )
    bins = [site.bindir]
    for name in modules:
        soft = os.path.join(
            site.prefix,
            "software",
            "linux",
            cpu,
            "software",
            name.split("/")[0],
            name.split("/")[1],
            "bin",
        )
        bins.append(soft)
    bins.append(os.path.join(site.prefix, "software", "linux", cpu, "bin"))
    bins.extend(["/usr/bin", "/bin"])
    env = {
        "PATH": ":".join(bins),
        "HOME": "/tmp",
        "USER": "tester",
        "LANG": "C",
        "LC_ALL": "C",
        "EESSI_PREFIX": site.prefix,
        "EESSI_OS_TYPE": "linux",
        "EESSI_INIT_PREFIX": site.init,
        "EESSI_VERSION": "2025.06",
        "EESSI_SOFTWARE_SUBDIR": cpu,
        "EESSI_PROC_CPUINFO": site.cpuinfo,
        "LOADEDMODULES": ":".join(names),
        "MODULEPATH": ":".join(modulepath),
        "LMOD_CMD": site.lmod,
        "EESSI_TEST_GPU": gpu,
        "EESSI_TEST_GPU_CC": gpu_cc,
        # Survives a purge the way /usr/bin does, so nvidia-smi is still found
        # when the reloaded EESSI module runs archdetect.
        "EESSI_TEST_BASE_PATH": site.bindir + ":/usr/bin:/bin",
        "EESSI_TEST_EVENT_LOG": site.log,
        "EESSI_TEST_ARCH_LOG": site.arch_log,
        "_ModuleTable001_": "table-for-" + cpu.replace("/", "-"),
    }
    if accel:
        env["EESSI_ACCEL_SUBDIR"] = accel
    for name in modules:
        soft = os.path.join(
            site.prefix,
            "software",
            "linux",
            cpu,
            "software",
            name.split("/")[0],
            name.split("/")[1],
        )
        token = "".join(ch for ch in name.split("/")[0] if ch.isalnum()).upper()
        env["EBROOT" + token] = soft
        env["EBVERSION" + token] = name.split("/")[1]
    return env


def run_guard(env, args=()):
    proc = subprocess.run(
        [sys.executable, "-I", str(GUARD), *args],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    return proc


def log_lines(path: str):
    if not os.path.isfile(path):
        return []
    with open(path, encoding="utf-8") as handle:
        return [line.rstrip("\n") for line in handle if line.strip()]
