"""Black-box tests for reanchor_rules.py (subprocess only, stdlib unittest).
Run: python -m unittest test_reanchor_rules
Pins: every op kind lands on a JSON rule file and round-trips through json;
a no-match op fails loudly and writes nothing; the kill path exits 3; a
retire deletes its file rather than leaving an empty rules array; a written
file never carries a CR byte."""
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "reanchor_rules.py")

FIXTURE_FILES = {
    "a.json": {
        "id": "a",
        "rules": [
            {"description": "rule a", "stock": ["old a stock - with dash"], "unnerf": ["a unnerf"]},
        ],
    },
    "b.json": {
        "id": "b",
        "rules": [
            {"description": "b1", "stock": ["b one"], "unnerf": ["b one fixed"]},
            {"description": "b2", "stock": ["b two"], "unnerf": ["b two fixed"]},
        ],
    },
    "c.json": {
        "id": "c",
        "rules": [
            {"description": "c", "stock": ["c gone"], "unnerf": ["c fixed"]},
        ],
    },
}


def run(argv, timeout=60, env=None):
    return subprocess.run([sys.executable, SCRIPT, *argv], capture_output=True, text=True, timeout=timeout, env=env)


def load_rules(rules_dir):
    """Returns {key: [(stock, unnerf, description), ...]} for every *.json file in rules_dir,
    keyed by "<stem>.md" so callers can compare against the old dict-literal keys."""
    result = {}
    for name in os.listdir(rules_dir):
        if not name.endswith(".json"):
            continue
        with open(os.path.join(rules_dir, name), encoding="utf-8") as f:
            data = json.load(f)
        key = f"{os.path.splitext(name)[0]}.md"
        result[key] = [
            ("\n".join(r["stock"]), "\n".join(r["unnerf"]), r["description"])
            for r in data["rules"]
        ]
    return result


class ReanchorTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="reanchor-")
        self.dir = os.path.join(self.root, "rules")
        os.mkdir(self.dir)
        for name, data in FIXTURE_FILES.items():
            with open(os.path.join(self.dir, name), "w", encoding="utf-8", newline="\n") as f:
                json.dump(data, f, indent=1, ensure_ascii=False)
                f.write("\n")

    def spec(self, ops):
        # Specs live outside the rules directory: a spec dropped into the
        # rules dir would collide with _load_store's *.json glob.
        p = os.path.join(self.root, "spec.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump(ops, f, ensure_ascii=False)
        return p

    def snapshot(self):
        """Byte contents of every file in the rules directory, for before/after equality checks."""
        out = {}
        for name in os.listdir(self.dir):
            with open(os.path.join(self.dir, name), "rb") as f:
                out[name] = f.read()
        return out

    def test_all_op_kinds_apply_and_round_trip(self):
        ops = [
            {"op": "reanchor", "file": "a.md", "match": "old a stock", "stock": "new a stock - hyphen", "unnerf": "a unnerf 2"},
            {"op": "rekey", "file": "b.md", "new_file": "b-renamed.md"},
            {"op": "add", "file": "b-renamed.md", "stock": "b three", "unnerf": "b three fixed", "description": "b3"},
            {"op": "retire", "file": "c.md"},
            {"op": "add", "file": "d.md", "stock": "d stock\nline2", "unnerf": "d fixed", "description": "d"},
        ]
        r = run([self.dir, self.spec(ops)])
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        rules = load_rules(self.dir)
        self.assertEqual(rules["a.md"], [("new a stock - hyphen", "a unnerf 2", "rule a")])
        self.assertEqual([s for s, _, _ in rules["b-renamed.md"]], ["b one", "b two", "b three"])
        self.assertNotIn("c.md", rules)
        self.assertEqual(rules["d.md"], [("d stock\nline2", "d fixed", "d")])
        with open(os.path.join(self.dir, "b-renamed.json"), encoding="utf-8") as f:
            self.assertEqual(json.load(f)["id"], "b-renamed")

    def test_no_match_fails_and_writes_nothing(self):
        before = self.snapshot()
        r = run([self.dir, self.spec([{"op": "reanchor", "file": "a.md", "match": "does not exist", "stock": "x"}])])
        self.assertEqual(r.returncode, 1)
        self.assertIn("selects 0 rule(s)", r.stdout)
        self.assertEqual(self.snapshot(), before)

    def test_ambiguous_match_fails(self):
        before = self.snapshot()
        r = run([self.dir, self.spec([{"op": "reanchor", "file": "b.md", "match": "b ", "stock": "x"}])])
        self.assertEqual(r.returncode, 1)
        self.assertIn("selects 2 rule(s)", r.stdout)
        self.assertEqual(self.snapshot(), before)

    def test_rekey_onto_existing_key_fails(self):
        before = self.snapshot()
        r = run([self.dir, self.spec([{"op": "rekey", "file": "a.md", "new_file": "b.md"}])])
        self.assertEqual(r.returncode, 1)
        self.assertEqual(self.snapshot(), before)

    def test_dry_run_writes_nothing(self):
        before = self.snapshot()
        r = run([self.dir, self.spec([{"op": "retire", "file": "c.md"}]), "--dry-run"])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.snapshot(), before)

    def test_kill_path_exits_3(self):
        t0 = time.monotonic()
        r = run([self.dir, self.spec([{"op": "retire", "file": "c.md"}]), "--watchdog-probe", "10", "--max-seconds", "1"])
        self.assertEqual(r.returncode, 3)
        self.assertLess(time.monotonic() - t0, 5)

    def test_range_validation_exits_2(self):
        for argv in (["--max-seconds", "0"], ["--watchdog-probe", "-1"]):
            r = run([self.dir, self.spec([{"op": "retire", "file": "c.md"}]), *argv])
            self.assertEqual(r.returncode, 2)
            self.assertTrue(r.stderr)

    def test_retire_deletes_the_file(self):
        r = run([self.dir, self.spec([{"op": "retire", "file": "c.md"}])])
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertFalse(os.path.exists(os.path.join(self.dir, "c.json")))

    def test_written_file_has_no_cr_byte(self):
        ops = [{"op": "reanchor", "file": "a.md", "match": "old a stock", "stock": "new a stock"}]
        r = run([self.dir, self.spec(ops)])
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        with open(os.path.join(self.dir, "a.json"), "rb") as f:
            self.assertNotIn(b"\r", f.read())

    def test_add_op_with_cr_in_stock_fails_loudly(self):
        """Write-side CR guard (fix-02): the loader's _load_rules already
        rejects a rule body line containing \\r. The add op must reject a
        CR-bearing stock/unnerf at write time too, instead of doing
        text.split("\\n") and silently writing the \\r into the store. RED
        today: the tool exits 0 and writes "line one\\r" as a rule line."""
        before = self.snapshot()
        ops = [{"op": "add", "file": "e.md", "stock": "line one\r\nline two", "unnerf": "fixed", "description": "e"}]
        r = run([self.dir, self.spec(ops)])
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("error:", r.stderr)
        self.assertEqual(self.snapshot(), before)

    def test_reanchor_op_with_cr_in_unnerf_fails_loudly(self):
        """Same write-side CR guard, for the reanchor op's unnerf field.
        RED today: the tool exits 0 and writes "a unnerf 2\\r" as a rule
        line."""
        before = self.snapshot()
        ops = [{"op": "reanchor", "file": "a.md", "match": "old a stock", "stock": "new a stock", "unnerf": "a unnerf 2\r\nmore"}]
        r = run([self.dir, self.spec(ops)])
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("error:", r.stderr)
        self.assertEqual(self.snapshot(), before)

    def test_cr_failure_is_aggregated_not_an_early_abort(self):
        """Fix-04: a CR-bearing field must be reported through the AGGREGATED
        failure path (append to failures, print FAIL, keep looping, print the
        summary, return 1), not by raise SystemExit inside the op loop, which
        aborts at the first op and never reaches later ops. This spec has TWO
        failing ops: op 0 has a carriage return in its unnerf field; op 1 fails
        for a different, already-supported reason (zero-match reanchor). RED
        today: the run aborts at op 0. Stdout carries no FAIL line at all (the
        SystemExit message bypasses the FAIL print), the aggregated summary
        line is absent from stderr, and op 1's FAIL line never appears because
        the loop never reaches it."""
        before = self.snapshot()
        ops = [
            {"op": "reanchor", "file": "a.md", "match": "old a stock", "stock": "new stock", "unnerf": "bad\r\nline"},
            {"op": "reanchor", "file": "a.md", "match": "does not exist", "stock": "x"},
        ]
        r = run([self.dir, self.spec(ops)])
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("2 of 2 operation(s) FAILED; nothing written", r.stderr)
        self.assertIn("FAIL op 0 reanchor a.md", r.stdout)
        self.assertIn("FAIL op 1 reanchor a.md", r.stdout)
        self.assertEqual(self.snapshot(), before)

    def test_non_object_top_level_exits_loud_case_e(self):
        """Case E (fix-01 review): a rules/<id>.json file whose top-level
        JSON value is a list, not an object. _load_store calls
        data.get("id") unconditionally; today that raises an uncaught
        AttributeError because a list has no .get method, and the process
        exits nonzero with a raw Python traceback on stderr instead of a
        clean error: message naming the file. Expected after fix: nonzero
        exit, stderr contains "error:" and this file's path, no traceback."""
        bad_path = os.path.join(self.dir, "case-e.json")
        with open(bad_path, "w", encoding="utf-8", newline="\n") as f:
            json.dump(["not", "an", "object"], f)
        r = run([self.dir, self.spec([{"op": "retire", "file": "c.md"}])])
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("error:", r.stderr)
        self.assertIn(bad_path, r.stderr)
        self.assertNotIn("Traceback", r.stderr)

    def test_empty_rules_list_exits_loud_fix_05(self):
        """Fix-05 contract 1: _load_store's docstring claims it refuses a
        store the plan-01 loader (_load_rules in apply-unnerfs.py) would
        reject. _load_rules raises SystemExit on a file whose "rules" is an
        empty list ({"id": "c", "rules": []}); _load_store today only checks
        that the top-level value is an object and that id matches the
        filename stem, so this file loads with no error. RED today: the tool
        operates on the store and the retire op below exits 0, deleting
        c.json, instead of failing loudly before any op runs."""
        bad_path = os.path.join(self.dir, "c.json")
        with open(bad_path, "w", encoding="utf-8", newline="\n") as f:
            json.dump({"id": "c", "rules": []}, f)
            f.write("\n")
        before = self.snapshot()
        r = run([self.dir, self.spec([{"op": "retire", "file": "c.md"}])])
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("error:", r.stderr)
        self.assertIn(bad_path, r.stderr)
        self.assertEqual(self.snapshot(), before)

    def test_add_op_with_empty_description_fails_loudly_and_writes_nothing(self):
        """Fix-06 contract 1: the add op authors a new rule from the spec's
        stock/unnerf/description fields with no field validation before
        write. _load_store rejects a rule whose description is missing,
        empty, or non-string on the NEXT load, so an add op that authors
        description="" writes a store its own loader will reject on the
        very next run. RED today: this add op exits 0 and creates a new
        rules/x.json file with "description": ""."""
        before = self.snapshot()
        ops = [{"op": "add", "file": "x.md", "stock": "s", "unnerf": "u", "description": ""}]
        r = run([self.dir, self.spec(ops)])
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("error:", r.stderr)
        self.assertEqual(self.snapshot(), before)

    def test_reanchor_op_with_empty_description_fails_loudly_and_writes_nothing(self):
        """Fix-06 contract 1, reanchor shape: reanchor's description branch
        (`rule["description"] = op["description"]`) writes the new value
        with no validation. RED today: this reanchor op exits 0 and
        overwrites a.json's rule description with the empty string."""
        before = self.snapshot()
        ops = [{"op": "reanchor", "file": "a.md", "match": "old a stock", "description": ""}]
        r = run([self.dir, self.spec(ops)])
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("error:", r.stderr)
        self.assertEqual(self.snapshot(), before)

    def test_add_op_with_empty_joined_stock_fails_loudly_and_writes_nothing(self):
        """Fix-06 contract 1, empty-joined stock: an add op whose stock
        string is empty joins to [""], a non-empty list containing one
        empty-string line, which _load_store's `not any(line for line in
        value)` check rejects on the next load. RED today: this add op
        exits 0 and writes a rules/x.json file _load_store cannot load."""
        before = self.snapshot()
        ops = [{"op": "add", "file": "x.md", "stock": "", "unnerf": "u", "description": "d"}]
        r = run([self.dir, self.spec(ops)])
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("error:", r.stderr)
        self.assertEqual(self.snapshot(), before)

    def test_reanchor_op_with_empty_joined_stock_fails_loudly_and_writes_nothing(self):
        """Fix-06 contract 1, reanchor shape of the empty-joined-stock case."""
        before = self.snapshot()
        ops = [{"op": "reanchor", "file": "a.md", "match": "old a stock", "stock": ""}]
        r = run([self.dir, self.spec(ops)])
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("error:", r.stderr)
        self.assertEqual(self.snapshot(), before)

    def test_non_object_rule_element_exits_loud_fix_05(self):
        """Fix-05 contract 1, second shape: a rule list element that is not
        an object (a bare string). _load_rules rejects any rule entry that
        is not a dict; _load_store never inspects individual rule elements,
        only the top-level object and the id/filename match, so this file
        loads with no error today. RED today: the tool operates on the
        store and the retire op below exits 0, deleting c.json, instead of
        failing loudly before any op runs."""
        bad_path = os.path.join(self.dir, "c.json")
        with open(bad_path, "w", encoding="utf-8", newline="\n") as f:
            json.dump({"id": "c", "rules": ["not an object"]}, f)
            f.write("\n")
        before = self.snapshot()
        r = run([self.dir, self.spec([{"op": "retire", "file": "c.md"}])])
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("error:", r.stderr)
        self.assertIn(bad_path, r.stderr)
        self.assertEqual(self.snapshot(), before)

    def test_partial_write_leaves_store_byte_unchanged_fix_07(self):
        """Fix-07 Facet A: _write_store validates and writes each touched key
        in the SAME per-key loop. A two-op spec mixes a VALID op (op 0, a
        reanchor of a.md that matches exactly one rule) with an authoring op
        that produces a malformed rule (op 1, add with description=""). The
        op loop records no failures for either op (reanchor succeeds at
        parse time; add's field validation only runs inside _write_store),
        so _write_store runs, writes a.json for op 0, then raises SystemExit
        on op 1's malformed entry before it reaches z.json. The tool must be
        all-or-nothing: a failure anywhere leaves the rules dir byte-
        unchanged. RED today: a.json IS changed on disk even though the run
        exits nonzero.

        _write_store iterates the `touched` set, whose iteration order for
        string keys depends on PYTHONHASHSEED. To make this test's RED/GREEN
        outcome deterministic (not a coin flip across interpreter runs), the
        subprocess is pinned to PYTHONHASHSEED=0, under which "a.md" iterates
        before "z.md" (verified separately: with this pin,
        list({"a.md", "z.md"}) == ["a.md", "z.md"]), so a.json is written
        before the z.json validation raises, reproducing the partial
        write."""
        before = self.snapshot()
        ops = [
            {"op": "reanchor", "file": "a.md", "match": "old a stock", "stock": "CHANGED"},
            {"op": "add", "file": "z.md", "stock": "s", "unnerf": "u", "description": ""},
        ]
        env = dict(os.environ, PYTHONHASHSEED="0")
        r = run([self.dir, self.spec(ops)], env=env)
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(self.snapshot(), before, "store must be byte-unchanged after an all-or-nothing failure")

    def test_authoring_failure_goes_through_aggregation_fix_07(self):
        """Fix-07 Facet B: a single authored-invalid op (add with
        description="") must fail through the SAME aggregated `failures`
        path every other op failure uses (a bad match, a missing key, a CR):
        append to failures, print a FAIL line, print the aggregated
        "N of M operation(s) FAILED; nothing written" summary, exit 1.
        RED today: _write_store raises SystemExit directly with only an
        "error: ...description" line on stderr; no FAIL line and no
        aggregated summary are ever printed, because the failure happens
        after the op loop already finished with failures == []."""
        before = self.snapshot()
        ops = [{"op": "add", "file": "z.md", "stock": "s", "unnerf": "u", "description": ""}]
        r = run([self.dir, self.spec(ops)])
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("1 of 1 operation(s) FAILED; nothing written", r.stderr)
        self.assertIn("FAIL op 0 add z.md", r.stdout)
        self.assertEqual(self.snapshot(), before)

    def test_rekey_authoring_failure_goes_through_aggregation_fix_08(self):
        """Fix-08: a rekey that AUTHORS an invalid output key must fail
        through the SAME aggregated `failures` path every other op failure
        uses. rekey pops the old key, sets entry["id"] to the new stem, and
        stores it at the new key, then prints PASS, without validating the
        authored entry. new_file ".md" yields new_stem "" and id "", but the
        written file is ".json" whose Path.stem is ".json", so _validate_entry
        rejects it on the id/stem-mismatch check (id '' does not equal stem
        '.json'). fix-07 wired add and reanchor through
        _validate_entry_or_raise_value_error inside the op try, but rekey was
        missed. RED today: _write_store raises SystemExit directly with only
        an "error: ...declares id ''" line on stderr, plus a "PASS op 0 rekey"
        line on stdout; no FAIL line and no aggregated summary are ever
        printed, because the failure happens after the op loop already
        finished with failures == []."""
        before = self.snapshot()
        ops = [{"op": "rekey", "file": "a.md", "new_file": ".md"}]
        r = run([self.dir, self.spec(ops)])
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("1 of 1 operation(s) FAILED; nothing written", r.stderr)
        self.assertIn("FAIL op 0 rekey a.md", r.stdout)
        self.assertEqual(self.snapshot(), before)


if __name__ == "__main__":
    unittest.main()
