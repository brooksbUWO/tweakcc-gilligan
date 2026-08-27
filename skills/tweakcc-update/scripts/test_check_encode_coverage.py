#!/usr/bin/env python3
"""Black-box termination-contract tests for check_encode_coverage.py.

Pins the hardening guarantee (recipe-skill-script-hardening-1.0.0 Step 5):
the kill path fires exit 3 under the ceiling, range validation exits 2,
--help names both flags. Subprocess-only, stdlib unittest.
"""

import subprocess
import sys
import time
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent / "check_encode_coverage.py"


def run(args, timeout=60):
    return subprocess.run([sys.executable, str(SCRIPT), *args],
                          capture_output=True, text=True, timeout=timeout)


class TerminationContract(unittest.TestCase):
    def test_kill_path_exits_3_under_ceiling(self):
        t0 = time.monotonic()
        r = run(["--watchdog-probe", "10", "--max-seconds", "1"])
        elapsed = time.monotonic() - t0
        self.assertEqual(r.returncode, 3)
        self.assertLess(elapsed, 5.0)

    def test_zero_max_seconds_exits_2(self):
        r = run(["--max-seconds", "0"])
        self.assertEqual(r.returncode, 2)
        self.assertTrue(r.stderr.strip())

    def test_negative_probe_exits_2(self):
        r = run(["--watchdog-probe", "-1"])
        self.assertEqual(r.returncode, 2)
        self.assertTrue(r.stderr.strip())

    def test_help_names_both_flags(self):
        r = run(["--help"])
        self.assertEqual(r.returncode, 0)
        self.assertIn("--max-seconds", r.stdout)
        self.assertIn("--watchdog-probe", r.stdout)


if __name__ == "__main__":
    unittest.main()
