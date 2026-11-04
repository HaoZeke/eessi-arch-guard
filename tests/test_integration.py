"""End-to-end runs against a fake EESSI tree, archdetect stub, and LMOD_CMD.

No real Lmod, Slurm, nvidia-smi, or CVMFS. Targets are faked with
EESSI_PROC_CPUINFO and a nvidia-smi stub that is first on PATH.
"""

from __future__ import annotations

import fcntl
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import eessi_arch_guard as guard  # noqa: E402
import harness  # noqa: E402
from slurm_model import apply_task_prolog  # noqa: E402

ZEN2 = "x86_64/amd/zen2"
ZEN4 = "x86_64/amd/zen4"
CC90 = "accel/nvidia/cc90"
CC120 = "accel/nvidia/cc120"


def _site():
    site = harness.Site()
    for cpu in (ZEN2, ZEN4):
        site.add_cpu(cpu)
        site.add_module(cpu, "R/4.4.2")
    for accel in (CC90, CC120):
        site.add_accel(ZEN4, accel)
        version = "12.6.0" if accel == CC90 else "12.8.0"
        site.add_module(ZEN4, "CUDA/" + version, accel=accel)
    return site


def _assert_prolog(proc, code=0):
    assert proc.returncode == code, (proc.returncode, proc.stdout, proc.stderr)
    for line in proc.stdout.split("\n"):
        if line == "":
            continue
        assert line.startswith(("export ", "unset ", "print ")), line
    changes, printed = apply_task_prolog(proc.stdout)
    for key, value in changes.items():
        assert guard.ENV_NAME_RE.fullmatch(key), key
        if value is not None:
            assert "\n" not in value and "\r" not in value
    return changes, printed


def _stop_limit(timeout: float) -> float:
    """Decision timeout plus the worst-case post-kill waits."""
    return timeout + 3 * guard.POST_KILL_WAIT_SECONDS


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _wait_until_dead(pid: int) -> None:
    for _ in range(50):
        if not _pid_alive(pid):
            return
        time.sleep(0.05)
    os.kill(pid, signal.SIGKILL)


