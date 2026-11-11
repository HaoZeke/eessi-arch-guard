#!/usr/bin/python3 -I
"""Reload EESSI modules when a Slurm task lands on another CPU or GPU target.

The TaskProlog target is this file. See DESIGN.md for the design record.
The documentation is under docs/.
Output is either Slurm TaskProlog lines (export, unset, print) or, with
--shell, bash assignments for eval. The process exits 0 unless a chained
site prolog exits non-zero: a non-zero status from this program would kill
the task.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import locale
import os
import re
import secrets
import shlex
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
from typing import Dict, List, Mapping, NamedTuple, Optional, Sequence, Tuple

__version__ = "0.1.1"

# ns-043 cache contract. EESSI_ARCH_GUARD is absent on purpose.
KEY_FIELDS = (
    "EESSI_SOFTWARE_SUBDIR",
    "EESSI_ACCEL_SUBDIR",
    "LOADEDMODULES",
    "MODULEPATH",
    "EESSI_INIT_PREFIX",
    "EESSI_PROC_CPUINFO",
    "PATH",
)

STARTUP_VARS = (
    "BASH_ENV",
    "ENV",
    "SHELLOPTS",
    "BASHOPTS",
    "BASH_XTRACEFD",
    "PS4",
)

# Never publish these through the prolog. TMPDIR is created by slurmstepd
# after the prolog; exporting it would change that directory.
NEVER_EMIT = frozenset(
    {
        "_",
        "SHLVL",
        "PWD",
        "OLDPWD",
        "TMPDIR",
        "BASH_ENV",
        "ENV",
        "SHELLOPTS",
        "BASHOPTS",
        "BASH_XTRACEFD",
        "PS4",
        "BASH_ARGC",
        "BASH_ARGV",
        "BASH_LINENO",
        "BASH_SOURCE",
        "BASH_VERSINFO",
        "BASH_VERSION",
        "DIRSTACK",
        "EPOCHREALTIME",
        "EPOCHSECONDS",
        "FUNCNAME",
        "GROUPS",
        "HOSTNAME",
        "HOSTTYPE",
        "IFS",
        "MACHTYPE",
        "OPTERR",
        "OPTIND",
        "OSTYPE",
        "PIPESTATUS",
        "PPID",
    }
)

# A newline here must not abort an otherwise good restore.
SOFT_DROP = frozenset({"PS1", "PS2", "PS3", "PROMPT_COMMAND"})

_MOD_SEGMENT = r"[A-Za-z0-9_][A-Za-z0-9._+-]*"
MODULE_NAME_RE = re.compile(rf"^{_MOD_SEGMENT}(?:/{_MOD_SEGMENT})+$")
YEAR_MODULE_RE = re.compile(r"^[0-9]{4}$")
EESSI_MODULE_RE = re.compile(rf"^EESSI/{_MOD_SEGMENT}$")
ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
ACCEL_RE = re.compile(r"^accel/(?:nvidia/cc[0-9]+|amd/gfx[0-9a-f]+)$")
_PATH_SEGMENT_RE = re.compile(r"^[A-Za-z0-9_.+-]+$")
_SAFE_COMPONENT_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")

DIAGNOSE_LIMIT = 32
MAX_MODULE_NAME = 256
MAX_ARCH_STDOUT = 65536
# Slurm 17.11 src/slurmd/slurmstepd/task.c `_run_script_and_set_env` reads a
# TaskProlog line with fgets(buf, sizeof(buf) - 1) and `char buf[4096]`.
# fgets stores at most size-1 bytes and size is sizeof(buf)-1, so the line
# is 4094 bytes. Current master does not cap the line. Stay inside the
# 17.11 read so one export cannot continue as a second directive.
PROLOG_LINE_MAX = 4094
# Slurm src/common/read_config.h DEFAULT_UNKILLABLE_TIMEOUT. One deadline
# covers the whole decision.
DEFAULT_TIMEOUT = 60.0
STRICT_FAILURE_EXIT = 78
TIMEOUT_MAX = threading.TIMEOUT_MAX
# flock has no timeout and Python retries EINTR, so the wait is a
# non-blocking loop. 0.05 s still ends a 1 s deadline in one shot, and a
# 192-task step does not spin on the lock.
LOCK_POLL_SECONDS = 0.05
# After SIGKILL, a setsid grandchild can keep the pipe open. 5 s is enough
# for a killed child to drop its fds. _finish_child may wait this long in
# communicate and twice more in wait, so the worst case is
# 3x POST_KILL_WAIT_SECONDS.
POST_KILL_WAIT_SECONDS = 5.0

APPLY_SH = """\
source "$1" >/dev/null || exit 4
exec env -0
"""


def _seconds_label(value: float) -> str:
    if value == int(value):
        return str(int(value))
    return str(value)


HELP = """\
usage: eessi-arch-guard [--shell] [--strict] [--chain PATH] [--timeout SEC] [--restore] [-h|--help] [--version]

