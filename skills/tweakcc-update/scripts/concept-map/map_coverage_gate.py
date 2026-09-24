#!/usr/bin/env python3
"""map_coverage_gate.py - per-concept coverage gate over a schema-1.1 concept map
(recipe-concept-prompt-mapping [R001] Steps 7, 8, 9). Python 3 stdlib only.

The map lists, per concept, the prompt files it governs (concepts[].governed_files,
one row per file with fix_kind, provenance, fix_present). The coverage denominator
is the GOVERNED SET recorded by the recognition-first read, never the whole store:
a whole-store read-verdict denominator is the cold-read false green this gate
replaced (2026-09-01).

A row is COVERED when any of these hold:
  - fix_present is "not-applicable" and fix_kind is "body-invariant" (no body fix needed),
  - fix_present is "true",
  - --rules is given and at least one rule whose id equals the row's slug anchors
    exactly once in the current store file (the fix is encoded and will splice),
  - --reminders-dir is given, fix_kind is "runtime-notice", and <slug>.md exists there
    or an override there lists <slug> under its `shadows:` frontmatter
    (the fix is delivered by the runtime override channel).
A concept is COVERED when every row is covered. The report names every uncovered
row (Step 8, the miss list).

Step 9 checks (each a named FAIL):
  - every row's file resolves in --store-dir (slug on the current store),
  - fix_kind, provenance and fix_present carry only the schema's values,
  - a body-invariant row is not-applicable (any other state on it is a false RED);
    not-applicable is allowed on any fix_kind, meaning the file already conforms,
  - a concept with rows has at least one recognition-first row (all search-extended
    means it was cold-read; redo Step 2),
  - a concept with zero rows carries a verified-zero statement ("governed: 0" and
    "verified" in its concept text or notes), never a bare empty list,
  - no duplicate concept_id, no duplicate (concept, file) row.

Read-only: never writes the map or the store. Exit codes (recipe-skill-script-
hardening): 0 every concept covered and every check passed; 1 one or more
uncovered rows or failed checks (each named); 2 usage or unreadable input;
3 the wall-clock watchdog fired.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import threading
import time

FIX_KINDS = {"body-rewrite", "body-invariant", "runtime-notice", "variable-driven"}
PROVENANCE = {"recognition-first", "search-extended"}
FIX_PRESENT = {"true", "false", "unread", "not-applicable"}


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


def read_raw(path: str) -> str:
    """CRLF-normalized whole file (frontmatter included): apply-unnerfs.py anchors
    rules on the whole .md, and the pipeline's canonical space is LF."""
    with open(path, encoding="utf-8", newline="") as fh:
        return fh.read().replace("\r\n", "\n")


def rule_anchors(store_file: str, rules: list) -> bool:
    """True when at least one rule's stock string occurs exactly once in the file."""
    if not rules:
        return False
    text = read_raw(store_file)
    return any(text.count(r["stock"]) == 1 for r in rules)


def is_verified_zero(concept: dict) -> bool:
    blob = " ".join(str(concept.get(k) or "") for k in ("concept", "statement", "notes")).lower()
    return "governed: 0" in blob and "verified" in blob


def gate(cmap: dict, store_dir: str, rules_by_slug: dict | None, reminders_dir: str | None,
         wanted: set | None) -> tuple[list[str], list[str], int]:
    """Return (failures, per-concept report lines, rows_checked)."""
    fails: list[str] = []
    report: list[str] = []
    rows_checked = 0
    ids = [c.get("concept_id") for c in cmap["concepts"]]
    for cid in {i for i in ids if ids.count(i) > 1}:
        fails.append(f"FAIL duplicate-concept: {cid}")
    for concept in cmap["concepts"]:
        cid = concept.get("concept_id") or "<anon>"
        if wanted is not None and cid not in wanted:
            continue
        rows = concept.get("governed_files") or []
        if not rows:
            if is_verified_zero(concept):
                report.append(f"COVERED {cid}: 0 governed (verified-zero finding)")
            else:
                fails.append(f"FAIL a-priori-empty: {cid} has no rows and no 'governed: 0 (verified ...)' statement")
            continue
        seen: set[str] = set()
        uncovered: list[str] = []
        recog = 0
        counts = {"true": 0, "false": 0, "unread": 0, "not-applicable": 0}
        for row in rows:
            rows_checked += 1
            f = row.get("file", "")
            if f in seen:
                fails.append(f"FAIL duplicate-row: {cid} :: {f}")
                continue
            seen.add(f)
            kind, prov, present = row.get("fix_kind"), row.get("provenance"), row.get("fix_present")
            bad = [n for n, v, ok in (("fix_kind", kind, FIX_KINDS), ("provenance", prov, PROVENANCE),
                                       ("fix_present", present, FIX_PRESENT)) if v not in ok]
            if bad:
                fails.append(f"FAIL schema: {cid} :: {f}: bad {', '.join(bad)}")
                continue
            if kind == "body-invariant" and present != "not-applicable":
                fails.append(f"FAIL kind-state: {cid} :: {f}: fix_kind={kind} with fix_present={present}")
                continue
            if prov == "recognition-first":
                recog += 1
            counts[present] += 1
            path = os.path.join(store_dir, f)
            if os.sep in f or "/" in f or not os.path.isfile(path):
                fails.append(f"FAIL missing-slug: {cid} :: {f} (not in store)")
                continue
            slug = f[:-3] if f.endswith(".md") else f
            covered = present in ("true", "not-applicable")
            how = present
            if not covered and rules_by_slug is not None and rule_anchors(path, rules_by_slug.get(slug, [])):
                covered, how = True, "rule-anchored"
            if not covered and reminders_dir and kind == "runtime-notice" \
                    and (os.path.isfile(os.path.join(reminders_dir, f))
                         or slug in shadowed_ids(reminders_dir)):
                covered, how = True, "runtime-override"
            if not covered:
                uncovered.append(f"{f} (fix_kind={kind}, fix_present={present})")
        if recog == 0:
            fails.append(f"FAIL cold-read: {cid} has {len(rows)} rows and no recognition-first row")
        status = "COVERED" if not uncovered else "INCOMPLETE"
        report.append(f"{status} {cid}: {len(rows)} governed, {counts['not-applicable']} not-applicable, "
                      f"{counts['true']} true, {counts['false']} false, {counts['unread']} unread; "
                      f"{len(uncovered)} uncovered")
        for u in uncovered:
            fails.append(f"FAIL uncovered: {cid} :: {u}")
    return fails, report, rows_checked


