"""Black-box tests for check_claims.py (stdlib unittest, subprocess only)."""
import copy
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "check_claims.py")


def _find_workspace():
    """Return the .claude/workspace directory of the project that holds
    this test file. Walk up from HERE until a parent holds
    .claude/workspace/prompt-store. If no parent holds one, return
    None. A copy of the skill outside the project has no such parent."""
    d = HERE
    while True:
        candidate = os.path.join(d, ".claude", "workspace")
        if os.path.isdir(os.path.join(candidate, "prompt-store")):
            return candidate
        parent = os.path.dirname(d)
        if parent == d:
            return None
        d = parent


WORKSPACE = _find_workspace()


def run(*args, timeout=60):
    return subprocess.run([sys.executable, SCRIPT, *args], capture_output=True, text=True, timeout=timeout)


def run_parallel_cases(case_fns, max_workers=16):
    """Run each zero-argument callable in case_fns on its own worker
    thread. Return their results in the same order case_fns lists them.
    Each callable does all of its own file I/O and subprocess work. It
    must touch no shared state. Each mutation walk gives every case its
    own temp directory for this reason. This function raises nothing
    itself. A callable's own exception surfaces from the corresponding
    result via Future.result(), at the call site, on the main thread.
    The caller then decides how to report it."""
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = [pool.submit(fn) for fn in case_fns]
        return [f.result() for f in futures]