Slurm TaskProlog: when the loaded EESSI CPU or accelerator target is not this
node's, print export/unset lines for the reloaded module environment.
--shell prints bash for: eval "$(eessi-arch-guard --shell)"
--strict returns 78 on a failed decision or an unexpected exception.
--chain PATH runs an existing task prolog first. Slurm does not pass arguments,
so a site sets this in a wrapper or in a root-owned eessi-arch-guard.conf.
--timeout SEC bounds the decision. The default is %s seconds.
--restore reads a JSON restore request on stdin and writes the JSON result.
-h, --help print this text. --version prints the version.
""" % _seconds_label(DEFAULT_TIMEOUT)


class Outcome(NamedTuple):
    """Environment delta, optional message, kind, and optional detail.

    ``kind`` is ``skip``, ``noop``, ``reloaded``, or ``failed``.
    """

    changes: Dict[str, Optional[str]]
    message: Optional[str]
    kind: str
    detail: Optional[str] = None


class CpuProbe(NamedTuple):
    """CPU walk result.

    ``status`` is ``ok``, ``none``, or ``error``. ``override`` is the
    installed directory when it differs from the unfiltered archdetect choice.
    """

    status: str  # ok, none, error
    target: str
    candidates: Tuple[str, ...]
    error: str
    # Set when the installed directory archdetect would pick needs a flag
    # this CPU does not have. The reload passes it as
    # EESSI_SOFTWARE_SUBDIR_OVERRIDE so the EESSI module follows the fallback.
    override: str = ""


class AccelProbe(NamedTuple):
    """Accelerator probe. ``status`` is ``ok``, ``none``, or ``error``."""

    status: str  # ok, none, error
    subdir: str
    error: str


class LoadedModules(NamedTuple):
    """The one ``EESSI/<version>`` name, the other loaded names, and an error token."""

    eessi: str
    others: Tuple[str, ...]
    error: str


class RestoreResult(NamedTuple):
    """Helper result: success flag, restored environment, missing modules, error text."""

    ok: bool
    env: Optional[Dict[str, str]]
    missing: Tuple[str, ...]
    error: str


def one_line(text: str, limit: int = 500) -> str:
    """Single line, no controls, bounded. Safe to place after print or on stderr."""
    chars = []
    for char in text:
        if char in "\r\n\0" or ord(char) < 32 or ord(char) == 127:
            chars.append(" ")
        else:
            chars.append(char)
    cleaned = "".join(chars).strip()
    if len(cleaned) > limit:
        return cleaned[: limit - 3] + "..."
    return cleaned


def module_name_ok(name: str) -> bool:
    """True when ``name`` matches the module grammar and is at most 256 characters."""
    return (
        isinstance(name, str)
        and 0 < len(name) <= MAX_MODULE_NAME
        and (MODULE_NAME_RE.fullmatch(name) is not None or YEAR_MODULE_RE.fullmatch(name) is not None)
    )


def safe_rel_subdir(value: str) -> bool:
    """Relative EESSI subdirectory: no empty, absolute, or .. segments."""
    if not value or value.startswith("/") or "\\" in value:
        return False
    parts = value.split("/")
    for part in parts:
        if part in (".", "..") or _PATH_SEGMENT_RE.fullmatch(part) is None:
            return False
    return True


def safe_absolute(path: str) -> bool:
    if not path or not path.startswith("/") or any(c in path for c in "\n\r\0"):
        return False
    return all(part != ".." for part in path.split("/"))


def strip_startup(env: Mapping[str, str]) -> Dict[str, str]:
    child = dict(env)
    for key in STARTUP_VARS:
        child.pop(key, None)
    return child


def value_emit_ok(value: str, multiline: bool) -> bool:
    if "\0" in value:
        return False
    if not multiline and ("\n" in value or "\r" in value):
        return False
    return True


def _never_touch(name: str) -> bool:
    return (
        name in NEVER_EMIT
        or name.startswith("BASH_FUNC_")
        or name.startswith("SLURM_")
    )


def environment_delta(
    old: Mapping[str, str],
    new: Mapping[str, str],
    multiline: bool,
) -> Optional[Dict[str, Optional[str]]]:
    """Changes to publish. None means a value cannot be carried: publish nothing."""
    changes: Dict[str, Optional[str]] = {}
    unsafe = False
    for key in set(old) | set(new):
        if _never_touch(key):
            continue
        old_v = old.get(key)
        new_v = new.get(key)
        if old_v == new_v:
            continue
        if ENV_NAME_RE.fullmatch(key) is None:
            if key in SOFT_DROP:
                continue
            unsafe = True
            continue
        if new_v is None:
            changes[key] = None
            continue
        if not value_emit_ok(new_v, multiline):
            if key in SOFT_DROP:
                continue
            unsafe = True
            continue
        changes[key] = new_v
    if unsafe:
        return None
    return changes


def _fit_encoded(raw: bytes, room: int, encoding: str) -> bytes:
    """Keep a prefix that is valid text and at most `room` bytes."""
    if len(raw) <= room:
        return raw
    cut = raw[:room]
    while cut:
        try:
            cut.decode(encoding)
            return cut
        except UnicodeDecodeError:
            cut = cut[:-1]
    return b""


def message_bytes(text: str) -> bytes:
    """One locale-encoded message, inside the prolog line buffer."""
    cleaned = one_line(text)
    encoding = locale.getpreferredencoding(False) or "ascii"
    try:
        raw = cleaned.encode(encoding, errors="replace")
    except LookupError:
        encoding = "ascii"
        raw = cleaned.encode(encoding, errors="replace")
    room = PROLOG_LINE_MAX - len(b"print ") - 1
    return _fit_encoded(raw, room, encoding)


def write_fd(fd: int, data: bytes) -> None:
    """Write bytes to a descriptor. A failed write is dropped."""
    if not data:
        return
    view = memoryview(data)
    while len(view):
        try:
            written = os.write(fd, view)
        except OSError:
            return
        if written <= 0:
            return
        view = view[written:]


def render_bytes(
    changes: Mapping[str, Optional[str]],
    message: Optional[str],
    shell: bool,
    announce: bool,
) -> bytes:
    """Prolog or shell bytes. Export values keep the bytes they stand for."""
    chunks = []
    for key in sorted(changes):
        if ENV_NAME_RE.fullmatch(key) is None:
            raise ValueError("refusing to emit an unsafe name")
        value = changes[key]
        if value is None:
            chunks.append(b"unset " + key.encode("ascii") + b"\n")
            continue
        if not isinstance(value, str):
            raise ValueError("refusing to emit a non-text value")
        if shell:
            line = "export %s=%s" % (key, shlex.quote(value))
            chunks.append(os.fsencode(line) + b"\n")
        else:
            if not value_emit_ok(value, multiline=False):
                raise ValueError("refusing to emit an unsafe prolog value")
            line = b"export " + key.encode("ascii") + b"=" + os.fsencode(value) + b"\n"
            if len(line) > PROLOG_LINE_MAX:
                raise ValueError("refusing to emit a prolog line past the Slurm 17.11 read")
            chunks.append(line)
    if message and announce and not shell:
        line = b"print " + message_bytes(message) + b"\n"
        if len(line) > PROLOG_LINE_MAX:
            raise ValueError("refusing to emit a prolog line past the Slurm 17.11 read")
        chunks.append(line)
    return b"".join(chunks)


def render(
    changes: Mapping[str, Optional[str]],
    message: Optional[str],
    shell: bool,
    announce: bool,
) -> str:
    """Prolog or shell text. Prolog values are literal; shell values are quoted."""
    lines = []
    for key in sorted(changes):
        if ENV_NAME_RE.fullmatch(key) is None:
            raise ValueError("refusing to emit an unsafe name")
        value = changes[key]
        if value is None:
            lines.append("unset " + key)
            continue
        if not isinstance(value, str):
            raise ValueError("refusing to emit a non-text value")
        if shell:
            lines.append("export %s=%s" % (key, shlex.quote(value)))
        else:
            if not value_emit_ok(value, multiline=False):
                raise ValueError("refusing to emit an unsafe prolog value")
            lines.append("export %s=%s" % (key, value))
    text = ""
    if lines:
        text = "\n".join(lines) + "\n"
    if message and announce and not shell:
        text += "print " + one_line(message) + "\n"
    return text


def parse_loaded_modules(env: Mapping[str, str]) -> LoadedModules:
    raw = env.get("LOADEDMODULES", "")
    names = [part for part in raw.split(":") if part]
    if not names:
        return LoadedModules("", (), "LOADEDMODULES is empty")
    if any(not module_name_ok(name) for name in names):
        return LoadedModules("", (), "unsafe-module-name")
    eessi = [name for name in names if name.startswith("EESSI/")]
    if len(eessi) != 1 or EESSI_MODULE_RE.fullmatch(eessi[0]) is None:
        return LoadedModules("", (), "eessi-module-name")
    others = tuple(name for name in names if name != eessi[0])
    return LoadedModules(eessi[0], others, "")


def parse_cpu_line(stdout: str) -> Optional[List[str]]:
    if "\0" in stdout or len(stdout) > MAX_ARCH_STDOUT:
        return None
    parts = [line for line in stdout.splitlines() if line != ""]
    if len(parts) != 1:
        return None
    line = parts[0]
    if any(ord(char) < 32 for char in line):
        return None
    return line.split(":")


class _Deadline(BaseException):
    """The decision's time limit ran out. Not an Exception, so it is not cached."""


