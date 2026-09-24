"""Black-box contract tests for ste_gate.py (REM-06 STE enforcement gate).

Test-author half of a dev-tdd firewall. These tests pin the OBSERVABLE
behavior of a script that does not exist yet: subprocess in, exit code +
stdout/stderr out. They say nothing about how the gate is implemented.

The gate: `ste_gate.py --revision-dir <DIR>` scans <DIR>/prompts/after/*.md,
decides per prompt whether ASD-STE100 Simplified Technical English was really
applied, and exits:
  0  every prose prompt is STE-clean or proven prose-free (exempt)
  1  one or more prose prompts have an unexplained STE violation
  2  usage/config error (bad flag value, missing revision dir)
  3  terminated at the wall-clock ceiling

The decision rule pinned here:
  1. PROSE-FREE EXEMPT: a body with zero sentence-form prose (pure code/JSON/
     data) auto-passes.
  2. PROSE-BEARING must be STE-CLEAN: zero ste_lint violations, except a
     violation whose text lies inside a preserved-verbatim span recorded in
     writing-quality/ste.json for that file. Any other violation FAILS.
  3. LOUD FAILURE: per-item PASS/FAIL, each FAIL names file + STE rule bucket +
     quotes offending text; pass/fail/exempt counts summarized.
  4/5. exit codes and the hardening flags above.

Every subprocess gets timeout=60 (outer guard, so a hung gate is a test
FAILURE, not a hung run). Fixture files are written directly, never through a
shell heredoc (loud-replace transport rule).

RED expectation: ste_gate.py does not exist, so the subprocess python exits
non-zero (a real FileNotFoundError-style message on stderr, NOT exit 1/2/3),
which is not any code these tests accept as PASS. When the gate is written and
matches the contract, they go green.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent
GATE = SCRIPTS / "ste_gate.py"

# The linter's own CLEAN_FIXTURE yields 0 violations of any bucket. Reused
# verbatim so the "clean prose" case is anchored to ste_lint's ground truth.
CLEAN_PROSE = (
    "The system retries a failed upload automatically. This process keeps the "
    "data correct.\n\n"
    "If failures continue, make sure that your credentials are correct. If the "
    "problem continues, contact support.\n"
)

# Prose that trips four distinct ste_lint buckets at once:
#   banned_modal        -> "should", "would"
#   semicolon           -> ";"
#   slop_word           -> "robust", "leverage", "simply"
#   sentence_over_limit -> the long opening sentence (> 20 words, procedural)
DIRTY_PROSE = (
    "You should simply leverage the robust retry mechanism here because it "
    "would handle every transient network failure automatically without any "
    "further configuration on your part at all; that is the whole idea.\n"
)

# A pure fenced code block: zero sentence-form natural-language prose.
PROSE_FREE_BODY = (
    "```json\n"
    '{\n'
    '  "tool": "search",\n'
    '  "args": {"query": "example", "limit": 10}\n'
    "}\n"
    "```\n"
)

# Prose carrying exactly one banned-modal violation ("must" is fine in STE,
# but "should" is banned). Used for the preserved-span exempt/unexempt pair.
ONE_MODAL_PROSE = (
    "You should keep the token secret.\n"
)

# A leading HTML-comment frontmatter block (stock prompt metadata) whose
# description deliberately trips several ste_lint buckets at once: a banned
# modal ("should", "would"), a semicolon, a Latin abbrev ("e.g."), and a
# > 25-word description. Only the BODY after the closing "-->" is model-facing
# prose and subject to STE. The frontmatter must be IGNORED by the gate.
DIRTY_FRONTMATTER_CLEAN_BODY = (
    "<!--\n"
    'name: "System Prompt: Example"\n'
    'description: "This description should use e.g. Latin abbreviations; it is '
    "deliberately long and full of banned modals like should and would to prove "
    'the frontmatter is not linted by the gate at all here."\n'
    'ccVersion: "2.1.88"\n'
    "-->\n"
    "The system shows the git status once. This status does not update later.\n"
)

# Clean frontmatter, but the model-facing BODY carries a banned modal. Proves
# the gate still lints the body after stripping frontmatter (a frontmatter
# strip must not blank the whole file).
CLEAN_FRONTMATTER_DIRTY_BODY = (
    "<!--\n"
    'name: "System Prompt: Example"\n'
    'ccVersion: "2.1.88"\n'
    "-->\n"
    "You should keep the token secret.\n"
)


def run(args, timeout=60):
    return subprocess.run(
        [sys.executable, "-B"] + [str(a) for a in args],
        cwd=str(SCRIPTS), capture_output=True, text=True, timeout=timeout)


def assert_gate_ran(test, p):
    """Guard against a false PASS from the gate being absent. When ste_gate.py
    does not exist, the interpreter itself exits 2 with "can't open file ...:
    No such file or directory" on stderr, which would otherwise satisfy the
    exit-2 usage-error tests for the wrong reason. A real usage/config error
    from the gate never carries that interpreter message. This keeps the
    exit-2 tests RED until the gate exists AND pins the exit code afterward."""
    combined = (p.stdout or "") + (p.stderr or "")
    # Only the interpreter's own launch failure is banned. Its signature is
    # "can't open file '<...>ste_gate.py'", which the gate never emits. The
    # broader "No such file or directory" is NOT banned: the gate legitimately
    # says that about a missing --revision-dir.
    test.assertNotIn(
        "can't open file", combined,
        "gate did not run (ste_gate.py is absent); the exit code is the interpreter's, not the gate's")


def make_revision(tmp: Path, prompts: dict, ste_json: dict | None = None) -> Path:
    """Write a revision dir: prompts/after/<name> for each item; optional
    writing-quality/ste.json. Files written directly (no shell layer)."""
    after = tmp / "prompts" / "after"
    after.mkdir(parents=True, exist_ok=True)
    for name, body in prompts.items():
        (after / name).write_text(body, encoding="utf-8")
    if ste_json is not None:
        wq = tmp / "writing-quality"
        wq.mkdir(parents=True, exist_ok=True)
        (wq / "ste.json").write_text(json.dumps(ste_json), encoding="utf-8")
    return tmp


class TestDecisionRule(unittest.TestCase):
    """Rule 1 (prose-free exempt) and rule 2 (prose-bearing STE-clean)."""

    def test_prose_free_pure_code_exits_0(self):
        # Contract point 1: a body with zero sentence-form prose auto-passes.
        with tempfile.TemporaryDirectory() as td:
            rev = make_revision(Path(td), {"schema.md": PROSE_FREE_BODY})
            p = run([GATE, "--revision-dir", str(rev)])
        self.assertEqual(p.returncode, 0,
                         f"prose-free prompt must pass; stderr={p.stderr!r} stdout={p.stdout!r}")

    def test_clean_prose_exits_0(self):
        # Contract point 2 (clean side): prose with 0 ste_lint violations passes.
        with tempfile.TemporaryDirectory() as td:
            rev = make_revision(Path(td), {"clean.md": CLEAN_PROSE})
            p = run([GATE, "--revision-dir", str(rev)])
        self.assertEqual(p.returncode, 0,
                         f"clean STE prose must pass; stderr={p.stderr!r} stdout={p.stdout!r}")

    def test_dirty_prose_exits_1_and_names_file(self):
        # Contract point 2 (dirty side) + 3: an unexplained violation FAILS,
        # and the offending filename appears in the output.
        with tempfile.TemporaryDirectory() as td:
            rev = make_revision(Path(td), {"dirty.md": DIRTY_PROSE})
            p = run([GATE, "--revision-dir", str(rev)])
        self.assertEqual(p.returncode, 1,
                         f"dirty prose must fail with exit 1; stderr={p.stderr!r} stdout={p.stdout!r}")
        self.assertIn("dirty.md", p.stdout + p.stderr,
                      "the offending filename must appear in output")


class TestPreservedSpans(unittest.TestCase):
    """Rule 2 exception: a violation inside a preserved-verbatim span is
    exempt; the same class of violation outside any span is not."""

    def test_violation_inside_preserved_span_exits_0(self):
        # The whole offending sentence is recorded as a preserved span for the
        # file, so its banned-modal violation is explained and the batch passes.
        with tempfile.TemporaryDirectory() as td:
            rev = make_revision(
                Path(td),
                {"quoted.md": ONE_MODAL_PROSE},
                ste_json={"preserved_spans": {
                    "quoted.md": ["You should keep the token secret."]}},
            )
            p = run([GATE, "--revision-dir", str(rev)])
        self.assertEqual(p.returncode, 0,
                         f"violation inside a preserved span must be exempt; "
                         f"stderr={p.stderr!r} stdout={p.stdout!r}")

    def test_violation_outside_preserved_span_exits_1(self):
        # Same file carries the preserved quoted sentence PLUS an unexplained
        # modal in ordinary prose. The unexplained one fails the batch.
        body = (
            "You should keep the token secret.\n"
            "You would then delete the temporary directory.\n"
        )
        with tempfile.TemporaryDirectory() as td:
            rev = make_revision(
                Path(td),
                {"mixed.md": body},
                ste_json={"preserved_spans": {
                    "mixed.md": ["You should keep the token secret."]}},
            )
            p = run([GATE, "--revision-dir", str(rev)])
        self.assertEqual(p.returncode, 1,
                         f"an unexplained violation outside every preserved span must fail; "
                         f"stderr={p.stderr!r} stdout={p.stdout!r}")
        self.assertIn("mixed.md", p.stdout + p.stderr,
                      "the failing file must be named")


class TestLoudFailure(unittest.TestCase):
    """Rule 3: per-item accounting, named rule bucket + quoted text, counts."""

    def test_per_item_pass_and_fail_both_reported(self):
        # A batch of one clean + one dirty prompt must report BOTH a PASS and a
        # FAIL, each naming its own file (not one aggregate boolean).
        with tempfile.TemporaryDirectory() as td:
            rev = make_revision(Path(td), {
                "clean.md": CLEAN_PROSE,
                "dirty.md": DIRTY_PROSE,
            })
            p = run([GATE, "--revision-dir", str(rev)])
        out = p.stdout + p.stderr
        self.assertEqual(p.returncode, 1, f"batch with a dirty prompt must exit 1; out={out!r}")
        self.assertIn("PASS", out, "must report a per-item PASS")
        self.assertIn("FAIL", out, "must report a per-item FAIL")
        self.assertIn("clean.md", out, "the passing file must be named")
        self.assertIn("dirty.md", out, "the failing file must be named")

    def test_failure_names_rule_bucket_and_quotes_offending_text(self):
        # A dirty prompt's FAIL must name a specific ste_lint rule bucket AND
        # quote the offending text so the failure is a one-step fix.
        with tempfile.TemporaryDirectory() as td:
            rev = make_revision(Path(td), {"dirty.md": DIRTY_PROSE})
            p = run([GATE, "--revision-dir", str(rev)])
        out = p.stdout + p.stderr
        self.assertEqual(p.returncode, 1, f"dirty prompt must exit 1; out={out!r}")
        buckets = ("banned_modal", "semicolon", "slop_word",
                   "sentence_over_limit", "latin_abbrev")
        self.assertTrue(any(b in out for b in buckets),
                        f"a FAIL must name an STE rule bucket; none of {buckets} in {out!r}")
        # Some offending token from DIRTY_PROSE must be quoted back.
        offenders = ("should", "would", "robust", "leverage", "simply", ";")
        self.assertTrue(any(o in out for o in offenders),
                        f"a FAIL must quote offending text; none of {offenders} in {out!r}")

    def test_summary_counts_printed(self):
        # A machine/human summary of pass/fail/exempt counts is printed; with a
        # dirty prompt present a nonzero fail count must be visible.
        with tempfile.TemporaryDirectory() as td:
            rev = make_revision(Path(td), {
                "clean.md": CLEAN_PROSE,
                "dirty.md": DIRTY_PROSE,
                "schema.md": PROSE_FREE_BODY,
            })
            p = run([GATE, "--revision-dir", str(rev)])
        out = p.stdout + p.stderr
        self.assertEqual(p.returncode, 1, f"expected exit 1; out={out!r}")
        low = out.lower()
        self.assertIn("fail", low, "summary must report a fail count")
        # A nonzero fail count digit must be visible somewhere in the summary.
        self.assertRegex(out, r"[1-9]", "a nonzero fail count must be visible")


class TestFrontmatterNotLinted(unittest.TestCase):
    """The gate lints only the model-facing BODY, never the leading HTML-comment
    frontmatter block (stock metadata: name/description/ccVersion/variables)."""

    def test_dirty_frontmatter_clean_body_exits_0(self):
        # Frontmatter description is full of STE violations; body is clean STE
        # prose. The gate must ignore the frontmatter and pass.
        with tempfile.TemporaryDirectory() as td:
            rev = make_revision(
                Path(td), {"fm.md": DIRTY_FRONTMATTER_CLEAN_BODY})
            p = run([GATE, "--revision-dir", str(rev)])
        self.assertEqual(p.returncode, 0,
                         f"frontmatter violations must be ignored; only the body is "
                         f"linted; stderr={p.stderr!r} stdout={p.stdout!r}")

    def test_clean_frontmatter_dirty_body_exits_1(self):
        # Clean frontmatter, dirty body: the gate still lints the body, so
        # stripping frontmatter did not accidentally blank the whole file.
        with tempfile.TemporaryDirectory() as td:
            rev = make_revision(
                Path(td), {"fm.md": CLEAN_FRONTMATTER_DIRTY_BODY})
            p = run([GATE, "--revision-dir", str(rev)])
        self.assertEqual(p.returncode, 1,
                         f"a dirty body must still fail even behind clean "
                         f"frontmatter; stderr={p.stderr!r} stdout={p.stdout!r}")
        self.assertIn("fm.md", p.stdout + p.stderr,
                      "the offending filename must appear in output")


class TestHardening(unittest.TestCase):
    """recipe-skill-script-hardening [R001] Step 5 + the gate's exit contract."""

    def _clean_revision_argv(self, td: Path):
        rev = make_revision(td, {"clean.md": CLEAN_PROSE})
        return ["--revision-dir", str(rev)]

    def test_kill_path_exits_3_fast(self):
        # --watchdog-probe 10 --max-seconds 1 must exit exactly 3 in < 5 s.
        with tempfile.TemporaryDirectory() as td:
            argv = self._clean_revision_argv(Path(td))
            start = time.monotonic()
            p = run([GATE, "--watchdog-probe", "10", "--max-seconds", "1"] + argv, timeout=60)
            elapsed = time.monotonic() - start
        self.assertEqual(p.returncode, 3,
                         f"expected exit 3, got {p.returncode}; stderr={p.stderr!r}")
        self.assertLess(elapsed, 5.0, f"kill took {elapsed:.2f}s, expected < 5s")

    def test_max_seconds_zero_exits_2(self):
        p = run([GATE, "--max-seconds", "0"], timeout=60)
        assert_gate_ran(self, p)
        self.assertEqual(p.returncode, 2, f"--max-seconds 0 should exit 2; stderr={p.stderr!r}")
        self.assertTrue(p.stderr.strip(), "expected non-empty stderr")

    def test_max_seconds_negative_exits_2(self):
        p = run([GATE, "--max-seconds", "-5"], timeout=60)
        assert_gate_ran(self, p)
        self.assertEqual(p.returncode, 2, f"negative --max-seconds should exit 2; stderr={p.stderr!r}")
        self.assertTrue(p.stderr.strip(), "expected non-empty stderr")

    def test_watchdog_probe_negative_exits_2(self):
        p = run([GATE, "--watchdog-probe", "-1", "--max-seconds", "5"], timeout=60)
        assert_gate_ran(self, p)
        self.assertEqual(p.returncode, 2, f"negative --watchdog-probe should exit 2; stderr={p.stderr!r}")
        self.assertTrue(p.stderr.strip(), "expected non-empty stderr")

    def test_missing_revision_dir_exits_2(self):
        # Config error: a revision dir that does not exist.
        with tempfile.TemporaryDirectory() as td:
            missing = Path(td) / "does-not-exist"
            p = run([GATE, "--revision-dir", str(missing)], timeout=60)
        assert_gate_ran(self, p)
        self.assertEqual(p.returncode, 2,
                         f"missing revision dir should exit 2; stderr={p.stderr!r}")
        self.assertTrue(p.stderr.strip(), "expected non-empty stderr")

    def test_help_mentions_both_flags(self):
        p = run([GATE, "--help"], timeout=60)
        self.assertEqual(p.returncode, 0)
        self.assertIn("--max-seconds", p.stdout)
        self.assertIn("--watchdog-probe", p.stdout)


