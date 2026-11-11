"""Opt-in strict status at the public shell and task-prolog interfaces."""
import os
from pathlib import Path
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import eessi_arch_guard as guard  # noqa: E402


class StrictStatusTests(unittest.TestCase):
    def run_main(self, flags, outcome=None, error=None):
        with mock.patch.dict(os.environ, {}, clear=True), mock.patch.object(
            guard, "site_config", return_value=({}, None)
        ), mock.patch.object(guard, "limited_decide", return_value=outcome, side_effect=error), mock.patch.object(
            guard, "write_fd"
        ) as output:
            code = guard.main(flags)
        return code, b"".join(call.args[1] for call in output.call_args_list if call.args[0] == 1)

    def test_failed_decision_has_strict_status_in_each_output_mode(self):
        outcome = guard.Outcome({"EESSI_ARCH_GUARD_FAILED": "old->restore-failed"}, "refused", "failed")
        for mode in ([], ["--shell"]):
            with self.subTest(mode=mode):
                code, text = self.run_main(["--strict", *mode], outcome)
                self.assertEqual(code, 78)
                self.assertIn(b"EESSI_ARCH_GUARD_FAILED", text)

    def test_default_failed_decision_remains_fail_open(self):
        outcome = guard.Outcome({"EESSI_ARCH_GUARD_FAILED": "old->restore-failed"}, "refused", "failed")
        for mode in ([], ["--shell"]):
            with self.subTest(mode=mode):
                code, text = self.run_main(mode, outcome)
                self.assertEqual(code, 0)
                self.assertIn(b"EESSI_ARCH_GUARD_FAILED", text)

    def test_unexpected_decision_exception_is_strict_in_each_output_mode(self):
        for mode in ([], ["--shell"]):
            with self.subTest(mode=mode):
                code, _text = self.run_main(["--strict", *mode], error=RuntimeError("decision failed"))
                self.assertEqual(code, 78)

    def test_unexpected_decision_exception_keeps_default_status(self):
        code, _text = self.run_main([], error=RuntimeError("decision failed"))
        self.assertEqual(code, 0)

    def test_outer_startup_exception_is_strict(self):
        with mock.patch.object(guard, "site_config", side_effect=RuntimeError("startup failed")), mock.patch.object(
            guard, "write_fd"
        ):
            self.assertEqual(guard.main(["--strict", "--shell"]), 78)
            self.assertEqual(guard.main(["--shell"]), 0)

    def test_interrupted_decision_is_strict(self):
        code, _text = self.run_main(["--strict", "--shell"], error=KeyboardInterrupt())
        self.assertEqual(code, 78)

    def test_successful_and_intentional_noop_decisions_remain_zero(self):
        for kind in ("skip", "noop", "reloaded"):
            with self.subTest(kind=kind):
                code, _text = self.run_main(["--strict"], guard.Outcome({}, None, kind))
                self.assertEqual(code, 0)

    def test_chained_prolog_failure_preserves_its_status(self):
        with mock.patch.dict(os.environ, {}, clear=True), mock.patch.object(
            guard, "site_config", return_value=({}, None)
        ), mock.patch.object(guard, "_chain_path_ok", return_value=True), mock.patch.object(
            guard, "run_chain", return_value=(17, b"print chain stopped\n", b"")
        ), mock.patch.object(guard, "limited_decide") as decision, mock.patch.object(guard, "write_fd"):
            self.assertEqual(guard.main(["--strict", "--chain", "/site/user-prolog"]), 17)
            decision.assert_not_called()