class _Terminated(BaseException):
    """SIGTERM arrived during the decision. Not an Exception, so it is not cached."""


class DecisionLimit:
    """One deadline for the lock, both archdetect runs, and the restore helper."""

    def __init__(self, seconds: float) -> None:
        self.seconds = seconds
        self.deadline = time.monotonic() + seconds
        self.child_pgid: Optional[int] = None
        self.terminated = False
        self.committing = False
        self._previous = None

    def remaining(self) -> float:
        return self.deadline - time.monotonic()

    def check(self) -> None:
        if self.committing:
            return
        if self.terminated:
            raise _Terminated()
        if self.remaining() <= 0:
            raise _Deadline()

    def install(self) -> None:
        self._previous = signal.signal(signal.SIGTERM, self._handle)

    def restore(self) -> None:
        if self._previous is not None:
            signal.signal(signal.SIGTERM, self._previous)
            self._previous = None

    def _handle(self, _signum: int, _frame: object) -> None:
        self.terminated = True
        if self.committing:
            return
        pgid = self.child_pgid
        if not pgid:
            return
        try:
            os.killpg(pgid, signal.SIGKILL)
        except OSError:
            return

    def hold_signals(self) -> None:
        signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGTERM})

    def release_signals(self) -> None:
        signal.pthread_sigmask(signal.SIG_UNBLOCK, {signal.SIGTERM})


_ACTIVE: Optional[DecisionLimit] = None


def _killpg(pgid: int) -> None:
    try:
        os.killpg(pgid, signal.SIGKILL)
    except OSError:
        return


def _finish_child(proc: subprocess.Popen) -> Tuple[bytes, bytes]:
    """Collect output after a kill.

    A setsid grandchild can hold the pipe. This may wait in communicate
    and then twice in wait, so the worst case is 3x POST_KILL_WAIT_SECONDS.
    """
    try:
        return proc.communicate(timeout=POST_KILL_WAIT_SECONDS)
    except subprocess.TimeoutExpired:
        for stream in (proc.stdout, proc.stderr, proc.stdin):
            if stream is None:
                continue
            try:
                stream.close()
            except OSError:
                pass
        if proc.poll() is None:
            _killpg(proc.pid)
            try:
                proc.wait(timeout=POST_KILL_WAIT_SECONDS)
            except subprocess.TimeoutExpired:
                try:
                    proc.kill()
                except OSError:
                    pass
                try:
                    proc.wait(timeout=POST_KILL_WAIT_SECONDS)
                except subprocess.TimeoutExpired:
                    pass
        return b"", b""


def run_captured(
    argv: Sequence[str],
    env: Mapping[str, str],
    input_bytes: Optional[bytes] = None,
    new_session: bool = False,
) -> subprocess.CompletedProcess:
    """Run a command. Under a decision limit, its session is killed at the deadline."""
    limit = _ACTIVE
    if limit is None or not new_session:
        return subprocess.run(
            list(argv),
            input=input_bytes,
            capture_output=True,
            env=dict(env),
            check=False,
        )
    limit.check()
    limit.hold_signals()
    proc = None
    try:
        proc = subprocess.Popen(
            list(argv),
            stdin=subprocess.PIPE if input_bytes is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=dict(env),
            start_new_session=True,
        )
        limit.child_pgid = proc.pid
    finally:
        limit.release_signals()
    if proc is None:
        raise OSError("could not start")
    try:
        if limit.terminated and not limit.committing:
            _killpg(proc.pid)
            _finish_child(proc)
            raise _Terminated()
        remaining = limit.remaining()
        if remaining <= 0:
            _killpg(proc.pid)
            _finish_child(proc)
            raise _Deadline()
        try:
            out, err = proc.communicate(input=input_bytes, timeout=remaining)
        except subprocess.TimeoutExpired:
            _killpg(proc.pid)
            _finish_child(proc)
            raise _Deadline()
        if limit.terminated and not limit.committing:
            raise _Terminated()
        return subprocess.CompletedProcess(list(argv), proc.returncode, out, err)
    finally:
        if limit.child_pgid == proc.pid:
            limit.child_pgid = None
        if proc.poll() is None:
            _killpg(proc.pid)
            _finish_child(proc)


def _run_bash(args: Sequence[str], env: Mapping[str, str]) -> subprocess.CompletedProcess:
    return run_captured(
        ["bash", "--noprofile", "--norc", "--", *args],
        strip_startup(env),
        new_session=_ACTIVE is not None,
    )


# GCC -march names whose PTA mask includes PTA_WAITPKG (tpause, umwait,
# umonitor). Leaves use '_' where the march spells '-'. Taken from GCC 16
# gcc/config/i386/i386.h (branch releases/gcc-16): the set GCC 15.2.0
# carried plus novalake; znver6, GCC 16's other new mask, has no WAITPKG.
# archdetect's x86 spec does not require the flag, so a VM that hides it
# is still offered that directory. A binary built with the march then
# SIGILLs.
WAITPKG_MARCHES = frozenset(
    (
        "alderlake",
        "arrowlake",
        "arrowlake_s",
        "clearwaterforest",
        "diamondrapids",
        "grandridge",
        "graniterapids",
        "graniterapids_d",
        "novalake",
        "pantherlake",
        "sapphirerapids",
        "sierraforest",
        "tremont",
    )
)

_CPUINFO_FLAGS_RE = re.compile(r"(?im)^flags[ \t]*:[ \t]*(.*)$")
_CPUINFO_READ_MAX = 16 * 1024 * 1024


def read_cpu_flags(path: str) -> frozenset:
    """Intersection of every flags line. One CPU without the flag rejects it."""
    if not path:
        return frozenset()
    try:
        with open(path, "rb") as handle:
            blob = handle.read(_CPUINFO_READ_MAX + 1)
    except OSError:
        return frozenset()
    text = blob[:_CPUINFO_READ_MAX].decode("utf-8", "replace")
    found = []
    for match in _CPUINFO_FLAGS_RE.finditer(text):
        found.append(frozenset(match.group(1).split()))
    if not found:
        return frozenset()
    common = found[0]
    for item in found[1:]:
        common &= item
    return common


def march_of(candidate: str) -> str:
    leaf = candidate.rsplit("/", 1)[-1]
    return leaf.replace("-", "_")


def candidate_supports_cpu(candidate: str, flags: frozenset) -> bool:
    """False when this march implies waitpkg and the CPU flags omit it."""
    if march_of(candidate) in WAITPKG_MARCHES:
        return "waitpkg" in flags
    return True


