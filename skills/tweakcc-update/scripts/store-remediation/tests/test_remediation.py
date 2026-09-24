"""Schema and fail-closed reducer tests (Task 2) covering REM-01..REM-05.

Exercises canonical vocabularies, clean derivation from six negative checks,
defective-record completeness, reviewer eligibility per lane (AntiGravity =>
Gemini; OpenCode => not Claude/OpenAI/Codex families and not any GitHub Copilot
provider; Copilot reviewer rejected; Ask-Claude not a Phase 2 lane), same-round
dual-zero, and the ordered terminal-review -> doctrine-reread -> approval chain
with strict monotonic sequence and timestamp and later-review invalidation.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import schema  # noqa: E402
import state  # noqa: E402
from schema import (  # noqa: E402
    DEFECT_TAGS,
    OVERRIDE_DISPOSITIONS,
    APPLY_STATES,
    SchemaError,
    derive_clean,
    ordered_placeholders,
    require_same_placeholders,
    validate_prompt_record,
)
from state import (  # noqa: E402
    Approval,
    DoctrineReread,
    LaneReport,
    TerminalReview,
    lane_reviewer_eligible,
    reduce_gate,
)


# --- helpers -----------------------------------------------------------------

def six_checks(positive: set[str] | None = None) -> list[dict]:
    positive = positive or set()
    return [
        {"defect": d, "result": "positive" if d in positive else "negative",
         "reasoning": f"check for {d}"}
        for d in DEFECT_TAGS
    ]


def clean_record() -> dict:
    return {
        "filename": "fixture-clean.md",
        "class_checks": six_checks(),
        "clean": True,
        "ordered_placeholders": [],
        "existing_override_disposition": "retain",
    }


def defective_record() -> dict:
    return {
        "filename": "fixture-defective.md",
        "class_checks": six_checks({"Over-constraint"}),
        "clean": False,
        "defect_tags": ["Over-constraint"],
        "before": {"path": "prompts/before/x.md", "sha256": "a" * 64},
        "after": {"path": "prompts/after/x.md", "sha256": "b" * 64},
        "ordered_placeholders": ["MAX_LINES"],
        "doctrine_map": [{"change": "lift cap", "doctrine": "Over-constraint"}],
        "existing_override_disposition": "supersede",
        "confidence": "high",
    }


def lane(lane_name, provider, model_id, digest, rnd, findings, ts, seq, catalog=None):
    return LaneReport(
        lane=lane_name, provider=provider, model_id=model_id,
        revision_digest=digest, round=rnd, report_digest=f"rep{seq}",
        finding_count=findings, timestamp=ts, sequence=seq,
        catalog=tuple(catalog if catalog is not None else [model_id]),
        probe_command="list", probe_exit_code=0, probe_output="ok",
    )


def dual_zero_reports(digest, rnd=1, base_ts=100.0, base_seq=10):
    return [
        lane("antigravity", "google", "gemini-3.6-flash", digest, rnd, 0, base_ts, base_seq),
        lane("opencode", "opencode-go", "glm-5.1", digest, rnd, 0, base_ts + 1, base_seq + 1),
    ]


DIGEST = "c" * 64


# --- REM-01 / REM-02: schema + canonical values ------------------------------

class TestCanonicalValues(unittest.TestCase):
    def test_verbatim_defect_tags(self):
        self.assertEqual(DEFECT_TAGS, (
            "Over-constraint", "Register mismatch", "Token economy",
            "Contradictory rules", "Silent failure", "Process/artifact split",
        ))

    def test_dispositions_and_apply_states(self):
        self.assertEqual(OVERRIDE_DISPOSITIONS, ("retain", "merge", "supersede", "reject"))
        self.assertEqual(APPLY_STATES, ("applied", "skipped", "failed", "missing", "normalized"))


class TestClassificationCoverage(unittest.TestCase):
    def test_clean_requires_all_six_negative(self):
        self.assertTrue(derive_clean(six_checks()))
        self.assertFalse(derive_clean(six_checks({"Silent failure"})))

    def test_clean_record_valid(self):
        validate_prompt_record(clean_record())

    def test_clean_flag_must_match_checks(self):
        rec = clean_record()
        rec["class_checks"] = six_checks({"Token economy"})  # a positive check
        with self.assertRaises(SchemaError):
            validate_prompt_record(rec)

    def test_class_checks_must_cover_all_six_in_order(self):
        rec = clean_record()
        rec["class_checks"] = six_checks()[:5]
        with self.assertRaises(SchemaError):
            validate_prompt_record(rec)

    def test_unknown_field_rejected(self):
        rec = clean_record()
        rec["surprise"] = 1
        with self.assertRaises(SchemaError):
            validate_prompt_record(rec)

    def test_unknown_defect_value_rejected(self):
        rec = clean_record()
        rec["class_checks"][0]["defect"] = "Made up"
        with self.assertRaises(SchemaError):
            validate_prompt_record(rec)


class TestDraftRecord(unittest.TestCase):
    def test_defective_record_valid(self):
        validate_prompt_record(defective_record())

    def test_defective_requires_before_after(self):
        rec = defective_record()
        del rec["after"]
        with self.assertRaises(SchemaError):
            validate_prompt_record(rec)

    def test_bad_digest_rejected(self):
        rec = defective_record()
        rec["before"]["sha256"] = "notahash"
        with self.assertRaises(SchemaError):
            validate_prompt_record(rec)

    def test_unknown_disposition_rejected(self):
        rec = defective_record()
        rec["existing_override_disposition"] = "delete"
        with self.assertRaises(SchemaError):
            validate_prompt_record(rec)

    def test_defective_requires_confidence(self):
        rec = defective_record()
        del rec["confidence"]
        with self.assertRaises(SchemaError):
            validate_prompt_record(rec)

    def test_filename_traversal_rejected(self):
        rec = defective_record()
        rec["filename"] = "../escape.md"
        with self.assertRaises(SchemaError):
            validate_prompt_record(rec)


class TestPlaceholders(unittest.TestCase):
    def test_ordered_extraction(self):
        self.assertEqual(ordered_placeholders("a ${X} b ${Y}"), ["X", "Y"])

    def test_equal_sequence_ok(self):
        require_same_placeholders("x ${A} y ${B}", "z ${A} w ${B}")

    def test_reordered_rejected(self):
        with self.assertRaises(SchemaError):
            require_same_placeholders("${A} ${B}", "${B} ${A}")

    def test_dropped_rejected(self):
        with self.assertRaises(SchemaError):
            require_same_placeholders("${A} ${B}", "${A}")


# --- REM-04: reviewer eligibility --------------------------------------------

class TestReviewerEligibility(unittest.TestCase):
    def test_antigravity_requires_gemini(self):
        self.assertTrue(lane_reviewer_eligible("antigravity", "google", "gemini-3.6-flash"))
        self.assertFalse(lane_reviewer_eligible("antigravity", "google", "llama-3"))

    def test_opencode_rejects_claude_openai_codex(self):
        for bad in ("claude-4-opus", "gpt-5.6", "codex-mini", "o3-pro"):
            self.assertFalse(
                lane_reviewer_eligible("opencode", "opencode-go", bad), bad)

    def test_opencode_rejects_copilot_provider(self):
        self.assertFalse(lane_reviewer_eligible("opencode", "github-copilot", "some-model"))

    def test_opencode_accepts_permitted(self):
        self.assertTrue(lane_reviewer_eligible("opencode", "opencode-go", "glm-5.1"))

    def test_copilot_lane_rejected(self):
        self.assertFalse(lane_reviewer_eligible("copilot", "github-copilot", "gpt-5"))

    def test_ask_claude_not_a_phase2_lane(self):
        self.assertFalse(lane_reviewer_eligible("ask-claude", "anthropic", "claude-4"))

    def test_selected_id_must_be_in_catalog_once(self):
        r = lane("antigravity", "google", "gemini-3.6", DIGEST, 1, 0, 1.0, 1,
                 catalog=["gemini-3.6", "gemini-3.6"])  # duplicate
        self.assertFalse(r.eligible())

    def test_probe_nonzero_disqualifies(self):
        r = lane("opencode", "opencode-go", "glm-5.1", DIGEST, 1, 0, 1.0, 1)
        r.probe_exit_code = 1
        self.assertFalse(r.eligible())


# --- REM-04: same-round dual-zero and terminal review ------------------------

class TestDualZero(unittest.TestCase):
    def test_same_round_dual_zero_accepts(self):
        gs = reduce_gate(
            lane_reports=dual_zero_reports(DIGEST),
            revision_digest=DIGEST,
            doctrine_reread=DoctrineReread(DIGEST, "d" * 64, 200.0, 20, "pass"),
            approval=Approval(DIGEST, 300.0, 30, "2026-08-21", "approved"),
        )
        self.assertTrue(gs.dual_zero_same_round)
        self.assertTrue(gs.phase3_eligible)

    def test_zeros_from_different_rounds_do_not_terminate(self):
        reports = [
            lane("antigravity", "google", "gemini-3.6", DIGEST, 1, 0, 100.0, 10),
            lane("opencode", "opencode-go", "glm-5.1", DIGEST, 2, 0, 105.0, 12),
        ]
        gs = reduce_gate(lane_reports=reports, revision_digest=DIGEST)
        self.assertFalse(gs.dual_zero_same_round)
        self.assertFalse(gs.phase3_eligible)

    def test_a_finding_starts_a_new_round(self):
        reports = dual_zero_reports(DIGEST, rnd=1)
        # A later round with a finding invalidates the terminal claim.
        reports.append(lane("antigravity", "google", "gemini-3.6", DIGEST, 2, 3, 150.0, 15))
        gs = reduce_gate(
            lane_reports=reports, revision_digest=DIGEST,
            doctrine_reread=DoctrineReread(DIGEST, "d" * 64, 200.0, 20, "pass"),
            approval=Approval(DIGEST, 300.0, 30, "2026-08-21", "approved"),
        )
        self.assertFalse(gs.latest_terminal_review_valid)
        self.assertFalse(gs.phase3_eligible)

    def test_rejected_evidence_never_advances(self):
        # Copilot lane report (ineligible) cannot form a dual-zero.
        reports = [
            lane("antigravity", "google", "gemini-3.6", DIGEST, 1, 0, 100.0, 10),
            lane("copilot", "github-copilot", "gpt-5", DIGEST, 1, 0, 101.0, 11),
        ]
        gs = reduce_gate(lane_reports=reports, revision_digest=DIGEST)
        self.assertFalse(gs.dual_zero_same_round)


# --- REM-04: ordered doctrine-reread predicate -------------------------------

class TestDoctrineReread(unittest.TestCase):
    def base_reports(self):
        return dual_zero_reports(DIGEST)  # terminal dual-zero at ts~100-101, seq 10-11

    def test_first_round_dual_zero_without_reread_denied(self):
        gs = reduce_gate(lane_reports=self.base_reports(), revision_digest=DIGEST)
        self.assertTrue(gs.dual_zero_same_round)
        self.assertFalse(gs.post_terminal_reread_valid)
        self.assertFalse(gs.phase3_eligible)

    def test_reread_before_terminal_denied(self):
        gs = reduce_gate(
            lane_reports=self.base_reports(), revision_digest=DIGEST,
            doctrine_reread=DoctrineReread(DIGEST, "d" * 64, 50.0, 5, "pass"),
        )
        self.assertFalse(gs.post_terminal_reread_valid)

    def test_reread_from_another_revision_denied(self):
        gs = reduce_gate(
            lane_reports=self.base_reports(), revision_digest=DIGEST,
            doctrine_reread=DoctrineReread("e" * 64, "d" * 64, 200.0, 20, "pass"),
        )
        self.assertFalse(gs.post_terminal_reread_valid)

    def test_equal_sequence_denied(self):
        gs = reduce_gate(
            lane_reports=self.base_reports(), revision_digest=DIGEST,
            doctrine_reread=DoctrineReread(DIGEST, "d" * 64, 200.0, 11, "pass"),
        )
        self.assertFalse(gs.post_terminal_reread_valid)

    def test_reversed_timestamp_denied(self):
        gs = reduce_gate(
            lane_reports=self.base_reports(), revision_digest=DIGEST,
            doctrine_reread=DoctrineReread(DIGEST, "d" * 64, 99.0, 20, "pass"),
        )
        self.assertFalse(gs.post_terminal_reread_valid)

    def test_one_valid_post_terminal_reread_accepts(self):
        gs = reduce_gate(
            lane_reports=self.base_reports(), revision_digest=DIGEST,
            doctrine_reread=DoctrineReread(DIGEST, "d" * 64, 200.0, 20, "pass"),
        )
        self.assertTrue(gs.post_terminal_reread_valid)

    def test_later_nonterminal_review_invalidates_reread(self):
        reports = self.base_reports()
        # a valid reread exists, but a later review event on same revision arrives
        reports.append(lane("opencode", "opencode-go", "glm-5.1", DIGEST, 2, 1, 250.0, 25))
        gs = reduce_gate(
            lane_reports=reports, revision_digest=DIGEST,
            doctrine_reread=DoctrineReread(DIGEST, "d" * 64, 200.0, 20, "pass"),
            approval=Approval(DIGEST, 300.0, 30, "2026-08-21", "approved"),
        )
        self.assertFalse(gs.latest_terminal_review_valid)
        self.assertFalse(gs.post_terminal_reread_valid)
        self.assertFalse(gs.phase3_eligible)


# --- REM-05: ordered approval predicate --------------------------------------

class TestApproval(unittest.TestCase):
    def reread(self):
        return DoctrineReread(DIGEST, "d" * 64, 200.0, 20, "pass")

    def test_missing_approval_denied(self):
        gs = reduce_gate(
            lane_reports=dual_zero_reports(DIGEST), revision_digest=DIGEST,
            doctrine_reread=self.reread(),
        )
        self.assertFalse(gs.post_reread_approval_valid)

    def test_approval_before_reread_denied(self):
        gs = reduce_gate(
            lane_reports=dual_zero_reports(DIGEST), revision_digest=DIGEST,
            doctrine_reread=self.reread(),
            approval=Approval(DIGEST, 150.0, 15, "2026-08-21", "approved"),
        )
        self.assertFalse(gs.post_reread_approval_valid)

    def test_equal_sequence_denied(self):
        gs = reduce_gate(
            lane_reports=dual_zero_reports(DIGEST), revision_digest=DIGEST,
            doctrine_reread=self.reread(),
            approval=Approval(DIGEST, 300.0, 20, "2026-08-21", "approved"),
        )
        self.assertFalse(gs.post_reread_approval_valid)

    def test_another_revision_approval_denied(self):
        gs = reduce_gate(
            lane_reports=dual_zero_reports(DIGEST), revision_digest=DIGEST,
            doctrine_reread=self.reread(),
            approval=Approval("f" * 64, 300.0, 30, "2026-08-21", "approved"),
        )
        self.assertFalse(gs.post_reread_approval_valid)

    def test_missing_binding_fields_denied(self):
        gs = reduce_gate(
            lane_reports=dual_zero_reports(DIGEST), revision_digest=DIGEST,
            doctrine_reread=self.reread(),
            approval=Approval(DIGEST, 300.0, 30, "", "approved"),  # no date
        )
        self.assertFalse(gs.post_reread_approval_valid)

    def test_one_valid_approval_accepts(self):
        gs = reduce_gate(
            lane_reports=dual_zero_reports(DIGEST), revision_digest=DIGEST,
            doctrine_reread=self.reread(),
            approval=Approval(DIGEST, 300.0, 30, "2026-08-21", "approved"),
        )
        self.assertTrue(gs.post_reread_approval_valid)
        self.assertTrue(gs.phase3_eligible)


class TestPositivePath(unittest.TestCase):
    def test_probe_passing_antigravity_and_opencode_same_round(self):
        gs = reduce_gate(
            lane_reports=dual_zero_reports(DIGEST), revision_digest=DIGEST,
            doctrine_reread=DoctrineReread(DIGEST, "d" * 64, 200.0, 20, "pass"),
            approval=Approval(DIGEST, 300.0, 30, "2026-08-21", "approved"),
        )
        self.assertTrue(gs.phase3_eligible)
        self.assertEqual([], [r for r in gs.reasons if "fail" in r.lower()])


# --- Task 3: CLI contract ----------------------------------------------------

import json  # noqa: E402
import subprocess  # noqa: E402

REMEDIATE = SCRIPTS / "remediate.py"
FIXTURES = Path(__file__).resolve().parent / "fixtures"


def run_cli(*argv) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(REMEDIATE), *argv],
        capture_output=True, text=True,
    )


class TestCliContract(unittest.TestCase):
    def test_help_lists_every_review_flag(self):
        proc = run_cli("--help")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        for flag in (
            "--require-latest-terminal-review",
            "--require-post-terminal-doctrine-reread",
            "--require-post-reread-approval",
        ):
            self.assertIn(flag, proc.stdout, f"{flag} missing from --help")

    def test_valid_two_lane_status(self):
        proc = run_cli("status", "--evidence", str(FIXTURES / "evidence-valid.json"))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = json.loads(proc.stdout)
        self.assertTrue(data["phase3_eligible"])
        ids = {(l["lane"], l["provider"], l["model_id"]) for l in data["lanes"]}
        self.assertIn(("antigravity", "google", "gemini-3.6-flash"), ids)
        self.assertIn(("opencode", "opencode-go", "glm-5.1"), ids)
        self.assertTrue(all(l["eligible"] for l in data["lanes"]))

    def test_valid_evidence_passes_all_predicates(self):
        proc = run_cli(
            "validate", "--evidence", str(FIXTURES / "evidence-valid.json"),
            "--require-latest-terminal-review",
            "--require-post-terminal-doctrine-reread",
            "--require-post-reread-approval",
            "--require-phase3-eligible",
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_copilot_lane_returns_nonzero_with_diagnostic(self):
        proc = run_cli(
            "validate", "--evidence", str(FIXTURES / "evidence-copilot-lane.json"),
            "--require-phase3-eligible",
        )
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("FAIL", proc.stderr)

    def test_copilot_lane_not_eligible_in_status(self):
        proc = run_cli("status", "--evidence", str(FIXTURES / "evidence-copilot-lane.json"))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = json.loads(proc.stdout)
        self.assertFalse(data["phase3_eligible"])
        self.assertFalse(data["dual_zero_same_round"])
        copilot = [l for l in data["lanes"] if l["provider"] == "github-copilot"]
        self.assertTrue(copilot and not copilot[0]["eligible"])

    def test_missing_evidence_file_fails_loud(self):
        proc = run_cli("status", "--evidence", str(FIXTURES / "does-not-exist.json"))
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("ERROR", proc.stderr)


if __name__ == "__main__":
    unittest.main()
