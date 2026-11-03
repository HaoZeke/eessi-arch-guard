"""Policy and parser tests that do not need a module tree."""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import eessi_arch_guard as guard  # noqa: E402
from slurm_model import apply_task_prolog  # noqa: E402

ZEN4 = "x86_64/amd/zen4"
ZEN2 = "x86_64/amd/zen2"


def _env(**extra):
    base = {
        "EESSI_SOFTWARE_SUBDIR": ZEN4,
        "EESSI_PREFIX": "/cvmfs/software.eessi.io/versions/2025.06",
        "EESSI_OS_TYPE": "linux",
        "EESSI_INIT_PREFIX": "/cvmfs/software.eessi.io/versions/2025.06/init",
        "EESSI_PROC_CPUINFO": "/proc/cpuinfo",
        "LOADEDMODULES": "EESSI/2025.06:R/4.4.2",
        "MODULEPATH": "/mod",
        "PATH": "/usr/bin:/bin",
        "LMOD_CMD": "/bin/bash",
        "_ModuleTable001_": "old-table",
    }
    base.update(extra)
    return base


def _cpu(status="ok", target=ZEN2, candidates=(ZEN2,), error=""):
    def probe(_env):
        return guard.CpuProbe(status, target, candidates, error)

    return probe


def _accel(status="none", subdir="", error=""):
    def probe(_env):
        return guard.AccelProbe(status, subdir, error)

    return probe


def _restore(target=ZEN2, accel="", newline=False):
    def restore(env, eessi, modules):
        new = dict(env)
        new["EESSI_SOFTWARE_SUBDIR"] = target
        new["EESSI_ACCEL_SUBDIR"] = accel
        new["PATH"] = "/node/" + target + "/bin"
        new["LOADEDMODULES"] = ":".join((eessi, *modules))
        new["_ModuleTable001_"] = "table-" + target
        if newline:
            new["UNSAFE_NEWLINE"] = "a\nexport EVIL=1"
        return guard.RestoreResult(True, new, (), "")

    return restore