class T(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        with open(os.path.join(self.d, "src.txt"), "w", encoding="utf-8") as f:
            f.write("line one\nthe server listens on 443\nline three\n")

    def write(self, name, obj):
        p = os.path.join(self.d, name)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(obj, f) if isinstance(obj, dict) else f.write(obj)
        return p

    def test_pass(self):
        p = self.write("c.json", {
            "claims": [
                {"id": "c1", "category": "observation", "anchor": "src.txt:2", "quote": "listens on 443"},
                {"id": "c2", "category": "inference", "from": ["c1"], "text": "443 is open"},
                {"id": "c3", "category": "prior", "text": "nginx supports SNI"},
            ],
            "decisions": [{"id": "d1", "depends_on": ["c2"]}],
        })
        r = run(p)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_decision_on_prior_fails(self):
        p = self.write("c.json", {
            "claims": [{"id": "c3", "category": "prior", "text": "nginx supports SNI"}],
            "decisions": [{"id": "d1", "depends_on": ["c3"]}],
        })
        r = run(p)
        self.assertEqual(r.returncode, 1)
        self.assertIn("ungrounded claim c3", r.stdout)

    def test_bad_quote_fails(self):
        p = self.write("c.json", {"claims": [
            {"id": "c1", "category": "observation", "anchor": "src.txt:2", "quote": "listens on 80"}]})
        r = run(p)
        self.assertEqual(r.returncode, 1)
        self.assertIn("quote not found", r.stdout)

    def test_prose_untagged_process_claim_fails(self):
        p = self.write("c.json", {"claims": [
            {"id": "c1", "category": "observation", "anchor": "src.txt:2"}]})
        prose = self.write("p.md", "I reviewed the list. The port is 443 [c1]. Every tool needs a server.")
        r = run(p, "--prose", prose)
        self.assertEqual(r.returncode, 1)
        self.assertIn("I reviewed the list", r.stdout)
        self.assertIn("Every tool", r.stdout)

    def test_enumeration_gate_fails_unenumerated_citation(self):
        p = self.write("c.json", {
            "claims": [{"id": "c1", "category": "observation", "anchor": "src.txt:2"}],
            "decisions": [{"id": "d1", "cites": ["router log system"], "depends_on": ["c1"]}],
        })
        r = run(p)
        self.assertEqual(r.returncode, 1)
        self.assertIn("no enumerated attributes", r.stdout)

    def test_enumerated_attribute_needs_no_justification(self):
        """Enumeration is cheap: an attribute nothing depends on needs three fields."""
        p = self.write("c.json", {
            "claims": [
                {"id": "c1", "category": "example-attribute", "source": "router log system",
                 "attribute": "stored on a USB drive", "kind": "incidental"},
                {"id": "c2", "category": "observation", "anchor": "src.txt:2"},
            ],
            "decisions": [{"id": "d1", "cites": ["router log system"], "depends_on": ["c2"]}],
        })
        r = run(p)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_load_bearing_attribute_needs_breakage_test(self):
        p = self.write("c.json", {
            "claims": [{"id": "c1", "category": "example-attribute", "source": "router log system",
                        "attribute": "rotate 9999", "kind": "essential"}],
            "decisions": [{"id": "d1", "cites": ["router log system"], "depends_on": ["c1"]}],
        })
        r = run(p)
        self.assertEqual(r.returncode, 1)
        self.assertIn("breaks_if_absent", r.stdout)

    def test_cycle_fails(self):
        p = self.write("c.json", {"claims": [
            {"id": "c1", "category": "inference", "from": ["c2"]},
            {"id": "c2", "category": "inference", "from": ["c1"]}]})
        r = run(p)
        self.assertEqual(r.returncode, 1)
        self.assertIn("cycle", r.stdout)

    def test_usage_errors(self):
        self.assertEqual(run("nope.json").returncode, 2)
        p = self.write("bad.json", "{not json")
        self.assertEqual(run(p).returncode, 2)
        self.assertEqual(run(p, "--max-seconds", "0").returncode, 2)

    def test_watchdog_kills(self):
        p = self.write("c.json", {"claims": [{"id": "c1", "category": "prior"}]})
        t0 = time.monotonic()
        r = run(p, "--watchdog-probe", "10", "--max-seconds", "1")
        self.assertEqual(r.returncode, 3)
        self.assertLess(time.monotonic() - t0, 5)

    def test_help_mentions_flags(self):
        r = run("--help")
        self.assertIn("--max-seconds", r.stdout)
        self.assertIn("--watchdog-probe", r.stdout)


class AddendumRoundOneTests(unittest.TestCase):
    """Round 1 addendum items B1 and B2. See round1-addendum.txt for the
    contract text."""

    def setUp(self):
        self.d = tempfile.mkdtemp()
        with open(os.path.join(self.d, "src.txt"), "w", encoding="utf-8") as f:
            f.write("line one\nthe server listens on 443\nline three\n")

    def write(self, name, obj):
        p = os.path.join(self.d, name)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(obj, f) if isinstance(obj, dict) else f.write(obj)
        return p

    def test_addendum_b1_root_not_object_fails_cleanly(self):
        p = self.write("c.json", "[]")
        r = run(p)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertNotIn("Traceback", r.stderr)

    def test_addendum_b2_claims_list_holds_non_object_item_fails_cleanly(self):
        p = self.write("c.json", {"claims": [None]})
        r = run(p)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertNotIn("Traceback", r.stderr)


class AddendumRoundTwoTests(unittest.TestCase):
    """Round 2 addendum items C1 and C5. See round2-addendum.txt for the
    contract text."""

    def setUp(self):
        self.d = tempfile.mkdtemp()
        with open(os.path.join(self.d, "src.txt"), "w", encoding="utf-8") as f:
            f.write("line one\nthe server listens on 443\nline three\n")

    def write(self, name, obj):
        p = os.path.join(self.d, name)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(obj, f) if isinstance(obj, dict) else f.write(obj)
        return p

    def test_addendum_c1_bad_max_seconds_and_watchdog_values_fail_cleanly(self):
        import threading as _threading
        over_ceiling = _threading.TIMEOUT_MAX * 10
        p = self.write("c.json", {"claims": [{"id": "c1", "category": "prior"}]})
        bad_flag_values = [
            ("--max-seconds", "nan"),
            ("--max-seconds", "inf"),
            ("--watchdog-probe", "nan"),
            ("--watchdog-probe", "inf"),
            ("--max-seconds", repr(over_ceiling)),
        ]
        for flag, value in bad_flag_values:
            with self.subTest(flag=flag, value=value):
                r = run(p, flag, value)
                self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
                self.assertNotIn("Traceback", r.stderr)
                self.assertNotIn("Exception in thread", r.stderr)

    def test_addendum_c1_positive_control_small_max_seconds_still_runs(self):
        p = self.write("c.json", {"claims": [{"id": "c1", "category": "prior"}]})
        r = run(p, "--max-seconds", "60")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_addendum_c5_decisions_list_holds_non_object_item_fails_cleanly(self):
        p = self.write("c.json", {
            "claims": [{"id": "c1", "category": "observation", "anchor": "src.txt:2",
                        "quote": "listens on 443"}],
            "decisions": [None],
        })
        r = run(p)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertNotIn("Traceback", r.stderr)


class AddendumRoundFourTests(unittest.TestCase):
    """Round 4 addendum items E1 and E2. See round4-addendum.txt for the
    contract text."""

    def setUp(self):
        self.d = tempfile.mkdtemp()
        with open(os.path.join(self.d, "src.txt"), "w", encoding="utf-8") as f:
            f.write("line one\nthe server listens on 443\nline three\n")

    def write(self, name, obj):
        p = os.path.join(self.d, name)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(obj, f) if isinstance(obj, dict) else f.write(obj)
        return p

    def test_addendum_e1_empty_cmd_anchor_with_output_is_not_grounded(self):
        # "cmd:" with nothing after it (or only spaces) must not ground a
        # claim, even with non-empty output. A decision that depends on it
        # must make the run exit 1, and stdout must name that decision.
        for bad_anchor in ("cmd:", "cmd: "):
            with self.subTest(anchor=bad_anchor):
                p = self.write("c.json", {
                    "claims": [{"id": "c1", "category": "observation",
                                "anchor": bad_anchor, "output": "some output"}],
                    "decisions": [{"id": "d1", "depends_on": ["c1"]}],
                })
                r = run(p)
                self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
                self.assertIn("d1", r.stdout)

    def test_addendum_e2_non_string_anchor_gives_exit_2_and_clean_error(self):
        p = self.write("c.json", {"claims": [{"id": "c1", "category": "observation", "anchor": 7}]})
        r = run(p)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertNotIn("Traceback", r.stderr)


class AddendumRoundFiveTests(unittest.TestCase):
    """Round 5 addendum items F1 and F2. See round5-addendum.txt for the
    contract text. F1 is a mutation property: it walks every field of a
    valid claims table and gives each field every JSON type it must not
    hold. F2 checks one specific string-typed depends_on value."""

    LOAD_BEARING_FIELDS = {"id", "category", "anchor", "quote", "output", "saved", "depends_on"}

    JSON_TYPE_SAMPLES = dict([
        ("number", 1), ("boolean", True), ("null", None),
        ("string", "x"), ("list", [1]), ("object", {"k": 1}),
    ])

    def setUp(self):
        self.d = tempfile.mkdtemp()
        with open(os.path.join(self.d, "src.txt"), "w", encoding="utf-8") as f:
            f.write("line one\nthe server listens on 443\nline three\n")
        with open(os.path.join(self.d, "saved.html"), "w", encoding="utf-8") as f:
            f.write("saved copy")

    def write(self, name, obj):
        p = os.path.join(self.d, name)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(obj, f) if isinstance(obj, dict) else f.write(obj)
        return p

    def _run_case_isolated(self, table, load_bearing_expected=None):
        """Run one mutation case in its own fresh temp directory, so
        parallel cases never share a directory or a file. This method
        does no assertion. It returns the raw subprocess result plus
        load_bearing_expected unchanged, so the caller (on the main
        thread) can assert against it after every worker finishes."""
        case_dir = tempfile.mkdtemp()
        with open(os.path.join(case_dir, "src.txt"), "w", encoding="utf-8") as f:
            f.write("line one\nthe server listens on 443\nline three\n")
        with open(os.path.join(case_dir, "saved.html"), "w", encoding="utf-8") as f:
            f.write("saved copy")
        p = os.path.join(case_dir, "mutated.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump(table, f)
        r = run(p)
        return r, load_bearing_expected

    def base_table(self):
        """One valid claims table. It holds one claim of each category
        the gate accepts. It holds an observation with each of the
        three anchor forms, a prior, an inference, and an
        example-attribute. Every optional field of a claim and of a
        decision is present in a valid form."""
        claims = [
            dict(id="c1", category="observation", text="t1",
                 anchor="src.txt:2", quote="listens on 443"),
            dict(id="c2", category="observation", text="t2",
                 anchor="cmd: claude --version", output="2.1.280"),
            dict(id="c3", category="observation", text="t3",
                 anchor="url: https://example.test/x", saved="saved.html"),
            dict(id="c4", category="prior", text="t4, not verified here"),
            dict(id="c5", category="inference", text="t5", **{"from": ["c1"]}),
            dict(id="c6", category="example-attribute", text="t6",
                 source="router log system", attribute="rotate 9999",
                 kind="essential", borrowed="retention policy",
                 breaks_if_absent="loses the rate baseline",
                 verdict="adopted", anchor="src.txt:2"),
        ]
        decisions = [
            dict(id="d1", text="decide", depends_on=["c1", "c5", "c6"],
                 cites=["router log system"], concept="k1", file="src.txt",
                 state="carries-defect", seed_id="s1", verdict="adopted",
                 phase=12, kind="rowset"),
        ]
        return {"claims": claims, "decisions": decisions}

    def _json_type_of(self, value):
        if isinstance(value, bool):
            return "boolean"
        if isinstance(value, str):
            return "string"
        if isinstance(value, list):
            return "list"
        if isinstance(value, dict):
            return "object"
        if isinstance(value, (int, float)):
            return "number"
        return "null"

    def _wrong_type_samples(self, value):
        own_type = self._json_type_of(value)
        return {t: v for t, v in self.JSON_TYPE_SAMPLES.items() if t != own_type}

    def test_addendum_f1_mutation_property_over_every_claim_and_decision_field(self):
        table = self.base_table()
        p = self.write("c.json", table)
        base_run = run(p)
        self.assertEqual(base_run.returncode, 0, base_run.stdout + base_run.stderr)

        # Build every case up front as a zero-argument callable, each
        # closed over its own mutated table copy. Every callable does
        # its own file I/O, in its own temp directory, through
        # _run_case_isolated. The parallel run below never lets two
        # cases share a directory or a file, for this reason.
        cases = []
        for claim_index, claim in enumerate(table["claims"]):
            for field, value in claim.items():
                for type_name, wrong_value in self._wrong_type_samples(value).items():
                    mutated = copy.deepcopy(table)
                    mutated["claims"][claim_index][field] = wrong_value
                    cases.append((
                        dict(node="claim", index=claim_index, field=field, type=type_name),
                        field, mutated))
        for decision_index, decision in enumerate(table["decisions"]):
            for field, value in decision.items():
                for type_name, wrong_value in self._wrong_type_samples(value).items():
                    mutated = copy.deepcopy(table)
                    mutated["decisions"][decision_index][field] = wrong_value
                    cases.append((
                        dict(node="decision", index=decision_index, field=field, type=type_name),
                        field, mutated))

        results = run_parallel_cases(
            [lambda mutated=mutated: self._run_case_isolated(mutated) for _, _, mutated in cases])

        sub_test_count = 0
        for (subtest_kwargs, field, _mutated), (r, _unused) in zip(cases, results):
            sub_test_count += 1
            with self.subTest(**subtest_kwargs):
                self.assertNotIn("Traceback", r.stderr)
                self.assertNotIn("Exception in thread", r.stderr)
                if field in self.LOAD_BEARING_FIELDS:
                    self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
                else:
                    self.assertIn(r.returncode, (0, 1, 2, 3), r.stdout + r.stderr)

        print(f"F1 subTest count: {sub_test_count}", file=sys.stderr)

    def test_addendum_f2_string_depends_on_names_the_field_and_no_letters(self):
        # A decision whose depends_on is a string (for example "c1") must
        # give exit 2 and a clean error that names depends_on. It must
        # never report the single letters of the string as unknown claims.
        table = self.base_table()
        table["decisions"] = [{"id": "d1", "text": "decide", "depends_on": "c1"}]
        p = self.write("c.json", table)
        r = run(p)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertNotIn("Traceback", r.stderr)
        combined = r.stdout + r.stderr
        self.assertIn("depends_on", combined)
        for letter in "c1":
            self.assertNotIn(f"unknown claim {letter!r}", combined)


class AddendumRoundSixTests(unittest.TestCase):
    """Round 6 addendum items G1 and G2. See round6-addendum.txt for the
    contract text. G1 checks that a list item of depends_on or cites must
    be a string. G2 extends the F1 mutation walk over each list field
    (depends_on, cites). Every element gets every wrong JSON type. This
    covers a second-position item too, in a two-item list whose first
    item stays a valid string."""

    JSON_TYPE_SAMPLES = {
        "number": 1,
        "boolean": True,
        "null": None,
        "string": "x",
        "list": [1],
        "object": {"k": 1},
    }

    def setUp(self):
        self.d = tempfile.mkdtemp()
        with open(os.path.join(self.d, "src.txt"), "w", encoding="utf-8") as f:
            f.write("line one\nthe server listens on 443\nline three\n")

    def write(self, name, obj):
        p = os.path.join(self.d, name)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(obj, f) if isinstance(obj, dict) else f.write(obj)
        return p

    def base_table(self):
        """One valid claims table with a decision whose depends_on and
        cites lists each hold one string item."""
        claims = [
            {"id": "c1", "category": "observation", "anchor": "src.txt:2",
             "quote": "listens on 443"},
            {"id": "c2", "category": "example-attribute", "source": "router log system",
             "attribute": "stored on a USB drive", "kind": "incidental"},
        ]
        decisions = [
            {"id": "d1", "depends_on": ["c1"], "cites": ["router log system"]},
        ]
        return {"claims": claims, "decisions": decisions}

    def _json_type_of(self, value):
        if isinstance(value, bool):
            return "boolean"
        if isinstance(value, str):
            return "string"
        if isinstance(value, list):
            return "list"
        if isinstance(value, dict):
            return "object"
        if isinstance(value, (int, float)):
            return "number"
        return "null"

    def _wrong_type_samples(self, value):
        own_type = self._json_type_of(value)
        return {t: v for t, v in self.JSON_TYPE_SAMPLES.items() if t != own_type}

    def test_addendum_g1_list_item_of_wrong_type_gives_exit_2(self):
        table = self.base_table()
        for list_field in ("depends_on", "cites"):
            for type_name, wrong_item in self._wrong_type_samples("c1").items():
                with self.subTest(list_field=list_field, type=type_name):
                    mutated = copy.deepcopy(table)
                    mutated["decisions"][0][list_field] = [wrong_item]
                    p = self.write("mutated.json", mutated)
                    r = run(p)
                    self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
                    self.assertNotIn("Traceback", r.stderr)
                    self.assertNotIn("Exception in thread", r.stderr)

    def _run_case_isolated(self, table):
        """Run one mutation case in its own fresh temp directory, so
        parallel cases never share a directory or a file. This method
        does no assertion. It returns the raw subprocess result for the
        caller to assert against on the main thread."""
        case_dir = tempfile.mkdtemp()
        with open(os.path.join(case_dir, "src.txt"), "w", encoding="utf-8") as f:
            f.write("line one\nthe server listens on 443\nline three\n")
        p = os.path.join(case_dir, "mutated.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump(table, f)
        return run(p)

    def test_addendum_g2_extend_f1_walk_over_every_list_element(self):
        table = self.base_table()
        p = self.write("c.json", table)
        base_run = run(p)
        self.assertEqual(base_run.returncode, 0, base_run.stdout + base_run.stderr)

        # Build every case's mutated table and label up front, then run
        # them all in parallel, each in its own temp directory.
        labeled_mutations = []
        for list_field in ("depends_on", "cites"):
            base_list = table["decisions"][0][list_field]
            # Every element of the base table's own list, at its own
            # index.
            for index, item in enumerate(base_list):
                for type_name, wrong_item in self._wrong_type_samples(item).items():
                    mutated = copy.deepcopy(table)
                    mutated["decisions"][0][list_field][index] = wrong_item
                    labeled_mutations.append((f"{list_field}[{index}]={type_name}", mutated))

            # A two-item list. The first item stays the valid string the
            # base table already uses in that list. The second item gets
            # each wrong JSON type.
            valid_item = base_list[0]
            for type_name, wrong_item in self._wrong_type_samples(valid_item).items():
                mutated = copy.deepcopy(table)
                mutated["decisions"][0][list_field] = [valid_item, wrong_item]
                labeled_mutations.append((f"{list_field}[1 of 2]={type_name}", mutated))

        results = run_parallel_cases(
            [lambda mutated=mutated: self._run_case_isolated(mutated)
             for _, mutated in labeled_mutations])

        sub_test_count = 0
        for (label, _mutated), r in zip(labeled_mutations, results):
            sub_test_count += 1
            with self.subTest(case=label):
                self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
                self.assertNotIn("Traceback", r.stderr)
                self.assertNotIn("Exception in thread", r.stderr)

        print(f"G2 subTest count: {sub_test_count}", file=sys.stderr)


class AddendumRoundEightTests(unittest.TestCase):
    """Round 8 addendum item I1. See round8-addendum.txt for the
    contract text. A url: anchor with nothing after it, or only spaces,
    names no URL. The claim cannot ground. This holds true whether or
    not its saved copy exists on disk."""

    def setUp(self):
        self.d = tempfile.mkdtemp()
        with open(os.path.join(self.d, "saved.html"), "w", encoding="utf-8") as f:
            f.write("saved copy")

    def write(self, name, obj):
        p = os.path.join(self.d, name)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(obj, f) if isinstance(obj, dict) else f.write(obj)
        return p

    def test_addendum_i1_empty_url_anchor_with_saved_copy_is_not_grounded(self):
        for bad_anchor in ("url:", "url:   "):
            with self.subTest(anchor=bad_anchor):
                p = self.write("c.json", {
                    "claims": [{"id": "c1", "category": "observation",
                                "anchor": bad_anchor, "saved": "saved.html"}],
                    "decisions": [{"id": "d1", "depends_on": ["c1"]}],
                })
                r = run(p)
                self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
                self.assertIn("c1", r.stdout)


class AddendumRoundNineTests(unittest.TestCase):
    """Round 9 addendum items J1 and J2. See round9-addendum.txt for the
    contract text. J1 extends the G2 walk to an inference claim's from
    list, the last untyped list in this script. J2 is a deletion
    property over the F1 fixture. Deleting any one field of any claim,
    any decision, or the table's own top-level keys must never raise. It
    must give exit 0, 1, or 2 with a clean stderr."""

    JSON_TYPE_SAMPLES = dict([
        ("number", 1), ("boolean", True), ("null", None),
        ("string", "x"), ("list", [1]), ("object", {"k": 1}),
    ])

    def setUp(self):
        self.d = tempfile.mkdtemp()
        with open(os.path.join(self.d, "src.txt"), "w", encoding="utf-8") as f:
            f.write("line one\nthe server listens on 443\nline three\n")
        with open(os.path.join(self.d, "saved.html"), "w", encoding="utf-8") as f:
            f.write("saved copy")

    def write(self, name, obj):
        p = os.path.join(self.d, name)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(obj, f) if isinstance(obj, dict) else f.write(obj)
        return p

    def _json_type_of(self, value):
        if isinstance(value, bool):
            return "boolean"
        if isinstance(value, str):
            return "string"
        if isinstance(value, list):
            return "list"
        if isinstance(value, dict):
            return "object"
        if isinstance(value, (int, float)):
            return "number"
        return "null"

    def _wrong_type_samples(self, value):
        own_type = self._json_type_of(value)
        return {t: v for t, v in self.JSON_TYPE_SAMPLES.items() if t != own_type}

    def j1_base_table(self):
        """One valid claims table whose inference claim's from list holds
        two grounded observation claim ids, so the G2 walk can mutate
        either element."""
        claims = [
            {"id": "c1", "category": "observation", "anchor": "src.txt:2",
             "quote": "listens on 443"},
            {"id": "c2", "category": "observation", "anchor": "cmd: claude --version",
             "output": "2.1.280"},
            {"id": "c3", "category": "inference", "from": ["c1", "c2"]},
        ]
        decisions = [{"id": "d1", "depends_on": ["c3"]}]
        return {"claims": claims, "decisions": decisions}

    def test_addendum_j1_inference_from_list_item_of_wrong_type_gives_exit_2(self):
        table = self.j1_base_table()
        base_run = run(self.write("c.json", table))
        self.assertEqual(base_run.returncode, 0, base_run.stdout + base_run.stderr)

        sub_test_count = 0

        def check_case(mutated, label):
            nonlocal sub_test_count
            sub_test_count += 1
            with self.subTest(case=label):
                r = run(self.write("mutated.json", mutated))
                self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
                self.assertNotIn("Traceback", r.stderr)
                self.assertNotIn("Exception in thread", r.stderr)

        from_list = table["claims"][2]["from"]
        for index, item in enumerate(from_list):
            for type_name, wrong_item in self._wrong_type_samples(item).items():
                mutated = copy.deepcopy(table)
                mutated["claims"][2]["from"][index] = wrong_item
                check_case(mutated, f"from[{index}]={type_name}")

        # A two-item list. The first item stays a valid claim id. The
        # second item gets each wrong JSON type.
        valid_item = from_list[0]
        for type_name, wrong_item in self._wrong_type_samples(valid_item).items():
            mutated = copy.deepcopy(table)
            mutated["claims"][2]["from"] = [valid_item, wrong_item]
            check_case(mutated, f"from[1 of 2]={type_name}")

        print(f"J1 subTest count: {sub_test_count}", file=sys.stderr)

    def _run_case_isolated(self, table):
        """Run one deletion case in its own fresh temp directory, so
        parallel cases never share a directory or a file. This method
        does no assertion. It returns the raw subprocess result for the
        caller to assert against on the main thread."""
        case_dir = tempfile.mkdtemp()
        with open(os.path.join(case_dir, "src.txt"), "w", encoding="utf-8") as f:
            f.write("line one\nthe server listens on 443\nline three\n")
        with open(os.path.join(case_dir, "saved.html"), "w", encoding="utf-8") as f:
            f.write("saved copy")
        p = os.path.join(case_dir, "mutated.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump(table, f)
        return run(p)

    def f1_base_table(self):
        """The F1 fixture from round 5. See AddendumRoundFiveTests for
        the reasoning behind each claim and decision it holds."""
        claims = [
            dict(id="c1", category="observation", text="t1",
                 anchor="src.txt:2", quote="listens on 443"),
            dict(id="c2", category="observation", text="t2",
                 anchor="cmd: claude --version", output="2.1.280"),
            dict(id="c3", category="observation", text="t3",
                 anchor="url: https://example.test/x", saved="saved.html"),
            dict(id="c4", category="prior", text="t4, not verified here"),
            dict(id="c5", category="inference", text="t5", **{"from": ["c1"]}),
            dict(id="c6", category="example-attribute", text="t6",
                 source="router log system", attribute="rotate 9999",
                 kind="essential", borrowed="retention policy",
                 breaks_if_absent="loses the rate baseline",
                 verdict="adopted", anchor="src.txt:2"),
        ]
        decisions = [
            dict(id="d1", text="decide", depends_on=["c1", "c5", "c6"],
                 cites=["router log system"]),
        ]
        return {"claims": claims, "decisions": decisions}

    def test_addendum_j2_deletion_property_over_f1_fixture(self):
        table = self.f1_base_table()
        base_run = run(self.write("c.json", table))
        self.assertEqual(base_run.returncode, 0, base_run.stdout + base_run.stderr)

        # Build every deletion case's mutated table and path up front.
        labeled_mutations = []

        # Every field of every claim, one deletion at a time.
        for claim_index, claim in enumerate(table["claims"]):
            for field in list(claim.keys()):
                mutated = copy.deepcopy(table)
                del mutated["claims"][claim_index][field]
                labeled_mutations.append((f"claims[{claim_index}].{field}", mutated))

        # Every field of every decision, one deletion at a time.
        for decision_index, decision in enumerate(table["decisions"]):
            for field in list(decision.keys()):
                mutated = copy.deepcopy(table)
                del mutated["decisions"][decision_index][field]
                labeled_mutations.append((f"decisions[{decision_index}].{field}", mutated))

        # Every top-level key of the table itself.
        for field in list(table.keys()):
            mutated = copy.deepcopy(table)
            del mutated[field]
            labeled_mutations.append((f"<table>.{field}", mutated))

        results = run_parallel_cases(
            [lambda mutated=mutated: self._run_case_isolated(mutated)
             for _, mutated in labeled_mutations])

        sub_test_count = 0
        failing_paths = []
        for (path, _mutated), r in zip(labeled_mutations, results):
            sub_test_count += 1
            with self.subTest(path=path):
                clean = "Traceback" not in r.stderr and "Exception in thread" not in r.stderr
                if r.returncode not in (0, 1, 2) or not clean:
                    failing_paths.append(
                        (path, f"exit {r.returncode}, clean={clean}: {r.stdout} {r.stderr}"))

        print(f"J2 subTest count: {sub_test_count}", file=sys.stderr)
        if failing_paths:
            self.fail("J2 deletion walk found failures:\n" + "\n".join(
                f"{path}: {msg}" for path, msg in failing_paths))


class AddendumRoundTwelveTests(unittest.TestCase):
    """Round 12 addendum item M2. See round12-addendum.txt for the
    contract text. If present, the top-level claims key holds a list.
    If present, the top-level decisions key holds a list too. A
    present key of any other JSON type, null included, gives exit 2
    and a clean error. This walks both keys with each wrong JSON
    type."""

    JSON_TYPE_SAMPLES = dict([
        ("number", 1), ("boolean", True), ("null", None),
        ("string", "x"), ("list", [1]), ("object", {"k": 1}),
    ])

    def _json_type_of(self, value):
        if isinstance(value, bool):
            return "boolean"
        if isinstance(value, str):
            return "string"
        if isinstance(value, list):
            return "list"
        if isinstance(value, dict):
            return "object"
        if isinstance(value, (int, float)):
            return "number"
        return "null"

    def _wrong_type_samples(self, value):
        own_type = self._json_type_of(value)
        return {t: v for t, v in self.JSON_TYPE_SAMPLES.items() if t != own_type}

    def base_table(self):
        """One valid observation claim, grounded on src.txt, with an
        empty decisions list. Both top-level keys hold a list, their
        declared type, so the walk can mutate either to any other
        type."""
        return {
            "claims": [{"id": "c1", "category": "observation",
                       "anchor": "src.txt:2", "quote": "listens on 443"}],
            "decisions": [],
        }

    def _run_case_isolated(self, table):
        """Run one mutation case in its own fresh temp directory, so
        parallel cases never share a directory or a file. This method
        does no assertion. It returns the raw subprocess result for the
        caller to assert against on the main thread."""
        case_dir = tempfile.mkdtemp()
        with open(os.path.join(case_dir, "src.txt"), "w", encoding="utf-8") as f:
            f.write("line one\nthe server listens on 443\nline three\n")
        p = os.path.join(case_dir, "mutated.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump(table, f)
        return run(p)

    def test_addendum_m2_top_level_claims_and_decisions_reject_wrong_json_type(self):
        table = self.base_table()
        base_run = self._run_case_isolated(table)
        self.assertEqual(base_run.returncode, 0, base_run.stdout + base_run.stderr)

        labeled_mutations = []
        for key in ("claims", "decisions"):
            for type_name, wrong_value in self._wrong_type_samples(table[key]).items():
                mutated = copy.deepcopy(table)
                mutated[key] = wrong_value
                labeled_mutations.append((f"{key}={type_name}", mutated))

        results = run_parallel_cases(
            [lambda mutated=mutated: self._run_case_isolated(mutated)
             for _, mutated in labeled_mutations])

        sub_test_count = 0
        for (label, _mutated), r in zip(labeled_mutations, results):
            sub_test_count += 1
            with self.subTest(case=label):
                self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
                self.assertNotIn("Traceback", r.stderr)
                self.assertNotIn("Exception in thread", r.stderr)

        print(f"M2 subTest count: {sub_test_count}", file=sys.stderr)


class AddendumRoundFourteenTests(unittest.TestCase):
    """Round 14 addendum item N2. See round13-addendum.txt for the
    contract text (round 13 found this, round 14 fixes it). Every
    string in the claims table, keys and values at any depth, is valid
    Unicode. A JSON escape of a lone surrogate (\\ud800 to \\udfff, with
    no matching pair) gives exit 2, a clean error, and no write. The
    test places an escaped lone surrogate in a key, in a nested value,
    and in a list item."""

    # A lone surrogate as raw JSON text: a backslash, then u, then
    # d800. chr(92) is a literal backslash, joined here with the ASCII
    # digits that follow it. A Python "\ud800" string literal holds the
    # real surrogate code point instead of this ASCII escape text. That
    # code point fails this file's own UTF-8 encoding, before the test
    # process even starts.
    SURROGATE_ESCAPE = chr(92) + "ud800"

    # A placeholder string json.dumps writes out verbatim, with no
    # backslash of its own to collide with the surrogate escape's own
    # backslash. A Python string that already holds a literal
    # backslash-u-d800 text sequence gets its own backslash escaped a
    # second time by json.dumps. That doubles the backslash and turns
    # the escape into inert literal text, instead of the text JSON
    # parses back into a lone surrogate.
    SURROGATE_MARKER = "SURROGATE-MARKER-PLACEHOLDER"

    def base_table(self):
        """One claims table whose only claim is ungrounded (category
        prior, with no anchor other claims or decisions can depend on).
        The gate reports this claim's id in a violation line, so the
        line the surrogate id crashes is always reached, whichever
        field carries the escape."""
        return {
            "claims": [{"id": "c1", "category": "prior", "text": "t1"}],
            "decisions": [{"id": "d1", "depends_on": ["c1"]}],
        }

    def _run_surrogate_case(self, table, steps, insertion_kind, path):
        """Write one N2 case's claims file. steps follows a chain of
        dict keys and list indexes to the dict holding the field the
        escape replaces. insertion_kind names one of three forms.
        "key": the field name itself becomes the marker. "value": the
        field's string value becomes the marker. "list_item": the
        field's list gets one extra element holding the marker.
        json.dumps writes the file first, as plain text, with the
        marker still in place. Only then does this method replace the
        one marker occurrence with the raw surrogate escape text,
        directly in the file's own text. json.dumps's own escaping
        never touches that text this way. This method asserts nothing
        itself. It returns the raw subprocess result for the caller to
        assert against on the main thread."""
        node = table
        for step in steps[:-1]:
            node = node[step]
        field = steps[-1]
        if insertion_kind == "key":
            node[self.SURROGATE_MARKER] = node.pop(field)
        elif insertion_kind == "value":
            node[field] = self.SURROGATE_MARKER
            if steps == ["claims", 0, "id"]:
                # depends_on must keep naming this same claim, by its
                # new id. This way the gate still reaches the print
                # that names the id. Otherwise it reports an unrelated
                # unknown-claim violation for the old id instead.
                table["decisions"][0]["depends_on"] = [self.SURROGATE_MARKER]
        else:
            node[field] = node[field] + [self.SURROGATE_MARKER]

        case_dir = tempfile.mkdtemp()
        with open(os.path.join(case_dir, "src.txt"), "w", encoding="utf-8") as f:
            f.write("line one\nthe server listens on 443\nline three\n")
        p = os.path.join(case_dir, "c.json")
        text = json.dumps(table)
        marker_count = text.count(self.SURROGATE_MARKER)
        self.assertGreaterEqual(marker_count, 1, f"{path}: expected at least one marker occurrence")
        # Every marker occurrence names the same claim's id, so every
        # one becomes the identical surrogate escape. A "value" case
        # holds two occurrences this way: the claim's own id field, and
        # depends_on naming that same id again.
        text = text.replace(self.SURROGATE_MARKER, self.SURROGATE_ESCAPE, marker_count)
        with open(p, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        return run(p)

    def test_addendum_n2_lone_surrogate_escape_gives_exit_2(self):
        table = self.base_table()
        p_ok = tempfile.mkdtemp()
        with open(os.path.join(p_ok, "src.txt"), "w", encoding="utf-8") as f:
            f.write("line one\nthe server listens on 443\nline three\n")
        base_path = os.path.join(p_ok, "base.json")
        with open(base_path, "w", encoding="utf-8") as f:
            json.dump(table, f)
        base_run = run(base_path)
        self.assertEqual(base_run.returncode, 1, base_run.stdout + base_run.stderr)
        self.assertIn("ungrounded claim c1", base_run.stdout)

        # One (steps, insertion_kind) point per case. check_claims.py
        # only reads a known field name, such as id or category. An
        # unknown key never reaches a print or a crash. A "key" case
        # here proves only the schema check's own missing-field rule,
        # not the surrogate crash. This walk covers "value" (the claim
        # id) and "list_item" (a decision's depends_on entry). These
        # are the two forms this script's own reads can actually reach.
        points = [
            (["claims", 0, "id"], "value"),
            (["decisions", 0, "depends_on"], "list_item"),
        ]

        case_fns = []
        labels = []
        for steps, insertion_kind in points:
            path = f"{'.'.join(str(s) for s in steps)}={insertion_kind}"
            labels.append(path)
            case_fns.append(
                lambda steps=steps, insertion_kind=insertion_kind, path=path:
                self._run_surrogate_case(copy.deepcopy(table), steps, insertion_kind, path))

        results = run_parallel_cases(case_fns)

        sub_test_count = 0
        for label, r in zip(labels, results):
            sub_test_count += 1
            with self.subTest(case=label):
                self.assertEqual(r.returncode, 2, f"{label}: {r.stdout} {r.stderr}")
                self.assertNotIn("Traceback", r.stderr, f"{label}: {r.stderr}")
                self.assertNotIn("Exception in thread", r.stderr, f"{label}: {r.stderr}")

        print(f"N2 check_claims subTest count: {sub_test_count}", file=sys.stderr)


class AddendumRoundFifteenTests(unittest.TestCase):
    """Round 15 addendum item O1. See round14-addendum.txt for the
    contract text. The claims file is read as strict UTF-8 and parsed
    as strict JSON. This walk starts from a grounded claims table, so
    the run otherwise passes the gate. It covers three defect forms.
    The first is an invalid UTF-8 byte inside a string value. The
    second is the constants NaN, Infinity, and -Infinity, at more than
    one depth. The third is a duplicated key, at the top level and
    inside a nested object."""

    def base_table(self):
        """One claims table with a single, fully grounded observation
        claim and no decisions. check_claims.py reports GATE PASSED
        for this table unchanged. Any exit other than 2 on a corrupted
        copy shows the corruption went unnoticed instead of being
        rejected."""
        return {
            "claims": [{"id": "c1", "category": "observation",
                       "anchor": "cmd: echo hi", "output": "hi", "text": "t1"}],
            "decisions": [],
        }

    def _write_case(self, table, path):
        case_dir = tempfile.mkdtemp()
        with open(os.path.join(case_dir, "src.txt"), "w", encoding="utf-8") as f:
            f.write("line one\nthe server listens on 443\nline three\n")
        p = os.path.join(case_dir, "c.json")
        with open(p, "w", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(table))
        return p

    def _invalid_utf8_case(self, table, marker_field, path):
        table = copy.deepcopy(table)
        table["claims"][0][marker_field] = "MARKER"
        p = self._write_case(table, path)
        with open(p, encoding="utf-8") as f:
            text = f.read()
        self.assertEqual(text.count('"MARKER"'), 1, f"{path}: expected one marker occurrence")
        raw = text.encode("utf-8").replace(b"MARKER", b"\xff")
        with open(p, "wb") as f:
            f.write(raw)
        return run(p)

    def _nan_family_case(self, table, container, marker_field, constant, path):
        table = copy.deepcopy(table)
        table[container][0][marker_field] = "MARKER"
        p = self._write_case(table, path)
        with open(p, encoding="utf-8") as f:
            text = f.read()
        self.assertEqual(text.count('"MARKER"'), 1, f"{path}: expected one marker occurrence")
        text = text.replace('"MARKER"', constant, 1)
        with open(p, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        return run(p)

    def _duplicate_top_level_case(self, table, path):
        p = self._write_case(copy.deepcopy(table), path)
        with open(p, encoding="utf-8") as f:
            text = f.read()
        self.assertTrue(text.rstrip().endswith("}"), f"{path}: expected a trailing brace")
        text = text.rstrip()[:-1] + ', "decisions": []}'
        with open(p, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        return run(p)

    def _duplicate_nested_case(self, table, path):
        table = copy.deepcopy(table)
        p = self._write_case(table, path)
        with open(p, encoding="utf-8") as f:
            text = f.read()
        marker = '"text": "t1"'
        self.assertEqual(text.count(marker), 1, f"{path}: expected one marker occurrence")
        text = text.replace(marker, marker + ', "text": "t2"', 1)
        with open(p, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        return run(p)

    def _duplicate_decision_field_case(self, table, path):
        table = copy.deepcopy(table)
        p = self._write_case(table, path)
        with open(p, encoding="utf-8") as f:
            text = f.read()
        marker = '"note": "unused"'
        self.assertEqual(text.count(marker), 1, f"{path}: expected one marker occurrence")
        text = text.replace(marker, marker + ', "note": "unused-dup"', 1)
        with open(p, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        return run(p)

    def test_addendum_o1_strict_json_rejects_bad_utf8_nan_and_duplicate_keys(self):
        # "annotation" and "note" are field names outside CLAIM_FIELD_TYPES
        # and DECISION_FIELD_TYPES. check_claims.py copies each one
        # unchanged. No type check stands ready to reject the corrupted
        # form for an unrelated reason.
        table = self.base_table()
        table["decisions"] = [{"id": "d1", "depends_on": ["c1"], "note": "unused"}]

        cases = [
            ("utf8:claims[0].text", lambda: self._invalid_utf8_case(table, "text", "utf8:claims[0].text")),
            ("utf8:claims[0].annotation",
             lambda: self._invalid_utf8_case(table, "annotation", "utf8:claims[0].annotation")),
            ("nan:claims[0].text=NaN",
             lambda: self._nan_family_case(table, "claims", "text", "NaN", "nan:claims[0].text=NaN")),
            ("nan:claims[0].annotation=Infinity",
             lambda: self._nan_family_case(table, "claims", "annotation", "Infinity",
                                           "nan:claims[0].annotation=Infinity")),
            ("nan:decisions[0].note=-Infinity",
             lambda: self._nan_family_case(table, "decisions", "note", "-Infinity",
                                           "nan:decisions[0].note=-Infinity")),
            ("dup:top-level",
             lambda: self._duplicate_top_level_case(table, "dup:top-level")),
            ("dup:claims[0].text",
             lambda: self._duplicate_nested_case(table, "dup:claims[0].text")),
            ("dup:decisions[0].note",
             lambda: self._duplicate_decision_field_case(table, "dup:decisions[0].note")),
        ]
        case_fns = [fn for _, fn in cases]
        labels = [label for label, _ in cases]

        results = run_parallel_cases(case_fns)

        sub_test_count = 0
        failing_paths = []
        for label, r in zip(labels, results):
            sub_test_count += 1
            with self.subTest(case=label):
                try:
                    self.assertEqual(r.returncode, 2, f"{label}: {r.stdout} {r.stderr}")
                    self.assertNotIn("Traceback", r.stderr, f"{label}: {r.stderr}")
                    self.assertNotIn("Exception in thread", r.stderr, f"{label}: {r.stderr}")
                except AssertionError as e:
                    failing_paths.append((label, str(e)))

        print(f"O1 check_claims subTest count: {sub_test_count}", file=sys.stderr)
        if failing_paths:
            self.fail("O1 walk found failures:\n" + "\n".join(
                f"{path}: {msg}" for path, msg in failing_paths))


REAL_CLAIMS_PATH = (os.path.join(WORKSPACE, "prompt-store", "claims.json")
                    if WORKSPACE else None)


class AddendumRoundSixteenTests(unittest.TestCase):
    """Round 16 addendum items P1 and P2. See round15-addendum.txt for
    the contract text. P1 checks that check_claims.py declares a JSON
    type for every field the real claims table holds and every field
    apply_recognition.py writes into a decision. P2 places an escaped
    lone surrogate in an object key, at the top level and inside a
    claim."""

    JSON_TYPE_SAMPLES = dict([
        ("number", 1), ("boolean", True), ("null", None),
        ("string", "x"), ("list", [1]), ("object", {"k": 1}),
    ])

    # A lone surrogate as raw JSON text: a backslash, then u, then
    # d800. chr(92) is a literal backslash, joined here with the ASCII
    # digits that follow it. A Python "\ud800" string literal holds the
    # real surrogate code point instead of this ASCII escape text. That
    # code point fails this file's own UTF-8 encoding, before the test
    # process even starts.
    SURROGATE_ESCAPE = chr(92) + "ud800"

    # A placeholder string json.dumps writes out verbatim as a plain
    # JSON string. Only after that text exists does this walk replace
    # the placeholder with the raw surrogate escape text, directly in
    # the file's own text. json.dumps's own escaping never touches
    # that text this way.
    MARKER = "SURROGATE-MARKER-PLACEHOLDER"

    def _json_type_of(self, value):
        if isinstance(value, bool):
            return "boolean"
        if isinstance(value, str):
            return "string"
        if isinstance(value, list):
            return "list"
        if isinstance(value, dict):
            return "object"
        if isinstance(value, (int, float)):
            return "number"
        return "null"

    def _wrong_type_samples(self, value):
        own_type = self._json_type_of(value)
        return {t: v for t, v in self.JSON_TYPE_SAMPLES.items() if t != own_type}

    def _run_case(self, table, path):
        case_dir = tempfile.mkdtemp()
        with open(os.path.join(case_dir, "src.txt"), "w", encoding="utf-8") as f:
            f.write("line one\nthe server listens on 443\nline three\n")
        with open(os.path.join(case_dir, "saved.html"), "w", encoding="utf-8") as f:
            f.write("saved copy")
        p = os.path.join(case_dir, "c.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump(table, f)
        r = run(p)
        return dict(path=path, returncode=r.returncode, stdout=r.stdout, stderr=r.stderr)

    def test_addendum_p1_every_real_field_has_a_declared_type(self):
        # Read the real claims table this project ships. Its own
        # fields are the completeness contract this test checks
        # against: a field it holds that check_claims.py does not
        # reject on a wrong JSON type has no declared type there.
        if REAL_CLAIMS_PATH is None:
            self.skipTest("no project workspace above this test file")
        with open(REAL_CLAIMS_PATH, encoding="utf-8") as f:
            real_data = json.load(f)
        real_claim_fields = set()
        for c in real_data.get("claims") or []:
            real_claim_fields.update(c.keys())
        real_decision_fields = set()
        for d in real_data.get("decisions") or []:
            real_decision_fields.update(d.keys())

        f1 = AddendumRoundFiveTests()
        base = f1.base_table()
        fixture_claim_fields = set().union(*[set(c.keys()) for c in base["claims"]])
        fixture_decision_fields = set().union(*[set(d.keys()) for d in base["decisions"]])
        missing_from_fixture = ((real_claim_fields - fixture_claim_fields)
                                | (real_decision_fields - fixture_decision_fields))
        self.assertFalse(missing_from_fixture,
                         f"the F1 fixture is missing real claims table field(s): "
                         f"{sorted(missing_from_fixture)}")

        base_run = self._run_case(base, "positive control")
        self.assertEqual(base_run["returncode"], 0,
                         f"positive control: {base_run['stdout']} {base_run['stderr']}")

        case_fns = []
        labels = []
        for field in sorted(real_claim_fields):
            claim_index = next(i for i, c in enumerate(base["claims"]) if field in c)
            value = base["claims"][claim_index][field]
            for type_name, wrong_value in self._wrong_type_samples(value).items():
                mutated = copy.deepcopy(base)
                mutated["claims"][claim_index][field] = wrong_value
                path = f"claim.{field}={type_name}"
                labels.append(path)
                case_fns.append(lambda mutated=mutated, path=path: self._run_case(mutated, path))
        for field in sorted(real_decision_fields):
            decision_index = next(i for i, d in enumerate(base["decisions"]) if field in d)
            value = base["decisions"][decision_index][field]
            for type_name, wrong_value in self._wrong_type_samples(value).items():
                mutated = copy.deepcopy(base)
                mutated["decisions"][decision_index][field] = wrong_value
                path = f"decision.{field}={type_name}"
                labels.append(path)
                case_fns.append(lambda mutated=mutated, path=path: self._run_case(mutated, path))

        results = run_parallel_cases(case_fns)

        sub_test_count = 0
        failing_paths = []
        for label, result in zip(labels, results):
            sub_test_count += 1
            with self.subTest(case=label):
                try:
                    self.assertEqual(result["returncode"], 2,
                                     f"{label}: {result['stdout']} {result['stderr']}")
                    self.assertNotIn("Traceback", result["stderr"], f"{label}: {result['stderr']}")
                    self.assertNotIn("Exception in thread", result["stderr"], f"{label}: {result['stderr']}")
                except AssertionError as e:
                    failing_paths.append((label, str(e)))

        print(f"P1 subTest count: {sub_test_count}", file=sys.stderr)
        if failing_paths:
            self.fail("P1 completeness walk found failures:\n" + "\n".join(
                f"{path}: {msg}" for path, msg in failing_paths))

    def test_addendum_p2_lone_surrogate_key_gives_exit_2(self):
        f1 = AddendumRoundFiveTests()
        base = f1.base_table()

        # top-level: rename the "decisions" key itself to the marker.
        top_level_table = copy.deepcopy(base)
        top_level_table[self.MARKER] = top_level_table.pop("decisions")

        # claim key: rename claims[0]'s "category" key to the marker.
        claim_key_table = copy.deepcopy(base)
        claim_key_table["claims"][0][self.MARKER] = claim_key_table["claims"][0].pop("category")

        def run_surrogate_case(table, path):
            case_dir = tempfile.mkdtemp()
            with open(os.path.join(case_dir, "src.txt"), "w", encoding="utf-8") as f:
                f.write("line one\nthe server listens on 443\nline three\n")
            with open(os.path.join(case_dir, "saved.html"), "w", encoding="utf-8") as f:
                f.write("saved copy")
            p = os.path.join(case_dir, "c.json")
            text = json.dumps(table)
            self.assertEqual(text.count(self.MARKER), 1, f"{path}: expected one marker occurrence")
            text = text.replace(self.MARKER, self.SURROGATE_ESCAPE, 1)
            with open(p, "w", encoding="utf-8", newline="\n") as f:
                f.write(text)
            r = run(p)
            return dict(path=path, returncode=r.returncode, stdout=r.stdout, stderr=r.stderr)

        labels = ["top-level key", "claim key"]
        case_fns = [
            lambda: run_surrogate_case(top_level_table, "top-level key"),
            lambda: run_surrogate_case(claim_key_table, "claim key"),
        ]

        results = run_parallel_cases(case_fns)

        sub_test_count = 0
        for label, result in zip(labels, results):
            sub_test_count += 1
            with self.subTest(case=label):
                self.assertEqual(result["returncode"], 2,
                                 f"{label}: {result['stdout']} {result['stderr']}")
                self.assertNotIn("Traceback", result["stderr"], f"{label}: {result['stderr']}")
                self.assertNotIn("Exception in thread", result["stderr"], f"{label}: {result['stderr']}")

        print(f"P2 subTest count: {sub_test_count}", file=sys.stderr)


if __name__ == "__main__":
    unittest.main()