_SHADOW_CACHE: dict = {}


def shadowed_ids(reminders_dir: str) -> set:
    """Prompt ids named under a `shadows:` frontmatter list by any override in reminders_dir."""
    if reminders_dir in _SHADOW_CACHE:
        return _SHADOW_CACHE[reminders_dir]
    ids: set = set()
    for name in os.listdir(reminders_dir):
        if not name.endswith(".md"):
            continue
        try:
            with open(os.path.join(reminders_dir, name), encoding="utf-8") as fh:
                text = fh.read()
        except OSError:
            continue
        m = re.search(r"^shadows:\s*\n((?:[ \t]+-[ \t]*\S+[ \t]*\n)+)", text, re.M)
        if m:
            ids.update(x.strip().lstrip("-").strip() for x in m.group(1).splitlines())
    _SHADOW_CACHE[reminders_dir] = ids
    return ids


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--map", required=True, dest="map_path", help="concept map JSON (schema 1.1)")
    p.add_argument("--store-dir", required=True, help="stock store directory holding <slug>.md")
    p.add_argument("--rules", default=None, help="rules JSON dump (apply-unnerfs.py --dump-rules)")
    p.add_argument("--reminders-dir", default=None, help="runtime override directory (~/.tweakcc/system-reminders)")
    p.add_argument("--concepts", default=None, help="comma-separated concept_id filter")
    p.add_argument("--max-seconds", type=float, default=120.0)
    p.add_argument("--watchdog-probe", type=float, default=0.0)
    a = p.parse_args()
    _arm_watchdog(a.max_seconds, a.watchdog_probe)
    if not os.path.isfile(a.map_path) or not os.path.isdir(a.store_dir):
        print("error: --map must be a file and --store-dir a directory", file=sys.stderr)
        return 2
    if a.reminders_dir and not os.path.isdir(a.reminders_dir):
        print(f"error: --reminders-dir not found: {a.reminders_dir}", file=sys.stderr)
        return 2
    try:
        with open(a.map_path, encoding="utf-8") as fh:
            cmap = json.load(fh)
        rules_by_slug = None
        if a.rules:
            with open(a.rules, encoding="utf-8") as fh:
                rules = json.load(fh)
            if not isinstance(rules, list):
                raise ValueError("rules dump is not a list")
            rules_by_slug = {}
            for r in rules:
                rules_by_slug.setdefault(r["id"], []).append(r)
    except (OSError, ValueError, KeyError, TypeError) as e:
        print(f"error: cannot read inputs: {e}", file=sys.stderr)
        return 2
    if not isinstance(cmap, dict) or not isinstance(cmap.get("concepts"), list) or not cmap["concepts"]:
        print("error: map has no concepts list", file=sys.stderr)
        return 2
    wanted = None
    if a.concepts:
        wanted = {c.strip() for c in a.concepts.split(",") if c.strip()}
        unknown = wanted - {c.get("concept_id") for c in cmap["concepts"]}
        if unknown:
            print(f"error: unknown concept_id(s): {sorted(unknown)}", file=sys.stderr)
            return 2
    fails, report, n = gate(cmap, a.store_dir, rules_by_slug, a.reminders_dir, wanted)
    for line in report:
        print(line)
    covered = sum(1 for r in report if r.startswith("COVERED"))
    print(f"\nSummary: {n} row(s) checked, {covered}/{len(report)} concept(s) covered, {len(fails)} failure(s)")
    if fails:
        for f in fails:
            print("  " + f, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