class UnitTests:
    def test_module_name_grammar(self):
        assert guard.module_name_ok("EESSI/2025.06")
        assert guard.module_name_ok("R/4.4.2-gfbf-2024a")
        assert guard.module_name_ok("Compiler/GCCcore/15.2.0")
        for bad in (
            "R/4.4.2\nexport LD_PRELOAD=x",
            "R/4.4.2;rm",
            "$(id)",
            "`id`",
            "foo/'bar",
            'foo/"bar',
            "has space/1.0",
            "EESSI",
            "/EESSI/2025.06",
            "R/../4.4.2",
            "name/ver\r",
            "name/ver$",
        ):
            assert not guard.module_name_ok(bad), bad

    def test_cpu_candidate_rejects_traversal(self):
        assert guard.safe_rel_subdir("x86_64/amd/zen2")
        assert not guard.safe_rel_subdir("../etc")
        assert not guard.safe_rel_subdir("/etc")
        assert not guard.safe_rel_subdir("x86_64/../../tmp")
        assert not guard.safe_rel_subdir("x86_64/amd/..")
        assert not guard.safe_rel_subdir("")

    def test_accel_grammar(self):
        assert guard.ACCEL_RE.fullmatch("accel/nvidia/cc90")
        assert guard.ACCEL_RE.fullmatch("accel/nvidia/cc120")
        assert guard.ACCEL_RE.fullmatch("accel/amd/gfx90a")
        assert guard.ACCEL_RE.fullmatch("nvidia/cc90") is None
        assert guard.ACCEL_RE.fullmatch("accel/nvidia/cc90\nexport X=1") is None

    def test_prolog_value_is_literal(self):
        text = guard.render({"FOO": "cost $5 `id` \"q\""}, None, shell=False, announce=False)
        assert text == "export FOO=cost $5 `id` \"q\"\n"
        changes, printed = apply_task_prolog(text)
        assert changes == {"FOO": "cost $5 `id` \"q\""}
        assert printed == []

    def test_newline_value_aborts_delta(self):
        delta = guard.environment_delta(
            {"PATH": "/old"},
            {"PATH": "/new", "FOO": "a\nexport LD_PRELOAD=/evil.so"},
            multiline=False,
        )
        assert delta is None

    def test_newline_name_aborts_delta(self):
        delta = guard.environment_delta(
            {},
            {"OK": "1", "FOO\nexport LD_PRELOAD": "/evil.so"},
            multiline=False,
        )
        assert delta is None

    def test_soft_drop_keeps_the_rest(self):
        delta = guard.environment_delta(
            {"PATH": "/old", "PS1": "old"},
            {"PATH": "/new", "PS1": "line\nline"},
            multiline=False,
        )
        assert delta == {"PATH": "/new"}

    def test_shell_quote_round_trip(self):
        value = "cost $5 `id` $(touch /tmp/nope) 'q'\nexport LD_PRELOAD=/evil"
        text = guard.render({"FOO": value}, "hello\nexport PWNED=1", shell=True, announce=True)
        assert "print " not in text
        # The message is not on stdout in shell mode; render leaves it to the caller.
        script = 'eval "$1"; printf %s "$FOO"; printf "\\0%s" "${LD_PRELOAD-unset}"'
        import subprocess

        proc = subprocess.run(
            ["bash", "--noprofile", "--norc", "-c", script, "bash", text],
            capture_output=True,
            env={"PATH": "/usr/bin:/bin", "HOME": "/tmp"},
            check=False,
        )
        assert proc.returncode == 0, proc.stderr
        foo, preload = proc.stdout.split(b"\0", 1)
        assert foo.decode() == value
        assert preload == b"unset"

    def test_print_newline_cannot_become_export(self):
        text = guard.render({}, "hello\nexport LD_PRELOAD=/evil.so", shell=False, announce=True)
        assert text.count("\n") == 1
        assert text.startswith("print ")
        changes, printed = apply_task_prolog(text)
        assert changes == {}
        assert printed == ["hello export LD_PRELOAD=/evil.so"]

    def test_slurm_never_emit(self):
        delta = guard.environment_delta(
            {"SLURM_JOB_ID": "1", "TMPDIR": "/old", "PATH": "/old"},
            {"SLURM_JOB_ID": "2", "TMPDIR": "/new", "PATH": "/new"},
            multiline=False,
        )
        assert delta == {"PATH": "/new"}

    def test_cache_key_omits_arch_guard(self):
        left = _env(EESSI_ARCH_GUARD="0", EESSI_ARCH_GUARD_MISSING="ignore")
        right = _env(EESSI_ARCH_GUARD="1")
        assert guard.cache_digest(left) == guard.cache_digest(right)
        right["LOADEDMODULES"] = "EESSI/2025.06:Other/1.0"
        assert guard.cache_digest(left) != guard.cache_digest(right)

    def test_cache_directory_falls_back(self):
        assert guard.cache_directory({"TMPDIR": "/no/such/eessi-arch-guard-dir"}) == "/tmp"
        missing = tempfile.mkdtemp()
        os.rmdir(missing)
        assert guard.cache_directory({"TMPDIR": missing}) == "/tmp"

    def test_not_loaded_is_silent(self):
        outcome = guard.decide({}, shell=False, cpu_probe=_cpu(), accel_probe=_accel())
        assert outcome.kind == "skip"
        assert outcome.changes == {}
        assert outcome.message is None

    def test_opt_out_and_overrides_are_silent(self):
        for extra in (
            {"EESSI_ARCH_GUARD": "0"},
            {"EESSI_SOFTWARE_SUBDIR_OVERRIDE": "x86_64/amd/zen2"},
            {"EESSI_ARCHDETECT_OPTIONS_OVERRIDE": "x86_64/amd/zen2"},
        ):
            outcome = guard.decide(
                _env(**extra), shell=False, cpu_probe=_cpu(), accel_probe=_accel()
            )
            assert outcome.kind == "skip", extra
            assert outcome.changes == {}

    def test_same_target_does_not_restore(self):
        calls = []

        def restore(*_args):
            calls.append(1)
            raise AssertionError("restore should not run")

        outcome = guard.decide(
            _env(),
            shell=False,
            cpu_probe=_cpu(target=ZEN4),
            accel_probe=_accel(),
            restore=restore,
        )
        assert outcome.kind == "noop"
        assert calls == []

    def test_cpu_mismatch_publishes_delta(self):
        outcome = guard.decide(
            _env(),
            shell=False,
            cpu_probe=_cpu(target=ZEN2),
            accel_probe=_accel(),
            restore=_restore(ZEN2),
        )
        assert outcome.kind == "reloaded"
        assert outcome.changes["EESSI_SOFTWARE_SUBDIR"] == ZEN2
        assert outcome.changes["PATH"] == "/node/" + ZEN2 + "/bin"
        assert outcome.changes["EESSI_ARCH_GUARD_RELOADED"] == "%s->%s" % (ZEN4, ZEN2)
        assert "SLURM_JOB_ID" not in outcome.changes

    def test_unsafe_restore_value_publishes_nothing_else(self):
        outcome = guard.decide(
            _env(),
            shell=False,
            cpu_probe=_cpu(target=ZEN2),
            accel_probe=_accel(),
            restore=_restore(ZEN2, newline=True),
        )
        assert outcome.kind == "failed"
        assert set(outcome.changes) == {"EESSI_ARCH_GUARD_FAILED"}
        text = guard.render(outcome.changes, outcome.message, shell=False, announce=True)
        changes, _printed = apply_task_prolog(text)
        assert "EVIL" not in changes
        assert set(changes) == {"EESSI_ARCH_GUARD_FAILED"}

    def test_bad_module_name_does_not_restore(self):
        calls = []

        def restore(*_args):
            calls.append(1)
            raise AssertionError("restore")

        env = _env(LOADEDMODULES="EESSI/2025.06:R/4.4.2\nexport LD_PRELOAD=evil")
        outcome = guard.decide(
            env,
            shell=False,
            cpu_probe=_cpu(target=ZEN2),
            accel_probe=_accel(),
            restore=restore,
        )
        assert calls == []
        assert outcome.kind == "failed"
        text = guard.render(outcome.changes, outcome.message, shell=False, announce=True)
        changes, _printed = apply_task_prolog(text)
        assert "LD_PRELOAD" not in changes

    def test_no_software_dir_is_loud_and_configurable(self):
        loud = guard.decide(
            _env(),
            shell=False,
            cpu_probe=_cpu(status="none", target="", candidates=(ZEN2,)),
            accel_probe=_accel(),
        )
        assert loud.kind == "failed"
        assert loud.changes["EESSI_ARCH_GUARD_FAILED"].endswith("->no-software-dir")
        quiet = guard.decide(
            _env(EESSI_ARCH_GUARD_MISSING="ignore"),
            shell=False,
            cpu_probe=_cpu(status="none", target="", candidates=(ZEN2,)),
            accel_probe=_accel(),
        )
        assert quiet.kind == "skip"
        assert quiet.changes == {}

    def test_archdetect_error_is_loud(self):
        outcome = guard.decide(
            _env(),
            shell=False,
            cpu_probe=_cpu(status="error", target="", error="exit 1"),
            accel_probe=_accel(),
        )
        assert outcome.kind == "failed"
        assert "archdetect-failed" in outcome.changes["EESSI_ARCH_GUARD_FAILED"]

    def test_accel_difference_is_part_of_the_label(self):
        outcome = guard.decide(
            _env(EESSI_ACCEL_SUBDIR="accel/nvidia/cc90"),
            shell=False,
            cpu_probe=_cpu(target=ZEN4),
            accel_probe=_accel(status="ok", subdir="accel/nvidia/cc120"),
            restore=_restore(ZEN4, accel="accel/nvidia/cc120"),
        )
        assert outcome.kind == "reloaded"
        reloaded = outcome.changes["EESSI_ARCH_GUARD_RELOADED"]
        assert "accel/nvidia/cc90" in reloaded
        assert "accel/nvidia/cc120" in reloaded

    def test_accel_override_skips_accel_probe(self):
        def accel_probe(_env):
            raise AssertionError("accel probe")

        outcome = guard.decide(
            _env(EESSI_ACCELERATOR_TARGET_OVERRIDE="accel/nvidia/cc90"),
            shell=False,
            cpu_probe=_cpu(target=ZEN4),
            accel_probe=accel_probe,
        )
        assert outcome.kind == "noop"

    def test_missing_lmod_is_loud_on_mismatch(self):
        env = _env()
        del env["LMOD_CMD"]
        outcome = guard.decide(
            env,
            shell=False,
            cpu_probe=_cpu(target=ZEN2),
            accel_probe=_accel(),
        )
        assert outcome.kind == "failed"
        assert "lmod-unavailable" in outcome.changes["EESSI_ARCH_GUARD_FAILED"]

    def test_main_exception_exits_zero(self):
        original = guard.cached_decide

        def boom(_env, shell):
            return guard.Outcome({"NOT A NAME": "x"}, "hi", "reloaded")

        guard.cached_decide = boom
        try:
            code, out = _capture_fd1(lambda: guard.main([]))
        finally:
            guard.cached_decide = original
        assert code == 0
        assert out.startswith(b"print eessi-arch-guard: skipped")

    def test_interrupt_exits_zero(self):
        original = guard.cached_decide

        def boom(_env, shell):
            raise KeyboardInterrupt()

        guard.cached_decide = boom
        try:
            code, out = _capture_fd1(lambda: guard.main([]))
        finally:
            guard.cached_decide = original
        assert code == 0
        assert b"KeyboardInterrupt" in out
        assert out.startswith(b"print eessi-arch-guard: skipped")

    def test_unwritable_stdout_still_exits_zero(self):
        script = str(Path(__file__).resolve().parents[1] / "src" / "eessi_arch_guard.py")
        for args in (["--version"], ["--help"]):
            _assert_writes_exit_zero(script, args)

    def test_chain_config_parser_and_untrusted_file(self):
        assert guard.parse_chain_config("# comment\nchain = /bin/true\n") == "/bin/true"
        assert guard.parse_chain_config("other = 1\n") is None
        handle = tempfile.NamedTemporaryFile("w", delete=False)
        try:
            handle.write("chain = /bin/true\n")
            handle.close()
            reason = guard._config_untrusted(handle.name)
            assert reason is not None
            assert "not owned by root" in reason
        finally:
            os.unlink(handle.name)

    def test_message_trim_keeps_a_character_boundary(self):
        import locale

        assert guard.PROLOG_LINE_MAX == 4094
        accent = "\u00e9"
        naive = (accent * 10).encode("utf-8")[:5]
        try:
            naive.decode("utf-8")
            split = False
        except UnicodeDecodeError:
            split = True
        assert split
        fitted = guard._fit_encoded((accent * 10).encode("utf-8"), 5, "utf-8")
        fitted.decode("utf-8")
        assert len(fitted) <= 5
        locale.setlocale(locale.LC_ALL, "C.UTF-8")
        original = guard.PROLOG_LINE_MAX
        try:
            guard.PROLOG_LINE_MAX = 20
            raw = guard.message_bytes(accent * 40)
            raw.decode("utf-8")
            room = guard.PROLOG_LINE_MAX - len(b"print ") - 1
            assert len(raw) <= room
            assert raw.endswith(accent.encode("utf-8"))
        finally:
            guard.PROLOG_LINE_MAX = original
            locale.setlocale(locale.LC_ALL, "C")

    def test_export_line_over_the_slurm_17_11_cap_is_refused(self):
        value = "a" * (guard.PROLOG_LINE_MAX + 8)
        raised = False
        try:
            guard.render_bytes({"PATH": value}, None, shell=False, announce=False)
        except ValueError:
            raised = True
        assert raised
        shell = guard.render_bytes({"PATH": "short"}, None, shell=True, announce=False)
        assert b"PATH" in shell

    def test_wire_round_trip_keeps_the_byte(self):
        text = "caf\udce9/bin"
        assert guard.from_wire(guard.to_wire(text)) == text
        assert os.fsencode(guard.from_wire(guard.to_wire(text))) == b"caf\xe9/bin"

    def test_fake_lmod_writes_environment_bytes(self):
        fake = Path(__file__).resolve().parent / "fake_lmod.py"
        env = dict(os.environ)
        env["PATH"] = "/usr/bin:/bin"
        env["EESSI_TEST_BASE_PATH"] = "/opt/caf\udce9/bin"
        proc = subprocess.run(
            [sys.executable, "-I", str(fake), "bash", "--force", "purge"],
            capture_output=True,
            env=env,
            check=False,
        )
        assert proc.returncode == 0, proc.stderr
        assert b"export PATH='/opt/caf\xe9/bin'" in proc.stdout

    def test_readme_lists_every_flag_and_env_name(self):
        root = Path(__file__).resolve().parents[1]
        source = (root / "src" / "eessi_arch_guard.py").read_text(encoding="utf-8")
        readme = (root / "README.md").read_text(encoding="utf-8")
        body = source.split("def parse_args", 1)[1].split("def _announce", 1)[0]
        flags = set(re.findall(r'["\'](-{1,2}[A-Za-z][A-Za-z0-9-]*)["\']', body))
        assert flags >= {"--shell", "--restore", "--help", "--version", "--chain", "-h"}
        missing_flags = sorted(flag for flag in flags if flag not in readme)
        assert missing_flags == []
        names = set(re.findall(r'["\']([A-Z][A-Z0-9_]{1,})["\']', source))
        missing_names = sorted(name for name in names if name not in readme)
        assert missing_names == []

    def test_select_first_directory(self):
        root = tempfile.mkdtemp()
        try:
            os.makedirs(os.path.join(root, "software", "linux", ZEN2, "software"))
            # probe_cpu runs the script; exercise the directory walk directly.
            chosen = None
            for candidate in ( "x86_64/amd/zen5", ZEN4, ZEN2):
                if not guard.safe_rel_subdir(candidate):
                    continue
                directory = os.path.join(root, "software", "linux", candidate, "software")
                if os.path.isdir(directory):
                    chosen = candidate
                    break
            assert chosen == ZEN2
        finally:
            import shutil

            shutil.rmtree(root)


