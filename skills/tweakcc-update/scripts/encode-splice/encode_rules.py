#!/usr/bin/env python3
"""Encode approved Phase 2 batch rewrites into unnerfcc apply-unnerfs.py Rule data.

The transform is mechanical and deterministic. It reads ONLY the sealed, approved
revision trees under .claude/workspace/remediation/<batch>/ (consumed read-only)
and emits one Rule(stock, unnerf, description) per changed prompt, keyed by the
prompt filename. It performs NO prompt editing: stock is the sealed before-body
byte-for-byte, unnerf is the sealed after-body byte-for-byte.

Gates (all loud, fail-closed; Python 3 stdlib only):
  - approval.json verbatim_response must equal exactly "approved" (D-07).
  - the revision tree's seal digest must equal approval.json revision_digest (D-07).
  - encode predicate: disposition "retain" -> no rule; after == before (raw bytes)
    -> no rule; else emit a rule.
  - slot contract: ordered ${NAME} placeholders of before == after == the record's
    ordered_placeholders (APP-01 gate); mismatch is a loud failure.
  - idempotency: after not in before and before not in after; violation is loud.

Exit codes: 0 clean, 2 usage/config error, 3 watchdog timeout.

Usage:
  encode_rules.py --batch <name>            encode one batch, report counts
  encode_rules.py --all                     encode all eight batches
  encode_rules.py --all --emit              rewrite apply-unnerfs.py RULES + verify_records
  encode_rules.py --max-seconds N           watchdog ceiling (default 120)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import threading
import time
from hashlib import file_digest
from pathlib import Path

def _find_workspace() -> Path:
    """Return the .claude/workspace directory of the project. Walk up from
    this script until a parent holds .claude/workspace. The script can then
    live in the skill or in the workspace, and it finds the same project."""
    for parent in Path(__file__).resolve().parents:
        candidate = parent / ".claude" / "workspace"
        if candidate.is_dir():
            return candidate
    raise SystemExit("error: no .claude/workspace directory above " + str(Path(__file__).resolve()))


WORKSPACE = _find_workspace()
REM = WORKSPACE / "remediation"
APPLY = WORKSPACE.parent.parent / "unnerfcc" / "scripts" / "apply-unnerfs.py"
VERIFY_DIR = REM / "encode-splice" / "verify_records"

# Same positional-slot pattern as apply-unnerfs.py and schema.ordered_placeholders.
VAR = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)")

# The eight approved batches. Each consumes exactly the revision its approval names.
BATCHES = (
    "browser-automation",
    "claude-code-identity",
    "context-compression",
    "git-commit-pr",
    "memory-architecture-multi-agent-swarm-permission-system-feature-flags-internal-modes",
    "safety-rules",
    "system-reminder",
    "tool-usage-guidelines",
)

# Legacy (non-phase3) rules whose stock drifted from the 2.1.235 store and whose
# sealed disposition is "retain" (the 2.1.235 stock is the desired final state, so
# no un-nerf should fire). Removed from RULES during --emit. Recorded here so the
# removal is auditable, not silent.
STALE_LEGACY_TO_REMOVE = {
    "agent-prompt-general-purpose.md": "general-purpose: senior-dev completeness + thorough final report",
    "skill-schedule-recurring-cron-and-run-immediately.md": "cron-run-immediately confirm: thorough, explain cadence",
}


class EncodeError(ValueError):
    """A slot-contract or idempotency invariant was violated. Loud, fail-closed."""


def _die(msg: str) -> None:
    sys.stderr.write(f"ERROR: {msg}\n")
    raise SystemExit(2)


def sha256_file(path: Path) -> str:
    with Path(path).open("rb") as f:
        return file_digest(f, "sha256").hexdigest()


def seal_tree_digest(rev_dir: Path) -> str:
    """SHA-256 over the sorted (relpath, filedigest) manifest — matches remediate.py."""
    h = hashlib.sha256()
    for p in sorted(Path(rev_dir).rglob("*")):
        if p.is_file():
            rel = p.relative_to(rev_dir).as_posix()
            h.update(rel.encode("utf-8"))
            h.update(b"\0")
            h.update(sha256_file(p).encode("ascii"))
            h.update(b"\0")
    return h.hexdigest()


def body_bytes(path: Path) -> bytes:
    """Return the prompt body: raw bytes after the leading <!-- ... --> frontmatter.

    Line endings are preserved verbatim (CRLF or LF); only the frontmatter block and
    the single newline that terminates it are removed. Comparison is on raw bytes so
    CRLF-vs-LF churn cannot mask or fabricate an after != before difference.
    """
    raw = Path(path).read_bytes()
    i = raw.find(b"-->")
    if i < 0:
        return raw
    j = i + 3
    if raw[j : j + 2] == b"\r\n":
        j += 2
    elif raw[j : j + 1] in (b"\n", b"\r"):
        j += 1
    return raw[j:]


def ordered_placeholders(text: str) -> list[str]:
    return VAR.findall(text)


from dataclasses import dataclass


@dataclass(frozen=True)
class EncodedRule:
    """One encoded rule: byte-faithful stock/unnerf plus provenance."""
    filename: str
    stock: str
    unnerf: str
    description: str
    ordered_placeholders: tuple
    existing_override_disposition: str


def load_approval(batch_dir: Path) -> dict:
    """Load approval.json and require verbatim_response == exactly 'approved'."""
    ap = json.loads((Path(batch_dir) / "approval.json").read_text(encoding="utf-8"))
    if ap.get("verbatim_response") != "approved":
        sys.stderr.write(
            f"ERROR: {batch_dir}: verbatim_response is not exactly 'approved'\n"
        )
        raise SystemExit(2)
    return ap


def rule_for(rec: dict, before: bytes, after: bytes):
    """Apply the encode predicate to one record. Return an EncodedRule or None.

    None means "author nothing" (retain disposition, or after == before). Raises
    EncodeError (a ValueError) loudly on a slot-contract or idempotency violation.
    """
    fn = rec["filename"]
    disp = rec["existing_override_disposition"]
    if disp == "retain":
        return None
    if after == before:
        return None

    stock = before.decode("utf-8")
    unnerf = after.decode("utf-8")

    # Slot contract (APP-01): before, after, and the record must agree on placeholders.
    pb = ordered_placeholders(stock)
    pa = ordered_placeholders(unnerf)
    pr = list(rec.get("ordered_placeholders") or [])
    if not (pb == pa == pr):
        raise EncodeError(
            f"{fn}: slot-contract violation: before={pb} after={pa} record={pr}"
        )

    # Idempotency: neither body may contain the other verbatim.
    if after in before or before in after:
        raise EncodeError(f"{fn}: non-idempotent rule (one body contains the other)")

    stem = fn[:-3] if fn.endswith(".md") else fn
    return EncodedRule(
        filename=fn,
        stock=stock,
        unnerf=unnerf,
        description=f"phase3 {disp}: approved {stem} rewrite",
        ordered_placeholders=tuple(pr),
        existing_override_disposition=disp,
    )


def encode_batch(batch: str) -> tuple[list[dict], dict]:
    """Encode one approved batch. Return (rules, counts)."""
    batch_dir = REM / batch
    if not (batch_dir / "approval.json").exists():
        _die(f"missing approval.json for batch {batch}")
    ap = load_approval(batch_dir)
    rev = REM / batch / ap["revision"]
    if not rev.is_dir():
        _die(f"{batch}: sealed revision dir missing: {rev}")
    computed = seal_tree_digest(rev)
    if computed != ap["revision_digest"]:
        _die(
            f"{batch}: revision digest drift: computed {computed} != "
            f"approved {ap['revision_digest']}"
        )

    rules: list[dict] = []
    retained = noop = 0
    records = sorted((rev / "records").glob("*.json"))
    if not records:
        _die(f"{batch}: no records found in {rev / 'records'}")
    for rj in records:
        rec = json.loads(rj.read_text(encoding="utf-8"))
        fn = rec["filename"]
        bpath = rev / "prompts" / "before" / fn
        apath = rev / "prompts" / "after" / fn
        if not bpath.exists():
            _die(f"{batch}: missing before-body for {fn}")
        if not apath.exists():
            _die(f"{batch}: missing after-body for {fn}")
        before = body_bytes(bpath)
        after = body_bytes(apath)
        r = rule_for(rec, before, after)
        if r is None:
            if rec["existing_override_disposition"] == "retain":
                retained += 1
            else:
                noop += 1
            continue
        rules.append({"rule": r, "batch": batch, "revision": ap["revision"]})
    counts = {
        "batch": batch,
        "revision": ap["revision"],
        "rules_written": len(rules),
        "retained": retained,
        "noop": noop,
        "total_records": len(records),
    }
    return rules, counts


# --- apply-unnerfs.py emission -----------------------------------------------

def py_str(s: str) -> str:
    """Render a string as a Python double-quoted literal, byte-faithful.

    Uses repr with a double-quote preference so the emitted RULES read like the
    existing file. repr guarantees a round-trippable literal for any content.
    """
    # repr picks single or double quotes; force a stable form via json-ish escaping
    # but keep it a valid Python literal. repr() is exact and safe.
    return repr(s)


def write_verify_records(all_rules: list[dict]) -> None:
    VERIFY_DIR.mkdir(parents=True, exist_ok=True)
    for item in all_rules:
        r = item["rule"]
        rec = {
            "filename": r.filename,
            "batch": item["batch"],
            "revision": item["revision"],
            "existing_override_disposition": r.existing_override_disposition,
            "ordered_placeholders": list(r.ordered_placeholders),
            "placeholder_equality": "PASS",
            "description": r.description,
        }
        out = VERIFY_DIR / f"{r.filename}.json"
        out.write_text(json.dumps(rec, indent=2, ensure_ascii=False), encoding="utf-8")


def _arm_watchdog(max_seconds: float, probe_seconds: float) -> None:
    """Deterministic termination guard: hard-kill with exit code 3 at the wall-clock ceiling.

    Uses threading.Timer + os._exit (cross-platform; SIGALRM does not exist on
    Windows). Daemon timer never blocks a normal fast exit. Per
    recipe-skill-script-hardening-1.0.0.md [A001.1].
    """
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


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--batch", help="encode one named batch")
    g.add_argument("--all", action="store_true", help="encode all eight batches")
    p.add_argument("--emit", action="store_true",
                   help="rewrite apply-unnerfs.py RULES and write verify_records/")
    p.add_argument("--max-seconds", type=float, default=120.0, dest="max_seconds",
                   help="Hard wall-clock ceiling in seconds; the process exits with "
                        "code 3 when it fires (default: 120)")
    p.add_argument("--watchdog-probe", type=float, default=0.0, dest="watchdog_probe",
                   help="Diagnostic: idle this many seconds after arming the watchdog "
                        "(default: 0)")
    args = p.parse_args(argv)

    _arm_watchdog(args.max_seconds, args.watchdog_probe)

    batches = BATCHES if args.all else (args.batch,)
    if not args.all and args.batch not in BATCHES:
        _die(f"unknown batch {args.batch!r}; known: {', '.join(BATCHES)}")

    all_rules: list[dict] = []
    summary = []
    for b in batches:
        rules, counts = encode_batch(b)
        all_rules.extend(rules)
        summary.append(counts)

    if args.emit:
        emit_into_apply(all_rules)
        write_verify_records(all_rules)

    print(json.dumps({
        "batches": summary,
        "total_rules": len(all_rules),
        "emitted": bool(args.emit),
    }, indent=2))
    return 0


def emit_into_apply(all_rules: list[dict]) -> None:
    """Rewrite the RULES dict in apply-unnerfs.py: keep legacy rules, replace phase3.

    Strategy: parse the existing file's RULES via import, split each filename's rule
    list into legacy (description not starting 'phase3 ') vs phase3. Rebuild RULES as
    legacy-minus-stale + freshly-encoded phase3. A count-asserted rewrite; a missed
    RULES boundary raises loudly.
    """
    src = APPLY.read_text(encoding="utf-8")

    # Load the current RULES object by executing the module in an isolated namespace.
    import importlib.util
    spec = importlib.util.spec_from_file_location("apply_unnerfs_mod", APPLY)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["apply_unnerfs_mod"] = mod
    spec.loader.exec_module(mod)
    current = mod.RULES
    Rule = mod.Rule

    # Separate legacy from phase3 in the current file.
    legacy: dict[str, list] = {}
    for fn, lst in current.items():
        keep = [r for r in lst if not r.description.startswith("phase3 ")]
        if keep:
            legacy[fn] = keep

    # Remove the stale legacy rules (retain slugs whose stock drifted).
    removed = []
    for fn, desc in STALE_LEGACY_TO_REMOVE.items():
        if fn in legacy:
            before = len(legacy[fn])
            legacy[fn] = [r for r in legacy[fn] if r.description != desc]
            if len(legacy[fn]) != before:
                removed.append((fn, desc))
            if not legacy[fn]:
                del legacy[fn]
    if len(removed) != len(STALE_LEGACY_TO_REMOVE):
        raise EncodeError(
            f"expected to remove {len(STALE_LEGACY_TO_REMOVE)} stale legacy rules, "
            f"removed {len(removed)}: {removed}"
        )

    # Build the new phase3 group.
    phase3_by_file: dict[str, list] = {}
    for item in all_rules:
        r = item["rule"]
        phase3_by_file.setdefault(r.filename, []).append(r)
    for fn, lst in phase3_by_file.items():
        if len(lst) != 1:
            raise EncodeError(f"{fn}: expected exactly one phase3 rule, got {len(lst)}")

    # Merge: for each filename, legacy rules first, then phase3. No slug may carry a
    # duplicate phase3 rule (one per filename here since one revision per batch).
    merged: dict[str, list] = {}
    for fn in sorted(set(legacy) | set(phase3_by_file)):
        merged[fn] = []
        merged[fn].extend(legacy.get(fn, []))
        for r in phase3_by_file.get(fn, []):
            merged[fn].append(Rule(stock=r.stock, unnerf=r.unnerf,
                                   description=r.description))

    # Render the entire RULES dict fresh.
    lines = ["RULES: dict[str, list[Rule]] = {"]
    for fn in sorted(merged):
        lines.append(f"    {py_str(fn)}: [")
        for r in merged[fn]:
            lines.append("        Rule(")
            lines.append(f"            stock={py_str(r.stock)},")
            lines.append(f"            unnerf={py_str(r.unnerf)},")
            lines.append(f"            description={py_str(r.description)},")
            lines.append("        ),")
        lines.append("    ],")
    lines.append("}")
    new_rules_src = "\n".join(lines)

    # Replace the existing RULES = { ... } block. Count-asserted (loud-replace
    # recipe): exactly one RULES assignment must exist, or fail without writing.
    marker = "RULES: dict[str, list[Rule]] = {"
    hits = src.count(marker)
    if hits != 1:
        raise EncodeError(
            f"expected exactly 1 RULES assignment in apply-unnerfs.py, found {hits}"
        )
    start = src.find(marker)
    end = src.find("\n}\n", start)
    if end < 0:
        raise EncodeError("could not locate end of RULES block (no column-0 close)")
    end += len("\n}\n")
    new_src = src[:start] + new_rules_src + "\n" + src[end:]

    # Write, then prove the result imports and carries the expected phase3 count.
    APPLY.write_text(new_src, encoding="utf-8")
    import importlib.util as _ilu
    spec2 = _ilu.spec_from_file_location("apply_unnerfs_check", APPLY)
    mod2 = _ilu.module_from_spec(spec2)
    sys.modules["apply_unnerfs_check"] = mod2
    spec2.loader.exec_module(mod2)
    phase3 = sum(1 for lst in mod2.RULES.values() for r in lst
                 if r.description.startswith("phase3 "))
    expected = sum(len(v) for v in phase3_by_file.values())
    if phase3 != expected:
        raise EncodeError(
            f"post-emit verify: apply-unnerfs.py has {phase3} phase3 rules, "
            f"expected {expected}"
        )


if __name__ == "__main__":
    sys.exit(main())
