"""Numeric year frontends in the validated loaded-module list."""

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import eessi_arch_guard as guard  # noqa: E402
import harness  # noqa: E402
from slurm_model import apply_task_prolog  # noqa: E402

LMOD = Path("/usr/share/lmod/lmod/libexec/lmod")
ZEN2 = "x86_64/amd/zen2"
ZEN4 = "x86_64/amd/zen4"


class YearModuleTests(unittest.TestCase):
    def test_only_four_ascii_digits_extend_unversioned_grammar(self):
        for name in ("2026", "1999"):
            self.assertTrue(guard.module_name_ok(name), name)
        for name in (
            "GCC",
            "EESSI",
            "202",
            "20261",
            "year2026",
            "\uff12\uff10\uff12\uff16",
            "2026/",
            "2026//x",
            "/2026",
        ):
            self.assertFalse(guard.module_name_ok(name), name)
        for name in ("GCC/15.2.0", "Compiler/GCCcore/15.2.0"):
            self.assertTrue(guard.module_name_ok(name), name)

    def test_year_keeps_loaded_module_order(self):
        result = guard.parse_loaded_modules({"LOADEDMODULES": "EESSI/2026.06:2026:GCC/15.2.0"})
        self.assertFalse(result.error)
        self.assertEqual(result.eessi, "EESSI/2026.06")
        self.assertEqual(result.others, ("2026", "GCC/15.2.0"))

    @unittest.skipUnless(LMOD.is_file(), "Lmod libexec is not installed")
    def test_real_lmod_restores_year_and_versioned_module_on_new_cpu(self):
        with tempfile.TemporaryDirectory(prefix="eessi-year-module-") as temporary:
            root = Path(temporary)
            for cpu in (ZEN2, ZEN4):
                software = root / "prefix/software/linux" / cpu / "software/R/4.4.2"
                modules = root / "prefix/software/linux" / cpu / "modules/all/R"
                software.mkdir(parents=True)
                modules.mkdir(parents=True)
                (modules / "4.4.2.lua").write_text('setenv("EBROOTR", "%s")\n' % software)
            init = root / "prefix/init"
            init.mkdir(parents=True)
            shutil.copy(
                Path(__file__).parent / "stubs/eessi_archdetect.sh", init / "eessi_archdetect.sh"
            )
            (init / "eessi_archdetect.sh").chmod(0o755)
            core = root / "core/EESSI"
            core.mkdir(parents=True)
            (core / "2025.06.lua").write_text(
                'local prefix = os.getenv("EESSI_PREFIX")\n'
                'local line = capture("bash --noprofile --norc " .. os.getenv("EESSI_INIT_PREFIX")\n'
                '  .. "/eessi_archdetect.sh -a cpupath")\n'
                "local chosen = nil\n"
                'for path in string.gmatch(line, "[^:%s]+") do\n'
                '  if isDir(prefix .. "/software/linux/" .. path .. "/software") then\n'
                '    chosen = path; break\n'
                '  end\n'
                "end\n"
                'if not chosen then LmodError("no software directory") end\n'
                'setenv("EESSI_SOFTWARE_SUBDIR", chosen)\n'
                'setenv("EESSI_VERSION", "2025.06")\n'
                'setenv("EESSI_ACCEL_SUBDIR", "")\n'
                'prepend_path("MODULEPATH", prefix .. "/software/linux/" .. chosen\n'
                '  .. "/modules/all")\n'
            )
            (root / "core/2026.lua").write_text(
                'setenv("ARCH_GUARD_YEAR_TARGET", os.getenv("EESSI_SOFTWARE_SUBDIR"))\n'
            )
            cpuinfo = root / "cpuinfo"
            cpuinfo.write_text("eessi_arch_matches: x86_64/amd/zen2:x86_64/generic\n")
            env = {
                "PATH": "/usr/bin:/bin",
                "HOME": str(root),
                "TMPDIR": str(root),
                "LANG": "C",
                "LC_ALL": "C",
                "MODULEPATH": str(root / "core"),
                "EESSI_PREFIX": str(root / "prefix"),
                "EESSI_INIT_PREFIX": str(init),
                "EESSI_PROC_CPUINFO": str(cpuinfo),
                "EESSI_OS_TYPE": "linux",
                "EESSI_SOFTWARE_SUBDIR": ZEN4,
                "EESSI_VERSION": "2025.06",
                "LOADEDMODULES": "EESSI/2025.06:2026:R/4.4.2",
                "LMOD_CMD": str(LMOD),
            }
            result = harness.run_guard(env, ["--strict"])
            self.assertEqual(result.returncode, 0, result.stderr)
            changes, _printed = apply_task_prolog(result.stdout)
            self.assertEqual(changes["EESSI_SOFTWARE_SUBDIR"], ZEN2)
            self.assertEqual(changes["ARCH_GUARD_YEAR_TARGET"], ZEN2)
            self.assertIn(ZEN2, changes["EBROOTR"])
            self.assertIn("2026", changes["LOADEDMODULES"].split(":"))
            self.assertNotIn("EESSI_ARCH_GUARD_FAILED", changes)
