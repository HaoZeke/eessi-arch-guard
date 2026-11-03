#!/usr/bin/python3 -I
"""Stand-in for LMOD_CMD. Speaks `bash --force purge` and `bash load ...`.

The tree under EESSI_PREFIX and eessi_archdetect.sh decide the target, the
same way a reload of the EESSI module does. This is not Lmod.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys

EB_NAME = re.compile(r"[^A-Za-z0-9]")


def log(line: str) -> None:
    path = os.environ.get("EESSI_TEST_EVENT_LOG", "")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def emit(line: str) -> None:
    # Lmod writes the script as bytes. A text stdout raises UnicodeEncodeError
    # when a locale cannot encode an environment byte.
    os.write(1, os.fsencode(line) + b"\n")


def fail(stderr: str, code: int = 1) -> int:
    if os.environ.get("EESSI_TEST_STDERR_INJECT") == "1":
        sys.stderr.write("export PWNED=1\n")
    sys.stderr.write(stderr)
    if not stderr.endswith("\n"):
        sys.stderr.write("\n")
    return code


def arch_cpu() -> tuple:
    script = os.path.join(os.environ["EESSI_INIT_PREFIX"], "eessi_archdetect.sh")
    proc = subprocess.run(
        ["bash", script, "-a", "cpupath"],
        capture_output=True,
        text=True,
        env=os.environ.copy(),
        check=False,
    )
    if proc.returncode != 0:
        return None, proc.stderr.strip() or "cpupath failed"
    prefix = os.environ["EESSI_PREFIX"]
    os_type = os.environ.get("EESSI_OS_TYPE", "linux")
    for candidate in proc.stdout.strip().split(":"):
        if not candidate:
            continue
        directory = os.path.join(prefix, "software", os_type, candidate, "software")
        if os.path.isdir(directory):
            return candidate, ""
    return None, "no software directory"


def arch_accel() -> str:
    override = os.environ.get("EESSI_ACCELERATOR_TARGET_OVERRIDE", "")
    if override:
        return override
    script = os.path.join(os.environ["EESSI_INIT_PREFIX"], "eessi_archdetect.sh")
    proc = subprocess.run(
        ["bash", script, "accelpath"],
        capture_output=True,
        text=True,
        env=os.environ.copy(),
        check=False,
    )
    if proc.returncode != 0:
        return ""
    return proc.stdout.strip()


def module_file(modulepath: str, name: str):
    return os.path.join(modulepath, name + ".lua")


def find_module(name: str):
    for entry in os.environ.get("MODULEPATH", "").split(":"):
        if not entry:
            continue
        path = module_file(entry, name)
        if os.path.isfile(path):
            return path
    return ""


def eb_key(prefix: str, name: str) -> str:
    base = name.split("/", 1)[0]
    return prefix + EB_NAME.sub("", base).upper()


def do_purge() -> int:
    log("argv " + " ".join(sys.argv[1:]))
    log("purge")
    base = os.environ.get("EESSI_TEST_BASE_PATH", "/usr/bin:/bin")
    emit("export LOADEDMODULES=''")
    emit("export _LMFILES_=''")
    emit("export MODULEPATH=''")
    emit("export PATH='%s'" % base)
    emit("export _ModuleTable001_=''")
    for key in (
        "EESSI_VERSION",
        "EESSI_SOFTWARE_SUBDIR",
        "EESSI_ACCEL_SUBDIR",
        "EESSI_ACCELERATOR_TARGET",
    ):
        emit("unset %s" % key)
    for key in list(os.environ):
        if key.startswith("EBROOT") or key.startswith("EBVERSION"):
            emit("unset %s" % key)
    return 0


def accel_modulepath(prefix: str, os_type: str, cpu: str, accel: str) -> str:
    if not accel:
        return ""
    exact = os.path.join(prefix, "software", os_type, cpu, accel, "modules", "all")
    if os.path.isdir(exact):
        return exact
    # Same last-character fallback the EESSI module applies to MODULEPATH.
    fallback = accel[:-1] + "0"
    folded = os.path.join(prefix, "software", os_type, cpu, fallback, "modules", "all")
    if os.path.isdir(folded):
        return folded
    return ""


def do_load_eessi(version_name: str) -> int:
    log("load " + version_name)
    cpu, err = arch_cpu()
    if cpu is None:
        return fail(
            'Lmod has detected the following error: Software directory check failed '
            'while loading "%s"\nPlease check the mount.\n' % version_name
        )
    prefix = os.environ["EESSI_PREFIX"]
    os_type = os.environ.get("EESSI_OS_TYPE", "linux")
    version = version_name.split("/", 1)[1]
    accel = arch_accel()
    cpu_modules = os.path.join(prefix, "software", os_type, cpu, "modules", "all")
    paths = []
    accel_modules = accel_modulepath(prefix, os_type, cpu, accel)
    if accel_modules:
        paths.append(accel_modules)
    paths.append(cpu_modules)
    base = os.environ.get("EESSI_TEST_BASE_PATH", "/usr/bin:/bin")
    cpu_bin = os.path.join(prefix, "software", os_type, cpu, "bin")
    emit("export EESSI_VERSION='%s'" % version)
    emit("export EESSI_PREFIX='%s'" % prefix)
    emit("export EESSI_OS_TYPE='%s'" % os_type)
    emit("export EESSI_INIT_PREFIX='%s'" % os.environ["EESSI_INIT_PREFIX"])
    emit("export EESSI_SOFTWARE_SUBDIR='%s'" % cpu)
    if accel:
        emit("export EESSI_ACCEL_SUBDIR='%s'" % accel)
    else:
        emit("export EESSI_ACCEL_SUBDIR=''")
    emit("export LOADEDMODULES='%s'" % version_name)
    emit("export MODULEPATH='%s'" % ":".join(paths))
    emit("export PATH='%s:%s'" % (cpu_bin, base))
    emit("export _ModuleTable001_='table-for-%s'" % cpu.replace("/", "-"))
    if os.environ.get("EESSI_TEST_INJECT_NEWLINE") == "1":
        emit("export UNSAFE_NEWLINE=$'a\\nexport EVIL=1'")
    sentinel = os.environ.get("EESSI_TEST_SENTINEL", "")
    if sentinel:
        # Single-quoted for bash. The guard's applicator sources this.
        quoted = "'" + sentinel.replace("'", "'\\''") + "'"
        emit("export ROUNDTRIP=%s" % quoted)
    return 0


def do_load(names) -> int:
    log("argv " + " ".join(sys.argv[1:]))
    if not names:
        return fail("Lmod has detected the following error: nothing to load\n")
    if len(names) == 1 and names[0].startswith("EESSI/"):
        return do_load_eessi(names[0])
    loaded = [part for part in os.environ.get("LOADEDMODULES", "").split(":") if part]
    if not any(part.startswith("EESSI/") for part in loaded):
        return fail("Lmod has detected the following error: EESSI is not loaded\n")
    missing = [name for name in names if not find_module(name)]
    if missing:
        shown = " ".join('"%s"' % name for name in missing)
        return fail(
            "Lmod has detected the following error: The following module(s) are unknown: %s\n"
            "\nPlease check the spelling or version number.\n" % shown
        )
    prefix = os.environ["EESSI_PREFIX"]
    os_type = os.environ.get("EESSI_OS_TYPE", "linux")
    cpu = os.environ.get("EESSI_SOFTWARE_SUBDIR", "")
    bins = []
    for name in names:
        soft = os.path.join(
            prefix, "software", os_type, cpu, "software", name.split("/")[0], name.split("/")[1]
        )
        bins.append(os.path.join(soft, "bin"))
        emit("export %s='%s'" % (eb_key("EBROOT", name), soft))
        emit("export %s='%s'" % (eb_key("EBVERSION", name), name.split("/")[1]))
    log("load " + " ".join(names))
    emit("export LOADEDMODULES='%s'" % ":".join(loaded + list(names)))
    emit("export PATH='%s:%s'" % (":".join(bins), os.environ.get("PATH", "")))
    emit("export MODULEPATH='%s'" % os.environ.get("MODULEPATH", ""))
    emit("export EESSI_SOFTWARE_SUBDIR='%s'" % cpu)
    accel = os.environ.get("EESSI_ACCEL_SUBDIR", "")
    emit("export EESSI_ACCEL_SUBDIR='%s'" % accel)
    emit("export _ModuleTable001_='%s'" % os.environ.get("_ModuleTable001_", ""))
    return 0


def main() -> int:
    args = sys.argv[1:]
    if not args or args[0] != "bash":
        return fail("fake lmod: expected the bash shell name\n")
    args = args[1:]
    if args[:2] == ["--force", "purge"]:
        return do_purge()
    if args[:1] == ["purge"]:
        return fail("purge without --force refused\n")
    if args[:1] == ["load"]:
        return do_load(args[1:])
    return fail("fake lmod: unsupported argv %s\n" % " ".join(args))


if __name__ == "__main__":
    sys.exit(main())
