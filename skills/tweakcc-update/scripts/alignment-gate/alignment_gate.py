#!/usr/bin/env python3
"""alignment_gate.py - three-way alignment gate over the concept map's governed rows.

Read-only. For every governed row in the concept map, compares up to three
bodies for the row's slug and issues a per-row verdict:

  stock store body  <->  un-nerf rule (stock/unnerf strings)  <->  live body

Modes:
  Two-way (no --live): per row, is the slug covered by a rule, and does every
    covering rule's `stock` string still byte-match inside the stock body?
    A rule whose stock no longer matches is STALE-ANCHOR (the splicer would
    silently not apply it): a hard failure.
  Three-way (--live DIR): additionally compares the live body against the
    stock body with all covering rules applied in dump order:
      stock-aligned   live == stock and no rule targets the slug
      rule-carried    live == stock with the rules' edits applied
      rule-not-applied  a rule targets the slug but live == stock (fail)
      unexplained-diff  live differs and no rule explains it (drift; fail)

Inputs:
  --map    concept-map-proposed.json (schema 1.1; reads concepts[].governed_files)
  --store  stock store dir (binary-faithful <slug>.md files, HTML-comment frontmatter)
  --rules  rules JSON dump: produce with
           python unnerfcc/scripts/apply-unnerfs.py --dump-rules <path>
  --live   optional dir of live-extracted <slug>.md bodies (same layout as --store)
  --concepts  comma-separated concept_id filter (default: all concepts)

Never writes or mutates any input. Exit codes: 0 clean, 1 one or more row
failures, 2 usage/config error, 3 terminated at the wall-clock ceiling.
Termination guard per recipe-skill-script-hardening-*.md; loud per-item
accounting per recipe-skill-script-loud-replace-*.md.
"""

import argparse
import json
import os
import sys
import threading
import time


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
    """Read a prompt file and normalize CRLF to LF. The pipeline's canonical
    space is the catalog's LF form: the binary patcher matches canonicalized
    AST text keyed by catalog hashes, and 250+ LF rules splice into a binary
    whose raw source spells \\r escapes (verified against the real binary,
    2026-08-30). A byte-faithful CRLF comparison flags rules the pipeline
    actually applies (the ultraplan false stale)."""
    with open(path, encoding="utf-8", newline="") as fh:
        return fh.read().replace("\r\n", "\n")


def read_body(path: str) -> str:
    """Strip the leading HTML-comment frontmatter block if present (the binary
    carries the body only, not the comment). Used for live-body comparison."""
    text = read_raw(path)
    if text.lstrip().startswith("<!--"):
        end = text.find("-->")
        if end != -1:
            # Keep everything after the comment close INCLUDING the leading
            # newline: rule stock strings anchor on it (verified: stripping it
            # false-flagged 2 anchored rules as stale on the first live run).
            return text[end + 3:]
    return text


