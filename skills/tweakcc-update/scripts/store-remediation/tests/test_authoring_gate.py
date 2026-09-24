"""Black-box contract tests for authoring_gate.py (the G2 authoring gate for
Phase 12 STE-clean authored store revisions).

Test-author half of a dev-tdd firewall. These tests pin the OBSERVABLE
behavior of a script that does not exist yet: subprocess in, exit code +
stdout/stderr out. They say nothing about how the gate is implemented.

See <gate_contract> of .planning/phases/12-ste-clean-authoring/12-01-PLAN.md
for the full row-item and revision-item contract this file pins.

Every subprocess gets timeout=60 (outer guard, so a hung gate is a test
FAILURE, not a hung run). Fixture files are written directly, never through a
shell heredoc (loud-replace transport rule).

RED expectation: authoring_gate.py does not exist, so the subprocess exits
non-zero with an interpreter-level message on stderr, which is not any code
these tests accept as PASS. When the gate is written and matches the
contract, they go green.
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
GATE = SCRIPTS / "authoring_gate.py"

FRONTMATTER = '<!--\nname: "System Prompt: Example"\nccVersion: "2.1.280"\n-->\n'

# Fixture 1: a stock body with a head marker, two placeholders, and one rule
# entry with one added and one removed line.
STOCK_BODY = (
    FRONTMATTER
    + "2. Send ${GREETING} to the user with ${SIGNATURE}. Be brief always.\n"
)
# The rewrite keeps the frontmatter, the head marker, both placeholders in
# order, drops the removed nerf line "Be brief always.", and adds the new
# un-nerf line "Explain the reasoning in full." The reader can act on it.
REWRITE_BODY = (
    FRONTMATTER
    + "2. Send ${GREETING} to the user with ${SIGNATURE}. "
    "Explain the reasoning in full.\n"
)

RULE_ID = "fixture-row"
RULE_FILE_CONTENT = {
    "id": RULE_ID,
    "rules": [
        {
            "description": "fixture rule",
            "stock": ["Be brief always."],
            "unnerf": ["Explain the reasoning in full."],
        }
    ],
}

GLOSSARY = {
    "schema": 1,
    "terms": {
        "plain-english": {
            "text": "plain English",
            "means": "simple, direct prose",
            "variants": ["simple english", "plain-English"],
        }
    },
}

CONTRACT_ONE_ROW = {
    "schema": 1,
    "required": {"fixture-row.md": ["plain-english"]},
    "ordered": {},
    "forbidden": {},
    "header_statement": {"rows": [], "term": "plain-english", "mentions": []},
}

# The rewrite body must carry the required term "plain English" for the
# fixture contract to require it truthfully in Test 1.
REWRITE_BODY_WITH_TERM = (
    FRONTMATTER
    + "2. Send ${GREETING} to the user with ${SIGNATURE} in plain English. "
    "Explain the reasoning in full.\n"
)

LEDGER_FIXTURE = {
    "row": "fixture-row.md",
    "points": [
        {
            "id": f"{RULE_ID}#0+0",
            "old": "Explain the reasoning in full.",
            "new": "Explain the reasoning in full.",
        }
    ],
}


def run(args, timeout=60):
    return subprocess.run(
        [sys.executable, "-B"] + [str(a) for a in args],
        cwd=str(SCRIPTS), capture_output=True, text=True, timeout=timeout)


def assert_gate_ran(test, p):
    """Guard against a false PASS from the gate being absent. When
    authoring_gate.py does not exist, the interpreter itself exits with
    "can't open file ...: No such file or directory" on stderr. A real
    usage/config error from the gate never carries that interpreter
    message. This keeps the exit-2 tests RED until the gate exists AND
    pins the exit code afterward."""
    combined = (p.stdout or "") + (p.stderr or "")
    test.assertNotIn(
        "can't open file", combined,
        "gate did not run (authoring_gate.py is absent); the exit code is "
        "the interpreter's, not the gate's")


def make_revision(tmp: Path, *, rows: dict, rule_files: dict | None = None,
                   glossary: dict | None = None, contract: dict | None = None,
                   ledgers: dict | None = None) -> dict:
    """Write one fixture revision tree and its rules/glossary/contract
    siblings. rows: {filename: (stock_body, after_body)}. Returns a dict of
    the paths the CLI needs: revision_dir, rules_dir, glossary_path,
    contract is written inside revision_dir/contract.json."""
    rev = tmp / "rev"
    before = rev / "prompts" / "before"
    after = rev / "prompts" / "after"
    before.mkdir(parents=True, exist_ok=True)
    after.mkdir(parents=True, exist_ok=True)
    queue = []
    for name, (stock, rewrite) in rows.items():
        (before / name).write_text(stock, encoding="utf-8")
        (after / name).write_text(rewrite, encoding="utf-8")
        queue.append({"file": name, "sha256": "0" * 64})
    (rev / "batch.json").write_text(
        json.dumps({"batch": "fixture", "queue": queue}), encoding="utf-8")
    (rev / "contract.json").write_text(
        json.dumps(contract if contract is not None else {
            "schema": 1, "required": {}, "ordered": {}, "forbidden": {},
            "header_statement": {"rows": [], "term": None, "mentions": []},
        }), encoding="utf-8")
    carry = rev / "carry-forward"
    carry.mkdir(parents=True, exist_ok=True)
    for name in rows:
        stem = name[:-3] if name.endswith(".md") else name
        ledger = (ledgers or {}).get(name)
        if ledger is not None:
            (carry / f"{stem}.json").write_text(json.dumps(ledger), encoding="utf-8")

    rules_dir = tmp / "rules"
    rules_dir.mkdir(parents=True, exist_ok=True)
    for stem, content in (rule_files or {}).items():
        (rules_dir / f"{stem}.json").write_text(json.dumps(content), encoding="utf-8")

    glossary_path = tmp / "glossary.json"
    glossary_path.write_text(
        json.dumps(glossary if glossary is not None else {"schema": 1, "terms": {}}),
        encoding="utf-8")

    return {
        "revision_dir": rev,
        "rules_dir": rules_dir,
        "glossary_path": glossary_path,
    }


def base_argv(paths: dict, *extra):
    return [
        GATE,
        "--revision-dir", str(paths["revision_dir"]),
        "--rules-dir", str(paths["rules_dir"]),
        "--glossary", str(paths["glossary_path"]),
        *extra,
    ]


class TestTracerEndToEnd(unittest.TestCase):
    """Task 1 tracer: one fixture row that passes every gate item."""

    def _fixture_paths(self, tmp: Path, *, with_ledger=True):
        return make_revision(
            tmp,
            rows={"fixture-row.md": (STOCK_BODY, REWRITE_BODY_WITH_TERM)},
            rule_files={RULE_ID: RULE_FILE_CONTENT},
            glossary=GLOSSARY,
            contract=CONTRACT_ONE_ROW,
            ledgers={"fixture-row.md": LEDGER_FIXTURE} if with_ledger else {},
        )

    def test_full_fixture_row_passes_every_item(self):
        with tempfile.TemporaryDirectory() as td:
            paths = self._fixture_paths(Path(td))
            p = run(base_argv(paths))
        out = p.stdout + p.stderr
        self.assertEqual(p.returncode, 0, f"expected exit 0; out={out!r}")
        for item in ("placeholders", "frame", "code", "carry-forward",
                     "glossary", "header", "twins"):
            self.assertIn(f"PASS {item} fixture-row.md", out,
                           f"missing PASS {item} line; out={out!r}")
        self.assertIn("PASS rowset", out)
        self.assertIn("PASS per-prompt-fit", out)
        self.assertIn("GATE PASSED", out)

    def test_list_points_prints_added_and_removed_markers(self):
        with tempfile.TemporaryDirectory() as td:
            paths = self._fixture_paths(Path(td))
            p = run(base_argv(paths, "--list-points", "--files", "fixture-row.md"))
        out = p.stdout + p.stderr
        self.assertEqual(p.returncode, 0, f"expected exit 0; out={out!r}")
        self.assertIn("+", out)
        self.assertEqual(p.returncode, 0)

    def test_missing_ledger_item_fails_carry_forward(self):
        with tempfile.TemporaryDirectory() as td:
            paths = self._fixture_paths(Path(td), with_ledger=False)
            p = run(base_argv(paths))
        out = p.stdout + p.stderr
        self.assertEqual(p.returncode, 1, f"expected exit 1; out={out!r}")
        self.assertIn("FAIL carry-forward", out)
        self.assertIn("GATE FAILED", out)


# --- Task 2: edge tests, one per <edge_coverage> and <gate_contract> line --


def one_row_revision(tmp: Path, name: str, stock: str, rewrite: str, **kw):
    """A minimal single-row fixture with a clean glossary/contract/rules/
    ledger, so a test can override exactly one axis (kw: rule_files,
    glossary, contract, ledgers)."""
    rows = {name: (stock, rewrite)}
    return make_revision(tmp, rows=rows, **kw)


SIMPLE_STOCK = FRONTMATTER + "Do the task now.\n"
SIMPLE_REWRITE = FRONTMATTER + "Do the task now.\n"


class TestPlaceholders(unittest.TestCase):
    def test_placeholders_swap_fails(self):
        stock = FRONTMATTER + "Send ${A} then ${B}.\n"
        rewrite = FRONTMATTER + "Send ${B} then ${A}.\n"
        with tempfile.TemporaryDirectory() as td:
            paths = one_row_revision(Path(td), "r.md", stock, rewrite)
            p = run(base_argv(paths))
        out = p.stdout + p.stderr
        self.assertEqual(p.returncode, 1, out)
        self.assertIn("FAIL placeholders r.md", out)

    def test_placeholders_new_in_empty_row_fails(self):
        stock = FRONTMATTER + "Send the message.\n"
        rewrite = FRONTMATTER + "Send ${A} the message.\n"
        with tempfile.TemporaryDirectory() as td:
            paths = one_row_revision(Path(td), "r.md", stock, rewrite)
            p = run(base_argv(paths))
        out = p.stdout + p.stderr
        self.assertEqual(p.returncode, 1, out)
        self.assertIn("FAIL placeholders r.md", out)

    def test_placeholders_text_between_touching_fails(self):
        stock = FRONTMATTER + "Send ${A}${B} now.\n"
        rewrite = FRONTMATTER + "Send ${A}X${B} now.\n"
        with tempfile.TemporaryDirectory() as td:
            paths = one_row_revision(Path(td), "r.md", stock, rewrite)
            p = run(base_argv(paths))
        out = p.stdout + p.stderr
        self.assertEqual(p.returncode, 1, out)
        self.assertIn("FAIL placeholders r.md", out)

    def test_placeholders_dropped_angle_bracket_fails(self):
        stock = FRONTMATTER + "Send <${A}> now.\n"
        rewrite = FRONTMATTER + "Send ${A} now.\n"
        with tempfile.TemporaryDirectory() as td:
            paths = one_row_revision(Path(td), "r.md", stock, rewrite)
            p = run(base_argv(paths))
        out = p.stdout + p.stderr
        self.assertEqual(p.returncode, 1, out)
        self.assertIn("FAIL placeholders r.md", out)


class TestFrame(unittest.TestCase):
    def test_frame_changed_frontmatter_byte_fails(self):
        stock = FRONTMATTER + "Do the task now.\n"
        rewrite = '<!--\nname: "System Prompt: Different"\nccVersion: "2.1.280"\n-->\nDo the task now.\n'
        with tempfile.TemporaryDirectory() as td:
            paths = one_row_revision(Path(td), "r.md", stock, rewrite)
            p = run(base_argv(paths))
        out = p.stdout + p.stderr
        self.assertEqual(p.returncode, 1, out)
        self.assertIn("FAIL frame r.md", out)

    def test_frame_dropped_leading_newline_fails(self):
        stock = FRONTMATTER + "\nDo the task now.\n"
        rewrite = FRONTMATTER + "Do the task now.\n"
        with tempfile.TemporaryDirectory() as td:
            paths = one_row_revision(Path(td), "r.md", stock, rewrite)
            p = run(base_argv(paths))
        out = p.stdout + p.stderr
        self.assertEqual(p.returncode, 1, out)
        self.assertIn("FAIL frame r.md", out)

    def test_frame_rewritten_head_marker_fails(self):
        stock = FRONTMATTER + "2. Do the task now.\n"
        rewrite = FRONTMATTER + "3. Do the task now.\n"
        with tempfile.TemporaryDirectory() as td:
            paths = one_row_revision(Path(td), "r.md", stock, rewrite)
            p = run(base_argv(paths))
        out = p.stdout + p.stderr
        self.assertEqual(p.returncode, 1, out)
        self.assertIn("FAIL frame r.md", out)

    def test_frame_dropped_tail_colon_fails(self):
        stock = FRONTMATTER + "Do the following:\n"
        rewrite = FRONTMATTER + "Do the following.\n"
        with tempfile.TemporaryDirectory() as td:
            paths = one_row_revision(Path(td), "r.md", stock, rewrite)
            p = run(base_argv(paths))
        out = p.stdout + p.stderr
        self.assertEqual(p.returncode, 1, out)
        self.assertIn("FAIL frame r.md", out)

    def test_frame_no_marker_passes_with_new_first_word(self):
        stock = FRONTMATTER + "Do the task now.\n"
        rewrite = FRONTMATTER + "Finish the task now.\n"
        with tempfile.TemporaryDirectory() as td:
            paths = one_row_revision(Path(td), "r.md", stock, rewrite)
            p = run(base_argv(paths))
        out = p.stdout + p.stderr
        self.assertIn("PASS frame r.md", out)


class TestCode(unittest.TestCase):
    def test_code_new_span_of_four_words_fails(self):
        stock = FRONTMATTER + "Do the task now.\n"
        rewrite = FRONTMATTER + "Do `this brand new thing` now.\n"
        with tempfile.TemporaryDirectory() as td:
            paths = one_row_revision(Path(td), "r.md", stock, rewrite)
            p = run(base_argv(paths))
        out = p.stdout + p.stderr
        self.assertEqual(p.returncode, 1, out)
        self.assertIn("FAIL code r.md", out)

    def test_code_four_word_span_from_stock_passes(self):
        stock = FRONTMATTER + "Run `git status now please` first.\n"
        rewrite = FRONTMATTER + "First run `git status now please`.\n"
        with tempfile.TemporaryDirectory() as td:
            paths = one_row_revision(Path(td), "r.md", stock, rewrite)
            p = run(base_argv(paths))
        out = p.stdout + p.stderr
        self.assertIn("PASS code r.md", out)

    def test_code_fence_from_old_unnerf_text_passes(self):
        stock = FRONTMATTER + "Do the task now.\n"
        rewrite = FRONTMATTER + "Do the task now.\n\n```\nold code here\n```\n"
        rule_files = {"r": {
            "id": "r",
            "rules": [{
                "description": "d",
                "stock": ["Do the task now."],
                "unnerf": ["Do the task now.\n\n```\nold code here\n```"],
            }],
        }}
        ledgers = {"r.md": {"row": "r.md", "points": []}}
        with tempfile.TemporaryDirectory() as td:
            paths = one_row_revision(Path(td), "r.md", stock, rewrite,
                                      rule_files=rule_files, ledgers=ledgers)
            p = run(base_argv(paths))
        out = p.stdout + p.stderr
        self.assertIn("PASS code r.md", out)

    def test_code_new_fence_fails(self):
        stock = FRONTMATTER + "Do the task now.\n"
        rewrite = FRONTMATTER + "Do the task now.\n\n```\nbrand new code\n```\n"
        with tempfile.TemporaryDirectory() as td:
            paths = one_row_revision(Path(td), "r.md", stock, rewrite)
            p = run(base_argv(paths))
        out = p.stdout + p.stderr
        self.assertEqual(p.returncode, 1, out)
        self.assertIn("FAIL code r.md", out)


def _rule_one_added(rule_id="r", added_line="Explain the reasoning in full."):
    return {
        "id": rule_id,
        "rules": [{
            "description": "d",
            "stock": ["Be brief always."],
            "unnerf": [added_line],
        }],
    }


class TestCarryForward(unittest.TestCase):
    def _base(self, tmp: Path, *, ledger_points=None, rewrite=None, removed_kept=False):
        stock = FRONTMATTER + "Be brief always.\n"
        after = rewrite if rewrite is not None else (
            FRONTMATTER + ("Be brief always.\n" if removed_kept else "Explain the reasoning in full.\n"))
        ledger = {"row": "r.md", "points": ledger_points} if ledger_points is not None else None
        return one_row_revision(
            tmp, "r.md", stock, after,
            rule_files={"r": _rule_one_added()},
            ledgers={"r.md": ledger} if ledger is not None else {},
        )

    def test_carry_forward_missing_item_fails(self):
        with tempfile.TemporaryDirectory() as td:
            paths = self._base(Path(td), ledger_points=[])
            p = run(base_argv(paths))
        out = p.stdout + p.stderr
        self.assertEqual(p.returncode, 1, out)
        self.assertIn("FAIL carry-forward r.md", out)

    def test_carry_forward_new_quote_absent_fails(self):
        with tempfile.TemporaryDirectory() as td:
            paths = self._base(Path(td), ledger_points=[
                {"id": "r#0+0", "old": "Explain the reasoning in full.", "new": "Not in the body anywhere."},
            ])
            p = run(base_argv(paths))
        out = p.stdout + p.stderr
        self.assertEqual(p.returncode, 1, out)
        self.assertIn("FAIL carry-forward r.md", out)

    def test_carry_forward_unknown_id_fails(self):
        with tempfile.TemporaryDirectory() as td:
            paths = self._base(Path(td), ledger_points=[
                {"id": "r#0+0", "old": "Explain the reasoning in full.", "new": "Explain the reasoning in full."},
                {"id": "r#0+9", "old": "nope", "new": "Explain the reasoning in full."},
            ])
            p = run(base_argv(paths))
        out = p.stdout + p.stderr
        self.assertEqual(p.returncode, 1, out)
        self.assertIn("FAIL carry-forward r.md", out)

    def test_carry_forward_duplicate_id_fails(self):
        with tempfile.TemporaryDirectory() as td:
            rev = self._base(Path(td), ledger_points=[
                {"id": "r#0+0", "old": "Explain the reasoning in full.", "new": "Explain the reasoning in full."},
            ])
            ledger_path = rev["revision_dir"] / "carry-forward" / "r.json"
            ledger_path.write_text(json.dumps({
                "row": "r.md",
                "points": [
                    {"id": "r#0+0", "old": "Explain the reasoning in full.", "new": "Explain the reasoning in full."},
                    {"id": "r#0+0", "old": "Explain the reasoning in full.", "new": "Explain the reasoning in full."},
                ],
            }), encoding="utf-8")
            p = run(base_argv(rev))
        out = p.stdout + p.stderr
        self.assertEqual(p.returncode, 1, out)
        self.assertIn("FAIL carry-forward r.md", out)

    def test_carry_forward_wrong_old_fails(self):
        with tempfile.TemporaryDirectory() as td:
            paths = self._base(Path(td), ledger_points=[
                {"id": "r#0+0", "old": "wrong text", "new": "Explain the reasoning in full."},
            ])
            p = run(base_argv(paths))
        out = p.stdout + p.stderr
        self.assertEqual(p.returncode, 1, out)
        self.assertIn("FAIL carry-forward r.md", out)

    def test_carry_forward_removed_line_kept_whole_fails(self):
        with tempfile.TemporaryDirectory() as td:
            paths = self._base(Path(td), ledger_points=[
                {"id": "r#0+0", "old": "Explain the reasoning in full.", "new": "Explain the reasoning in full."},
            ], removed_kept=True)
            p = run(base_argv(paths))
        out = p.stdout + p.stderr
        self.assertEqual(p.returncode, 1, out)
        self.assertIn("FAIL carry-forward r.md", out)

    def test_carry_forward_removed_line_kept_as_prefix_passes(self):
        rewrite = FRONTMATTER + "Be brief always. Then explain the reasoning in full.\n"
        with tempfile.TemporaryDirectory() as td:
            paths = self._base(Path(td), ledger_points=[
                {"id": "r#0+0", "old": "Explain the reasoning in full.", "new": "Then explain the reasoning in full."},
            ], rewrite=rewrite)
            p = run(base_argv(paths))
        out = p.stdout + p.stderr
        self.assertIn("PASS carry-forward r.md", out)

    def test_carry_forward_second_rule_file_entry_shows_in_list_points(self):
        # r.md's stock body contains BOTH "Be brief always." (r's own rule)
        # AND "Also skip the explanation." (other-rule's stock line), so
        # D-25 pulls other-rule's entry in by stock containment.
        stock = FRONTMATTER + "Be brief always. Also skip the explanation.\n"
        after = FRONTMATTER + "Explain the reasoning in full. Give full context too.\n"
        rule_files = {
            "r": _rule_one_added(),
            "other-rule": {
                "id": "other-rule",
                "rules": [{
                    "description": "d2",
                    "stock": ["Also skip the explanation."],
                    "unnerf": ["Give full context too."],
                }],
            },
        }
        with tempfile.TemporaryDirectory() as td:
            paths = one_row_revision(Path(td), "r.md", stock, after, rule_files=rule_files)
            p = run(base_argv(paths, "--list-points", "--files", "r.md"))
        out = p.stdout + p.stderr
        self.assertEqual(p.returncode, 0, out)
        self.assertIn("other-rule#0+0", out)


TERM_GLOSSARY = {
    "schema": 1,
    "terms": {
        "plain-english": {
            "text": "plain English",
            "means": "simple, direct prose",
            "variants": ["simple english"],
        },
        "lead-with-result": {
            "text": "lead with the result",
            "means": "state the outcome first",
            "variants": [],
        },
    },
}


class TestGlossary(unittest.TestCase):
    def _rev(self, tmp: Path, body: str, *, contract=None):
        stock = FRONTMATTER + "Do the task now.\n"
        return one_row_revision(tmp, "r.md", stock, FRONTMATTER + body,
                                 glossary=TERM_GLOSSARY, contract=contract)

    def test_glossary_variant_in_prose_fails(self):
        with tempfile.TemporaryDirectory() as td:
            paths = self._rev(Path(td), "Write in simple english please.\n")
            p = run(base_argv(paths))
        out = p.stdout + p.stderr
        self.assertEqual(p.returncode, 1, out)
        self.assertIn("FAIL glossary r.md", out)

    def test_glossary_variant_only_in_code_span_passes(self):
        with tempfile.TemporaryDirectory() as td:
            paths = self._rev(Path(td), "Run `simple english mode` now.\n")
            p = run(base_argv(paths))
        out = p.stdout + p.stderr
        self.assertIn("PASS glossary r.md", out)

    def test_glossary_variant_only_in_frontmatter_passes(self):
        """A stock frontmatter name/description can restate a glossary
        variant (for example a prompt titled with the exact stock line a
        rewrite must drop). item_frame holds the frontmatter byte-identical
        to stock, so the glossary and header items must never scan it."""
        fm = ('<!--\nname: "System Prompt: Lead with the outcome"\n'
              'ccVersion: "2.1.280"\n-->\n')
        with tempfile.TemporaryDirectory() as td:
            stock = fm + "Do the task now.\n"
            rewrite = fm + "Do the task now, and lead with the result.\n"
            paths = one_row_revision(Path(td), "r.md", stock, rewrite,
                                      glossary=TERM_GLOSSARY, contract=None)
            p = run(base_argv(paths))
        out = p.stdout + p.stderr
        self.assertIn("PASS glossary r.md", out)

    def test_glossary_missing_required_term_fails(self):
        contract = {
            "schema": 1, "required": {"r.md": ["plain-english"]},
            "ordered": {}, "forbidden": {},
            "header_statement": {"rows": [], "term": None, "mentions": []},
        }
        with tempfile.TemporaryDirectory() as td:
            paths = self._rev(Path(td), "Do the task now.\n", contract=contract)
            p = run(base_argv(paths))
        out = p.stdout + p.stderr
        self.assertEqual(p.returncode, 1, out)
        self.assertIn("FAIL glossary r.md", out)

    def test_glossary_ordered_out_of_order_fails(self):
        contract = {
            "schema": 1, "required": {},
            "ordered": {"r.md": ["lead-with-result", "plain-english"]},
            "forbidden": {},
            "header_statement": {"rows": [], "term": None, "mentions": []},
        }
        with tempfile.TemporaryDirectory() as td:
            paths = self._rev(Path(td), "Write in plain English, and lead with the result.\n",
                               contract=contract)
            p = run(base_argv(paths))
        out = p.stdout + p.stderr
        self.assertEqual(p.returncode, 1, out)
        self.assertIn("FAIL glossary r.md", out)

    def test_glossary_forbidden_phrase_fails(self):
        contract = {
            "schema": 1, "required": {}, "ordered": {},
            "forbidden": {"r.md": ["short beats complete"]},
            "header_statement": {"rows": [], "term": None, "mentions": []},
        }
        with tempfile.TemporaryDirectory() as td:
            paths = self._rev(Path(td), "Short beats complete, always.\n", contract=contract)
            p = run(base_argv(paths))
        out = p.stdout + p.stderr
        self.assertEqual(p.returncode, 1, out)
        self.assertIn("FAIL glossary r.md", out)

    def test_glossary_variant_matches_inside_canonical_exits_2(self):
        bad_glossary = {
            "schema": 1,
            "terms": {
                "a": {"text": "lead with the result", "means": "m", "variants": ["result"]},
            },
        }
        stock = FRONTMATTER + "Do the task now.\n"
        with tempfile.TemporaryDirectory() as td:
            paths = one_row_revision(Path(td), "r.md", stock, FRONTMATTER + "Do the task now.\n",
                                      glossary=bad_glossary)
            p = run(base_argv(paths))
        self.assertEqual(p.returncode, 2, p.stdout + p.stderr)

    def test_glossary_unknown_term_id_exits_2(self):
        contract = {
            "schema": 1, "required": {"r.md": ["no-such-term"]},
            "ordered": {}, "forbidden": {},
            "header_statement": {"rows": [], "term": None, "mentions": []},
        }
        with tempfile.TemporaryDirectory() as td:
            paths = self._rev(Path(td), "Do the task now.\n", contract=contract)
            p = run(base_argv(paths))
        self.assertEqual(p.returncode, 2, p.stdout + p.stderr)


class TestHeader(unittest.TestCase):
    def test_header_new_conventional_commits_line_fails(self):
        stock = FRONTMATTER + "Write a commit message.\n"
        rewrite = FRONTMATTER + "Write a commit message.\n\nfeat(x): add y\n"
        with tempfile.TemporaryDirectory() as td:
            paths = one_row_revision(Path(td), "r.md", stock, rewrite)
            p = run(base_argv(paths))
        out = p.stdout + p.stderr
        self.assertEqual(p.returncode, 1, out)
        self.assertIn("FAIL header r.md", out)

    def test_header_statement_sentence_without_term_fails(self):
        stock = FRONTMATTER + "Follow the header convention.\n"
        rewrite = FRONTMATTER + "The header line follows repository convention.\n"
        contract = {
            "schema": 1, "required": {}, "ordered": {}, "forbidden": {},
            "header_statement": {
                "rows": ["r.md"], "term": "header-convention",
                "mentions": ["header line"],
            },
        }
        glossary = {
            "schema": 1,
            "terms": {"header-convention": {
                "text": "the header-convention term",
                "means": "m", "variants": [],
            }},
        }
        with tempfile.TemporaryDirectory() as td:
            paths = one_row_revision(Path(td), "r.md", stock, rewrite,
                                      glossary=glossary, contract=contract)
            p = run(base_argv(paths))
        out = p.stdout + p.stderr
        self.assertEqual(p.returncode, 1, out)
        self.assertIn("FAIL header r.md", out)

    def test_header_statement_sentence_with_term_passes(self):
        stock = FRONTMATTER + "Follow the header convention.\n"
        rewrite = FRONTMATTER + "The header line follows the header-convention term.\n"
        contract = {
            "schema": 1, "required": {}, "ordered": {}, "forbidden": {},
            "header_statement": {
                "rows": ["r.md"], "term": "header-convention",
                "mentions": ["header line"],
            },
        }
        glossary = {
            "schema": 1,
            "terms": {"header-convention": {
                "text": "the header-convention term",
                "means": "m", "variants": [],
            }},
        }
        with tempfile.TemporaryDirectory() as td:
            paths = one_row_revision(Path(td), "r.md", stock, rewrite,
                                      glossary=glossary, contract=contract)
            p = run(base_argv(paths))
        out = p.stdout + p.stderr
        self.assertIn("PASS header r.md", out)


TWIN_STOCK = FRONTMATTER + "Do the task now.\n"


class TestTwins(unittest.TestCase):
    def test_twins_different_rewrites_fail(self):
        rows = {
            "a.md": (TWIN_STOCK, FRONTMATTER + "Do the task now, version A.\n"),
            "b.md": (TWIN_STOCK, FRONTMATTER + "Do the task now, version B.\n"),
        }
        with tempfile.TemporaryDirectory() as td:
            paths = make_revision(Path(td), rows=rows)
            p = run(base_argv(paths))
        out = p.stdout + p.stderr
        self.assertEqual(p.returncode, 1, out)
        self.assertTrue("FAIL twins a.md" in out or "FAIL twins b.md" in out, out)

    def test_twins_files_scope_with_one_twin_fails(self):
        rows = {
            "a.md": (TWIN_STOCK, FRONTMATTER + "Do the task now.\n"),
            "b.md": (TWIN_STOCK, FRONTMATTER + "Do the task now.\n"),
        }
        with tempfile.TemporaryDirectory() as td:
            paths = make_revision(Path(td), rows=rows)
            p = run(base_argv(paths, "--files", "a.md"))
        out = p.stdout + p.stderr
        self.assertEqual(p.returncode, 1, out)
        self.assertIn("FAIL twins a.md", out)

    def test_twins_identical_rewrites_pass(self):
        rows = {
            "a.md": (TWIN_STOCK, FRONTMATTER + "Do the task now.\n"),
            "b.md": (TWIN_STOCK, FRONTMATTER + "Do the task now.\n"),
        }
        with tempfile.TemporaryDirectory() as td:
            paths = make_revision(Path(td), rows=rows)
            p = run(base_argv(paths))
        out = p.stdout + p.stderr
        self.assertEqual(p.returncode, 0, out)
        self.assertIn("PASS twins a.md", out)
        self.assertIn("PASS twins b.md", out)


class TestRowset(unittest.TestCase):
    def test_rowset_empty_after_directory_fails(self):
        # Contract item 8: "An empty set fails." This is a row-item failure
        # (exit 1, FAIL rowset), not a usage error: prompts/after/ existing
        # but empty is a valid, checkable revision state.
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            paths = make_revision(tmp, rows={"a.md": (TWIN_STOCK, FRONTMATTER + "Do the task now.\n")})
            # Empty prompts/after/ by deleting the file the fixture wrote.
            (paths["revision_dir"] / "prompts" / "after" / "a.md").unlink()
            p = run(base_argv(paths))
        out = p.stdout + p.stderr
        self.assertEqual(p.returncode, 1, out)
        self.assertIn("FAIL rowset", out)

    def test_rowset_missing_row_fails(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            paths = make_revision(tmp, rows={
                "a.md": (TWIN_STOCK, FRONTMATTER + "Do the task now.\n"),
                "b.md": (TWIN_STOCK, FRONTMATTER + "Do the task now.\n"),
            })
            (paths["revision_dir"] / "prompts" / "after" / "b.md").unlink()
            p = run(base_argv(paths))
        out = p.stdout + p.stderr
        self.assertEqual(p.returncode, 1, out)
        self.assertIn("FAIL rowset", out)

    def test_rowset_extra_row_fails(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            paths = make_revision(tmp, rows={"a.md": (TWIN_STOCK, FRONTMATTER + "Do the task now.\n")})
            (paths["revision_dir"] / "prompts" / "after" / "extra.md").write_text(
                FRONTMATTER + "Extra body.\n", encoding="utf-8")
            p = run(base_argv(paths))
        out = p.stdout + p.stderr
        self.assertEqual(p.returncode, 1, out)
        self.assertIn("FAIL rowset", out)


class TestPerPromptFit(unittest.TestCase):
    def _rows_with_term_sentence(self, n: int) -> dict:
        rows = {}
        sentence = ("Write in plain English so the reader can act on it in one single read "
                    "through the whole prompt every time.\n")
        for i in range(n):
            name = f"row{i}.md"
            rows[name] = (FRONTMATTER + f"Stock body {i}.\n", FRONTMATTER + sentence)
        return rows

    def test_per_prompt_fit_term_sentence_in_three_rows_fails(self):
        with tempfile.TemporaryDirectory() as td:
            paths = make_revision(Path(td), rows=self._rows_with_term_sentence(3),
                                   glossary=TERM_GLOSSARY)
            p = run(base_argv(paths))
        out = p.stdout + p.stderr
        self.assertEqual(p.returncode, 1, out)
        self.assertIn("FAIL per-prompt-fit", out)

    def test_per_prompt_fit_term_sentence_in_two_rows_passes(self):
        with tempfile.TemporaryDirectory() as td:
            paths = make_revision(Path(td), rows=self._rows_with_term_sentence(2),
                                   glossary=TERM_GLOSSARY)
            p = run(base_argv(paths))
        out = p.stdout + p.stderr
        self.assertIn("PASS per-prompt-fit", out)


# Contract item S1 of reviews/sweep-addendum.txt. The gate counts one
# sentence, not one term.
SWEEP_GLOSSARY = {
    "schema": 1,
    "terms": {
        **TERM_GLOSSARY["terms"],
        "reader-can-act": {
            "text": "the reader can act",
            "means": "the reader can do the next step",
            "variants": [],
        },
    },
}


def sweep_rows(bodies: list) -> dict:
    """Give each rewrite body its own row with a different stock body."""
    return {f"row{i}.md": (FRONTMATTER + f"Stock body {i}.\n", FRONTMATTER + body + "\n")
            for i, body in enumerate(bodies)}


def fit_line(test, out: str) -> str:
    """Return the one per-prompt-fit line of the gate output."""
    found = [ln for ln in out.splitlines()
             if ln.startswith(("PASS per-prompt-fit", "FAIL per-prompt-fit"))]
    test.assertEqual(len(found), 1, out)
    return found[0]


class TestPerPromptFitSweep(unittest.TestCase):
    def _gate(self, rows: dict, glossary: dict = TERM_GLOSSARY):
        with tempfile.TemporaryDirectory() as td:
            paths = make_revision(Path(td), rows=rows, glossary=glossary)
            p = run(base_argv(paths))
        return p, p.stdout + p.stderr

    def test_s1a_different_sentences_one_term_three_rows_pass(self):
        rows = sweep_rows([
            "Write the summary in plain English for every new reader today.",
            "Keep each error message in plain English so users understand it.",
            "The final report uses plain English and short direct sentences throughout.",
        ])
        p, out = self._gate(rows)
        line = fit_line(self, out)
        self.assertTrue(line.startswith("PASS per-prompt-fit"), line)
        self.assertEqual(p.returncode, 0, out)

    def test_s1b_different_sentences_three_terms_five_rows_pass(self):
        rows = sweep_rows([
            "Write each answer in plain English for the person who asked it. "
            "Always lead with the result before you give any of the details.",
            "Use plain English in the status line at the top of the page. "
            "Lead with the result so the team sees the outcome at once. "
            "Check that the reader can act on each step without extra help.",
            "Plain English keeps the commit message clear for the next maintainer here. "
            "In a bug report, lead with the result and then list the steps. "
            "Write the plan so the reader can act on it with no open question.",
            "Explain the test failure in plain English before you propose a fix. "
            "Lead with the result of the search, then show the matching files. "
            "Give the path so the reader can act on the finding right away.",
            "Describe the risk in plain English and name the file it affects. "
            "Each summary must show that the reader can act on what it says.",
        ])
        p, out = self._gate(rows, SWEEP_GLOSSARY)
        line = fit_line(self, out)
        self.assertTrue(line.startswith("PASS per-prompt-fit"), line)
        self.assertEqual(p.returncode, 0, out)

    def test_s1c_same_sentence_case_and_space_differ_three_rows_fails(self):
        rows = sweep_rows([
            "Write the whole summary in plain English for each new reader.",
            "WRITE the whole  summary in plain English for each   new reader.",
            "write The Whole summary in plain English\tfor each new READER.",
        ])
        p, out = self._gate(rows)
        line = fit_line(self, out)
        self.assertTrue(line.startswith("FAIL per-prompt-fit"), line)
        for name in ("row0.md", "row1.md", "row2.md"):
            self.assertIn(name, line)
        self.assertEqual(p.returncode, 1, out)

    def test_s1d_same_sentence_twin_pair_and_one_row_passes(self):
        sentence = FRONTMATTER + "Write the whole summary in plain English for each new reader.\n"
        rows = {
            "a.md": (TWIN_STOCK, sentence),
            "b.md": (TWIN_STOCK, sentence),
            "c.md": (FRONTMATTER + "Stock body c.\n", sentence),
        }
        p, out = self._gate(rows)
        self.assertIn("PASS twins a.md", out)
        self.assertIn("PASS twins b.md", out)
        line = fit_line(self, out)
        self.assertTrue(line.startswith("PASS per-prompt-fit"), line)
        self.assertEqual(p.returncode, 0, out)

    def test_s1e_same_seven_word_sentence_three_rows_passes(self):
        rows = sweep_rows(["Write the summary in plain English now."] * 3)
        p, out = self._gate(rows)
        line = fit_line(self, out)
        self.assertTrue(line.startswith("PASS per-prompt-fit"), line)
        self.assertEqual(p.returncode, 0, out)


class TestScope(unittest.TestCase):
    def test_scope_failing_row_outside_files_leaves_exit_0(self):
        rows = {
            "good.md": (FRONTMATTER + "Do the task now.\n", FRONTMATTER + "Do the task now.\n"),
            "bad.md": (FRONTMATTER + "Send ${A} now.\n", FRONTMATTER + "Send now.\n"),
        }
        with tempfile.TemporaryDirectory() as td:
            paths = make_revision(Path(td), rows=rows)
            p = run(base_argv(paths, "--files", "good.md"))
        out = p.stdout + p.stderr
        self.assertEqual(p.returncode, 0, out)

    def test_scope_unknown_name_in_files_exits_2(self):
        with tempfile.TemporaryDirectory() as td:
            paths = one_row_revision(Path(td), "r.md", TWIN_STOCK, FRONTMATTER + "Do the task now.\n")
            p = run(base_argv(paths, "--files", "no-such-row.md"))
        self.assertEqual(p.returncode, 2, p.stdout + p.stderr)

    def test_scope_name_with_slash_exits_2(self):
        with tempfile.TemporaryDirectory() as td:
            paths = one_row_revision(Path(td), "r.md", TWIN_STOCK, FRONTMATTER + "Do the task now.\n")
            p = run(base_argv(paths, "--files", "sub/r.md"))
        self.assertEqual(p.returncode, 2, p.stdout + p.stderr)


class TestOutput(unittest.TestCase):
    def test_output_two_runs_byte_identical_sorted_order(self):
        rows = {
            "zzz.md": (FRONTMATTER + "Do the task now.\n", FRONTMATTER + "Do the task now.\n"),
            "aaa.md": (FRONTMATTER + "Do the task now.\n", FRONTMATTER + "Do the task now.\n"),
        }
        with tempfile.TemporaryDirectory() as td:
            paths = make_revision(Path(td), rows=rows)
            p1 = run(base_argv(paths))
            p2 = run(base_argv(paths))
        self.assertEqual(p1.stdout, p2.stdout)
        aaa_idx = p1.stdout.index("aaa.md")
        zzz_idx = p1.stdout.index("zzz.md")
        self.assertLess(aaa_idx, zzz_idx, "rows must print in sorted filename order")


class TestHardening(unittest.TestCase):
    def _clean_argv(self, td: Path):
        paths = one_row_revision(Path(td), "r.md", TWIN_STOCK, FRONTMATTER + "Do the task now.\n")
        return base_argv(paths)

    def test_hardening_max_seconds_zero_exits_2(self):
        with tempfile.TemporaryDirectory() as td:
            p = run(self._clean_argv(Path(td)) + ["--max-seconds", "0"])
        assert_gate_ran(self, p)
        self.assertEqual(p.returncode, 2, p.stderr)

    def test_hardening_watchdog_probe_negative_exits_2(self):
        with tempfile.TemporaryDirectory() as td:
            p = run(self._clean_argv(Path(td)) + ["--watchdog-probe", "-1"])
        assert_gate_ran(self, p)
        self.assertEqual(p.returncode, 2, p.stderr)

    def test_hardening_kill_path_exits_3_fast(self):
        with tempfile.TemporaryDirectory() as td:
            argv = self._clean_argv(Path(td)) + ["--max-seconds", "1", "--watchdog-probe", "5"]
            start = time.monotonic()
            p = run(argv, timeout=60)
            elapsed = time.monotonic() - start
        self.assertEqual(p.returncode, 3, f"stderr={p.stderr!r}")
        self.assertLess(elapsed, 5.0, f"kill took {elapsed:.2f}s")

    def test_hardening_glossary_with_nan_exits_2(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            paths = one_row_revision(tmp, "r.md", TWIN_STOCK, FRONTMATTER + "Do the task now.\n")
            paths["glossary_path"].write_text(
                '{"schema": 1, "terms": {"a": {"text": "x", "means": "m", "variants": [], "n": NaN}}}',
                encoding="utf-8")
            p = run(base_argv(paths))
        self.assertEqual(p.returncode, 2, p.stdout + p.stderr)

    def test_hardening_contract_with_duplicate_key_exits_2(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            paths = one_row_revision(tmp, "r.md", TWIN_STOCK, FRONTMATTER + "Do the task now.\n")
            contract_path = paths["revision_dir"] / "contract.json"
            contract_path.write_text(
                '{"schema": 1, "required": {}, "required": {}, "ordered": {}, '
                '"forbidden": {}, "header_statement": {"rows": [], "term": null, "mentions": []}}',
                encoding="utf-8")
            p = run(base_argv(paths))
        self.assertEqual(p.returncode, 2, p.stdout + p.stderr)


if __name__ == "__main__":
    unittest.main()