class TestRound1SpanOrder(unittest.TestCase):
    """R1-4: the gate blanks the preserved spans of a body longest first.

    The body holds two nested preserved spans. The long span holds the short
    span and one more banned modal. The result must not change with the
    order of the two spans in ste.json.
    """

    LONG_SPAN = "Quote: you should keep the token and you would lock it."
    SHORT_SPAN = "you should keep the token"
    BODY = "The system shows the git status once.\n" + LONG_SPAN + "\n"

    def _run_order(self, spans):
        with tempfile.TemporaryDirectory() as td:
            rev = make_revision(Path(td), {"nested.md": self.BODY},
                                ste_json={"preserved_spans": {"nested.md": spans}})
            p = run([GATE, "--revision-dir", str(rev)])
        assert_gate_ran(self, p)
        return p

    def test_long_span_first_exits_0(self):
        # The long span is first in the list. The gate blanks all of it, and
        # no violation stays.
        p = self._run_order([self.LONG_SPAN, self.SHORT_SPAN])
        self.assertEqual(p.returncode, 0, f"stdout={p.stdout!r} stderr={p.stderr!r}")
        self.assertIn("PASS nested.md", p.stdout)

    def test_short_span_first_exits_0(self):
        # The short span is first in the list. The long span must still be
        # blanked in full, so the modal outside the short span is exempt.
        p = self._run_order([self.SHORT_SPAN, self.LONG_SPAN])
        self.assertEqual(p.returncode, 0, f"stdout={p.stdout!r} stderr={p.stderr!r}")
        self.assertIn("PASS nested.md", p.stdout)
        self.assertNotIn("FAIL nested.md", p.stdout)

    def test_both_orders_give_the_same_output(self):
        # The two list orders give the same exit code and the same stdout.
        a = self._run_order([self.LONG_SPAN, self.SHORT_SPAN])
        b = self._run_order([self.SHORT_SPAN, self.LONG_SPAN])
        self.assertEqual((a.returncode, a.stdout), (b.returncode, b.stdout))


if __name__ == "__main__":
    unittest.main()