def probe_cpu(env: Mapping[str, str]) -> CpuProbe:
    """Select the first installed cpupath candidate that satisfies the waitpkg rule."""
    prefix = env.get("EESSI_PREFIX", "")
    os_type = env.get("EESSI_OS_TYPE", "")
    init = env.get("EESSI_INIT_PREFIX", "")
    if (
        not safe_absolute(prefix)
        or not safe_rel_subdir(os_type)
        or not safe_absolute(init)
    ):
        return CpuProbe(
            "error",
            "",
            (),
            "EESSI_PREFIX, EESSI_OS_TYPE, or EESSI_INIT_PREFIX is missing or unsafe",
        )
    script = os.path.join(init, "eessi_archdetect.sh")
    if not os.path.isfile(script):
        return CpuProbe("error", "", (), "eessi_archdetect.sh is not available")
    try:
        proc = _run_bash([script, "-a", "cpupath"], env)
    except OSError as exc:
        return CpuProbe("error", "", (), "could not run archdetect: %s" % exc)
    if proc.returncode != 0:
        err = proc.stderr.decode("utf-8", "replace").splitlines()
        detail = err[0] if err else "exit %s" % proc.returncode
        return CpuProbe("error", "", (), one_line(detail, 200))
    stdout = proc.stdout.decode("utf-8", "replace")
    parsed = parse_cpu_line(stdout)
    if parsed is None:
        return CpuProbe("error", "", (), "cpupath output is not a single line")
    candidates = [item for item in parsed if item]
    if not candidates:
        return CpuProbe("error", "", (), "cpupath returned no candidates")
    for candidate in candidates:
        if not safe_rel_subdir(candidate):
            return CpuProbe("error", "", tuple(candidates), "cpupath returned an unsafe candidate")
    flags = read_cpu_flags(env.get("EESSI_PROC_CPUINFO") or "/proc/cpuinfo")
    compatible = [item for item in candidates if candidate_supports_cpu(item, flags)]

    def installed(items: Sequence[str]) -> str:
        for item in items:
            directory = os.path.join(prefix, "software", os_type, item, "software")
            if os.path.isdir(directory):
                return item
        return ""

    chosen = installed(compatible)
    # archdetect walks the unfiltered list. When that directory needs a
    # missing flag, pin the fallback so the reloaded module does not
    # select the rejected one again.
    raw = installed(candidates)
    override = chosen if chosen and chosen != raw else ""
    if chosen:
        return CpuProbe("ok", chosen, tuple(compatible), "", override)
    return CpuProbe("none", "", tuple(compatible), "")


def probe_accel(env: Mapping[str, str]) -> AccelProbe:
    """Return accelpath stdout, or status ``none`` when the script reports no GPU."""
    init = env.get("EESSI_INIT_PREFIX", "")
    if not safe_absolute(init):
        return AccelProbe("error", "", "EESSI_INIT_PREFIX is missing or unsafe")
    script = os.path.join(init, "eessi_archdetect.sh")
    if not os.path.isfile(script):
        return AccelProbe("error", "", "eessi_archdetect.sh is not available")
    try:
        proc = _run_bash([script, "accelpath"], env)
    except OSError as exc:
        return AccelProbe("error", "", "could not run archdetect: %s" % exc)
    # Exit 2 (no nvidia-smi) and exit 3 (nvidia-smi failed) are the script's
    # no-GPU paths. The EESSI wrapper stores that as an empty subdirectory.
    if proc.returncode != 0:
        return AccelProbe("none", "", "")
    text = proc.stdout.decode("utf-8", "replace").strip()
    if text == "":
        return AccelProbe("none", "", "")
    if "\n" in text or "\r" in text or ACCEL_RE.fullmatch(text) is None:
        return AccelProbe("error", "", "accelpath output is not an accelerator subdirectory")
    return AccelProbe("ok", text, "")


def lmod_invocable(path: str) -> bool:
    if not path or path.startswith("-") or any(c in path for c in "\n\r\0"):
        return False
    return os.path.isfile(path) and os.access(path, os.X_OK)


def _write_private(path: str, data: bytes) -> None:
    fd = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
        0o600,
    )
    try:
        os.write(fd, data)
    finally:
        os.close(fd)


def _parse_env0(blob: bytes) -> Dict[str, str]:
    parsed: Dict[str, str] = {}
    for part in blob.split(b"\0"):
        if b"=" not in part:
            continue
        key, value = part.split(b"=", 1)
        parsed[key.decode("utf-8", "surrogateescape")] = value.decode(
            "utf-8", "surrogateescape"
        )
    return parsed


def source_exports(
    module_env: Mapping[str, str], script: bytes
) -> Tuple[Optional[Dict[str, str]], str]:
    base = os.environ.get("EESSI_ARCH_GUARD_WORKDIR", "/tmp")
    if not (safe_absolute(base) and os.path.isdir(base)):
        base = "/tmp"
    tmp = tempfile.mkdtemp(prefix="eessi-arch-guard-", dir=base)
    try:
        os.chmod(tmp, 0o700)
        fragment = os.path.join(tmp, "fragment.sh")
        apply_sh = os.path.join(tmp, "apply.sh")
        _write_private(fragment, script)
        _write_private(apply_sh, APPLY_SH.encode("ascii"))
        os.chmod(apply_sh, 0o700)
        proc = subprocess.run(
            ["bash", "--noprofile", "--norc", "--", apply_sh, fragment],
            capture_output=True,
            env=strip_startup(module_env),
            check=False,
        )
        if proc.returncode != 0:
            err = proc.stderr.decode("utf-8", "replace")
            return None, err or "apply exited %s" % proc.returncode
        return _parse_env0(proc.stdout), ""
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def run_lmod(
    module_env: Mapping[str, str], lmod_cmd: str, args: Sequence[str]
) -> subprocess.CompletedProcess:
    return subprocess.run(
        [lmod_cmd, "bash", *args],
        capture_output=True,
        env=strip_startup(module_env),
        check=False,
    )


def apply_lmod(
    module_env: Mapping[str, str], lmod_cmd: str, args: Sequence[str]
) -> Tuple[Optional[Dict[str, str]], str]:
    proc = run_lmod(module_env, lmod_cmd, args)
    if proc.returncode != 0:
        return None, proc.stderr.decode("utf-8", "replace")
    return source_exports(module_env, proc.stdout)


def _diagnose(
    original: Mapping[str, str],
    lmod_cmd: str,
    eessi: str,
    modules: Sequence[str],
) -> List[str]:
    missing = []
    for name in list(modules)[:DIAGNOSE_LIMIT]:
        current, _err = apply_lmod(original, lmod_cmd, ["--force", "purge"])
        if current is None:
            missing.append(name)
            continue
        current, _err = apply_lmod(current, lmod_cmd, ["load", eessi])
        if current is None:
            missing.append(name)
            continue
        current, _err = apply_lmod(current, lmod_cmd, ["load", name])
        if current is None:
            missing.append(name)
    return missing


def to_wire(value: str) -> str:
    """One character per byte this process would pass to an exec."""
    return os.fsencode(value).decode("latin-1")


def from_wire(value: str) -> str:
    """Inverse of to_wire in this process."""
    return os.fsdecode(value.encode("latin-1"))


