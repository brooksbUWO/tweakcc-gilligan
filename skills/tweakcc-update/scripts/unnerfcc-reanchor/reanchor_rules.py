#!/usr/bin/env python3
"""Count-asserted editor for the unnerfcc rule catalog at rules_dir/<id>.json.

Edits the JSON files under a rules directory (one file per prompt id, the
shape `{"id": "<id>", "rules": [{"description", "stock", "unnerf"}, ...]}`,
each body an array of lines) so every change lands on exactly one rule
entry and the store is rewritten only when every operation matched.
Operations come from a JSON spec (a list):

  {"op": "reanchor", "file": "<key>.md", "match": "<substring of the CURRENT stock>",
   "stock": "<new stock>", "unnerf": "<new unnerf>"?, "description": "<new text>"?}
  {"op": "rekey",    "file": "<old key>.md", "new_file": "<new key>.md"}
  {"op": "retire",   "file": "<key>.md"}                      # drops the whole entry
  {"op": "add",      "file": "<key>.md", "stock": ..., "unnerf": ..., "description": ...}

Rules of the contract (recipe-skill-script-loud-replace):
  - reanchor/retire must select exactly one target; rekey's new key must not exist.
  - Nothing is written when any operation fails; the report names each failure.
  - --dry-run reports what would change without writing.
Exit codes: 0 all operations applied, 1 one or more failed, 2 usage error,
3 terminated at the wall-clock ceiling (recipe-skill-script-hardening).

Usage: python reanchor_rules.py <rules dir> <spec.json> [--dry-run] [--max-seconds N]
Substitute for another project: any directory of `<id>.json` files with the
same {"id", "rules": [{"description", "stock", "unnerf"}]} shape.
"""
import argparse
import json
import os
import sys
import threading
import time
from pathlib import Path


def _arm_watchdog(max_seconds: float, probe_seconds: float) -> None:
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


def _validate_entry(path, data) -> None:
    """Validates one store entry against the same rules the plan-01 loader
    enforces, raising SystemExit with the loader's exact messages on failure.

    Shared by _load_store (on read) and _write_store (on every touched entry,
    before it is written), so an authoring op cannot produce a rule the
    loader would reject on the next run.
    """
    if not isinstance(data, dict):
        raise SystemExit(f"error: {path}: top-level JSON must be an object")
    pid = Path(path).stem
    if data.get("id") != pid:
        raise SystemExit(f"error: {path} declares id {data.get('id')!r}, filename stem is {pid!r}")
    rules = data.get("rules")
    if not isinstance(rules, list) or not rules:
        raise SystemExit(f"error: {path}: 'rules' must be a non-empty list")
    for r in rules:
        if not isinstance(r, dict):
            raise SystemExit(f"error: {path}: rule entry must be an object")
        description = r.get("description")
        if not isinstance(description, str) or not description:
            raise SystemExit(f"error: {path}: rule has a missing or non-string 'description'")
        for field in ("stock", "unnerf"):
            value = r.get(field)
            if not isinstance(value, list) or not value:
                raise SystemExit(f"error: {path}: rule has a missing or empty list '{field}'")
            if not all(isinstance(line, str) for line in value):
                raise SystemExit(f"error: {path}: rule field '{field}' has a non-string element")
            if not any(line for line in value):
                raise SystemExit(f"error: {path}: rule field '{field}' is empty")
            if any("\r" in line for line in value):
                raise SystemExit(f"error: {path}: rule field '{field}' contains a carriage return")


def _validate_entry_or_raise_value_error(path, data) -> None:
    """Same check as _validate_entry, but as a ValueError the op loop's
    `except (ValueError, KeyError, AttributeError)` can catch and aggregate,
    instead of the SystemExit _validate_entry raises for _load_store's
    loud-fail callers. Keeps _load_store's own SystemExit messages unchanged."""
    try:
        _validate_entry(path, data)
    except SystemExit as e:
        raise ValueError(str(e))


def _load_store(rules_dir: Path) -> dict:
    """Loads every rules_dir/*.json into {"<stem>.md": {"id":..., "rules":[...]}}.

    Fails loudly (SystemExit) on malformed JSON or a filename/id mismatch, so
    the tool refuses to operate on a store the plan-01 loader would reject.
    """
    store = {}
    for path in sorted(rules_dir.glob("*.json")):
        pid = path.stem
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            raise SystemExit(f"error: malformed JSON in {path}: {e}")
        _validate_entry(path, data)
        store[f"{pid}.md"] = data
    return store


