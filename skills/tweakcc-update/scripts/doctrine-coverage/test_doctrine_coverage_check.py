#!/usr/bin/env python3
"""Black-box tests for doctrine_coverage_check.py: exit-code contract, loud per-row
failures, the termination guard (recipe-skill-script-hardening), and --help."""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

CHECK = Path(__file__).resolve().parent / "doctrine_coverage_check.py"


def _write(tmp: Path, items: list, concept_ids: list) -> list:
    table = tmp / "table.json"
    cmap = tmp / "map.json"
    table.write_text(json.dumps({"items": items}), encoding="utf-8")
    cmap.write_text(json.dumps({"concepts": [{"concept_id": c, "governed_files": [{"file": "a.md"}]}
                                              for c in concept_ids]}), encoding="utf-8")
    return [sys.executable, str(CHECK), "--table", str(table), "--map", str(cmap)]


def _run(args, **kw):
    return subprocess.run(args, capture_output=True, text=True, timeout=60, **kw)


def _item(i, concepts):
    return {"id": i, "title": f"t{i}", "role": "r", "concepts": concepts}


def test_covered_and_markdown(tmp_path):
    args = _write(tmp_path, [_item("D1", ["c1"]), _item("1", ["c1", "c2"])], ["c1", "c2"])
    md = tmp_path / "out.md"
    r = _run(args + ["--expect-ids", "D1-D1,1-1", "--markdown", str(md)])
    assert r.returncode == 0, r.stderr + r.stdout
    assert "OK: 2 doctrine row(s)" in r.stdout
    assert "| D1 | tD1 | r | c1 (1) | 1 |" in md.read_text(encoding="utf-8")


def test_dangling_concept_fails(tmp_path):
    args = _write(tmp_path, [_item("1", ["nope"])], ["c1"])
    r = _run(args)
    assert r.returncode == 1
    assert "names unknown concept_id 'nope'" in r.stdout
    assert "orphan map concept" in r.stdout


def test_empty_concepts_is_exclusion(tmp_path):
    args = _write(tmp_path, [_item("1", [])], ["c1"])
    r = _run(args)
    assert r.returncode == 1 and "a-priori exclusion" in r.stdout


def test_expected_id_missing(tmp_path):
    args = _write(tmp_path, [_item("1", ["c1"])], ["c1"])
    r = _run(args + ["--expect-ids", "1-3"])
    assert r.returncode == 1
    assert "absent from table: 2" in r.stdout and "absent from table: 3" in r.stdout


def test_bad_range_is_usage_error(tmp_path):
    args = _write(tmp_path, [_item("1", ["c1"])], ["c1"])
    r = _run(args + ["--expect-ids", "D1-7"])
    assert r.returncode == 2 and r.stderr


def test_missing_input_is_usage_error(tmp_path):
    r = _run([sys.executable, str(CHECK), "--table", str(tmp_path / "x.json"), "--map", str(tmp_path / "y.json")])
    assert r.returncode == 2


def test_range_validation(tmp_path):
    args = _write(tmp_path, [_item("1", ["c1"])], ["c1"])
    for flag, val in (("--max-seconds", "0"), ("--max-seconds", "-5"), ("--watchdog-probe", "-1")):
        r = _run(args + [flag, val])
        assert r.returncode == 2 and r.stderr, (flag, val, r)


def test_watchdog_kills_at_ceiling(tmp_path):
    args = _write(tmp_path, [_item("1", ["c1"])], ["c1"])
    t0 = time.monotonic()
    r = _run(args + ["--max-seconds", "1", "--watchdog-probe", "10"])
    assert r.returncode == 3
    assert time.monotonic() - t0 < 5


def test_help_mentions_flags():
    r = _run([sys.executable, str(CHECK), "--help"])
    assert r.returncode == 0
    assert "--max-seconds" in r.stdout and "--watchdog-probe" in r.stdout


if __name__ == "__main__":
    sys.exit(subprocess.call([sys.executable, "-m", "pytest", "-q", __file__]))
