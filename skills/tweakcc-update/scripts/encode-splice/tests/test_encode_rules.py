import importlib.util
import json
import sys
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "encode_rules.py"


def load_module():
    spec = importlib.util.spec_from_file_location("encode_rules", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class RuleForTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.encoder = load_module()

    def record(self, disposition="supersede", placeholders=None):
        return {
            "filename": "sample.md",
            "existing_override_disposition": disposition,
            "ordered_placeholders": placeholders or [],
        }

    def test_retain_returns_none(self):
        self.assertIsNone(self.encoder.rule_for(self.record("retain"), b"old", b"new"))

    def test_equal_bytes_returns_none(self):
        self.assertIsNone(self.encoder.rule_for(self.record(), b"same", b"same"))

    def test_changed_bytes_return_rule(self):
        rule = self.encoder.rule_for(self.record(), b"old", b"new")
        self.assertEqual((rule.stock, rule.unnerf), ("old", "new"))

    def test_placeholder_mismatch_fails(self):
        with self.assertRaises(ValueError):
            self.encoder.rule_for(self.record(placeholders=["EXPECTED"]), b"${ACTUAL} old", b"${ACTUAL} new")

    def test_containment_fails(self):
        with self.assertRaises(ValueError):
            self.encoder.rule_for(self.record(), b"old", b"old plus")

    def test_nonapproved_batch_fails(self):
        root = Path(__file__).resolve().parent / ".nonapproved-fixture"
        root.mkdir(exist_ok=True)
        path = root / "approval.json"
        path.write_text(json.dumps({"verbatim_response": "Approved"}))
        try:
            with self.assertRaises(SystemExit):
                self.encoder.load_approval(root)
        finally:
            path.unlink()
            root.rmdir()


class TerminationContractTests(unittest.TestCase):
    """Black-box subprocess tests for the deterministic-termination guard.

    Per recipe-skill-script-hardening-1.0.0.md [R001] Step 5.
    """

    def run_cli(self, *args, timeout=60):
        import subprocess
        return subprocess.run(
            [sys.executable, str(MODULE_PATH), *args],
            capture_output=True, text=True, timeout=timeout,
        )

    def test_kill_path_exits_3(self):
        import time
        t0 = time.monotonic()
        r = self.run_cli("--batch", "system-reminder",
                         "--watchdog-probe", "10", "--max-seconds", "1", timeout=15)
        elapsed = time.monotonic() - t0
        self.assertEqual(r.returncode, 3)
        self.assertLess(elapsed, 5.0)

    def test_zero_max_seconds_exits_2(self):
        r = self.run_cli("--batch", "system-reminder", "--max-seconds", "0")
        self.assertEqual(r.returncode, 2)
        self.assertTrue(r.stderr.strip())

    def test_negative_probe_exits_2(self):
        r = self.run_cli("--batch", "system-reminder", "--watchdog-probe", "-1")
        self.assertEqual(r.returncode, 2)
        self.assertTrue(r.stderr.strip())

    def test_help_mentions_both_flags(self):
        r = self.run_cli("--help")
        self.assertEqual(r.returncode, 0)
        self.assertIn("--max-seconds", r.stdout)
        self.assertIn("--watchdog-probe", r.stdout)

    def test_normal_run_untouched_at_defaults(self):
        r = self.run_cli("--batch", "system-reminder")
        self.assertEqual(r.returncode, 0)
        self.assertIn('"rules_written": 62', r.stdout)


if __name__ == "__main__":
    unittest.main()
