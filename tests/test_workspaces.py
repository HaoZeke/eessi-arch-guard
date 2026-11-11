"""Private configured restore workspaces for strict job guards."""

import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import eessi_arch_guard as guard  # noqa: E402


class WorkspaceTests(unittest.TestCase):
    def test_strict_restore_honors_existing_private_tmpdir(self):
        with tempfile.TemporaryDirectory() as root, mock.patch.object(guard, "_STRICT_MODE", True):
            self.assertEqual(guard.restore_work_base({"TMPDIR": root}), os.path.realpath(root))
            self.assertEqual(guard.cache_directory({"TMPDIR": root}), os.path.realpath(root))

    def test_explicit_workspace_has_priority(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as other:
            env = {"EESSI_ARCH_GUARD_WORKDIR": root, "TMPDIR": other}
            self.assertEqual(guard.restore_work_base(env), os.path.realpath(root))

    def test_untrusted_explicit_workspace_is_refused_without_creation(self):
        with tempfile.TemporaryDirectory() as root:
            missing = str(Path(root) / "missing")
            with self.assertRaises(ValueError):
                guard.restore_work_base({"EESSI_ARCH_GUARD_WORKDIR": missing})
            self.assertFalse(Path(missing).exists())
            public = Path(root) / "public"
            public.mkdir(mode=0o755)
            with self.assertRaises(ValueError):
                guard.restore_work_base({"EESSI_ARCH_GUARD_WORKDIR": str(public)})

    def test_explicit_symlink_is_refused(self):
        with tempfile.TemporaryDirectory() as root:
            link = Path(root) / "alias"
            link.symlink_to(root, target_is_directory=True)
            with self.assertRaises(ValueError):
                guard.restore_work_base({"EESSI_ARCH_GUARD_WORKDIR": str(link)})

    def test_existing_state_workspace_handles_missing_job_tmpdir(self):
        with tempfile.TemporaryDirectory() as root, mock.patch.object(guard, "_STRICT_MODE", True):
            state = Path(root) / "state"
            work = state / "eessi-arch-guard"
            work.mkdir(parents=True, mode=0o700)
            env = {"TMPDIR": str(Path(root) / "not-created"), "XDG_STATE_HOME": str(state)}
            self.assertEqual(guard.restore_work_base(env), str(work))
            self.assertFalse(Path(env["TMPDIR"]).exists())

    def test_strict_mode_without_private_workspace_has_no_cache_fallback(self):
        with tempfile.TemporaryDirectory() as root, mock.patch.object(guard, "_STRICT_MODE", True):
            env = {"HOME": root, "TMPDIR": str(Path(root) / "missing")}
            self.assertIsNone(guard.cache_directory(env))
            with self.assertRaises(ValueError):
                guard.restore_work_base(env)
            self.assertFalse((Path(root) / ".local").exists())

    def test_restore_helper_and_its_cleanup_stay_under_selected_parent(self):
        with tempfile.TemporaryDirectory() as root, mock.patch.object(guard, "_STRICT_MODE", True):
            observed = []

            def helper(argv, clean, **kwargs):
                work = Path(clean["EESSI_ARCH_GUARD_WORKDIR"])
                self.assertEqual(work.parent, Path(root))
                self.assertTrue(work.is_dir())
                self.assertEqual(clean["HOME"], str(work))
                self.assertEqual(clean["TMPDIR"], str(work))
                self.assertIn("--strict", argv)
                observed.append(work)
                data = {"ok": True, "env": guard._wire_map({"RESULT": "restored"}), "missing": []}
                return types.SimpleNamespace(
                    returncode=0, stdout=json.dumps(data).encode(), stderr=b""
                )

            with mock.patch.object(guard, "run_captured", side_effect=helper):
                result = guard.call_restore({"TMPDIR": root}, "EESSI/2026.06", ["2026"])
            self.assertTrue(result.ok, result.error)
            self.assertEqual(result.env, {"RESULT": "restored"})
            self.assertEqual(len(observed), 1)
            self.assertFalse(observed[0].exists())
            self.assertTrue(Path(root).is_dir())

    def test_strict_call_does_not_leak_policy_into_default_calls(self):
        with (
            mock.patch.object(guard, "site_config", return_value=({}, None)),
            mock.patch.object(
                guard, "limited_decide", return_value=guard.Outcome({}, None, "noop")
            ),
            mock.patch.object(guard, "write_fd"),
        ):
            self.assertFalse(guard._STRICT_MODE)
            self.assertEqual(guard.main(["--strict"]), 0)
            self.assertFalse(guard._STRICT_MODE)