def _wire_map(mapping: Mapping[str, str]) -> Dict[str, str]:
    return {to_wire(str(key)): to_wire(str(value)) for key, value in mapping.items()}


def do_restore(payload: Mapping[str, object]) -> Dict[str, object]:
    """Purge and reload from a JSON request. The return value is the JSON object."""
    env = payload.get("env")
    eessi = payload.get("eessi")
    modules = payload.get("modules")
    lmod_cmd = payload.get("lmod")
    if not isinstance(env, dict) or not isinstance(eessi, str) or not isinstance(modules, list):
        return {"ok": False, "missing": [], "error": "restore request is malformed"}
    if not isinstance(lmod_cmd, str):
        return {"ok": False, "missing": [], "error": "restore request is malformed"}
    try:
        eessi = from_wire(eessi)
        lmod_cmd = from_wire(lmod_cmd)
        modules = [from_wire(name) if isinstance(name, str) else name for name in modules]
        module_env = {
            from_wire(str(key)): from_wire(str(value)) for key, value in env.items()
        }
    except UnicodeError:
        return {"ok": False, "missing": [], "error": "restore request is malformed"}
    if not lmod_invocable(lmod_cmd):
        return {"ok": False, "missing": [], "error": "LMOD_CMD is not executable"}
    if not module_name_ok(eessi) or EESSI_MODULE_RE.fullmatch(eessi) is None:
        return {"ok": False, "missing": [], "error": "unsafe-module-name"}
    if any(not isinstance(name, str) or not module_name_ok(name) for name in modules):
        return {"ok": False, "missing": [], "error": "unsafe-module-name"}
    current, err = apply_lmod(module_env, lmod_cmd, ["--force", "purge"])
    if current is None:
        return {"ok": False, "missing": [], "error": one_line(err or "purge failed", 300)}
    current, err = apply_lmod(current, lmod_cmd, ["load", eessi])
    if current is None:
        return {
            "ok": False,
            "missing": [eessi],
            "error": one_line(err or "EESSI load failed", 300),
        }
    if modules:
        nxt, err = apply_lmod(current, lmod_cmd, ["load", *modules])
        if nxt is None:
            missing = _diagnose(module_env, lmod_cmd, eessi, modules)
            return {
                "ok": False,
                "missing": missing,
                "error": one_line(err or "module load failed", 300),
            }
        current = nxt
    return {"ok": True, "env": _wire_map(current), "missing": [], "error": ""}


def restore_main() -> int:
    try:
        payload = json.loads(sys.stdin.buffer.read().decode("utf-8"))
        sys.stdout.write(json.dumps(do_restore(payload)))
        return 0
    except Exception as exc:
        sys.stderr.write("eessi-arch-guard: restore failed: %s\n" % one_line(str(exc), 300))
        return 1


def call_restore(
    env: Mapping[str, str], eessi: str, modules: Sequence[str]
) -> RestoreResult:
    payload = {
        "v": 1,
        "env": _wire_map(env),
        "eessi": to_wire(eessi),
        "modules": [to_wire(name) for name in modules],
        "lmod": to_wire(env.get("LMOD_CMD", "")),
    }
    work = tempfile.mkdtemp(prefix="eessi-arch-guard-", dir="/tmp")
    clean = {
        "PATH": "/usr/bin:/bin",
        "LC_ALL": "C",
        "LANG": "C",
        "HOME": "/tmp",
        "TMPDIR": "/tmp",
        "EESSI_ARCH_GUARD_WORKDIR": work,
    }
    try:
        try:
            proc = run_captured(
                [sys.executable, "-I", "-X", "utf8", os.path.abspath(__file__), "--restore"],
                clean,
                input_bytes=json.dumps(payload).encode("utf-8"),
                new_session=_ACTIVE is not None,
            )
        except OSError as exc:
            return RestoreResult(False, None, (), "could not start restore helper: %s" % exc)
        if proc.returncode != 0 or not proc.stdout:
            err = proc.stderr.decode("utf-8", "replace")
            return RestoreResult(False, None, (), one_line(err or "restore helper failed", 300))
        try:
            data = json.loads(proc.stdout.decode("utf-8"))
        except json.JSONDecodeError:
            return RestoreResult(False, None, (), "restore helper returned invalid output")
        missing_raw = data.get("missing") or []
        missing = tuple(
            name for name in missing_raw if isinstance(name, str) and module_name_ok(name)
        )
        if not data.get("ok"):
            return RestoreResult(False, None, missing, one_line(str(data.get("error") or ""), 300))
        restored = data.get("env")
        if not isinstance(restored, dict):
            return RestoreResult(False, None, missing, "restore helper returned no environment")
        try:
            cleaned = {
                from_wire(str(key)): from_wire(str(value)) for key, value in restored.items()
            }
        except UnicodeError:
            return RestoreResult(False, None, missing, "restore helper returned no environment")
        return RestoreResult(True, cleaned, missing, "")
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _side(text: str) -> str:
    if not text or any(char in text for char in "\n\r\0"):
        return "unrecognized"
    return text


def _failed(was: str, now: str, message: str, detail: Optional[str] = None) -> Outcome:
    token = "%s->%s" % (_side(was), _side(now))
    return Outcome(
        {"EESSI_ARCH_GUARD_FAILED": token},
        one_line(message),
        "failed",
        one_line(detail) if detail else None,
    )


def _restore_mismatch(
    new: Mapping[str, str],
    now: str,
    now_accel: str,
    compare_accel: bool,
    eessi: str,
    others: Sequence[str],
) -> str:
    if new.get("EESSI_SOFTWARE_SUBDIR", "") != now:
        return "reload did not select this node's CPU target"
    if compare_accel and new.get("EESSI_ACCEL_SUBDIR", "") != now_accel:
        return "reload did not select this node's accelerator target"
    loaded = [part for part in new.get("LOADEDMODULES", "").split(":") if part]
    if eessi not in loaded:
        return "reload did not load the EESSI module"
    for name in others:
        if name not in loaded:
            return "reload did not load %s" % name
    return ""


