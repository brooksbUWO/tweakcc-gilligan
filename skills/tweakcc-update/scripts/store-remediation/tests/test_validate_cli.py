"""CLI-surface tests for the validate --batch mode and review-gate flags.

Covers the 02-VALIDATION.md CLI contract row and the two 02-02 through 02-09
task command lines: --batch resolution against the on-disk remediation layout,
the --require <csv> gate-name list, and the nine review-gate flags. Every flag
changes a predicate; invalid evidence returns nonzero naming the offending
value; missing evidence for a required gate returns nonzero naming the gate;
and --help lists every flag. Standard library only, subprocess-driven like the
existing CLI tests.
"""

from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

REMEDIATE = SCRIPTS / "remediate.py"
FIXTURES = Path(__file__).resolve().parent / "fixtures"


def run_cli(*argv) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(REMEDIATE), *argv],
        capture_output=True, text=True,
    )


# --- evidence-bundle builders (write temp fixtures per test) -----------------

def _lane(lane, provider, model_id, digest, rnd, findings, ts, seq, catalog=None):
    return {
        "lane": lane, "provider": provider, "model_id": model_id,
        "revision_digest": digest, "round": rnd, "report_digest": f"rep{seq}",
        "finding_count": findings, "timestamp": ts, "sequence": seq,
        "catalog": catalog if catalog is not None else [model_id],
        "probe_command": "list", "probe_exit_code": 0, "probe_output": "ok",
    }


DIGEST = "c" * 64


def valid_bundle():
    return {
        "revision_digest": DIGEST,
        "lane_reports": [
            _lane("antigravity", "google", "gemini-3.6-flash", DIGEST, 1, 0, 100.0, 10,
                  catalog=["gemini-3.6-flash", "gemini-3.6-pro"]),
            _lane("opencode", "opencode-go", "glm-5.1", DIGEST, 1, 0, 101.0, 11,
                  catalog=["glm-5.1", "deepseek-v4-pro"]),
        ],
        "doctrine_reread": {
            "revision_digest": DIGEST, "doctrine_digest": "d" * 64,
            "reread_timestamp": 200.0, "reread_sequence": 20, "result": "pass",
        },
        "approval": {
            "revision_digest": DIGEST, "approval_timestamp": 300.0,
            "approval_sequence": 30, "date": "2026-08-21",
            "verbatim_response": "approved",
        },
    }


