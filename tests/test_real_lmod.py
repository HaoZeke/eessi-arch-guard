"""One reload through the Ubuntu Lmod binary, when it is installed.

Skipped when libexec/lmod is absent. This is Lmod 8.6 as packaged by Ubuntu,
not the 9.4.5 sequence from the notes, and not a Slurm job.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import harness  # noqa: E402
from slurm_model import apply_task_prolog  # noqa: E402

LMOD = Path("/usr/share/lmod/lmod/libexec/lmod")
STUB = Path(__file__).resolve().parent / "stubs" / "eessi_archdetect.sh"
ZEN2 = "x86_64/amd/zen2"
ZEN4 = "x86_64/amd/zen4"

LUA = r"""
local init = os.getenv("EESSI_INIT_PREFIX")
local prefix = os.getenv("EESSI_PREFIX")
local line = capture("bash --noprofile --norc " .. init .. "/eessi_archdetect.sh -a cpupath")
local chosen = nil
for cand in string.gmatch(line, "[^:%s]+") do
  local dir = prefix .. "/software/linux/" .. cand .. "/software"
  if isDir(dir) then chosen = cand break end
end
if not chosen then LmodError("no software directory") end
setenv("EESSI_SOFTWARE_SUBDIR", chosen)
setenv("EESSI_VERSION", "2025.06")
setenv("EESSI_ACCEL_SUBDIR", "")
prepend_path("MODULEPATH", prefix .. "/software/linux/" .. chosen .. "/modules/all")
"""


@unittest.skipUnless(LMOD.is_file(), "Lmod libexec is not installed")
class RealLmodTests(unittest.TestCase):
    def test_zen4_loaded_env_reloads_on_zen2(self):
        root = Path(tempfile.mkdtemp(prefix="eessi-arch-guard-lmod-"))
        try:
            for cpu in (ZEN2, ZEN4):
                soft = root / "prefix/software/linux" / cpu / "software/R/4.4.2"
                mod = root / "prefix/software/linux" / cpu / "modules/all/R"
                soft.mkdir(parents=True)
                mod.mkdir(parents=True)
                (mod / "4.4.2.lua").write_text('setenv("EBROOTR", "%s")\n' % soft, encoding="utf-8")
            init = root / "prefix/init"
            init.mkdir(parents=True)
            shutil.copy(STUB, init / "eessi_archdetect.sh")
            os.chmod(init / "eessi_archdetect.sh", 0o755)
            core = root / "core/EESSI"
            core.mkdir(parents=True)
            (core / "2025.06.lua").write_text(LUA, encoding="utf-8")
            cpuinfo = root / "cpuinfo"
            cpuinfo.write_text(
                "eessi_arch_matches: x86_64/amd/zen2:x86_64/generic\n",
                encoding="utf-8",
            )
            env = {
                "PATH": "/usr/bin:/bin",
                "HOME": "/tmp",
                "USER": "tester",
                "LANG": "C",
                "LC_ALL": "C",
                "MODULEPATH": str(root / "core"),
                "EESSI_PREFIX": str(root / "prefix"),
                "EESSI_INIT_PREFIX": str(init),
                "EESSI_PROC_CPUINFO": str(cpuinfo),
                "EESSI_OS_TYPE": "linux",
                "EESSI_SOFTWARE_SUBDIR": ZEN4,
                "EESSI_VERSION": "2025.06",
                "LOADEDMODULES": "EESSI/2025.06:R/4.4.2",
                "LMOD_CMD": str(LMOD),
            }
            proc = harness.run_guard(env)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            changes, printed = apply_task_prolog(proc.stdout)
            self.assertEqual(changes["EESSI_SOFTWARE_SUBDIR"], ZEN2)
            self.assertIn(ZEN2, changes["EBROOTR"])
            self.assertEqual(changes["EESSI_ARCH_GUARD_RELOADED"], "%s->%s" % (ZEN4, ZEN2))
            self.assertIn("_ModuleTable001_", changes)
            self.assertNotIn("\n", changes["_ModuleTable001_"])
            self.assertTrue(printed)
            self.assertIn("reloaded modules", printed[0])
        finally:
            shutil.rmtree(root, ignore_errors=True)
