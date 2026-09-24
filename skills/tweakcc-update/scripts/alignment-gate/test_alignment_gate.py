#!/usr/bin/env python3
"""Black-box subprocess tests for alignment_gate.py (stdlib unittest only).

Pins: the termination contract (kill path exit 3, range validation exit 2,
--help mentions both flags), the loud-failure contract (unexplained diff and
stale anchor exit 1 naming the row; clean fixtures exit 0), and read-only
behavior (inputs byte-identical after a run)."""

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
GATE = os.path.join(HERE, "alignment_gate.py")
PY = sys.executable


def run(*argv, timeout=60):
    return subprocess.run([PY, GATE, *argv], capture_output=True, text=True,
                          timeout=timeout)


def make_fixture(root, live_body=None, rule_stock="OLD TEXT", with_rule=True):
    """One-slug fixture: store/, rules.json, map.json, and optionally live/."""
    store = os.path.join(root, "store")
    os.makedirs(store, exist_ok=True)
    stock_body = f"<!--\nname: x\n-->\nBody with {rule_stock} inside.\n"
    with open(os.path.join(store, "slug-a.md"), "w", encoding="utf-8", newline="") as f:
        f.write(stock_body)
    rules = [{"id": "slug-a", "stock": rule_stock, "unnerf": "NEW TEXT",
              "description": "test rule"}] if with_rule else []
    rules_path = os.path.join(root, "rules.json")
    with open(rules_path, "w", encoding="utf-8") as f:
        json.dump(rules, f)
    cmap = {"schema": "concept-to-prompt-file-map/1.1", "concepts": [
        {"concept_id": "c-test", "concept": "test", "tag": "diffuse",
         "markers": [], "coverage_method": "read-verified", "notes": "",
         "governed_files": [{"file": "slug-a.md", "marker": "m",
                             "fix_kind": "body-rewrite",
                             "provenance": "recognition-first",
                             "fix_present": "false"}]}]}
    map_path = os.path.join(root, "map.json")
    with open(map_path, "w", encoding="utf-8") as f:
        json.dump(cmap, f)
    live = None
    if live_body is not None:
        live = os.path.join(root, "live")
        os.makedirs(live, exist_ok=True)
        with open(os.path.join(live, "slug-a.md"), "w", encoding="utf-8", newline="") as f:
            f.write(live_body)
    return map_path, store, rules_path, live


class TerminationContract(unittest.TestCase):
    def test_kill_path_exits_3_quickly(self):
        with tempfile.TemporaryDirectory() as d:
            m, s, r, _ = make_fixture(d)
            t0 = time.monotonic()
            p = run("--map", m, "--store", s, "--rules", r,
                    "--watchdog-probe", "10", "--max-seconds", "1")
            self.assertEqual(p.returncode, 3)
            self.assertLess(time.monotonic() - t0, 5.0)

    def test_zero_and_negative_ceiling_exit_2(self):
        for val in ("0", "-5"):
            p = run("--map", "x", "--store", "y", "--rules", "z",
                    "--max-seconds", val)
            self.assertEqual(p.returncode, 2, val)
            self.assertTrue(p.stderr.strip())

    def test_help_mentions_both_flags(self):
        p = run("--help")
        self.assertEqual(p.returncode, 0)
        self.assertIn("--max-seconds", p.stdout)
        self.assertIn("--watchdog-probe", p.stdout)


class UsageErrors(unittest.TestCase):
    def test_missing_inputs_exit_2(self):
        p = run("--map", "no.json", "--store", "no-dir", "--rules", "no.json")
        self.assertEqual(p.returncode, 2)

    def test_unknown_concept_filter_exits_2(self):
        with tempfile.TemporaryDirectory() as d:
            m, s, r, _ = make_fixture(d)
            p = run("--map", m, "--store", s, "--rules", r,
                    "--concepts", "does-not-exist")
            self.assertEqual(p.returncode, 2)
            self.assertIn("unknown concept_id", p.stderr)


class TwoWayMode(unittest.TestCase):
    def test_anchored_rule_passes_and_warns_live_skipped(self):
        with tempfile.TemporaryDirectory() as d:
            m, s, r, _ = make_fixture(d)
            p = run("--map", m, "--store", s, "--rules", r)
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertIn("LIVE CHECKS SKIPPED", p.stdout)
            self.assertIn("rule-covered", p.stdout)

    def test_stale_anchor_fails_naming_rule(self):
        with tempfile.TemporaryDirectory() as d:
            m, s, r, _ = make_fixture(d, rule_stock="OLD TEXT")
            # rewrite the rule so its stock no longer matches the store body
            with open(r, encoding="utf-8") as f:
                rules = json.load(f)
            rules[0]["stock"] = "TEXT THAT IS NOT THERE"
            with open(r, "w", encoding="utf-8") as f:
                json.dump(rules, f)
            p = run("--map", m, "--store", s, "--rules", r)
            self.assertEqual(p.returncode, 1)
            self.assertIn("STALE-ANCHOR", p.stdout + p.stderr)
            self.assertIn("slug-a", p.stdout + p.stderr)


class ThreeWayMode(unittest.TestCase):
    def test_rule_carried_passes(self):
        with tempfile.TemporaryDirectory() as d:
            live_body = "\nBody with NEW TEXT inside.\n"
            m, s, r, live = make_fixture(d, live_body=live_body)
            p = run("--map", m, "--store", s, "--rules", r, "--live", live)
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
            self.assertIn("rule-carried", p.stdout)

    def test_unexplained_diff_fails(self):
        with tempfile.TemporaryDirectory() as d:
            m, s, r, live = make_fixture(d, live_body="Something else entirely.\n")
            p = run("--map", m, "--store", s, "--rules", r, "--live", live)
            self.assertEqual(p.returncode, 1)
            self.assertIn("UNEXPLAINED-DIFF", p.stdout + p.stderr)

    def test_rule_not_applied_fails(self):
        with tempfile.TemporaryDirectory() as d:
            live_body = "\nBody with OLD TEXT inside.\n"  # live still == stock
            m, s, r, live = make_fixture(d, live_body=live_body)
            p = run("--map", m, "--store", s, "--rules", r, "--live", live)
            self.assertEqual(p.returncode, 1)
            self.assertIn("RULE-NOT-APPLIED", p.stdout + p.stderr)

    def test_stock_aligned_passes_without_rule(self):
        with tempfile.TemporaryDirectory() as d:
            live_body = "\nBody with OLD TEXT inside.\n"
            m, s, r, live = make_fixture(d, live_body=live_body, with_rule=False)
            p = run("--map", m, "--store", s, "--rules", r)
            # empty rules list is a usage error by contract; make one unrelated rule
            with open(r, "w", encoding="utf-8") as f:
                json.dump([{"id": "other-slug", "stock": "x", "unnerf": "y",
                            "description": "unrelated"}], f)
            p = run("--map", m, "--store", s, "--rules", r, "--live", live)
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
            self.assertIn("stock-aligned", p.stdout)


class ReadOnly(unittest.TestCase):
    def test_inputs_unchanged_after_run(self):
        with tempfile.TemporaryDirectory() as d:
            m, s, r, live = make_fixture(d, live_body="\nBody with NEW TEXT inside.\n")
            paths = [m, r, os.path.join(s, "slug-a.md"), os.path.join(live, "slug-a.md")]
            before = [(p, open(p, "rb").read()) for p in paths]
            run("--map", m, "--store", s, "--rules", r, "--live", live)
            for p, b in before:
                self.assertEqual(open(p, "rb").read(), b, p)


if __name__ == "__main__":
    unittest.main()