def _write_store(rules_dir: Path, store: dict, touched: set, dry_run: bool = False) -> None:
    """Validates every touched entry (pass 1), then writes/deletes (pass 2, unless dry_run).

    Two-pass and all-or-nothing: pass 1 validates every touched entry still in
    the store and pre-serializes it, writing and deleting nothing; only if
    pass 1 raises no error does pass 2 delete retired keys and write the rest.
    This backstops the op loop's own validation (main() already validates
    authored/mutated entries before reaching here) so a failure here still
    cannot leave the store partially mutated. A retired key (absent from store
    but present in touched) has its file deleted. Every write opens with
    newline="\\n" so an edit made on Windows cannot introduce a CR byte into a
    rule body. Under dry_run, the serialize-then-parse-back check still runs;
    nothing is deleted or written. `touched` is iterated in sorted order so
    the outcome does not depend on PYTHONHASHSEED-driven set ordering.
    """
    pending_writes = {}
    for key in sorted(touched):
        if key not in store:
            continue
        stem = key[:-3] if key.endswith(".md") else key
        path = rules_dir / f"{stem}.json"
        _validate_entry(path, store[key])
        text = json.dumps(store[key], indent=1, ensure_ascii=False) + "\n"
        json.loads(text)  # re-parse guard: catches a malformed write before it lands
        pending_writes[key] = (path, text)

    if dry_run:
        return
    for key in sorted(touched):
        if key not in store:
            stem = key[:-3] if key.endswith(".md") else key
            path = rules_dir / f"{stem}.json"
            if path.exists():
                path.unlink()
            continue
        path, text = pending_writes[key]
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("rules_dir")
    parser.add_argument("spec")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--max-seconds", type=float, default=120.0, dest="max_seconds")
    parser.add_argument("--watchdog-probe", type=float, default=0.0, dest="watchdog_probe")
    args = parser.parse_args()
    _arm_watchdog(args.max_seconds, args.watchdog_probe)

    rules_dir = Path(args.rules_dir)
    try:
        with open(args.spec, encoding="utf-8-sig") as f:
            ops = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    if not isinstance(ops, list) or not ops:
        print("error: spec must be a non-empty JSON list", file=sys.stderr)
        return 2
    if not rules_dir.is_dir():
        print(f"error: {rules_dir} is not a directory", file=sys.stderr)
        return 2

    store = _load_store(rules_dir)

    failures = []
    retired = set()
    touched = set()
    for i, op in enumerate(ops):
        kind = op.get("op")
        key = op.get("file")
        label = f"op {i} {kind} {key}"
        stem = key[:-3] if isinstance(key, str) and key.endswith(".md") else key
        path = rules_dir / f"{stem}.json"
        try:
            if kind == "reanchor":
                if key not in store:
                    raise ValueError(f"key {key!r} not in store")
                entry = store[key]
                rule_list = entry["rules"]
                hits = [r for r in rule_list if op["match"] in "\n".join(r["stock"])]
                if len(hits) != 1:
                    raise ValueError(f"match selects {len(hits)} rule(s), expected 1")
                rule = hits[0]
                for field in ("stock", "unnerf", "description"):
                    if field in op:
                        if field == "description":
                            rule["description"] = op["description"]
                        else:
                            if "\r" in op[field]:
                                raise ValueError(f"error: {field} contains a carriage return (\\r)")
                            rule[field] = op[field].split("\n")
                _validate_entry_or_raise_value_error(path, entry)
                touched.add(key)
                print(f"PASS {label}: {', '.join(f for f in ('stock', 'unnerf', 'description') if f in op)} updated")
            elif kind == "rekey":
                if key not in store:
                    raise ValueError(f"key {key!r} not in store")
                new_key = op["new_file"]
                if new_key in store:
                    raise ValueError(f"new key {new_key!r} already exists (use retire + add to merge)")
                entry = store.pop(key)
                new_stem = new_key[:-3] if new_key.endswith(".md") else new_key
                entry["id"] = new_stem
                store[new_key] = entry
                new_path = rules_dir / f"{new_stem}.json"
                _validate_entry_or_raise_value_error(new_path, entry)
                touched.add(key)
                touched.add(new_key)
                print(f"PASS {label} -> {new_key}")
            elif kind == "retire":
                if key not in store:
                    raise ValueError(f"key {key!r} not in store")
                if key in retired:
                    raise ValueError("already retired in this spec")
                entry = store.pop(key)
                retired.add(key)
                touched.add(key)
                print(f"PASS {label}: entry removed ({len(entry['rules'])} rule(s))")
            elif kind == "add":
                for field in ("stock", "unnerf"):
                    if "\r" in op[field]:
                        raise ValueError(f"error: {field} contains a carriage return (\\r)")
                new_rule = {
                    "description": op["description"],
                    "stock": op["stock"].split("\n"),
                    "unnerf": op["unnerf"].split("\n"),
                }
                if key in store:
                    store[key]["rules"].append(new_rule)
                    _validate_entry_or_raise_value_error(path, store[key])
                    print(f"PASS {label}: rule appended to existing entry")
                else:
                    store[key] = {"id": stem, "rules": [new_rule]}
                    _validate_entry_or_raise_value_error(path, store[key])
                    print(f"PASS {label}: new entry created")
                touched.add(key)
            else:
                raise ValueError(f"unknown op {kind!r}")
        except (ValueError, KeyError, AttributeError) as e:
            failures.append(f"{label}: {e}")
            print(f"FAIL {label}: {e}")

    if failures:
        print(f"\n{len(failures)} of {len(ops)} operation(s) FAILED; nothing written:", file=sys.stderr)
        for f_ in failures:
            print("  " + f_, file=sys.stderr)
        return 1

    _write_store(rules_dir, store, touched, dry_run=args.dry_run)
    if args.dry_run:
        print(f"\ndry run: {len(ops)} operation(s) would apply; store still parses")
        return 0
    print(f"\n{len(ops)} operation(s) applied to {rules_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