def decide(
    env: Mapping[str, str],
    shell: bool,
    cpu_probe=probe_cpu,
    accel_probe=probe_accel,
    restore=call_restore,
) -> Outcome:
    """Compare the loaded target with this node and build the environment delta."""
    if _ACTIVE is not None:
        _ACTIVE.check()
    if env.get("EESSI_ARCH_GUARD") == "0":
        return Outcome({}, None, "skip", "EESSI_ARCH_GUARD=0")
    was = env.get("EESSI_SOFTWARE_SUBDIR", "")
    if not was:
        return Outcome({}, None, "skip", "EESSI is not loaded")
    if env.get("EESSI_SOFTWARE_SUBDIR_OVERRIDE"):
        return Outcome({}, None, "skip", "EESSI_SOFTWARE_SUBDIR_OVERRIDE is set")
    if env.get("EESSI_ARCHDETECT_OPTIONS_OVERRIDE"):
        return Outcome({}, None, "skip", "EESSI_ARCHDETECT_OPTIONS_OVERRIDE is set")

    cpu = cpu_probe(env)
    if cpu.status == "error":
        return _failed(
            was,
            "archdetect-failed",
            "eessi-arch-guard: archdetect failed (%s); environment left as it was" % cpu.error,
        )
    if cpu.status == "none":
        if env.get("EESSI_ARCH_GUARD_MISSING") == "ignore":
            return Outcome({}, None, "skip", "no EESSI software directory (ignored)")
        shown = " ".join(cpu.candidates) or "(none)"
        return _failed(
            was,
            "no-software-dir",
            "eessi-arch-guard: no EESSI software directory for this node "
            "under %s (candidates %s); modules were loaded for %s; "
            "environment left as it was" % (env.get("EESSI_PREFIX", ""), shown, was),
        )

    compare_accel = not env.get("EESSI_ACCELERATOR_TARGET_OVERRIDE")
    was_accel = env.get("EESSI_ACCEL_SUBDIR", "")
    if compare_accel:
        accel = accel_probe(env)
        if accel.status == "error":
            return _failed(
                was,
                "archdetect-failed",
                "eessi-arch-guard: archdetect failed (%s); environment left as it was"
                % accel.error,
            )
        now_accel = accel.subdir
    else:
        now_accel = was_accel

    now = cpu.target
    if now == was and now_accel == was_accel:
        return Outcome({}, None, "noop", "target already matches this node")

    if compare_accel and was_accel != now_accel:
        was_label = "%s %s" % (was, was_accel or "no accel")
        now_label = "%s %s" % (now, now_accel or "no accel")
    else:
        was_label, now_label = was, now

    loaded = parse_loaded_modules(env)
    if loaded.error:
        why = (
            "a loaded module name is not safe to pass to Lmod"
            if loaded.error == "unsafe-module-name"
            else "the loaded module list has no single EESSI/<version> entry"
        )
        return _failed(
            was_label,
            "unsafe-module-name" if loaded.error == "unsafe-module-name" else "restore-failed",
            "eessi-arch-guard: %s; environment left as it was" % why,
        )
    if not lmod_invocable(env.get("LMOD_CMD", "")):
        return _failed(
            was_label,
            "lmod-unavailable",
            "eessi-arch-guard: LMOD_CMD is missing or not executable; "
            "this node is %s, the modules were loaded for %s; environment left as it was"
            % (now_label, was_label),
        )

    module_env: Mapping[str, str] = env
    if cpu.override:
        pinned = dict(env)
        # A login-node EESSI_ARCHDETECT_OPTIONS would outrank the pin inside
        # the EESSI module. Drop it for the reload. The module's archdetect
        # wrapper then exports the single overridden subdirectory.
        pinned.pop("EESSI_ARCHDETECT_OPTIONS", None)
        pinned["EESSI_SOFTWARE_SUBDIR_OVERRIDE"] = cpu.override
        module_env = pinned
    try:
        result = restore(module_env, loaded.eessi, loaded.others)
    except Exception as exc:
        return _failed(
            was_label,
            "restore-failed",
            "eessi-arch-guard: restore failed (%s); environment left as it was"
            % one_line(str(exc), 200),
        )
    if not result.ok or result.env is None:
        if result.missing:
            names = " ".join(result.missing)
            if len(loaded.others) > DIAGNOSE_LIMIT:
                names += " (stopped after %s)" % DIAGNOSE_LIMIT
            why = "not available for this target: " + names
        else:
            why = "module load failed"
        return _failed(
            was_label,
            now_label,
            "eessi-arch-guard: this node is %s, the modules were loaded for %s; "
            "%s; environment left as it was" % (now_label, was_label, why),
            result.error,
        )

    mismatch = _restore_mismatch(
        result.env, now, now_accel, compare_accel, loaded.eessi, loaded.others
    )
    if mismatch:
        return _failed(
            was_label,
            "restore-failed",
            "eessi-arch-guard: %s; environment left as it was" % mismatch,
        )

    delta = environment_delta(env, result.env, multiline=shell)
    if delta is None:
        return _failed(
            was_label,
            "unsafe-environment",
            "eessi-arch-guard: reload produced a value this output mode cannot carry; "
            "environment left as it was",
        )
    delta["EESSI_ARCH_GUARD_RELOADED"] = "%s->%s" % (_side(was_label), _side(now_label))
    # A successful reload must not leave a previous failure flag set.
    if "EESSI_ARCH_GUARD_FAILED" in env and "EESSI_ARCH_GUARD_FAILED" not in delta:
        delta["EESSI_ARCH_GUARD_FAILED"] = None
    message = "eessi-arch-guard: reloaded modules for %s (loaded for %s)" % (
        now_label,
        was_label,
    )
    if not shell:
        try:
            render_bytes(delta, message, False, True)
        except ValueError:
            return _failed(
                was_label,
                "unsafe-environment",
                "eessi-arch-guard: reload produced a prolog line past the "
                "Slurm 17.11 read; environment left as it was",
            )
    return Outcome(delta, message, "reloaded", None)


def cache_digest(env: Mapping[str, str]) -> str:
    inputs = "\0".join(env.get(key, "") for key in KEY_FIELDS)
    return hashlib.sha256(inputs.encode("utf-8", "surrogateescape")).hexdigest()[:16]


def _safe_component(text: str) -> str:
    if _SAFE_COMPONENT_RE.fullmatch(text or ""):
        return text
    return hashlib.sha256((text or "").encode("utf-8", "surrogateescape")).hexdigest()[:16]


def cache_directory(env: Mapping[str, str]) -> Optional[str]:
    tmp = env.get("TMPDIR", "")
    if tmp and safe_absolute(tmp) and os.path.isdir(tmp):
        return tmp
    if os.path.isdir("/tmp"):
        return "/tmp"
    return None


def cache_file_path(env: Mapping[str, str]) -> Optional[str]:
    """Cache path for this uid, node, job, and step. None when the job id is unset."""
    job = env.get("SLURM_JOB_ID", "")
    if not job:
        return None
    directory = cache_directory(env)
    if directory is None:
        return None
    step = env.get("SLURM_STEP_ID", "batch")
    name = ".eessi-arch-guard.%s.%s.%s.%s.%s" % (
        os.getuid(),
        _safe_component(os.uname().nodename),
        _safe_component(job),
        _safe_component(step),
        cache_digest(env),
    )
    return os.path.join(directory, name)


def _cache_changes_ok(changes: object, shell: bool) -> bool:
    if not isinstance(changes, dict):
        return False
    for key, value in changes.items():
        if not isinstance(key, str) or ENV_NAME_RE.fullmatch(key) is None:
            return False
        if value is None:
            continue
        if not isinstance(value, str) or not value_emit_ok(value, multiline=shell):
            return False
    return True