def _assert_writes_exit_zero(script, args, env=None):
    if env is None:
        env = {"PATH": "/usr/bin:/bin", "HOME": "/tmp", "LC_ALL": "C"}
    with open("/dev/full", "wb") as sink:
        proc = subprocess.run(
            [sys.executable, "-I", script, *args],
            stdout=sink,
            stderr=subprocess.PIPE,
            env=env,
            check=False,
        )
    assert proc.returncode == 0, (args, proc.returncode, proc.stderr)
    read_fd, write_fd = os.pipe()
    os.close(read_fd)
    proc = subprocess.run(
        [sys.executable, "-I", script, *args],
        stdout=write_fd,
        stderr=subprocess.PIPE,
        env=env,
        check=False,
    )
    os.close(write_fd)
    assert proc.returncode == 0, (args, proc.returncode, proc.stderr)


def _capture_fd1(fn):
    read_fd, write_fd = os.pipe()
    saved = os.dup(1)
    os.dup2(write_fd, 1)
    os.close(write_fd)
    try:
        result = fn()
    finally:
        os.dup2(saved, 1)
        os.close(saved)
    os.set_blocking(read_fd, False)
    chunks = []
    while True:
        try:
            block = os.read(read_fd, 65536)
        except BlockingIOError:
            break
        if not block:
            break
        chunks.append(block)
    os.close(read_fd)
    return result, b"".join(chunks)


def load_tests(_loader, _tests, _pattern):
    import unittest

    suite = unittest.TestSuite()
    for name in sorted(dir(UnitTests)):
        if name.startswith("test_"):
            suite.addTest(unittest.FunctionTestCase(getattr(UnitTests(), name), description=name))
    return suite


if __name__ == "__main__":
    import unittest

    unittest.main()