class _BundleCase(unittest.TestCase):
    """Base that writes a per-test evidence bundle under a temp dir."""

    def write_bundle(self, obj) -> Path:
        import tempfile
        d = Path(tempfile.mkdtemp(prefix="rem-ev-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(d, ignore_errors=True))
        p = d / "evidence.json"
        p.write_text(json.dumps(obj), encoding="utf-8")
        return p


# --- --help lists every new flag ---------------------------------------------

class TestHelpListsNewFlags(unittest.TestCase):
    def test_help_lists_batch_and_review_gate_flags(self):
        proc = run_cli("validate", "--help")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        for flag in (
            "--batch",
            "--require",
            "--require-dual-zero-same-round",
            "--require-latest-terminal-review",
            "--require-post-terminal-doctrine-reread",
            "--require-post-reread-approval",
            "--require-live-model-lists",
            "--require-model-usability-probes",
            "--require-approval-binding",
            "--require-phase3-eligible",
            "--exclude-review-family",
            "--exclude-review-provider",
            "--deny-reviewer",
            "--deny-approval",
        ):
            self.assertIn(flag, proc.stdout, f"{flag} missing from validate --help")


# --- empty-batch behavior (plan 02-02 Task 1 against no artifacts) -----------

class TestEmptyBatch(unittest.TestCase):
    def test_empty_batch_fails_named_gate_not_argparse(self):
        import tempfile
        root = Path(tempfile.mkdtemp(prefix="rem-root-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(root, ignore_errors=True))
        # An empty remediation root: the batch dir does not exist at all.
        proc = run_cli(
            "validate", "--batch", "system-reminder",
            "--remediation-root", str(root),
            "--require", "classification,drafts,boolean,writing,doctrine,dual-review",
            "--require-dual-zero-same-round",
            "--require-latest-terminal-review",
            "--require-post-terminal-doctrine-reread",
            "--require-live-model-lists",
            "--require-model-usability-probes",
            "--exclude-review-family", "claude,openai,codex",
            "--exclude-review-provider", "github-copilot",
            "--deny-reviewer", "copilot",
            "--deny-approval",
        )
        # Not an argparse usage error (which prints "usage:" to stderr, exit 2).
        self.assertNotEqual(proc.returncode, 0)
        self.assertNotIn("usage:", proc.stderr.lower())
        self.assertIn("FAIL", proc.stderr)
        # The first missing gate is named.
        self.assertIn("classification", proc.stderr)


# --- --require gate-name list -------------------------------------------------

class TestRequireGateNames(_BundleCase):
    def test_unknown_gate_name_rejected(self):
        ev = self.write_bundle(valid_bundle())
        proc = run_cli("validate", "--evidence", str(ev), "--require", "made-up-gate")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("made-up-gate", proc.stderr)

    def test_dual_review_gate_passes_on_valid_bundle(self):
        ev = self.write_bundle(valid_bundle())
        proc = run_cli("validate", "--evidence", str(ev), "--require", "dual-review,approved")
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_missing_approved_gate_fails_named(self):
        b = valid_bundle()
        del b["approval"]
        ev = self.write_bundle(b)
        proc = run_cli("validate", "--evidence", str(ev), "--require", "approved")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("approved", proc.stderr)

    def test_missing_dual_review_gate_fails_named(self):
        b = valid_bundle()
        b["lane_reports"] = b["lane_reports"][:1]  # only one lane -> no dual-zero
        ev = self.write_bundle(b)
        proc = run_cli("validate", "--evidence", str(ev), "--require", "dual-review")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("dual-review", proc.stderr)


# --- each review-gate flag changes a predicate -------------------------------

class TestReviewGateFlags(_BundleCase):
    def test_require_dual_zero_same_round_pass_and_fail(self):
        ev = self.write_bundle(valid_bundle())
        self.assertEqual(
            run_cli("validate", "--evidence", str(ev), "--require-dual-zero-same-round").returncode,
            0)
        b = valid_bundle()
        b["lane_reports"] = b["lane_reports"][:1]
        ev2 = self.write_bundle(b)
        self.assertNotEqual(
            run_cli("validate", "--evidence", str(ev2), "--require-dual-zero-same-round").returncode,
            0)

    def test_require_approval_binding_pass_and_fail(self):
        ev = self.write_bundle(valid_bundle())
        self.assertEqual(
            run_cli("validate", "--evidence", str(ev), "--require-approval-binding").returncode,
            0, )
        b = valid_bundle()
        b["approval"]["approval_timestamp"] = 100.0  # before reread -> unbound
        b["approval"]["approval_sequence"] = 5
        ev2 = self.write_bundle(b)
        self.assertNotEqual(
            run_cli("validate", "--evidence", str(ev2), "--require-approval-binding").returncode,
            0)

    def test_require_live_model_lists_needs_nonempty_catalog(self):
        ev = self.write_bundle(valid_bundle())
        self.assertEqual(
            run_cli("validate", "--evidence", str(ev), "--require-live-model-lists").returncode,
            0)
        b = valid_bundle()
        for lr in b["lane_reports"]:
            lr["catalog"] = []  # no recorded catalog
        ev2 = self.write_bundle(b)
        proc = run_cli("validate", "--evidence", str(ev2), "--require-live-model-lists")
        self.assertNotEqual(proc.returncode, 0)

    def test_require_model_usability_probes_needs_zero_exit(self):
        ev = self.write_bundle(valid_bundle())
        self.assertEqual(
            run_cli("validate", "--evidence", str(ev), "--require-model-usability-probes").returncode,
            0)
        b = valid_bundle()
        b["lane_reports"][0]["probe_exit_code"] = 1
        ev2 = self.write_bundle(b)
        proc = run_cli("validate", "--evidence", str(ev2), "--require-model-usability-probes")
        self.assertNotEqual(proc.returncode, 0)

    def test_deny_approval_fails_when_approval_present(self):
        ev = self.write_bundle(valid_bundle())
        proc = run_cli("validate", "--evidence", str(ev), "--deny-approval")
        self.assertNotEqual(proc.returncode, 0)
        b = valid_bundle()
        del b["approval"]
        ev2 = self.write_bundle(b)
        self.assertEqual(
            run_cli("validate", "--evidence", str(ev2), "--deny-approval").returncode,
            0)


# --- exclude/deny reviewer flags name the offending value --------------------

class TestExcludeDenyFlags(_BundleCase):
    def test_exclude_review_provider_rejects_named_provider(self):
        proc = run_cli(
            "validate", "--evidence", str(FIXTURES / "evidence-copilot-lane.json"),
            "--exclude-review-provider", "github-copilot",
        )
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("github-copilot", proc.stderr)

    def test_exclude_review_family_rejects_named_family(self):
        b = valid_bundle()
        b["lane_reports"][1]["model_id"] = "claude-4-opus"
        ev = self.write_bundle(b)
        proc = run_cli(
            "validate", "--evidence", str(ev),
            "--exclude-review-family", "claude,openai,codex",
        )
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("claude", proc.stderr)

    def test_deny_reviewer_rejects_named_lane(self):
        b = valid_bundle()
        b["lane_reports"].append(
            _lane("copilot", "github-copilot", "gpt-5", DIGEST, 1, 0, 102.0, 12))
        ev = self.write_bundle(b)
        proc = run_cli("validate", "--evidence", str(ev), "--deny-reviewer", "copilot")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("copilot", proc.stderr)

    def test_clean_bundle_passes_all_exclude_deny_flags(self):
        ev = self.write_bundle(valid_bundle())
        proc = run_cli(
            "validate", "--evidence", str(ev),
            "--exclude-review-family", "claude,openai,codex",
            "--exclude-review-provider", "github-copilot",
            "--deny-reviewer", "copilot",
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)


if __name__ == "__main__":
    unittest.main()
