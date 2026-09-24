#!/usr/bin/env python3
"""doctrine_coverage_check.py - assert that a concept map covers every doctrine item.

The doctrine-coverage table (JSON) is the single denominator: one row per doctrine
item, each naming the map concept_id(s) that carry it. This check fails loud when:
  - a table row names a concept_id the map does not define (dangling),
  - a table row names no concept at all (an a-priori exclusion; exclude-nothing),
  - a map concept is referenced by no table row (an orphan concept),
  - two table rows share an id, or a row lacks id/title/role/concepts,
  - --expect-ids is given and any expected doctrine id is absent from the table.
It never writes. With --markdown PATH it renders the table for human readers.

Substitute for another project: any map whose root holds concepts[].concept_id and
any table with items[].{id,title,role,concepts}. The expected-id list is the
project's doctrine numbering (here D1-D7, 1-51, P1).

Exit codes (recipe-skill-script-hardening): 0 covered, 1 one or more failures
(each named), 2 usage or unreadable input, 3 wall-clock watchdog fired.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time

REQUIRED_ROW_KEYS = ("id", "title", "role", "concepts")


def _arm_watchdog(max_seconds: float, probe_seconds: float) -> None:
    """Deterministic termination guard: hard-kill with exit code 3 at the ceiling."""
    if max_seconds <= 0:
        print("error: --max-seconds must be greater than 0", file=sys.stderr)
        sys.exit(2)
    if probe_seconds < 0:
        print("error: --watchdog-probe must be at least 0", file=sys.stderr)
        sys.exit(2)
    timer = threading.Timer(max_seconds, lambda: os._exit(3))
    timer.daemon = True
    timer.start()
    if probe_seconds > 0:
        time.sleep(probe_seconds)


def expand_ids(spec: str) -> list[str]:
    """'D1-D7,1-51,P1' -> ['D1',...,'D7','1',...,'51','P1']. Ranges need a shared
    non-digit prefix (possibly empty) and integer bounds."""
    out: list[str] = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            lo, hi = part.split("-", 1)
            plo = lo.rstrip("0123456789")
            phi = hi.rstrip("0123456789")
            if plo != phi or not lo[len(plo):] or not hi[len(phi):]:
                raise ValueError(f"bad id range: {part!r}")
            for n in range(int(lo[len(plo):]), int(hi[len(phi):]) + 1):
                out.append(f"{plo}{n}")
        else:
            out.append(part)
    return out


def check(table: dict, cmap: dict, expected_ids: list[str] | None) -> list[str]:
    fails: list[str] = []
    items = table.get("items")
    concepts = cmap.get("concepts")
    if not isinstance(items, list) or not items:
        return ["FAIL table: items is missing or empty"]
    if not isinstance(concepts, list) or not concepts:
        return ["FAIL map: concepts is missing or empty"]
    map_ids = [c.get("concept_id") for c in concepts]
    if len(set(map_ids)) != len(map_ids):
        fails.append("FAIL map: duplicate concept_id")
    map_set = set(map_ids)

    seen: set[str] = set()
    referenced: set[str] = set()
    for row in items:
        if not isinstance(row, dict) or any(k not in row for k in REQUIRED_ROW_KEYS):
            fails.append(f"FAIL row shape: {json.dumps(row)[:120]}")
            continue
        rid = str(row["id"])
        if rid in seen:
            fails.append(f"FAIL duplicate row id: {rid}")
        seen.add(rid)
        cids = row["concepts"]
        if not isinstance(cids, list) or not cids:
            fails.append(f"FAIL {rid} names no map concept (a-priori exclusion)")
            continue
        for cid in cids:
            if cid not in map_set:
                fails.append(f"FAIL {rid} names unknown concept_id {cid!r}")
            referenced.add(cid)
    for cid in map_ids:
        if cid not in referenced:
            fails.append(f"FAIL orphan map concept (no doctrine row names it): {cid}")
    if expected_ids:
        for eid in expected_ids:
            if eid not in seen:
                fails.append(f"FAIL expected doctrine id absent from table: {eid}")
    return fails


def render_markdown(table: dict, cmap: dict) -> str:
    rows_by_cid: dict[str, int] = {}
    for c in cmap.get("concepts", []):
        rows_by_cid[c.get("concept_id")] = len(c.get("governed_files", []))
    lines = ["| id | doctrine item | role | map concept(s) | governed rows |", "|---|---|---|---|---|"]
    for row in table["items"]:
        cids = row["concepts"]
        counts = ", ".join(f"{cid} ({rows_by_cid.get(cid, '?')})" for cid in cids)
        lines.append(f"| {row['id']} | {row['title']} | {row['role']} | {counts} | "
                     f"{sum(rows_by_cid.get(cid, 0) for cid in cids)} |")
    return "\n".join(lines) + "\n"


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--table", required=True, help="doctrine-coverage JSON")
    p.add_argument("--map", required=True, dest="map_path", help="concept map JSON")
    p.add_argument("--expect-ids", default=None,
                   help="comma list with ranges, e.g. 'D1-D7,1-51,P1'; every id must be a table row")
    p.add_argument("--markdown", default=None, help="write the rendered table to this path")
    p.add_argument("--max-seconds", type=float, default=60.0)
    p.add_argument("--watchdog-probe", type=float, default=0.0)
    a = p.parse_args()
    _arm_watchdog(a.max_seconds, a.watchdog_probe)
    try:
        with open(a.table, encoding="utf-8") as fh:
            table = json.load(fh)
        with open(a.map_path, encoding="utf-8") as fh:
            cmap = json.load(fh)
        expected = expand_ids(a.expect_ids) if a.expect_ids else None
    except (OSError, json.JSONDecodeError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    if not isinstance(table, dict) or not isinstance(cmap, dict):
        print("error: table and map must be JSON objects", file=sys.stderr)
        return 2
    fails = check(table, cmap, expected)
    if a.markdown:
        try:
            with open(a.markdown, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(render_markdown(table, cmap))
        except OSError as e:
            print(f"error: cannot write --markdown: {e}", file=sys.stderr)
            return 2
    n_items = len(table.get("items", []))
    n_concepts = len(cmap.get("concepts", []))
    if fails:
        print(f"FAIL: {len(fails)} failure(s) over {n_items} doctrine row(s), {n_concepts} map concept(s)")
        for f in fails:
            print("  " + f)
        return 1
    print(f"OK: {n_items} doctrine row(s) all carried by the map's {n_concepts} concept(s); no orphan concept")
    return 0


if __name__ == "__main__":
    sys.exit(main())