def apply_rules(stock_body: str, rules: list) -> tuple:
    """Apply each rule's stock->unnerf edit to the stock body in dump order.
    Returns (result_body, applied_ids, unanchored_ids). Count-asserted: a rule
    whose stock does not appear exactly once in the current body is unanchored
    (0 matches = stale; >1 = ambiguous), and is NOT applied."""
    body = stock_body
    applied, unanchored = [], []
    for r in rules:
        n = body.count(r["stock"])
        if n == 1:
            body = body.replace(r["stock"], r["unnerf"], 1)
            applied.append(r["id"])
        else:
            unanchored.append((r["id"], n))
    return body, applied, unanchored


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--map", required=True, dest="map_path",
                        help="concept-map-proposed.json path")
    parser.add_argument("--store", required=True, help="stock store directory")
    parser.add_argument("--rules", required=True,
                        help="rules JSON dump (apply-unnerfs.py --dump-rules)")
    parser.add_argument("--live", default=None,
                        help="optional live-extracted prompt directory (three-way mode)")
    parser.add_argument("--concepts", default=None,
                        help="comma-separated concept_id filter (default: all)")
    parser.add_argument("--max-seconds", type=float, default=120.0, dest="max_seconds",
                        help="Hard wall-clock ceiling; exit 3 when it fires (default: 120)")
    parser.add_argument("--watchdog-probe", type=float, default=0.0, dest="watchdog_probe",
                        help="Diagnostic: idle this many seconds after arming the watchdog")
    args = parser.parse_args()
    _arm_watchdog(args.max_seconds, args.watchdog_probe)

    for label, path, is_dir in [("--map", args.map_path, False),
                                ("--store", args.store, True),
                                ("--rules", args.rules, False)]:
        ok = os.path.isdir(path) if is_dir else os.path.isfile(path)
        if not ok:
            print(f"error: {label} path not found: {path}", file=sys.stderr)
            return 2
    if args.live is not None and not os.path.isdir(args.live):
        print(f"error: --live directory not found: {args.live}", file=sys.stderr)
        return 2

    try:
        with open(args.map_path, encoding="utf-8") as fh:
            cmap = json.load(fh)
        with open(args.rules, encoding="utf-8") as fh:
            rules = json.load(fh)
    except (OSError, json.JSONDecodeError) as e:
        print(f"error: cannot read inputs: {e}", file=sys.stderr)
        return 2
    if not isinstance(cmap, dict) or "concepts" not in cmap or not cmap["concepts"]:
        print("error: map has no concepts", file=sys.stderr)
        return 2
    if not isinstance(rules, list) or not rules:
        print("error: rules dump is empty or not a list", file=sys.stderr)
        return 2

    rules_by_slug = {}
    for r in rules:
        rules_by_slug.setdefault(r["id"], []).append(r)

    wanted = None
    if args.concepts:
        wanted = {c.strip() for c in args.concepts.split(",") if c.strip()}
        known = {c.get("concept_id") for c in cmap["concepts"]}
        missing = wanted - known
        if missing:
            print(f"error: unknown concept_id(s): {sorted(missing)}", file=sys.stderr)
            return 2

    mode = "three-way" if args.live else "two-way"
    if not args.live:
        print("LIVE CHECKS SKIPPED: no --live directory given; running two-way "
              "(stock vs rules anchoring only). Drift between live and stock is "
              "NOT checked in this mode.")

    failures = []
    rows_checked = 0
    seen = set()
    for concept in cmap["concepts"]:
        cid = concept.get("concept_id") or concept["concept"][:40]
        if wanted is not None and cid not in wanted:
            continue
        for row in concept.get("governed_files", []):
            slug_file = row["file"]
            slug = slug_file[:-3] if slug_file.endswith(".md") else slug_file
            key = (cid, slug_file)
            if key in seen:
                continue
            seen.add(key)
            rows_checked += 1
            stock_path = os.path.join(args.store, slug_file)
            if not os.path.isfile(stock_path):
                failures.append(f"{cid} :: {slug_file}: MISSING-STOCK (slug not in store)")
                print(f"FAIL {cid} :: {slug_file}: MISSING-STOCK")
                continue
            stock_body = read_body(stock_path)
            slug_rules = rules_by_slug.get(slug, [])
            # Anchor check runs on the RAW file (frontmatter included), because
            # apply-unnerfs.py processes whole .md files and some rules edit
            # frontmatter description text (verified: body-only anchoring
            # false-flagged the 4 effort-tier description rules as stale).
            _, applied, unanchored = apply_rules(read_raw(stock_path), slug_rules)
            for rid, n in unanchored:
                failures.append(f"{cid} :: {slug_file}: STALE-ANCHOR rule {rid} "
                                f"(stock string matched {n} times, expected 1)")
                print(f"FAIL {cid} :: {slug_file}: STALE-ANCHOR {rid} (matches={n})")
            if args.live:
                live_path = os.path.join(args.live, slug_file)
                if not os.path.isfile(live_path):
                    failures.append(f"{cid} :: {slug_file}: MISSING-LIVE (not in live extract)")
                    print(f"FAIL {cid} :: {slug_file}: MISSING-LIVE")
                    continue
                live_body = read_body(live_path)
                expected, applied, _ = apply_rules(stock_body, slug_rules)
                if live_body == stock_body:
                    if slug_rules:
                        failures.append(f"{cid} :: {slug_file}: RULE-NOT-APPLIED "
                                        f"({[r['id'] for r in slug_rules]})")
                        print(f"PASS-STOCK-BUT FAIL {cid} :: {slug_file}: RULE-NOT-APPLIED")
                    else:
                        print(f"PASS {cid} :: {slug_file}: stock-aligned")
                elif live_body == expected:
                    print(f"PASS {cid} :: {slug_file}: rule-carried ({applied})")
                else:
                    failures.append(f"{cid} :: {slug_file}: UNEXPLAINED-DIFF "
                                    f"(live matches neither stock nor stock+rules)")
                    print(f"FAIL {cid} :: {slug_file}: UNEXPLAINED-DIFF")
            else:
                if unanchored:
                    pass  # already failed above
                elif slug_rules:
                    print(f"PASS {cid} :: {slug_file}: rule-covered ({[r['id'] for r in slug_rules]})")
                else:
                    print(f"PASS {cid} :: {slug_file}: no-rule (stock body is the live teaching)")

    print(f"\nSummary [{mode}]: {rows_checked} row(s) checked, {len(failures)} failure(s)")
    if failures:
        print(f"{len(failures)} failure(s); no silent partial success:", file=sys.stderr)
        for msg in failures:
            print("  " + msg, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
