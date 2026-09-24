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
        self.assertEqual(p.returncode, 2, out)
        self.assertTrue(str(ledger_path) in out or ledger_path.as_posix() in out, out)
        self.assertNotIn("FAIL carry-forward r.md", out)
        self.assertNotIn("PASS carry-forward r.md", out)

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


class TestRound1(unittest.TestCase):
    """Items R1-1, R1-2, and R1-3 of reviews/round1-addendum.txt.

    Each test walks many cases. It keeps each failing case, prints the case
    count, and fails one time at the end.
    """

    STOCK = "Be brief always."
    ADDED = "Explain every reasoning step in full detail."
    QUOTE = "Explain every reasoning step in full detail"
    UNIT8 = "Keep every answer short and skip the details."
    FM_QUOTE = '<!--\nname: "System Prompt: Explain every reasoning step in full detail"\nccVersion: "2.1.280"\n-->\n'
    FM_A = '<!--\nname: "System Prompt: Alpha"\nccVersion: "2.1.280"\n-->\n'
    FM_B = '<!--\nname: "System Prompt: Beta"\nccVersion: "2.1.280"\n-->\n'
    TERM_LINE = "Write the whole summary in plain English for each new reader"
    SHORT_TERM_LINE = "Write the summary in plain English now"

    def _run_rows(self, rows, *extra, glossary=None, rule_files=None, ledgers=None):
        with tempfile.TemporaryDirectory() as td:
            paths = make_revision(Path(td), rows=rows, glossary=glossary,
                                  rule_files=rule_files, ledgers=ledgers)
            p = run(base_argv(paths, *extra))
        return p, p.stdout + p.stderr, paths

    def _run_cf(self, body, ledger, stock=None, added=None, fm=FRONTMATTER):
        stock = self.STOCK if stock is None else stock
        added = self.ADDED if added is None else added
        rule = {"id": "r", "rules": [{"description": "d", "stock": [stock], "unnerf": [added]}]}
        rows = {"r.md": (fm + stock + "\n", fm + body + "\n")}
        return self._run_rows(rows, rule_files={"r": rule}, ledgers={"r.md": ledger})

    @staticmethod
    def _ledger(*points):
        return {"row": "r.md", "points": list(points)}

    def _point(self, new, old=None, pid="r#0+0"):
        return {"id": pid, "old": self.ADDED if old is None else old, "new": new}

    @staticmethod
    def _item_line(out, prefix):
        for ln in out.splitlines():
            if ln.startswith(("PASS " + prefix, "FAIL " + prefix)):
                return ln
        return ""

    @staticmethod
    def _cf_block(out):
        """Return the carry-forward FAIL line and the detail lines below it."""
        block, inside = [], False
        for ln in out.splitlines():
            if ln.startswith(("PASS ", "FAIL ", "summary:", "GATE ")):
                inside = ln.startswith("FAIL carry-forward r.md")
            if inside:
                block.append(ln)
        return block

    def _finish(self, count, failures):
        print(f"\n{self._testMethodName}: {count} case(s), {len(failures)} failing")
        self.assertEqual(failures, [], "\n".join(failures))

    def _cf_verdict(self, name, want, body, ledger, **kw):
        p, out, _ = self._run_cf(body, ledger, **kw)
        line = self._item_line(out, "carry-forward r.md")
        code = 0 if want == "PASS" else 1
        if line.startswith(want + " ") and p.returncode == code:
            return None
        return f"{name}: want {want} carry-forward and exit {code}, got exit {p.returncode}: {out[-700:]!r}"

    def test_r1_1a_quote_only_in_frontmatter_fails(self):
        body_quote = "Explain each reasoning step in full"
        cases = [
            ("string quote", "FAIL", "Give the answer now.", self._point(self.QUOTE)),
            ("list quote", "FAIL", "Give the answer now.", self._point([self.QUOTE])),
            ("list with one quote in the body", "FAIL", body_quote + ".", self._point([body_quote, self.QUOTE])),
            ("control with the quote in the body", "PASS", self.ADDED, self._point(self.QUOTE)),
        ]
        failures = [m for m in (self._cf_verdict(n, w, b, self._ledger(pt), fm=self.FM_QUOTE)
                                for n, w, b, pt in cases) if m]
        self._finish(len(cases), failures)

    def test_r1_1b_quote_forms_and_quote_rules(self):
        q, a = self.QUOTE, self.ADDED
        cases = [
            ("one string quote", "PASS", a, self._point(q)),
            ("list of one quote", "PASS", a, self._point([q])),
            ("list of two quotes", "PASS", "Explain every reasoning step now. Give it in full detail.", self._point(["Explain every reasoning step now", "Give it in full detail"])),
            ("1-word string quote", "FAIL", a, self._point("Explain")),
            ("2-word string quote", "FAIL", a, self._point("Explain every")),
            ("3-word string quote", "FAIL", a, self._point("Explain every reasoning")),
            ("1-word quote in a list", "FAIL", a, self._point([q, "detail"])),
            ("2-word quote in a list", "FAIL", a, self._point([q, "full detail"])),
            ("3-word quote in a list", "FAIL", a, self._point([q, "in full detail"])),
            ("string quote not in the body", "FAIL", a, self._point("Explain each reasoning step in full detail")),
            ("list quote not in the body", "FAIL", a, self._point([q, "Give the answer in one word"])),
            ("body with other whitespace", "PASS", "Explain every  reasoning\tstep in\nfull   detail.", self._point(q)),
            ("quote with other whitespace", "PASS", a, self._point("Explain  every reasoning\tstep in\nfull   detail")),
            ("list quote with other whitespace", "PASS", a, self._point(["Explain every\treasoning step", "step  in full detail"])),
        ]
        failures = [m for m in (self._cf_verdict(n, w, b, self._ledger(pt)) for n, w, b, pt in cases) if m]
        self._finish(len(cases), failures)

    def test_r1_1c_coverage_of_old_content_words(self):
        # Each synthetic word has 5 letters, so each one has its own 5-letter form.
        words = ["zq" + chr(97 + i // 26) + chr(97 + i % 26) + "a" for i in range(200)]
        cases = []
        for covered, total, want in ((59, 100, "FAIL"), (60, 100, "PASS"), (119, 200, "FAIL"),
                                     (120, 200, "PASS"), (3, 5, "PASS"), (5, 9, "FAIL"),
                                     (11, 19, "FAIL"), (6, 10, "PASS")):
            old = " ".join(words[:total]) + "."
            cases.append((f"{covered} of {total} words", want, old, [" ".join(words[:covered]) + " and then"]))
        cases += [
            ("stop words in old never count", "PASS", "explain reasoning with every source and each risk they have.", ["explain reasoning source risk"]),
            ("short words in old never count", "PASS", "go to it so we do explain reasoning.", ["please explain your reasoning"]),
            ("stop word in a quote never matches", "FAIL", "explain reasoning thereafter cite sources.", ["explain the reasoning there now"]),
            ("first 5 letters match", "PASS", "explaining reasoning carefully.", ["an explanation of your reasons"]),
            ("first 4 letters are not sufficient", "FAIL", "stressing the reasoning.", ["the stream of reasons"]),
            ("all quotes of one point count together", "PASS", "explain reasoning cite sources name gaps.", ["explain the reasoning now", "cite all the sources"]),
        ]
        failures = []
        for name, want, old, quotes in cases:
            new = quotes[0] if len(quotes) == 1 else quotes
            msg = self._cf_verdict(name, want, ". ".join(quotes) + ".",
                                   self._ledger(self._point(new, old=old)), added=old)
            if msg:
                failures.append(msg)
        self._finish(len(cases), failures)

    def test_r1_1d_removed_unit_in_a_longer_line_fails(self):
        a, u = self.ADDED, self.UNIT8
        cases = [
            ("8-word unit in a longer line", "FAIL", u, f"{a} {u} Then stop."),
            ("unit with other case and spacing", "FAIL", u, f"{a} KEEP every answer  short and\tskip the Details. Then stop."),
            ("unit of a stock line with 2 units", "FAIL", "Be brief. " + u, f"{a} {u}"),
            ("unit as a list item", "FAIL", u, f"{a}\n- {u}\nThen stop."),
            ("6-word unit", "FAIL", "Keep every answer very short always.", f"{a} Keep every answer very short always. Then stop."),
            ("5-word unit", "PASS", "Keep every answer very short.", f"{a} Keep every answer very short. Then stop."),
            ("old whole-line case", "FAIL", self.STOCK, f"{a}\n{self.STOCK}"),
        ]
        ledger = self._ledger(self._point(self.QUOTE))
        failures = [m for m in (self._cf_verdict(n, w, b, ledger, stock=s) for n, w, s, b in cases) if m]
        self._finish(len(cases), failures)

    def test_r1_1e_fail_detail_names_point_and_check(self):
        q, a, u = self.QUOTE, self.ADDED, self.UNIT8
        cases = [
            ("quote not found", "r#0+0", ("not found",), self.STOCK, a, self._point("Explain each reasoning step in full detail")),
            ("quote under 4 words", "r#0+0", ("4", "word"), self.STOCK, a, self._point([q, "full detail"])),
            ("coverage", "r#0+0", ("coverage", "40"), self.STOCK, "Explain the reasoning to them.", self._point("Explain the reasoning to them")),
            ("removed unit present", "r#0-0", ("removed", "unit"), u, f"{a} {u} Then stop.", self._point(q)),
        ]
        failures = []
        for name, pid, keys, stock, body, pt in cases:
            p, out, _ = self._run_cf(body, self._ledger(pt), stock=stock)
            hits = [ln for ln in self._cf_block(out)
                    if pid in ln and all(k in ln.lower() for k in keys)]
            if p.returncode != 1 or not hits:
                failures.append(f"{name}: want a FAIL detail line with {pid} and {keys}, "
                                f"got exit {p.returncode}: {out[-700:]!r}")
        self._finish(len(cases), failures)

    def test_r1_1f_ledger_shape_errors_exit_2(self):
        q = self.QUOTE
        good = self._point(q)
        non_string = [0, 1.5, True, None, [], {}]
        cases = [(f"points={v!r}", {"row": "r.md", "points": v}) for v in ({}, "points", "", 0, 1.5, True, False, None)]
        cases += [(f"point={v!r}", self._ledger(v)) for v in ([], "point", 0, 1.5, True, None)]
        cases += [(f"id={v!r}", self._ledger({**good, "id": v})) for v in non_string]
        cases.append(("duplicate id", self._ledger(good, dict(good))))
        cases += [(f"old={v!r}", self._ledger({**good, "old": v})) for v in non_string]
        bad_new = ["", [], [""], [0], [None], [[q]], [{}], [q, ""], [q, 0], 0, 1.5, True, False, None, {}]
        cases += [(f"new={v!r}", self._ledger({**good, "new": v})) for v in bad_new]
        failures = []
        for name, ledger in cases:
            p, out, paths = self._run_cf(self.ADDED, ledger)
            path = paths["revision_dir"] / "carry-forward" / "r.json"
            named = str(path) in out or path.as_posix() in out
            item_lines = [ln for ln in out.splitlines() if ln.startswith(("PASS ", "FAIL "))]
            if p.returncode != 2 or not named or item_lines:
                failures.append(f"{name}: want exit 2, the ledger path, and no item line, "
                                f"got exit {p.returncode}: {out[-500:]!r}")
        p, out, _ = self._run_cf(self.ADDED, self._ledger(good, self._point(q, old="nope", pid="r#0+9")))
        if p.returncode != 1 or "FAIL carry-forward r.md" not in out:
            failures.append(f"unknown id: want exit 1 and FAIL carry-forward, got exit {p.returncode}: {out[-500:]!r}")
        self._finish(len(cases) + 1, failures)

    def test_r1_2_twins_ignore_the_frontmatter(self):
        fa, fb, stock = self.FM_A, self.FM_B, "Do the task now.\n"
        term = self.TERM_LINE + ".\n"

        def pair(ra, rb, **more):
            rows = {"a.md": (fa + stock, fa + ra), "b.md": (fb + stock, fb + rb)}
            rows.update(more)
            return rows

        def both_pass(o):
            return "PASS twins a.md" in o and "PASS twins b.md" in o

        def one_fails(o):
            return "FAIL twins a.md" in o or "FAIL twins b.md" in o

        same = "Do the whole task now.\n"
        cases = [
            ("other frontmatter, other rewrites", pair("Do the task now, version A.\n", "Do the task now, version B.\n"), (), one_fails, 1),
            ("other frontmatter, same rewrites", pair(same, same), (), both_pass, 0),
            ("other frontmatter, one twin in --files", pair(same, same), ("--files", "a.md"), lambda o: "FAIL twins a.md" in o, 1),
            ("same frontmatter, other rewrites", {"a.md": (TWIN_STOCK, FRONTMATTER + "Version A.\n"), "b.md": (TWIN_STOCK, FRONTMATTER + "Version B.\n")}, (), one_fails, 1),
            ("same frontmatter, same rewrites", {"a.md": (TWIN_STOCK, FRONTMATTER + same), "b.md": (TWIN_STOCK, FRONTMATTER + same)}, (), both_pass, 0),
            ("fit counts the pair as one row", pair(term, term, **{"c.md": (FRONTMATTER + "Stock body c.\n", FRONTMATTER + term)}), (), lambda o: both_pass(o) and "PASS per-prompt-fit" in o, 0),
            ("fit control with no pair", {"a.md": (fa + "Stock a.\n", fa + term), "b.md": (fb + "Stock b.\n", fb + term), "c.md": (FRONTMATTER + "Stock c.\n", FRONTMATTER + term)}, (), lambda o: "FAIL per-prompt-fit" in o, 1),
        ]
        failures = []
        for name, rows, extra, check, code in cases:
            p, out, _ = self._run_rows(rows, *extra, glossary=TERM_GLOSSARY)
            if p.returncode != code or not check(out):
                failures.append(f"{name}: want exit {code}, got exit {p.returncode}: {out[-700:]!r}")
        self._finish(len(cases), failures)

    def test_r1_3_fit_units_split_on_line_breaks(self):
        def body8(i, m):
            return f"Row {i} has these steps:\n{m}{self.TERM_LINE}\nThen add row {i} to the log\n"

        def body7(i, m):
            return f"Row {i} has one step.\n{m}{self.SHORT_TERM_LINE}\n"

        def rows(bodies):
            return {f"row{i}.md": (FRONTMATTER + f"Stock body {i}.\n", FRONTMATTER + b)
                    for i, b in enumerate(bodies)}

        cases = []
        for m in ("- ", "* ", "1. "):
            cases.append((f"8+ word line after {m!r} in 3 rows", rows([body8(i, m) for i in range(3)]), "FAIL"))
            cases.append((f"8+ word line after {m!r} in 2 rows", rows([body8(0, m), body8(1, m), "Row 2 has no such step.\n"]), "PASS"))
            cases.append((f"7 words after {m!r} in 3 rows", rows([body7(i, m) for i in range(3)]), "PASS"))
        cases.append(("8+ word line with no marker in 3 rows", rows([body8(i, "") for i in range(3)]), "FAIL"))
        cases.append(("8+ word line after mixed markers in 3 rows", rows([body8(0, "- "), body8(1, "* "), body8(2, "1. ")]), "FAIL"))
        failures = []
        for name, rws, want in cases:
            p, out, _ = self._run_rows(rws, glossary=TERM_GLOSSARY)
            line = self._item_line(out, "per-prompt-fit")
            ok = line.startswith(want + " ") and p.returncode == (0 if want == "PASS" else 1)
            if ok and want == "FAIL":
                ok = all(r in line for r in ("row0.md", "row1.md", "row2.md"))
            if not ok:
                failures.append(f"{name}: want {want} per-prompt-fit, got exit {p.returncode}: {out[-700:]!r}")
        self._finish(len(cases), failures)


class TestRound1RemovedUnitExemption(unittest.TestCase):
    """Item R1-1d' of reviews/round1-addendum.txt.

    If an added line of the same entry has a removed unit, that unit is exempt.
    The test walks each case, prints the case count, and fails one time.
    """

    SHARED = "Write the answer in plain words for the reader."
    EXTRA = "Give full detail when the task needs it."
    OTHER = "Stop after the first answer and add nothing more."

    def _case(self, name, want, entries, body, points):
        # entries: a list of (stock lines, unnerf lines), one per entry.
        # points: a list of (point id, old line, quote).
        rule = {"id": "r", "rules": [
            {"description": "d", "stock": s, "unnerf": u} for s, u in entries]}
        stock = FRONTMATTER + "\n".join(ln for s, _ in entries for ln in s) + "\n"
        ledger = {"row": "r.md", "points": [
            {"id": i, "old": o, "new": q} for i, o, q in points]}
        with tempfile.TemporaryDirectory() as td:
            paths = one_row_revision(
                Path(td), "r.md", stock, FRONTMATTER + body + "\n",
                rule_files={"r": rule}, ledgers={"r.md": ledger})
            p = run(base_argv(paths))
        out = p.stdout + p.stderr
        code = 0 if want == "PASS" else 1
        line = next((ln for ln in out.splitlines()
                     if ln.startswith(("PASS carry-forward r.md", "FAIL carry-forward r.md"))), "")
        ok = line.startswith(want + " ") and p.returncode == code
        if ok and want == "FAIL":
            ok = any("r#0-0" in ln and "removed unit" in ln for ln in out.splitlines())
        if ok:
            return None
        return f"{name}: want {want} carry-forward and exit {code}, got exit {p.returncode}: {out[-700:]!r}"

    def test_r1_1d_prime_removed_unit_kept_by_an_added_line(self):
        sh, ex, ot = self.SHARED, self.EXTRA, self.OTHER
        stock1 = "Keep it short. " + sh
        added1 = sh + " " + ex
        stock2 = stock1 + " " + ot
        added3 = "write the  answer in PLAIN words\tfor the reader. " + ex
        cases = [
            ("shared unit of the same entry", "PASS",
             [([stock1], [added1])], added1,
             [("r#0+0", added1, added1)]),
            ("second unit that no added line holds", "FAIL",
             [([stock2], [added1])], added1 + " " + ot,
             [("r#0+0", added1, added1)]),
            ("shared unit with other case and spacing", "PASS",
             [([stock1], [added3])], added1,
             [("r#0+0", added3, added1)]),
            ("shared unit only in the added line of an other entry", "FAIL",
             [([stock1], [ex]), (["Be brief always."], [sh + " Name every risk that you find."])],
             ex + " " + sh + " Name every risk that you find.",
             [("r#0+0", ex, ex), ("r#1+0", sh + " Name every risk that you find.",
                                  sh + " Name every risk that you find.")]),
            ("shared unit as a list item in the added line", "PASS",
             [([stock1], [ex, "- " + sh])], ex + "\n- " + sh,
             [("r#0+0", ex, ex), ("r#0+1", "- " + sh, sh)]),
        ]
        failures = [m for m in (self._case(*c) for c in cases) if m]
        print(f"\n{self._testMethodName}: {len(cases)} case(s), {len(failures)} failing")
        self.assertEqual(failures, [], "\n".join(failures))


class TestRound2TwinGroups(unittest.TestCase):
    """R2-1: a twin group of any size counts as one row in per-prompt-fit."""

    SENTENCE = FRONTMATTER + "Write the whole summary in plain English for each new reader.\n"

    def _gate(self, rows: dict):
        with tempfile.TemporaryDirectory() as td:
            paths = make_revision(Path(td), rows=rows, glossary=TERM_GLOSSARY)
            p = run(base_argv(paths))
        return p, p.stdout + p.stderr

    def _triplet(self, after=None):
        after = after or {}
        return {name: (TWIN_STOCK, after.get(name, self.SENTENCE)) for name in ("a.md", "b.md", "c.md")}

    def test_r2_1a_triplet_and_one_row_passes(self):
        rows = self._triplet()
        rows["d.md"] = (FRONTMATTER + "Stock body d.\n", self.SENTENCE)
        p, out = self._gate(rows)
        line = fit_line(self, out)
        self.assertTrue(line.startswith("PASS per-prompt-fit"), line)
        self.assertEqual(p.returncode, 0, out)

    def test_r2_1b_triplet_and_two_rows_fails_and_names_the_sentence(self):
        rows = self._triplet()
        rows["d.md"] = (FRONTMATTER + "Stock body d.\n", self.SENTENCE)
        rows["e.md"] = (FRONTMATTER + "Stock body e.\n", self.SENTENCE)
        p, out = self._gate(rows)
        line = fit_line(self, out)
        self.assertTrue(line.startswith("FAIL per-prompt-fit"), line)
        self.assertIn("write the whole summary in plain english", line.lower())
        self.assertEqual(p.returncode, 1, out)

    def test_r2_1d_triplet_with_one_different_rewrite_fails_twins(self):
        rows = self._triplet({"c.md": FRONTMATTER + "Do the task now.\n"})
        p, out = self._gate(rows)
        self.assertRegex(out, r"FAIL twins [abc]\.md")
        self.assertEqual(p.returncode, 1, out)


if __name__ == "__main__":
    unittest.main()
