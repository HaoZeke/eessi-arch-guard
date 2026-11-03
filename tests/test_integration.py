"""End-to-end runs against a fake EESSI tree, archdetect stub, and LMOD_CMD.

No real Lmod, Slurm, nvidia-smi, or CVMFS. Targets are faked with
EESSI_PROC_CPUINFO and a nvidia-smi stub that is first on PATH.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
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
        self.assertIn("-h", proc.stdout)
        self.assertEqual(proc.returncode, 0)