def _read_cache(directory: str, name: str, shell: bool) -> Optional[Outcome]:
    dirfd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        try:
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=dirfd)
        except OSError:
            return None
        try:
            blob = os.read(fd, 8 * 1024 * 1024)
        finally:
            os.close(fd)
    finally:
        os.close(dirfd)
    try:
        data = json.loads(blob.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict) or data.get("v") != 1:
        return None
    message = data.get("message")
    detail = data.get("detail")
    bad_message = message is not None and (
        not isinstance(message, str) or any(c in message for c in "\n\r\0")
    )
    if bad_message:
        return None
    if detail is not None and not isinstance(detail, str):
        return None
    if not _cache_changes_ok(data.get("changes"), shell):
        return None
    kind = data.get("kind")
    if kind not in ("skip", "noop", "reloaded", "failed"):
        return None
    return Outcome(dict(data["changes"]), message, kind, detail)


def _discard_file(path: str) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass


def _write_cache(path: str, outcome: Outcome) -> None:
    directory, name = os.path.split(path)
    payload = {
        "v": 1,
        "kind": outcome.kind,
        "message": outcome.message,
        "detail": outcome.detail,
        "changes": outcome.changes,
    }
    data = json.dumps(payload, ensure_ascii=True).encode("ascii") + b"\n"
    tmp = ".%s.%s.%s.tmp" % (name, os.getpid(), secrets.token_hex(6))
    tmp_path = os.path.join(directory, tmp)
    dirfd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    fd = -1
    try:
        try:
            fd = os.open(
                tmp,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                0o600,
                dir_fd=dirfd,
            )
            os.write(fd, data)
            os.fchmod(fd, 0o600)
            os.close(fd)
            fd = -1
            os.replace(tmp_path, path)
        except OSError:
            _discard_file(tmp_path)
            return
        except BaseException:
            _discard_file(tmp_path)
            raise
    finally:
        if fd >= 0:
            os.close(fd)
        os.close(dirfd)


