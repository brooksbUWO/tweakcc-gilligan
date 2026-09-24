"""Tracer test: one fixture prompt through backup, immutable r0001, denial.

Proves the end-to-end fixture path (Task 1): a verified backup precedes revision
creation, r0001 is immutable, the record carries complete before/after text and
the exact ordered placeholder list, and eligibility is fail-closed with no
review/reread/approval evidence.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent
FIXTURES = HERE / "fixtures"
REMEDIATE = SCRIPTS / "remediate.py"


def run_tracer(out_root: Path, backup_root: Path, filename: str) -> subprocess.CompletedProcess:
    # Run from a temp cwd so the locked backup root resolves under it.
    return subprocess.run(
        [
            sys.executable, str(REMEDIATE), "tracer",
            "--queue", str(FIXTURES / "queue.csv"),
            "--prompts-dir", str(FIXTURES / "prompts"),
            "--out", str(out_root),
            "--batch", "fixture-batch",
            "--filename", filename,
        ],
        cwd=str(backup_root),
        capture_output=True, text=True,
    )


class TestTracer(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="tracer-"))
        self.out = self.tmp / "remediation"
        self.filename = "fixture-defective.md"

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_backup_verified_before_revision(self) -> None:
        proc = run_tracer(self.out, self.tmp, self.filename)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        result = json.loads(proc.stdout)
        # Verified source == backup digest before revision creation.
        self.assertEqual(result["source_digest"], result["backup_digest"])
        self.assertTrue((self.out / "fixture-batch" / "r0001").is_dir())
        # Backup snapshot actually exists under the locked backup root.
        backup_dir = Path(result["backup_dir"])
        self.assertTrue(backup_dir.is_dir())
        self.assertTrue((backup_dir / self.filename).is_file())

    def test_immutable_r0001(self) -> None:
        first = run_tracer(self.out, self.tmp, self.filename)
        self.assertEqual(first.returncode, 0, first.stderr)
        sealed = json.loads(first.stdout)["revision_digest"]
        # A second r0001 creation attempt must fail nonzero and not mutate tree.
        second = run_tracer(self.out, self.tmp, self.filename)
        self.assertNotEqual(second.returncode, 0)
        gate = json.loads((self.out / "fixture-batch" / "r0001" / "gate.json").read_text())
        self.assertEqual(gate["revision_digest"], sealed)

    def test_record_complete_and_placeholders(self) -> None:
        proc = run_tracer(self.out, self.tmp, self.filename)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        rev = self.out / "fixture-batch" / "r0001"
        record = json.loads((rev / "records" / (self.filename + ".json")).read_text())
        # Exact ordered placeholder list from the fixture body.
        self.assertEqual(record["ordered_placeholders"], ["MAX_LINES"])
        # Complete before/after text preserved on disk.
        before = (rev / "prompts" / "before" / self.filename).read_text()
        after = (rev / "prompts" / "after" / self.filename).read_text()
        self.assertIn("${MAX_LINES}", before)
        self.assertIn("${MAX_LINES}", after)
        self.assertTrue(len(after) > 0 and len(before) > 0)

    def test_eligibility_fail_closed(self) -> None:
        proc = run_tracer(self.out, self.tmp, self.filename)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        result = json.loads(proc.stdout)
        self.assertFalse(result["phase3_eligible"])
        gate = json.loads((self.out / "fixture-batch" / "r0001" / "gate.json").read_text())
        self.assertFalse(gate["phase3_eligible"])
        self.assertFalse(gate["dual_zero_same_round"])


if __name__ == "__main__":
    unittest.main()