def _apply(env, changes):
    updated = dict(env)
    for key, value in changes.items():
        if value is None:
            updated.pop(key, None)
        else:
            updated[key] = value
    return updated


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.site = _site()

    def tearDown(self):
        self.site.close()

    def _cpu_env(self, loaded, node, modules=("R/4.4.2",)):
        self.site.write_cpuinfo(node)
        return harness.base_env(self.site, loaded, list(modules), gpu="none")

    def test_zen4_to_zen2(self):
        env = self._cpu_env(ZEN4, "zen2")
        proc = harness.run_guard(env)
        changes, printed = _assert_prolog(proc)
        self.assertEqual(changes["EESSI_SOFTWARE_SUBDIR"], ZEN2)
        self.assertIn(ZEN2, changes["EBROOTR"])
        self.assertNotIn(ZEN4, changes["EBROOTR"])
        self.assertEqual(changes["EESSI_ARCH_GUARD_RELOADED"], "%s->%s" % (ZEN4, ZEN2))
        self.assertTrue(printed)
        self.assertIn("--force", " ".join(harness.log_lines(self.site.log)))
        # Idempotent once the task environment is the reloaded one.
        again = harness.run_guard(_apply(env, changes))
        self.assertEqual(again.stdout, "")
        self.assertEqual(again.returncode, 0)

    def test_zen2_to_zen4(self):
        env = self._cpu_env(ZEN2, "zen4")
        proc = harness.run_guard(env)
        changes, _printed = _assert_prolog(proc)
        self.assertEqual(changes["EESSI_SOFTWARE_SUBDIR"], ZEN4)
        self.assertIn(ZEN4, changes["EBROOTR"])
        self.assertEqual(changes["EESSI_ARCH_GUARD_RELOADED"], "%s->%s" % (ZEN2, ZEN4))

    def test_cc90_to_cc120(self):
        self.site.write_cpuinfo("zen4")
        env = harness.base_env(
            self.site, ZEN4, ["R/4.4.2", "CUDA/12.6.0"], accel=CC90, gpu="1", gpu_cc="12.0"
        )
        # CUDA/12.6.0 is not on cc120; the reload must fail rather than half-load
        # if that module is absent. Put the same module name on cc120 too so the
        # target change itself can succeed, and check the path moved.
        self.site.add_module(ZEN4, "CUDA/12.6.0", accel=CC120)
        proc = harness.run_guard(env)
        changes, printed = _assert_prolog(proc)
        self.assertEqual(changes["EESSI_ACCEL_SUBDIR"], CC120)
        self.assertIn(CC120, changes["MODULEPATH"])
        self.assertIn(CC90, changes["EESSI_ARCH_GUARD_RELOADED"])
        self.assertIn(CC120, changes["EESSI_ARCH_GUARD_RELOADED"])
        self.assertTrue(any("cc120" in line or "cc90" in line for line in printed))

    def test_no_gpu_to_gpu(self):
        self.site.write_cpuinfo("zen4")
        self.site.add_module(ZEN4, "CUDA/12.8.0", accel=CC120)
        env = harness.base_env(self.site, ZEN4, ["R/4.4.2"], gpu="1", gpu_cc="12.0")
        proc = harness.run_guard(env)
        changes, _printed = _assert_prolog(proc)
        self.assertEqual(changes.get("EESSI_ACCEL_SUBDIR"), CC120)
        self.assertIn("no accel", changes["EESSI_ARCH_GUARD_RELOADED"])
        self.assertIn(CC120, changes["EESSI_ARCH_GUARD_RELOADED"])

    def test_gpu_to_no_gpu(self):
        self.site.write_cpuinfo("zen4")
        env = harness.base_env(self.site, ZEN4, ["R/4.4.2"], accel=CC90, gpu="none")
        proc = harness.run_guard(env)
        changes, _printed = _assert_prolog(proc)
        self.assertEqual(changes.get("EESSI_ACCEL_SUBDIR"), "")
        self.assertIn("no accel", changes["EESSI_ARCH_GUARD_RELOADED"])
        self.assertNotIn(CC90, changes.get("MODULEPATH", env["MODULEPATH"]))

    def test_accel_pinned_is_a_noop(self):
        self.site.write_cpuinfo("zen4")
        env = harness.base_env(
            self.site, ZEN4, ["R/4.4.2"], accel=CC90, gpu="1", gpu_cc="12.0"
        )
        env["EESSI_ACCELERATOR_TARGET_OVERRIDE"] = CC90
        proc = harness.run_guard(env)
        _assert_prolog(proc)
        self.assertEqual(proc.stdout, "")
        self.assertEqual(harness.log_lines(self.site.log), [])

    def test_same_target_noop(self):
        self.site.write_cpuinfo("zen4")
        env = harness.base_env(self.site, ZEN4, ["R/4.4.2"], gpu="none")
        proc = harness.run_guard(env)
        _assert_prolog(proc)
        self.assertEqual(proc.stdout, "")
        self.assertEqual(harness.log_lines(self.site.log), [])
        self.assertEqual(harness.log_lines(self.site.arch_log), ["cpupath", "accelpath"])

    def test_missing_software_dir_is_loud_and_configurable(self):
        bare = harness.Site()
        try:
            bare.write_cpuinfo("zen2")
            env = harness.base_env(bare, ZEN4, [], gpu="none")
            # No module files were added; the software directory itself is absent.
            proc = harness.run_guard(env)
            changes, printed = _assert_prolog(proc)
            self.assertTrue(changes["EESSI_ARCH_GUARD_FAILED"].endswith("->no-software-dir"))
            self.assertNotIn("EESSI_SOFTWARE_SUBDIR", changes)
            self.assertTrue(printed)
            self.assertIn("no EESSI software directory", printed[0])
            env["EESSI_ARCH_GUARD_MISSING"] = "ignore"
            quiet = harness.run_guard(env)
            _assert_prolog(quiet)
            self.assertEqual(quiet.stdout, "")
        finally:
            bare.close()

    def test_tmpdir_absent_uses_tmp_mode_0600(self):
        self.site.write_cpuinfo("zen2")
        env = self._cpu_env(ZEN4, "zen2")
        env["TMPDIR"] = os.path.join(self.site.root, "not-created-yet")
        env["SLURM_JOB_ID"] = "424242"
        env["SLURM_STEP_ID"] = "batch"
        path = guard.cache_file_path(env)
        self.assertIsNotNone(path)
        self.assertTrue(path.startswith("/tmp/"))
        old = os.umask(0)
        try:
            proc = harness.run_guard(env)
        finally:
            os.umask(old)
        try:
            _assert_prolog(proc)
            self.assertTrue(os.path.isfile(path))
            self.assertFalse(os.path.islink(path))
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
            self.assertEqual(os.stat(path + ".lock").st_mode & 0o777, 0o600)
        finally:
            for suffix in ("", ".lock"):
                try:
                    os.unlink(path + suffix)
                except OSError:
                    pass

    def test_cache_symlink_is_not_followed(self):
        self.site.write_cpuinfo("zen2")
        env = self._cpu_env(ZEN4, "zen2")
        env["TMPDIR"] = self.site.root
        env["SLURM_JOB_ID"] = "515151"
        path = guard.cache_file_path(env)
        canary = path + ".canary"
        with open(canary, "w", encoding="utf-8") as handle:
            handle.write(
                '{"v":1,"kind":"reloaded","message":null,"detail":null,'
                '"changes":{"LD_PRELOAD":"/evil.so"}}'
            )
        os.symlink(canary, path)
        proc = harness.run_guard(env)
        try:
            changes, _printed = _assert_prolog(proc)
            self.assertNotIn("LD_PRELOAD", changes)
            self.assertEqual(changes["EESSI_SOFTWARE_SUBDIR"], ZEN2)
            with open(canary, encoding="utf-8") as handle:
                body = handle.read()
            self.assertTrue(body.startswith("{"))
            self.assertIn("/evil.so", body)
            self.assertFalse(os.path.islink(path))
        finally:
            for item in (path, path + ".lock", canary):
                try:
                    os.unlink(item)
                except OSError:
                    pass

    def test_bash_env_is_not_sourced(self):
        script = os.path.join(self.site.root, "bash_env.sh")
        with open(script, "w", encoding="utf-8") as handle:
            handle.write(
                "#!/bin/bash\n"
                "echo BASH_ENV_RAN >> %s\n"
                "echo INJECTED_STDOUT\n" % self.site.bash_env_log
            )
        os.chmod(script, 0o755)
        env = self._cpu_env(ZEN4, "zen2")
        env["BASH_ENV"] = script
        env["EESSI_TEST_SENTINEL"] = "cost $5 `id` \"q\""
        proc = harness.run_guard(env)
        changes, _printed = _assert_prolog(proc)
        self.assertFalse(os.path.exists(self.site.bash_env_log))
        self.assertNotIn("INJECTED_STDOUT", proc.stdout)
        self.assertNotIn("BASH_ENV_RAN", proc.stdout)
        self.assertEqual(changes["ROUNDTRIP"], "cost $5 `id` \"q\"")
        self.assertEqual(changes["EESSI_SOFTWARE_SUBDIR"], ZEN2)

    def test_newline_module_name_is_not_an_export(self):
        env = self._cpu_env(ZEN4, "zen2")
        env["LOADEDMODULES"] = "EESSI/2025.06:R/4.4.2\nexport LD_PRELOAD=evil"
        proc = harness.run_guard(env)
        changes, _printed = _assert_prolog(proc)
        self.assertNotIn("LD_PRELOAD", changes)
        self.assertNotIn("EESSI_SOFTWARE_SUBDIR", changes)
        self.assertIn("unsafe-module-name", changes["EESSI_ARCH_GUARD_FAILED"])
        self.assertEqual(harness.log_lines(self.site.log), [])

    def test_newline_value_aborts_without_partial_apply(self):
        env = self._cpu_env(ZEN4, "zen2")
        env["EESSI_TEST_INJECT_NEWLINE"] = "1"
        proc = harness.run_guard(env)
        changes, _printed = _assert_prolog(proc)
        self.assertNotIn("EVIL", changes)
        self.assertNotIn("UNSAFE_NEWLINE", changes)
        self.assertNotIn("EESSI_SOFTWARE_SUBDIR", changes)
        self.assertIn("unsafe-environment", changes["EESSI_ARCH_GUARD_FAILED"])

    def test_missing_modules_are_all_named(self):
        self.site.add_module(ZEN4, "OnlyZen4/1.0")
        env = self._cpu_env(ZEN4, "zen2", modules=("R/4.4.2", "OnlyZen4/1.0"))
        # R exists on zen2; OnlyZen4 does not. Remove R from zen2 as well so both fail.
        shutil.rmtree(os.path.join(self.site.prefix, "software", "linux", ZEN2, "modules"))
        os.makedirs(os.path.join(self.site.prefix, "software", "linux", ZEN2, "modules", "all"))
        proc = harness.run_guard(env)
        changes, printed = _assert_prolog(proc)
        self.assertNotIn("EESSI_SOFTWARE_SUBDIR", changes)
        self.assertIn("R/4.4.2", printed[0])
        self.assertIn("OnlyZen4/1.0", printed[0])
        self.assertIn("environment left as it was", printed[0])

    def test_stderr_injection_stays_out_of_prolog_directives(self):
        self.site.add_module(ZEN4, "OnlyZen4/1.0")
        env = self._cpu_env(ZEN4, "zen2", modules=("OnlyZen4/1.0",))
        env["EESSI_TEST_STDERR_INJECT"] = "1"
        proc = harness.run_guard(env)
        changes, _printed = _assert_prolog(proc)
        self.assertNotIn("PWNED", changes)
        self.assertNotIn("export PWNED=1\n", proc.stdout)

    def test_shell_mode_stdout_is_eval_safe(self):
        env = self._cpu_env(ZEN4, "zen2")
        canary = os.path.join(self.site.root, "canary")
        env["EESSI_TEST_SENTINEL"] = "$(touch %s) `touch %s`" % (canary, canary)
        proc = harness.run_guard(env, ["--shell"])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertNotIn("print ", proc.stdout)
        self.assertNotIn("INJECTED", proc.stdout)
        self.assertIn("eessi-arch-guard:", proc.stderr)
        check = subprocess.run(
            [
                "bash",
                "--noprofile",
                "--norc",
                "-c",
                'eval "$1"; printf %s "$EESSI_SOFTWARE_SUBDIR"; printf "\\n%s" "$ROUNDTRIP"',
                "bash",
                proc.stdout,
            ],
            capture_output=True,
            text=True,
            env={"PATH": "/usr/bin:/bin", "HOME": "/tmp"},
            check=False,
        )
        self.assertEqual(check.returncode, 0, check.stderr)
        subdir, sentinel = check.stdout.split("\n", 1)
        self.assertEqual(subdir, ZEN2)
        self.assertEqual(sentinel, env["EESSI_TEST_SENTINEL"])
        self.assertFalse(os.path.exists(canary))

    def test_localid_suppresses_the_print_line(self):
        env = self._cpu_env(ZEN4, "zen2")
        env["SLURM_LOCALID"] = "3"
        proc = harness.run_guard(env)
        changes, printed = _assert_prolog(proc)
        self.assertEqual(printed, [])
        self.assertIn("EESSI_ARCH_GUARD_RELOADED", changes)

    def test_opt_out_and_archdetect_failure(self):
        env = self._cpu_env(ZEN4, "zen2")
        env["EESSI_ARCH_GUARD"] = "0"
        proc = harness.run_guard(env)
        _assert_prolog(proc)
        self.assertEqual(proc.stdout, "")
        self.site.write_cpuinfo("zen2", fail=True)
        env = self._cpu_env(ZEN4, "zen2")
        # write_cpuinfo again inside _cpu_env; set fail after.
        self.site.write_cpuinfo("zen2", fail=True)
        proc = harness.run_guard(env)
        changes, printed = _assert_prolog(proc)
        self.assertIn("archdetect-failed", changes["EESSI_ARCH_GUARD_FAILED"])
        self.assertTrue(printed)
        self.assertEqual(harness.log_lines(self.site.log), [])

    def test_first_candidate_with_a_software_directory(self):
        # Drop zen4's tree so the node's best match cannot be used.
        shutil.rmtree(os.path.join(self.site.prefix, "software", "linux", ZEN4))
        self.site.write_cpuinfo(matches="x86_64/amd/zen5:%s:%s" % (ZEN4, ZEN2))
        env = harness.base_env(self.site, ZEN2, ["R/4.4.2"], gpu="none")
        # Loaded for zen2, and zen2 is the first candidate that exists: no-op
        # even though the cpuinfo prefers zen5. Then load as zen4-in-name only.
        proc = harness.run_guard(env)
        self.assertEqual(proc.stdout, "")
        env["EESSI_SOFTWARE_SUBDIR"] = ZEN4
        env["_ModuleTable001_"] = "table-for-foreign"
        proc = harness.run_guard(env)
        changes, _printed = _assert_prolog(proc)
        self.assertEqual(changes["EESSI_SOFTWARE_SUBDIR"], ZEN2)

    def test_eight_tasks_one_reload(self):
        env = self._cpu_env(ZEN4, "zen2")
        env["TMPDIR"] = self.site.root
        env["SLURM_JOB_ID"] = "8888"
        env["SLURM_STEP_ID"] = "0"
        procs = []
        for rank in range(8):
            one = dict(env)
            one["SLURM_LOCALID"] = str(rank)
            procs.append(
                subprocess.Popen(
                    [sys.executable, "-I", str(harness.GUARD)],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    env=one,
                )
            )
        prints = 0
        for proc in procs:
            out, err = proc.communicate(timeout=60)
            self.assertEqual(proc.returncode, 0, err)
            _changes, printed = apply_task_prolog(out)
            prints += len(printed)
        loads = [
            line for line in harness.log_lines(self.site.log) if line.startswith("load EESSI/")
        ]
        self.assertEqual(loads, ["load EESSI/2025.06"])
        self.assertEqual(prints, 1)

    def test_parallel_noop_detects_once(self):
        self.site.write_cpuinfo("zen4")
        env = harness.base_env(self.site, ZEN4, ["R/4.4.2"], gpu="none")
        env["TMPDIR"] = self.site.root
        env["SLURM_JOB_ID"] = "7777"
        procs = []
        for rank in range(4):
            one = dict(env)
            one["SLURM_LOCALID"] = str(rank)
            procs.append(
                subprocess.Popen(
                    [sys.executable, "-I", str(harness.GUARD)],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    env=one,
                )
            )
        for proc in procs:
            out, err = proc.communicate(timeout=60)
            self.assertEqual(proc.returncode, 0, err)
            self.assertEqual(out, "")
        self.assertEqual(harness.log_lines(self.site.log), [])
        self.assertEqual(harness.log_lines(self.site.arch_log).count("cpupath"), 1)

    def test_chain_success_and_failure(self):
        env = self._cpu_env(ZEN4, "zen2")
        ok_path = os.path.join(self.site.root, "chain-ok.sh")
        with open(ok_path, "w", encoding="utf-8") as handle:
            handle.write("#!/bin/sh\necho 'export FROM_CHAIN=yes'\n")
        os.chmod(ok_path, 0o755)
        proc = harness.run_guard(env, ["--chain", ok_path])
        changes, _printed = _assert_prolog(proc)
        self.assertEqual(changes["FROM_CHAIN"], "yes")
        self.assertEqual(changes["EESSI_SOFTWARE_SUBDIR"], ZEN2)

        bad_path = os.path.join(self.site.root, "chain-bad.sh")
        with open(bad_path, "w", encoding="utf-8") as handle:
            handle.write("#!/bin/sh\necho 'export FROM_CHAIN=yes'\nexit 9\n")
        os.chmod(bad_path, 0o755)
        # Fresh log so a second reload would be visible.
        open(self.site.log, "w").close()
        proc = harness.run_guard(env, ["--chain", bad_path])
        self.assertEqual(proc.returncode, 9)
        self.assertIn("export FROM_CHAIN=yes", proc.stdout)
        self.assertNotIn("EESSI_ARCH_GUARD_RELOADED", proc.stdout)
        self.assertEqual(harness.log_lines(self.site.log), [])

    def test_bad_config_byte_keeps_the_other_keys(self):
        conf = Path(harness.GUARD).with_name("eessi-arch-guard.conf")
        ok_path = os.path.join(self.site.root, "chain-ok.sh")
        with open(ok_path, "w", encoding="utf-8") as handle:
            handle.write("#!/bin/sh\necho 'export FROM_CHAIN=yes'\n")
        os.chmod(ok_path, 0o755)
        conf.write_bytes(
            ("chain = %s\n" % ok_path).encode("ascii") + b"# caf\xe9\ntimeout = 1\xff\n"
        )
        subprocess.check_call(["sudo", "chown", "root:root", str(conf)])
        subprocess.check_call(["sudo", "chmod", "644", str(conf)])
        try:
            env = self._cpu_env(ZEN4, "zen2")
            proc = harness.run_guard(env)
            changes, _printed = _assert_prolog(proc)
            self.assertEqual(changes["FROM_CHAIN"], "yes")
            self.assertEqual(changes["EESSI_SOFTWARE_SUBDIR"], ZEN2)
            self.assertIn("original bytes", proc.stderr)
            self.assertIn("timeout", proc.stderr)
        finally:
            subprocess.check_call(["sudo", "rm", "-f", str(conf)])

    def test_untrusted_config_is_ignored(self):
        conf = Path(harness.GUARD).with_name("eessi-arch-guard.conf")
        conf.write_text("chain = /bin/true\n", encoding="utf-8")
        try:
            env = self._cpu_env(ZEN4, "zen2")
            proc = harness.run_guard(env)
            _assert_prolog(proc)
            self.assertIn("not owned by root", proc.stderr)
            self.assertIn("EESSI_ARCH_GUARD_RELOADED", proc.stdout)
        finally:
            conf.unlink(missing_ok=True)

    def test_latin1_locale_keeps_bytes_through_the_restore(self):
        parent = os.path.join(tempfile.gettempdir(), "caf\udce9-eessi-arch-guard")
        os.makedirs(parent, exist_ok=True)
        locale_dir = os.path.join(parent, "locale")
        os.makedirs(locale_dir, exist_ok=True)
        subprocess.run(
            [
                "localedef",
                "-f",
                "ISO-8859-1",
                "-i",
                "en_US",
                os.path.join(locale_dir, "en_US.ISO-8859-1"),
            ],
            check=True,
            capture_output=True,
        )
        self.site.close()
        self.site = harness.Site(directory=parent)
        for cpu in (ZEN2, ZEN4):
            self.site.add_cpu(cpu)
            self.site.add_module(cpu, "R/4.4.2")
        env = self._cpu_env(ZEN4, "zen2")
        env["LOCPATH"] = locale_dir
        env["LC_ALL"] = "en_US.ISO-8859-1"
        env["LANG"] = "en_US.ISO-8859-1"
        proc = subprocess.run(
            [sys.executable, "-I", str(harness.GUARD)],
            capture_output=True,
            env=env,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertNotIn(b"module load failed", proc.stdout)
        self.assertNotIn(b"skipped", proc.stdout)
        self.assertIn(b"caf\xe9", proc.stdout)
        self.assertIn(("EESSI_SOFTWARE_SUBDIR=%s" % ZEN2).encode("ascii"), proc.stdout)

    def test_non_utf8_path_entry_keeps_its_bytes(self):
        env = self._cpu_env(ZEN4, "zen2")
        env["LC_ALL"] = "en_US.UTF-8"
        env["LANG"] = "en_US.UTF-8"
        raw = "/opt/caf\udce9/bin"
        env["PATH"] = raw + ":" + env["PATH"]
        env["EESSI_TEST_BASE_PATH"] = raw + ":" + env["EESSI_TEST_BASE_PATH"]
        proc = subprocess.run(
            [sys.executable, "-I", str(harness.GUARD)],
            capture_output=True,
            env=env,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertNotIn(b"UnicodeEncodeError", proc.stdout)
        self.assertNotIn(b"skipped", proc.stdout)
        self.assertNotIn(b"\xed\xb0\xa9", proc.stdout)
        self.assertIn(b"/opt/caf\xe9/bin", proc.stdout)
        self.assertIn(("EESSI_SOFTWARE_SUBDIR=%s" % ZEN2).encode("ascii"), proc.stdout)
        for args in ([], ["--shell"]):
            with open("/dev/full", "wb") as sink:
                blocked = subprocess.run(
                    [sys.executable, "-I", str(harness.GUARD), *args],
                    stdout=sink,
                    stderr=subprocess.PIPE,
                    env=env,
                    check=False,
                )
            self.assertEqual(blocked.returncode, 0, (args, blocked.returncode, blocked.stderr))
            read_fd, write_fd = os.pipe()
            os.close(read_fd)
            blocked = subprocess.run(
                [sys.executable, "-I", str(harness.GUARD), *args],
                stdout=write_fd,
                stderr=subprocess.PIPE,
                env=env,
                check=False,
            )
            os.close(write_fd)
            self.assertEqual(blocked.returncode, 0, (args, blocked.returncode, blocked.stderr))

    def _hang_env(self, loaded=ZEN4, node="zen4"):
        self.site.write_cpuinfo(node)
        env = harness.base_env(self.site, loaded, ["R/4.4.2"], gpu="1", gpu_cc="8.0")
        env["TMPDIR"] = self.site.root
        env["SLURM_JOB_ID"] = "6060"
        env["SLURM_STEP_ID"] = "batch"
        env["EESSI_TEST_HANG_PID"] = os.path.join(self.site.root, "hang.pid")
        return env

    def test_hung_gpu_query_is_stopped_and_not_cached(self):
        env = self._hang_env()
        env["EESSI_TEST_GPU_HANG"] = "30"
        path = guard.cache_file_path(env)
        started = time.monotonic()
        proc = harness.run_guard(env, ["--timeout", "1"])
        elapsed = time.monotonic() - started
        try:
            changes, printed = _assert_prolog(proc)
            self.assertLess(elapsed, _stop_limit(1))
            self.assertTrue(changes["EESSI_ARCH_GUARD_FAILED"].endswith("->timeout"))
            self.assertTrue(printed)
            self.assertFalse(os.path.isfile(path))
            pid = int(Path(env["EESSI_TEST_HANG_PID"]).read_text(encoding="ascii"))
            _wait_until_dead(pid)
            self.assertFalse(_pid_alive(pid))
        finally:
            pid_path = Path(env["EESSI_TEST_HANG_PID"])
            if pid_path.is_file():
                try:
                    os.kill(int(pid_path.read_text(encoding="ascii")), signal.SIGKILL)
                except OSError:
                    pass

    def test_hung_reload_is_stopped(self):
        env = self._cpu_env(ZEN4, "zen2")
        env["TMPDIR"] = self.site.root
        env["SLURM_JOB_ID"] = "6161"
        env["EESSI_TEST_LMOD_HANG"] = "30"
        env["EESSI_TEST_HANG_PID"] = os.path.join(self.site.root, "hang.pid")
        started = time.monotonic()
        proc = harness.run_guard(env, ["--timeout", "1"])
        elapsed = time.monotonic() - started
        try:
            changes, _printed = _assert_prolog(proc)
            self.assertLess(elapsed, _stop_limit(1))
            self.assertTrue(changes["EESSI_ARCH_GUARD_FAILED"].endswith("->timeout"))
            self.assertNotIn("EESSI_SOFTWARE_SUBDIR", changes)
            pid = int(Path(env["EESSI_TEST_HANG_PID"]).read_text(encoding="ascii"))
            _wait_until_dead(pid)
            self.assertFalse(_pid_alive(pid))
        finally:
            pid_path = Path(env["EESSI_TEST_HANG_PID"])
            if pid_path.is_file():
                try:
                    os.kill(int(pid_path.read_text(encoding="ascii")), signal.SIGKILL)
                except OSError:
                    pass

    def test_lock_wait_ends_at_the_limit(self):
        env = self._cpu_env(ZEN4, "zen2")
        env["TMPDIR"] = self.site.root
        env["SLURM_JOB_ID"] = "6262"
        path = guard.cache_file_path(env)
        lock_path = path + ".lock"
        fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            started = time.monotonic()
            proc = harness.run_guard(env, ["--timeout", "1"])
            elapsed = time.monotonic() - started
            changes, _printed = _assert_prolog(proc)
            self.assertLess(elapsed, _stop_limit(1))
            self.assertGreaterEqual(elapsed, 0.5)
            self.assertTrue(changes["EESSI_ARCH_GUARD_FAILED"].endswith("->timeout"))
            self.assertFalse(os.path.isfile(path))
            self.assertEqual(harness.log_lines(self.site.log), [])
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def test_sigterm_stops_the_decision(self):
        env = self._hang_env()
        env["EESSI_TEST_GPU_HANG"] = "30"
        proc = subprocess.Popen(
            [sys.executable, "-I", str(harness.GUARD), "--timeout", "30"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
        )
        pid_path = Path(env["EESSI_TEST_HANG_PID"])
        try:
            for _ in range(50):
                if pid_path.is_file():
                    break
                if proc.poll() is not None:
                    break
                time.sleep(0.05)
            self.assertTrue(pid_path.is_file(), proc.stderr)
            proc.send_signal(signal.SIGTERM)
            out, err = proc.communicate(timeout=10)
            self.assertEqual(proc.returncode, 0, err)
            changes, _printed = apply_task_prolog(out)
            self.assertTrue(changes["EESSI_ARCH_GUARD_FAILED"].endswith("->terminated"))
            child = int(pid_path.read_text(encoding="ascii"))
            _wait_until_dead(child)
            self.assertFalse(_pid_alive(child))
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=5)
            if pid_path.is_file():
                try:
                    os.kill(int(pid_path.read_text(encoding="ascii")), signal.SIGKILL)
                except OSError:
                    pass

    def test_limit_does_not_undo_a_cached_decision(self):
        env = self._hang_env(loaded=ZEN4, node="zen2")
        env["EESSI_TEST_GPU_HANG"] = "30"
        path = guard.cache_file_path(env)
        guard._write_cache(
            path,
            guard.Outcome(
                {"SENTINEL": "cached-decision", "EESSI_ARCH_GUARD_RELOADED": "from-cache"},
                "cached",
                "reloaded",
            ),
        )
        before = Path(path).read_bytes()
        started = time.monotonic()
        proc = harness.run_guard(env, ["--timeout", "1"])
        elapsed = time.monotonic() - started
        changes, printed = _assert_prolog(proc)
        self.assertLess(elapsed, 0.8)
        self.assertEqual(changes["SENTINEL"], "cached-decision")
        self.assertEqual(changes["EESSI_ARCH_GUARD_RELOADED"], "from-cache")
        self.assertEqual(printed, ["cached"])
        self.assertEqual(Path(path).read_bytes(), before)
        self.assertEqual(harness.log_lines(self.site.arch_log), [])

    def test_chain_keeps_the_config_timeout(self):
        conf = Path(harness.GUARD).with_name("eessi-arch-guard.conf")
        conf.write_text("timeout = 1\n", encoding="utf-8")
        subprocess.check_call(["sudo", "chown", "root:root", str(conf)])
        subprocess.check_call(["sudo", "chmod", "644", str(conf)])
        ok_path = os.path.join(self.site.root, "chain-ok.sh")
        with open(ok_path, "w", encoding="utf-8") as handle:
            handle.write("#!/bin/sh\necho 'export FROM_CHAIN=yes'\n")
        os.chmod(ok_path, 0o755)
        env = self._hang_env()
        env["EESSI_TEST_GPU_HANG"] = "30"
        try:
            started = time.monotonic()
            proc = harness.run_guard(env, ["--chain", ok_path])
            elapsed = time.monotonic() - started
            changes, _printed = _assert_prolog(proc)
            self.assertLess(elapsed, _stop_limit(1))
            self.assertEqual(changes["FROM_CHAIN"], "yes")
            self.assertTrue(changes["EESSI_ARCH_GUARD_FAILED"].endswith("->timeout"))
        finally:
            subprocess.check_call(["sudo", "rm", "-f", str(conf)])
            pid_path = Path(env["EESSI_TEST_HANG_PID"])
            if pid_path.is_file():
                try:
                    os.kill(int(pid_path.read_text(encoding="ascii")), signal.SIGKILL)
                except OSError:
                    pass

    def test_setsid_grandchild_does_not_hold_the_guard(self):
        env = self._hang_env()
        env["EESSI_TEST_SETSID_HOLD"] = "30"
        started = time.monotonic()
        proc = harness.run_guard(env, ["--timeout", "1"])
        elapsed = time.monotonic() - started
        pid_path = Path(env["EESSI_TEST_HANG_PID"])
        try:
            changes, _printed = _assert_prolog(proc)
            self.assertLess(elapsed, _stop_limit(1))
            self.assertTrue(changes["EESSI_ARCH_GUARD_FAILED"].endswith("->timeout"))
        finally:
            if pid_path.is_file():
                try:
                    os.kill(int(pid_path.read_text(encoding="ascii")), signal.SIGKILL)
                except OSError:
                    pass

    def test_killed_helper_leaves_no_tmp_directory(self):
        before = set(Path("/tmp").glob("eessi-arch-guard-*"))
        env = self._cpu_env(ZEN4, "zen2")
        env["TMPDIR"] = self.site.root
        env["SLURM_JOB_ID"] = "6363"
        env["EESSI_TEST_APPLY_HANG"] = "30"
        started = time.monotonic()
        proc = harness.run_guard(env, ["--timeout", "1"])
        elapsed = time.monotonic() - started
        try:
            changes, _printed = _assert_prolog(proc)
            self.assertLess(elapsed, _stop_limit(1))
            self.assertTrue(changes["EESSI_ARCH_GUARD_FAILED"].endswith("->timeout"))
            after = set(Path("/tmp").glob("eessi-arch-guard-*"))
            self.assertEqual(before, after)
        finally:
            for path in Path("/tmp").glob("eessi-arch-guard-*"):
                if path not in before:
                    shutil.rmtree(path, ignore_errors=True)

    def test_config_timeout_bounds_the_decision(self):
        conf = Path(harness.GUARD).with_name("eessi-arch-guard.conf")
        conf.write_text("timeout = 1\n", encoding="utf-8")
        subprocess.check_call(["sudo", "chown", "root:root", str(conf)])
        subprocess.check_call(["sudo", "chmod", "644", str(conf)])
        env = self._hang_env()
        env["EESSI_TEST_GPU_HANG"] = "30"
        try:
            started = time.monotonic()
            proc = harness.run_guard(env)
            elapsed = time.monotonic() - started
            changes, _printed = _assert_prolog(proc)
            self.assertLess(elapsed, _stop_limit(1))
            self.assertTrue(changes["EESSI_ARCH_GUARD_FAILED"].endswith("->timeout"))
        finally:
            subprocess.check_call(["sudo", "rm", "-f", str(conf)])
            pid_path = Path(env["EESSI_TEST_HANG_PID"])
            if pid_path.is_file():
                try:
                    os.kill(int(pid_path.read_text(encoding="ascii")), signal.SIGKILL)
                except OSError:
                    pass

    def test_intel_a100_uses_the_directory_that_exists(self):
        # open-001: do not bake in either Intel subdirectory. The first
        # candidate with a software directory wins, including when the
        # listed-first tree is absent and the GPU is an A100 (cc 8.0).
        icelake = "x86_64/intel/icelake"
        skylake = "x86_64/intel/skylake_avx512"
        source = harness.GUARD.read_text(encoding="utf-8")
        self.assertNotIn(icelake, source)
        self.assertNotIn(skylake, source)
        self.site.add_cpu(skylake)
        self.site.add_module(skylake, "R/4.4.2")
        self.site.write_cpuinfo(matches="%s:%s" % (icelake, skylake))
        env = harness.base_env(self.site, icelake, ["R/4.4.2"], gpu="1", gpu_cc="8.0")
        proc = harness.run_guard(env)
        changes, _printed = _assert_prolog(proc)
        self.assertEqual(changes["EESSI_SOFTWARE_SUBDIR"], skylake)
        self.assertEqual(changes["EESSI_ACCEL_SUBDIR"], "accel/nvidia/cc80")
        self.assertIn(icelake, changes["EESSI_ARCH_GUARD_RELOADED"])
        self.assertIn(skylake, changes["EESSI_ARCH_GUARD_RELOADED"])
        self.assertIn("accel/nvidia/cc80", changes["EESSI_ARCH_GUARD_RELOADED"])

    def _copy_cpuinfo(self, name):
        source = Path(__file__).resolve().parent / "fixtures" / "cpuinfo" / name
        shutil.copy(source, self.site.cpuinfo)

    def _stub_cpupath(self):
        env = os.environ.copy()
        env["EESSI_PROC_CPUINFO"] = self.site.cpuinfo
        script = os.path.join(self.site.init, "eessi_archdetect.sh")
        proc = subprocess.run(
            ["bash", script, "-a", "cpupath"],
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return proc.stdout.strip()

    def test_kvm_sapphirerapids_without_waitpkg_falls_back(self):
        # The stub still offers sapphirerapids: its flag list matches
        # archdetect and does not require waitpkg. The guard must not.
        spr = "x86_64/intel/sapphirerapids"
        icelake = "x86_64/intel/icelake"
        self.site.add_cpu(spr)
        self.site.add_cpu(icelake)
        self.site.add_module(spr, "R/4.4.2")
        self.site.add_module(icelake, "R/4.4.2")
        self._copy_cpuinfo("sapphirerapids-kvm-no-waitpkg.cpuinfo")
        offered = self._stub_cpupath()
        self.assertTrue(offered.startswith(spr + ":"), offered)
        self.assertIn(icelake, offered)
        env = harness.base_env(self.site, spr, ["R/4.4.2"], gpu="none")
        proc = harness.run_guard(env)
        changes, printed = _assert_prolog(proc)
        self.assertEqual(changes["EESSI_SOFTWARE_SUBDIR"], icelake)
        self.assertNotEqual(changes["EESSI_SOFTWARE_SUBDIR"], spr)
        self.assertEqual(changes["EESSI_SOFTWARE_SUBDIR_OVERRIDE"], icelake)
        self.assertEqual(changes["EESSI_ARCHDETECT_OPTIONS"], icelake)
        self.assertIn(spr, changes["EESSI_ARCH_GUARD_RELOADED"])
        self.assertIn(icelake, changes["EESSI_ARCH_GUARD_RELOADED"])
        self.assertTrue(printed)
        source = harness.GUARD.read_text(encoding="utf-8")
        self.assertNotIn(icelake, source)

    def test_kvm_sapphirerapids_falls_back_to_skylake_when_that_tree_exists(self):
        spr = "x86_64/intel/sapphirerapids"
        skylake = "x86_64/intel/skylake_avx512"
        self.site.add_cpu(spr)
        self.site.add_cpu(skylake)
        self.site.add_module(skylake, "R/4.4.2")
        self._copy_cpuinfo("sapphirerapids-kvm-no-waitpkg.cpuinfo")
        env = harness.base_env(self.site, spr, ["R/4.4.2"], gpu="none")
        proc = harness.run_guard(env)
        changes, _printed = _assert_prolog(proc)
        self.assertEqual(changes["EESSI_SOFTWARE_SUBDIR"], skylake)
        self.assertEqual(changes["EESSI_SOFTWARE_SUBDIR_OVERRIDE"], skylake)
        source = harness.GUARD.read_text(encoding="utf-8")
        self.assertNotIn(skylake, source)

    def test_real_sapphirerapids_keeps_the_target(self):
        spr = "x86_64/intel/sapphirerapids"
        self.site.add_cpu(spr)
        self.site.add_module(spr, "R/4.4.2")
        self._copy_cpuinfo("sapphirerapids-8488C.cpuinfo")
        offered = self._stub_cpupath()
        self.assertTrue(offered.startswith(spr + ":"), offered)
        self.assertIn("x86_64/intel/icelake:", offered)
        env = harness.base_env(self.site, spr, ["R/4.4.2"], gpu="none")
        proc = harness.run_guard(env)
        _assert_prolog(proc)
        self.assertEqual(proc.stdout, "")

    def test_graniterapids_without_waitpkg_falls_back(self):
        spr = "x86_64/intel/sapphirerapids"
        gnr = "x86_64/intel/graniterapids"
        icelake = "x86_64/intel/icelake"
        text = (
            Path(__file__).resolve().parent
            / "fixtures"
            / "cpuinfo"
            / "sapphirerapids-kvm-no-waitpkg.cpuinfo"
        ).read_text(encoding="ascii")
        text = text.replace("model           : 143", "model           : 173", 1)
        with open(self.site.cpuinfo, "w", encoding="ascii") as handle:
            handle.write(text)
        self.site.add_cpu(gnr)
        self.site.add_cpu(spr)
        self.site.add_cpu(icelake)
        self.site.add_module(icelake, "R/4.4.2")
        offered = self._stub_cpupath()
        self.assertTrue(offered.startswith(gnr + ":" + spr + ":"), offered)
        env = harness.base_env(self.site, gnr, ["R/4.4.2"], gpu="none")
        proc = harness.run_guard(env)
        changes, _printed = _assert_prolog(proc)
        self.assertEqual(changes["EESSI_SOFTWARE_SUBDIR"], icelake)
        self.assertEqual(changes["EESSI_SOFTWARE_SUBDIR_OVERRIDE"], icelake)

    def test_help_version_and_shebang(self):
        first = harness.GUARD.read_text(encoding="utf-8").splitlines()[0]
        self.assertEqual(first, "#!/usr/bin/python3 -I")
        proc = harness.run_guard({"PATH": "/usr/bin:/bin"}, ["--version"])
        self.assertEqual(proc.stdout.strip(), guard.__version__)
        proc = harness.run_guard({"PATH": "/usr/bin:/bin"}, ["--help"])
        self.assertIn("--shell", proc.stdout)
        self.assertIn("--restore", proc.stdout)
        self.assertIn("--version", proc.stdout)
        self.assertIn("--chain", proc.stdout)
        self.assertIn("--timeout", proc.stdout)
        self.assertIn("-h", proc.stdout)
        self.assertEqual(proc.returncode, 0)