def _lock_exclusive(lockfd: int) -> None:
    limit = _ACTIVE
    if limit is None:
        fcntl.flock(lockfd, fcntl.LOCK_EX)
        return
    while True:
        limit.check()
        try:
            fcntl.flock(lockfd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        except BlockingIOError:
            pause = min(LOCK_POLL_SECONDS, max(limit.remaining(), 0.0))
            time.sleep(pause)


def cached_decide(
    env: Mapping[str, str],
    shell: bool,
    cpu_probe=probe_cpu,
    accel_probe=probe_accel,
    restore=call_restore,
) -> Outcome:
    path = cache_file_path(env)
    if path is None or not hasattr(os, "O_NOFOLLOW"):
        return decide(env, shell, cpu_probe, accel_probe, restore)
    directory, name = os.path.split(path)
    try:
        dirfd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    except OSError:
        return decide(env, shell, cpu_probe, accel_probe, restore)
    try:
        try:
            lockfd = os.open(
                name + ".lock",
                os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC,
                0o600,
                dir_fd=dirfd,
            )
        except OSError:
            return decide(env, shell, cpu_probe, accel_probe, restore)
        try:
            os.fchmod(lockfd, 0o600)
            _lock_exclusive(lockfd)
            loaded = _read_cache(directory, name, shell)
            if loaded is not None:
                return loaded
            if _ACTIVE is not None:
                _ACTIVE.check()
            outcome = decide(env, shell, cpu_probe, accel_probe, restore)
            if _ACTIVE is not None:
                _ACTIVE.committing = True
            try:
                _write_cache(path, outcome)
            finally:
                if _ACTIVE is not None:
                    _ACTIVE.committing = False
            return outcome
        finally:
            os.close(lockfd)
    finally:
        os.close(dirfd)


def _limit_outcome(env: Mapping[str, str], token: str, message: str) -> Outcome:
    return _failed(env.get("EESSI_SOFTWARE_SUBDIR", ""), token, message)


def limited_decide(
    env: Mapping[str, str],
    shell: bool,
    timeout: float = DEFAULT_TIMEOUT,
    cpu_probe=probe_cpu,
    accel_probe=probe_accel,
    restore=call_restore,
) -> Outcome:
    """Decide under one deadline. A timeout or SIGTERM is not written to the cache."""
    global _ACTIVE
    if _ACTIVE is not None:
        return cached_decide(env, shell, cpu_probe, accel_probe, restore)
    limit = DecisionLimit(timeout)
    _ACTIVE = limit
    limit.install()
    try:
        try:
            return cached_decide(env, shell, cpu_probe, accel_probe, restore)
        except _Deadline:
            path = cache_file_path(env)
            if path is not None:
                directory, name = os.path.split(path)
                loaded = _read_cache(directory, name, shell)
                if loaded is not None:
                    return loaded
            return _limit_outcome(
                env,
                "timeout",
                "eessi-arch-guard: decision exceeded the time limit; "
                "environment left as it was",
            )
        except _Terminated:
            return _limit_outcome(
                env,
                "terminated",
                "eessi-arch-guard: decision was terminated; environment left as it was",
            )
    finally:
        limit.restore()
        _ACTIVE = None


def parse_timeout(text: str) -> Optional[float]:
    """A positive number of seconds at most TIMEOUT_MAX, or None."""
    try:
        value = float(text)
    except (TypeError, ValueError):
        return None
    if value != value or value <= 0 or value > TIMEOUT_MAX:
        return None
    return value


def resolve_timeout(
    cli_text: Optional[str], settings: Optional[Mapping[str, str]]
) -> Tuple[float, Optional[str]]:
    """CLI overrides the config key, which overrides DEFAULT_TIMEOUT."""
    chosen = DEFAULT_TIMEOUT
    warnings = []
    if settings and "timeout" in settings:
        parsed = parse_timeout(settings["timeout"])
        if parsed is None:
            warnings.append(
                "eessi-arch-guard: config timeout is not a positive number of seconds "
                "at most %s" % TIMEOUT_MAX
            )
        else:
            chosen = parsed
    if cli_text is not None:
        parsed = parse_timeout(cli_text)
        if parsed is None:
            warnings.append(
                "eessi-arch-guard: --timeout is not a positive number of seconds at most %s"
                % TIMEOUT_MAX
            )
        else:
            chosen = parsed
    if not warnings:
        return chosen, None
    return chosen, " ".join(warnings)


def _config_untrusted(path: str) -> Optional[str]:
    try:
        info = os.lstat(path)
    except OSError as exc:
        return "%s (%s)" % (path, exc)
    if stat.S_ISLNK(info.st_mode):
        return "%s is a symlink and is ignored" % path
    if not stat.S_ISREG(info.st_mode):
        return "%s is not a regular file and is ignored" % path
    if info.st_uid != 0:
        return "%s is not owned by root and is ignored" % path
    if info.st_mode & 0o022:
        return "%s is writable by group or others and is ignored" % path
    return None


def parse_config(text: str) -> Dict[str, str]:
    settings, _bad = parse_config_bytes(text.encode("utf-8"))
    return settings


def parse_config_bytes(blob: bytes) -> Tuple[Dict[str, str], Tuple[str, ...]]:
    """Settings from raw config bytes. A bad value does not drop other keys."""
    settings: Dict[str, str] = {}
    bad: List[str] = []
    for raw in blob.splitlines():
        line = raw.strip()
        if not line or line.startswith(b"#") or b"=" not in line:
            continue
        key_b, value_b = line.split(b"=", 1)
        key_b = key_b.strip()
        value_b = value_b.strip()
        try:
            key = key_b.decode("utf-8")
        except UnicodeDecodeError:
            continue
        if not key:
            continue
        try:
            value = value_b.decode("utf-8")
        except UnicodeDecodeError:
            value = value_b.decode("utf-8", "surrogateescape")
            bad.append(key)
        settings[key] = value
    return settings, tuple(bad)


def parse_chain_config(text: str) -> Optional[str]:
    return config_chain(parse_config(text))


def config_chain(settings: Optional[Mapping[str, str]]) -> Optional[str]:
    if not settings:
        return None
    return settings.get("chain") or None


def _chain_path_ok(path: str) -> bool:
    if not path or not path.startswith("/") or any(c in path for c in "\n\r\0"):
        return False
    return os.path.isfile(path) and os.access(path, os.X_OK)


def site_config(script_path: str) -> Tuple[Optional[Dict[str, str]], Optional[str]]:
    """Return (settings, warning). The first existing candidate wins."""
    beside = os.path.join(os.path.dirname(os.path.abspath(script_path)), "eessi-arch-guard.conf")
    for path in ("/etc/eessi-arch-guard.conf", beside):
        if not os.path.exists(path):
            continue
        reason = _config_untrusted(path)
        if reason:
            return None, "eessi-arch-guard: config ignored: " + reason
        try:
            with open(path, "rb") as handle:
                blob = handle.read()
        except OSError as exc:
            return None, "eessi-arch-guard: config ignored: %s" % exc
        settings, bad = parse_config_bytes(blob)
        if bad:
            shown = " ".join(bad)
            return settings, (
                "eessi-arch-guard: config value kept with its original bytes: %s" % shown
            )
        return settings, None
    return None, None


def configured_chain(script_path: str) -> Tuple[Optional[str], Optional[str]]:
    """Return (chain, warning). A user-owned config is not a chain source."""
    settings, warning = site_config(script_path)
    if settings is None:
        return None, warning
    chain = config_chain(settings)
    if chain and _chain_path_ok(chain):
        return chain, warning
    if chain:
        text = "eessi-arch-guard: config chain is not an absolute executable: %s" % one_line(
            chain, 200
        )
        if warning:
            text = warning + " " + text
        return None, text
    return None, warning


def run_chain(path: str, env: Mapping[str, str]) -> Tuple[int, bytes, bytes]:
    proc = subprocess.run(
        [path],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=dict(env),
        check=False,
    )
    code = proc.returncode if proc.returncode >= 0 else 1
    return code, proc.stdout, proc.stderr


class Args(NamedTuple):
    """Parsed output mode, helper mode, configuration and failure policy."""

    shell: bool
    restore: bool
    help: bool
    version: bool
    chain: Optional[str]
    timeout: Optional[str]
    strict: bool = False


def parse_args(argv: Sequence[str]) -> Args:
    """Parse the prolog argv. Flags other than the documented ones are skipped."""
    shell = restore = help_ = version = strict = False
    chain = None
    timeout = None
    index = 0
    while index < len(argv):
        arg = argv[index]
        if arg == "--shell":
            shell = True
        elif arg == "--strict":
            strict = True
        elif arg == "--restore":
            restore = True
        elif arg in ("-h", "--help"):
            help_ = True
        elif arg == "--version":
            version = True
        elif arg == "--chain":
            index += 1
            chain = argv[index] if index < len(argv) else ""
        elif arg.startswith("--chain="):
            chain = arg.split("=", 1)[1]
        elif arg == "--timeout":
            index += 1
            timeout = argv[index] if index < len(argv) else ""
        elif arg.startswith("--timeout="):
            timeout = arg.split("=", 1)[1]
        index += 1
    return Args(shell, restore, help_, version, chain, timeout, strict)


def _announce(env: Mapping[str, str], shell: bool) -> bool:
    if shell:
        return False
    return env.get("SLURM_LOCALID", "0") == "0"


def _write_stdout(chain_out: bytes, text: bytes) -> None:
    if chain_out:
        write_fd(1, chain_out)
        if not chain_out.endswith(b"\n"):
            write_fd(1, b"\n")
    write_fd(1, text)


def _emergency(exc: BaseException, shell: bool) -> None:
    message = "eessi-arch-guard: skipped, %s: %s" % (type(exc).__name__, exc)
    try:
        encoded = message_bytes(message) + b"\n"
        if shell:
            write_fd(2, encoded)
        else:
            write_fd(1, b"print " + encoded)
    except Exception:
        return


def _main(argv: Sequence[str]) -> int:
    args = parse_args(argv)
    if args.restore:
        return restore_main()
    if args.help:
        write_fd(1, HELP.encode("ascii"))
        return 0
    if args.version:
        write_fd(1, (__version__ + "\n").encode("ascii"))
        return 0

    env = dict(os.environ)
    chain_out = b""
    # `timeout` is read for `--shell` and for a `--chain` wrapper too.
    settings, config_warning = site_config(__file__)
    notes = []
    if config_warning:
        notes.append(config_warning)
    chain = None
    if not args.shell:
        chain = args.chain
        if chain is None:
            chain = config_chain(settings)
            if chain and not _chain_path_ok(chain):
                notes.append(
                    "eessi-arch-guard: config chain is not an absolute executable: %s"
                    % one_line(chain, 200)
                )
                chain = None
        elif not _chain_path_ok(chain):
            notes.append("eessi-arch-guard: --chain is not an absolute executable")
            chain = None
    for note in notes:
        write_fd(2, message_bytes(note) + b"\n")
    if chain:
        code, chain_out, err = run_chain(chain, env)
        if err:
            write_fd(2, err)
        if code != 0:
            _write_stdout(chain_out, b"")
            return code

    timeout, timeout_warning = resolve_timeout(args.timeout, settings)
    if timeout_warning:
        write_fd(2, message_bytes(timeout_warning) + b"\n")

    try:
        outcome = limited_decide(env, shell=args.shell, timeout=timeout)
        text = render_bytes(
            outcome.changes, outcome.message, args.shell, _announce(env, args.shell)
        )
    except Exception as exc:
        _write_stdout(chain_out, b"")
        _emergency(exc, args.shell)
        return STRICT_FAILURE_EXIT if args.strict else 0

    if args.shell and outcome.message:
        write_fd(2, message_bytes(outcome.message) + b"\n")
    if env.get("EESSI_ARCH_GUARD_VERBOSE") == "1" and outcome.detail:
        write_fd(2, message_bytes(outcome.detail) + b"\n")
    _write_stdout(chain_out, text)
    if args.strict and outcome.kind == "failed":
        return STRICT_FAILURE_EXIT
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Run the guard with its selected decision failure policy."""
    if argv is None:
        argv = sys.argv[1:]
    try:
        return _main(argv)
    except (Exception, KeyboardInterrupt) as exc:
        _emergency(exc, "--shell" in argv)
        return STRICT_FAILURE_EXIT if "--strict" in argv else 0


if __name__ == "__main__":
    sys.exit(main())
