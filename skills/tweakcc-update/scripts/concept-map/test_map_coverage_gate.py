#!/usr/bin/env python3
"""Black-box tests for map_coverage_gate.py (schema 1.1, governed-set denominator).
Each test drives the gate as a subprocess against a synthetic store, map and rules
dump in a temp dir and asserts the exit code plus the named failure."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

GATE = Path(__file__).resolve().parent / "map_coverage_gate.py"
FM = "<!--\nname: x\n-->\n"


def _fixture(tmp: Path, rows_by_concept: dict, bodies: dict, rules: list | None = None,
             extra_concept: dict | None = None):
    store = tmp / "before"
    store.mkdir()
    for name, body in bodies.items():
        (store / name).write_text(FM + body, encoding="utf-8", newline="\n")
    concepts = []
    for cid, rows in rows_by_concept.items():
        concepts.append({"concept_id": cid, "concept": cid, "tag": "diffuse", "governed_files": rows})
    if extra_concept:
        concepts.append(extra_concept)
    m = tmp / "map.json"
    m.write_text(json.dumps({"schema": "1.1", "concepts": concepts}), encoding="utf-8")
    args = [sys.executable, str(GATE), "--map", str(m), "--store-dir", str(store)]
    if rules is not None:
        r = tmp / "rules.json"
        r.write_text(json.dumps(rules), encoding="utf-8")
        args += ["--rules", str(r)]
    return args


def _row(f, kind="body-rewrite", prov="recognition-first", present="false"):
    return {"file": f, "marker": "m", "fix_kind": kind, "provenance": prov, "fix_present": present}


def _run(args):
    return subprocess.run(args, capture_output=True, text=True)


def test_all_covered(tmp_path):
    args = _fixture(tmp_path, {"c": [_row("a.md", "body-invariant", present="not-applicable"),
                                     _row("b.md", present="true")]}, {"a.md": "A", "b.md": "B"})
    r = _run(args)
    assert r.returncode == 0, r.stderr
    assert "COVERED c: 2 governed" in r.stdout


def test_false_row_uncovered_without_rule(tmp_path):
    args = _fixture(tmp_path, {"c": [_row("a.md", present="false")]}, {"a.md": "old text"})
    r = _run(args)
    assert r.returncode == 1
    assert "FAIL uncovered: c :: a.md" in r.stderr


def test_rule_anchored_covers(tmp_path):
    rules = [{"id": "a", "stock": "old text", "unnerf": "new text", "description": "d"}]
    args = _fixture(tmp_path, {"c": [_row("a.md", present="false")]}, {"a.md": "old text"}, rules)
    r = _run(args)
    assert r.returncode == 0, r.stderr


def test_stale_rule_does_not_cover(tmp_path):
    rules = [{"id": "a", "stock": "drifted", "unnerf": "n", "description": "d"}]
    args = _fixture(tmp_path, {"c": [_row("a.md", present="false")]}, {"a.md": "old text"}, rules)
    r = _run(args)
    assert r.returncode == 1
    assert "FAIL uncovered" in r.stderr


def test_kind_state_mismatch(tmp_path):
    args = _fixture(tmp_path, {"c": [_row("a.md", "body-invariant", present="false")]}, {"a.md": "A"})
    r = _run(args)
    assert r.returncode == 1
    assert "FAIL kind-state" in r.stderr


def test_missing_slug(tmp_path):
    args = _fixture(tmp_path, {"c": [_row("gone.md", present="true")]}, {"a.md": "A"})
    r = _run(args)
    assert r.returncode == 1
    assert "FAIL missing-slug: c :: gone.md" in r.stderr


def test_cold_read_all_search_extended(tmp_path):
    args = _fixture(tmp_path, {"c": [_row("a.md", prov="search-extended", present="true")]}, {"a.md": "A"})
    r = _run(args)
    assert r.returncode == 1
    assert "FAIL cold-read: c" in r.stderr


def test_empty_concept_needs_verified_zero(tmp_path):
    bad = {"concept_id": "e", "concept": "nothing here", "tag": "diffuse", "governed_files": []}
    args = _fixture(tmp_path, {"c": [_row("a.md", present="true")]}, {"a.md": "A"}, extra_concept=bad)
    r = _run(args)
    assert r.returncode == 1 and "FAIL a-priori-empty: e" in r.stderr
    good = dict(bad, concept="governed: 0 (verified: 0 recognized, 0 disk-confirmed)")
    sub = tmp_path / "g"
    sub.mkdir()
    args = _fixture(sub, {"c": [_row("a.md", present="true")]}, {"a.md": "A"}, extra_concept=good)
    r = _run(args)
    assert r.returncode == 0, r.stderr
    assert "COVERED e: 0 governed (verified-zero finding)" in r.stdout


def test_duplicate_row_and_concept(tmp_path):
    dup = {"concept_id": "c", "concept": "c", "tag": "diffuse", "governed_files": [_row("a.md", present="true")]}
    args = _fixture(tmp_path, {"c": [_row("a.md", present="true"), _row("a.md", present="true")]},
                    {"a.md": "A"}, extra_concept=dup)
    r = _run(args)
    assert r.returncode == 1
    assert "FAIL duplicate-row: c :: a.md" in r.stderr and "FAIL duplicate-concept: c" in r.stderr


def test_usage_error(tmp_path):
    r = _run([sys.executable, str(GATE), "--map", str(tmp_path / "nope.json"), "--store-dir", str(tmp_path)])
    assert r.returncode == 2


def test_watchdog_fires(tmp_path):
    args = _fixture(tmp_path, {"c": [_row("a.md", present="true")]}, {"a.md": "A"})
    r = _run(args + ["--max-seconds", "0.2", "--watchdog-probe", "2"])
    assert r.returncode == 3


if __name__ == "__main__":
    sys.exit(subprocess.call([sys.executable, "-m", "pytest", "-q", __file__]))


def test_shadowed_runtime_notice_covers(tmp_path):
    rem = tmp_path / "reminders"
    rem.mkdir()
    (rem / "other-name.md").write_text("<!--\nname: o\nshadows:\n  - a\n-->\nbody\n", encoding="utf-8")
    args = _fixture(tmp_path, {"c": [_row("a.md", "runtime-notice", present="false")]}, {"a.md": "old"})
    r = _run(args + ["--reminders-dir", str(rem)])
    assert r.returncode == 0, r.stderr
    r2 = _run(args)
    assert r2.returncode == 1 and "FAIL uncovered: c :: a.md" in r2.stderr
