#!/usr/bin/env python3
"""Reusable stdlib remediation harness CLI for Phase 2 per-batch processing.

Commands (fail-closed; corpus/reviewer text is untrusted data, never executed):

  tracer       End-to-end fixture path: backup -> verify -> immutable r0001 ->
               fail-closed eligibility. Used by the Wave 0 tracer test.
  materialize  Copy an exact CSV batch into a new immutable revision tree.
  validate     Check queue equality, record completeness, and gate predicates
               (including --require-latest-terminal-review,
               --require-post-terminal-doctrine-reread, --require-post-reread-approval).
  seal         Compute and record the revision tree SHA-256 digest.
  import-evidence  Import schema-valid ordered review/reread/approval evidence.
  status       Print evidence-derived gate status as JSON.

Every input is resolved beneath an allowlisted root; traversal, links, unknown
fields, unknown enum values, missing files, and digest mismatches are rejected.
Revision directories and evidence files use exclusive creation so a prior
revision can never be overwritten. Python 3 standard library only.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from hashing import (  # noqa: E402
    BackupError,
    PathSafetyError,
    copy_verified,
    is_valid_sha256,
    reject_link,
    resolve_within,
    sha256_file,
)
from schema import (  # noqa: E402
    SchemaError,
    ordered_placeholders,
    require_same_placeholders,
    validate_prompt_record,
)
from state import (  # noqa: E402
    Approval,
    DoctrineReread,
    EvidenceError,
    GateState,
    LaneReport,
    TerminalReview,
    reduce_gate,
)

BACKUP_ROOT = Path(".claude/workspace/.backups")
REMEDIATION_ROOT = Path(".claude/workspace/remediation")


class HarnessError(RuntimeError):
    """A loud, fail-closed harness failure with an actionable message."""


# --- shared helpers ----------------------------------------------------------

def read_queue(csv_path: Path) -> list[dict[str, str]]:
    """Read the batch queue CSV; reject links and non-bare filenames."""
    reject_link(csv_path)
    if not csv_path.exists():
        raise HarnessError(f"queue not found: {csv_path}")
    rows: list[dict[str, str]] = []
    with csv_path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        if reader.fieldnames is None or "file" not in reader.fieldnames:
            raise HarnessError(f"queue missing 'file' column: {csv_path}")
        for row in reader:
            name = row["file"]
            if "/" in name or "\\" in name or name in ("", ".", ".."):
                raise HarnessError(f"queue row has unsafe filename: {name!r}")
            rows.append(row)
    if not rows:
        raise HarnessError(f"queue is empty: {csv_path}")
    return rows


def create_revision_dir(batch_root: Path, revision: str) -> Path:
    """Exclusively create batch_root/<revision>; refuse to overwrite."""
    rev_dir = batch_root / revision
    try:
        rev_dir.mkdir(parents=True, exist_ok=False)
    except FileExistsError as exc:
        raise HarnessError(
            f"revision already exists (immutable): {rev_dir}"
        ) from exc
    return rev_dir


def write_json_exclusive(path: Path, obj: Any) -> None:
    """Write JSON with exclusive creation so evidence is never overwritten."""
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(obj, indent=2, sort_keys=True).encode("utf-8")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    with os.fdopen(fd, "wb") as out:
        out.write(data)


def timestamped_backup_dir(batch: str, revision: str) -> Path:
    """A unique snapshot dir under the locked backup root; never overwritten."""
    import datetime
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    return BACKUP_ROOT / batch / f"{stamp}-{revision}"


def seal_tree_digest(rev_dir: Path) -> str:
    """SHA-256 over the sorted (relpath, filedigest) manifest of a revision tree."""
    import hashlib
    h = hashlib.sha256()
    for p in sorted(rev_dir.rglob("*")):
        if p.is_file():
            rel = p.relative_to(rev_dir).as_posix()
            h.update(rel.encode("utf-8"))
            h.update(b"\0")
            h.update(sha256_file(p).encode("ascii"))
            h.update(b"\0")
    return h.hexdigest()


# --- tracer command ----------------------------------------------------------

def cmd_tracer(args: argparse.Namespace) -> int:
    """One fixture prompt: backup+verify source, create immutable r0001 record."""
    queue_csv = Path(args.queue)
    prompts_dir = Path(args.prompts_dir)
    out_root = Path(args.out)
    batch = args.batch

    rows = read_queue(queue_csv)
    target = args.filename
    row = next((r for r in rows if r["file"] == target), None)
    if row is None:
        raise HarnessError(f"{target} not in queue {queue_csv}")

    source = prompts_dir / target
    reject_link(source)
    if not source.exists():
        raise HarnessError(f"source prompt missing: {source}")

    # Backup BEFORE any change: copy, then prove byte-identical.
    backup_dir = timestamped_backup_dir(batch, "r0001")
    backup_path = backup_dir / target
    verified_digest = copy_verified(source, backup_path)
    source_digest = sha256_file(source)
    if verified_digest != source_digest:
        raise BackupError("verified backup digest does not equal source digest")

    # Immutable revision r0001 (exclusive create).
    batch_root = out_root / batch
    rev_dir = create_revision_dir(batch_root, "r0001")
    before_text = source.read_text(encoding="utf-8")
    placeholders = ordered_placeholders(before_text)

    # Seed a complete defective record (tracer proves the schema path).
    before_ref_dir = rev_dir / "prompts" / "before"
    before_ref_dir.mkdir(parents=True, exist_ok=True)
    before_copy = before_ref_dir / target
    copy_verified(source, before_copy)

    after_ref_dir = rev_dir / "prompts" / "after"
    after_ref_dir.mkdir(parents=True, exist_ok=True)
    after_copy = after_ref_dir / target
    # Tracer "after" preserves placeholders and adds a self-check line.
    after_text = before_text.rstrip("\n") + "\nReport what you cut and why.\n"
    require_same_placeholders(before_text, after_text)
    after_copy.write_bytes(after_text.encode("utf-8"))

    record = {
        "filename": target,
        "class_checks": [
            {"defect": d, "result": "positive" if d == "Over-constraint" else "negative",
             "reasoning": f"tracer fixture check for {d}"}
            for d in (
                "Over-constraint", "Register mismatch", "Token economy",
                "Contradictory rules", "Silent failure", "Process/artifact split",
            )
        ],
        "clean": False,
        "defect_tags": ["Over-constraint"],
        "before": {"path": "prompts/before/" + target, "sha256": sha256_file(before_copy)},
        "after": {"path": "prompts/after/" + target, "sha256": sha256_file(after_copy)},
        "ordered_placeholders": placeholders,
        "doctrine_map": [{"change": "lifted line cap", "doctrine": "Over-constraint"}],
        "existing_override_disposition": "supersede",
        "confidence": "high",
        "gate_status": "draft",
    }
    validate_prompt_record(record)
    records_dir = rev_dir / "records"
    write_json_exclusive(records_dir / (target + ".json"), record)

    # Seal the revision tree and write a gate.json with fail-closed eligibility.
    tree_digest = seal_tree_digest(rev_dir)
    gate = {
        "batch": batch,
        "revision": "r0001",
        "revision_digest": tree_digest,
        "source_hash_verified": True,
        "backup_verified": True,
        "backup_dir": str(backup_dir),
        # No review/reread/approval evidence yet -> eligibility must be false.
        "dual_zero_same_round": False,
        "doctrine_reread_passed": False,
        "dated_user_approval_recorded": False,
        "phase3_eligible": False,
    }
    write_json_exclusive(rev_dir / "gate.json", gate)

    print(json.dumps({
        "backup_dir": str(backup_dir.resolve()),
        "source_digest": source_digest,
        "backup_digest": verified_digest,
        "revision": "r0001",
        "revision_digest": tree_digest,
        "ordered_placeholders": placeholders,
        "phase3_eligible": False,
    }, indent=2))
    return 0


# --- materialize / validate / seal / import-evidence / status ----------------

def cmd_materialize(args: argparse.Namespace) -> int:
    queue_csv = Path(args.queue)
    prompts_dir = Path(args.prompts_dir)
    out_root = Path(args.out)
    batch = args.batch
    rows = read_queue(queue_csv)
    batch_rows = [r for r in rows if r.get("batch") == batch] if args.match_batch else rows
    if not batch_rows:
        raise HarnessError(f"no queue rows for batch {batch}")

    batch_root = out_root / batch
    rev_dir = create_revision_dir(batch_root, args.revision)
    before_dir = rev_dir / "prompts" / "before"
    before_dir.mkdir(parents=True, exist_ok=True)
    manifest = []
    for r in batch_rows:
        name = r["file"]
        src = prompts_dir / name
        reject_link(src)
        if not src.exists():
            raise HarnessError(f"source prompt missing: {src}")
        digest = copy_verified(src, before_dir / name)
        manifest.append({"file": name, "sha256": digest})
    write_json_exclusive(rev_dir / "batch.json", {"batch": batch, "queue": manifest})
    print(json.dumps({"batch": batch, "revision": args.revision, "count": len(manifest)}, indent=2))
    return 0


def _load_json(path: Path) -> Any:
    reject_link(path)
    if not path.exists():
        raise HarnessError(f"evidence file missing: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _lane_from_dict(d: dict[str, Any]) -> LaneReport:
    return LaneReport(
        lane=d["lane"],
        provider=d.get("provider", ""),
        model_id=d["model_id"],
        revision_digest=d["revision_digest"],
        round=int(d["round"]),
        report_digest=d.get("report_digest", ""),
        finding_count=int(d["finding_count"]),
        timestamp=float(d["timestamp"]),
        sequence=int(d["sequence"]),
        catalog=tuple(d.get("catalog", [])),
        probe_command=d.get("probe_command", ""),
        probe_exit_code=d.get("probe_exit_code"),
        probe_output=d.get("probe_output", ""),
    )


def load_evidence(evidence_path: Path) -> dict[str, Any]:
    """Load an ordered-evidence bundle: lanes + optional terminal/reread/approval."""
    data = _load_json(evidence_path)
    lanes = [_lane_from_dict(x) for x in data.get("lane_reports", [])]
    tr = data.get("terminal_review")
    dr = data.get("doctrine_reread")
    ap = data.get("approval")
    return {
        "revision_digest": data["revision_digest"],
        "lane_reports": lanes,
        "terminal_review": TerminalReview(**tr) if tr else None,
        "doctrine_reread": DoctrineReread(
            revision_digest=dr["revision_digest"],
            doctrine_digest=dr["doctrine_digest"],
            reread_timestamp=float(dr["reread_timestamp"]),
            reread_sequence=int(dr["reread_sequence"]),
            result=dr["result"],
            blind_spots=tuple(dr.get("blind_spots", [])),
        ) if dr else None,
        "approval": Approval(
            revision_digest=ap["revision_digest"],
            approval_timestamp=float(ap["approval_timestamp"]),
            approval_sequence=int(ap["approval_sequence"]),
            date=ap["date"],
            verbatim_response=ap["verbatim_response"],
        ) if ap else None,
    }


def evidence_gate(evidence_path: Path) -> GateState:
    ev = load_evidence(evidence_path)
    return reduce_gate(
        lane_reports=ev["lane_reports"],
        revision_digest=ev["revision_digest"],
        terminal_review=ev["terminal_review"],
        doctrine_reread=ev["doctrine_reread"],
        approval=ev["approval"],
    )


# --batch resolution against the on-disk remediation layout -------------------
#
# materialize/seal build remediation/<batch>/<revision>/ trees (records under
# <revision>/records/) and import-evidence conventionally lands the ordered
# bundle at remediation/<batch>/evidence.json. resolve_batch_paths derives the
# queue, records-dir, and evidence path a validate run needs from that layout,
# choosing the highest-numbered existing revision so a re-materialized batch
# validates its latest immutable revision. Resolution is deterministic and never
# creates anything.

def _latest_revision_dir(batch_dir: Path) -> Path | None:
    if not batch_dir.is_dir():
        return None
    revs = sorted(
        (p for p in batch_dir.iterdir() if p.is_dir() and p.name.startswith("r")),
        key=lambda p: p.name,
    )
    return revs[-1] if revs else None


def resolve_batch_paths(remediation_root: Path, batch: str) -> dict[str, Path | None]:
    """Resolve queue/records-dir/evidence for a batch from the on-disk layout."""
    batch_dir = remediation_root / batch
    rev = _latest_revision_dir(batch_dir)
    queue = batch_dir / "queue.csv"
    evidence = batch_dir / "evidence.json"
    records = (rev / "records") if rev is not None else None
    return {
        "batch_dir": batch_dir,
        "queue": queue if queue.exists() else None,
        "records_dir": records if (records and records.exists()) else None,
        "evidence": evidence if evidence.exists() else None,
    }


# Gate names accepted by --require, mapped to their evidence-derived predicate.
_GATE_NAMES = (
    "classification", "drafts", "boolean", "writing",
    "doctrine", "dual-review", "approved",
)


def _raw_lanes(evidence_path: Path) -> list[dict[str, Any]]:
    """Raw lane_reports dicts (untrusted data; read-only inspection only)."""
    data = _load_json(evidence_path)
    return list(data.get("lane_reports", []))


def _raw_approval(evidence_path: Path) -> Any:
    return _load_json(evidence_path).get("approval")


def _gate_satisfied(name: str, evidence_path: Path, records_dir: Path | None,
                    gate: GateState | None) -> tuple[bool, str]:
    """Return (ok, detail) for a single --require gate name.

    classification/drafts/writing bind to per-prompt records existing and being
    schema-valid; boolean/doctrine/dual-review/approved bind to executed
    evidence via the reducer. A gate whose evidence is absent is not satisfied.
    """
    if name in ("classification", "drafts", "writing"):
        if not records_dir or not records_dir.is_dir():
            return False, "no records directory"
        recs = sorted(records_dir.glob("*.json"))
        if not recs:
            return False, "no per-prompt records"
        for p in recs:
            try:
                validate_prompt_record(json.loads(p.read_text(encoding="utf-8")))
            except SchemaError as exc:
                return False, f"invalid record {p.name}: {exc}"
        return True, ""
    if gate is None:
        return False, "no evidence bundle"
    if name == "boolean":
        # Boolean evidence is a precondition of any eligible review lane.
        ok = bool(_raw_lanes(evidence_path))
        return ok, "" if ok else "no lane reports"
    if name == "doctrine":
        return gate.post_terminal_reread_valid, "; ".join(gate.reasons)
    if name == "dual-review":
        return gate.dual_zero_same_round, "; ".join(gate.reasons)
    if name == "approved":
        return gate.post_reread_approval_valid, "; ".join(gate.reasons)
    return False, "unknown gate"


def cmd_validate(args: argparse.Namespace) -> int:
    problems: list[str] = []

    queue = args.queue
    records_dir = args.records_dir
    evidence = args.evidence

    # --batch resolves queue/records/evidence from the on-disk layout.
    if args.batch:
        root = Path(args.remediation_root) if args.remediation_root else REMEDIATION_ROOT
        resolved = resolve_batch_paths(root, args.batch)
        queue = queue or (str(resolved["queue"]) if resolved["queue"] else None)
        records_dir = records_dir or (str(resolved["records_dir"]) if resolved["records_dir"] else None)
        evidence = evidence or (str(resolved["evidence"]) if resolved["evidence"] else None)

    records_path = Path(records_dir) if records_dir else None

    # Queue-vs-records equality and record completeness.
    if queue and records_dir:
        rows = read_queue(Path(queue))
        want = {r["file"] for r in rows}
        rec_dir = Path(records_dir)
        have = {p.name[:-5] for p in rec_dir.glob("*.json")} if rec_dir.exists() else set()
        if want != have:
            problems.append(f"queue/record set mismatch: missing={sorted(want-have)} extra={sorted(have-want)}")
        for p in sorted(rec_dir.glob("*.json")) if rec_dir.exists() else []:
            try:
                validate_prompt_record(json.loads(p.read_text(encoding="utf-8")))
            except SchemaError as exc:
                problems.append(f"{p.name}: {exc}")

    gate: GateState | None = None
    if evidence:
        gate = evidence_gate(Path(evidence))

    # --require <csv-list>: each named gate must be satisfied. First missing or
    # invalid gate is named. Unknown gate names are rejected with their value.
    if args.require:
        names = [g.strip() for g in args.require.split(",") if g.strip()]
        for name in names:
            if name not in _GATE_NAMES:
                problems.append(f"unknown gate name {name!r}; expected one of {list(_GATE_NAMES)}")
                continue
            ok, detail = _gate_satisfied(name, Path(evidence) if evidence else Path(os.devnull),
                                         records_path, gate)
            if not ok:
                problems.append(f"required gate {name!r} not satisfied: {detail}".rstrip(": "))

    # Reducer-backed predicate flags. Each requires an evidence bundle.
    if gate is not None:
        if args.require_dual_zero_same_round and not gate.dual_zero_same_round:
            problems.append("dual-zero-same-round predicate failed: " + "; ".join(gate.reasons))
        if args.require_latest_terminal_review and not gate.latest_terminal_review_valid:
            problems.append("latest-terminal-review predicate failed: " + "; ".join(gate.reasons))
        if args.require_post_terminal_doctrine_reread and not gate.post_terminal_reread_valid:
            problems.append("post-terminal doctrine-reread predicate failed: " + "; ".join(gate.reasons))
        if args.require_post_reread_approval and not gate.post_reread_approval_valid:
            problems.append("post-reread approval predicate failed: " + "; ".join(gate.reasons))
        if args.require_approval_binding and not gate.post_reread_approval_valid:
            problems.append("approval-binding predicate failed: " + "; ".join(gate.reasons))
        if args.require_phase3_eligible and not gate.phase3_eligible:
            problems.append("phase3-eligible predicate failed: " + "; ".join(gate.reasons))
    else:
        for flag, attr in (
            ("--require-dual-zero-same-round", "require_dual_zero_same_round"),
            ("--require-latest-terminal-review", "require_latest_terminal_review"),
            ("--require-post-terminal-doctrine-reread", "require_post_terminal_doctrine_reread"),
            ("--require-post-reread-approval", "require_post_reread_approval"),
            ("--require-approval-binding", "require_approval_binding"),
            ("--require-phase3-eligible", "require_phase3_eligible"),
        ):
            if getattr(args, attr):
                problems.append(f"{flag} requires an evidence bundle, none resolved")

    # Live-model-list and usability-probe checks read the raw catalog/probe
    # evidence (selections in catalog exactly once, read-only probes exit 0).
    if args.require_live_model_lists or args.require_model_usability_probes or \
            args.exclude_review_family or args.exclude_review_provider or args.deny_reviewer:
        if not evidence:
            problems.append("model/reviewer checks require an evidence bundle, none resolved")
        else:
            lanes = _raw_lanes(Path(evidence))
            excl_families = [f.strip().lower() for f in (args.exclude_review_family or "").split(",") if f.strip()]
            excl_provider = (args.exclude_review_provider or "").strip().lower()
            deny_lane = (args.deny_reviewer or "").strip().lower()
            for lr in lanes:
                lane = str(lr.get("lane", "")).lower()
                provider = str(lr.get("provider", "")).lower()
                model_id = str(lr.get("model_id", "")).lower()
                catalog = lr.get("catalog", []) or []
                if args.require_live_model_lists and not catalog:
                    problems.append(f"live-model-lists: lane {lane!r} has no recorded catalog")
                if args.require_live_model_lists and catalog.count(lr.get("model_id")) != 1:
                    problems.append(
                        f"live-model-lists: {lr.get('model_id')!r} not exactly once in catalog for lane {lane!r}")
                if args.require_model_usability_probes and lr.get("probe_exit_code") != 0:
                    problems.append(
                        f"model-usability-probes: lane {lane!r} probe exit code {lr.get('probe_exit_code')!r} != 0")
                for fam in excl_families:
                    if fam in model_id:
                        problems.append(
                            f"excluded review family {fam!r} present in lane {lane!r} model {lr.get('model_id')!r}")
                if excl_provider and excl_provider in provider:
                    problems.append(
                        f"excluded review provider {excl_provider!r} present in lane {lane!r}")
                if deny_lane and deny_lane == lane:
                    problems.append(f"denied reviewer lane {deny_lane!r} present in evidence")

    # --deny-approval: any approval evidence at all is a failure.
    if args.deny_approval and evidence and _raw_approval(Path(evidence)) is not None:
        problems.append("deny-approval: approval evidence is present but must be absent")

    if problems:
        for msg in problems:
            print(f"FAIL: {msg}", file=sys.stderr)
        return 1
    print(json.dumps({"validate": "ok"}, indent=2))
    return 0


def cmd_seal(args: argparse.Namespace) -> int:
    rev_dir = Path(args.revision_dir)
    reject_link(rev_dir)
    if not rev_dir.is_dir():
        raise HarnessError(f"revision dir missing: {rev_dir}")
    digest = seal_tree_digest(rev_dir)
    print(json.dumps({"revision_dir": str(rev_dir), "revision_digest": digest}, indent=2))
    return 0


def cmd_import_evidence(args: argparse.Namespace) -> int:
    src = Path(args.evidence)
    ev = load_evidence(src)  # validates shape / raises loudly
    dest = Path(args.out)
    write_json_exclusive(dest, _load_json(src))
    print(json.dumps({"imported": str(dest), "revision_digest": ev["revision_digest"]}, indent=2))
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    gate = evidence_gate(Path(args.evidence))
    ev = load_evidence(Path(args.evidence))
    lanes = [
        {
            "lane": r.lane, "provider": r.provider, "model_id": r.model_id,
            "round": r.round, "finding_count": r.finding_count,
            "probe_exit_code": r.probe_exit_code, "eligible": r.eligible(),
        }
        for r in ev["lane_reports"]
    ]
    print(json.dumps({
        "revision_digest": ev["revision_digest"],
        "lanes": lanes,
        "dual_zero_same_round": gate.dual_zero_same_round,
        "latest_terminal_review_valid": gate.latest_terminal_review_valid,
        "post_terminal_reread_valid": gate.post_terminal_reread_valid,
        "post_reread_approval_valid": gate.post_reread_approval_valid,
        "phase3_eligible": gate.phase3_eligible,
        "reasons": gate.reasons,
    }, indent=2))
    return 0


# --- argument parsing --------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="remediate.py",
        description="Phase 2 per-batch remediation evidence and state-machine harness.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "review-evidence flags (validate command):\n"
            "  --require-latest-terminal-review        terminal dual-zero must be the\n"
            "                                          latest relevant review event\n"
            "  --require-post-terminal-doctrine-reread doctrine reread strictly after\n"
            "                                          the terminal dual-zero\n"
            "  --require-post-reread-approval          approval strictly after the\n"
            "                                          doctrine reread on the same digest\n"
            "  --require-phase3-eligible               all gates satisfied on one revision\n"
        ),
    )
    sub = p.add_subparsers(dest="command", required=True)

    t = sub.add_parser("tracer", help="end-to-end fixture path (backup + immutable r0001)")
    t.add_argument("--queue", required=True)
    t.add_argument("--prompts-dir", required=True)
    t.add_argument("--out", required=True)
    t.add_argument("--batch", required=True)
    t.add_argument("--filename", required=True)
    t.set_defaults(func=cmd_tracer)

    m = sub.add_parser("materialize", help="copy an exact CSV batch into an immutable revision")
    m.add_argument("--queue", required=True)
    m.add_argument("--prompts-dir", required=True)
    m.add_argument("--out", required=True)
    m.add_argument("--batch", required=True)
    m.add_argument("--revision", default="r0001")
    m.add_argument("--match-batch", action="store_true", help="only rows whose batch column equals --batch")
    m.set_defaults(func=cmd_materialize)

    v = sub.add_parser(
        "validate", help="validate queue equality, records, and gate predicates",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "--batch resolves queue/records/evidence from the on-disk layout:\n"
            "  <remediation-root>/<batch>/queue.csv, the highest-numbered\n"
            "  revision's records/ directory, and <batch>/evidence.json.\n"
            "  A batch with no artifacts fails the first required gate by name,\n"
            "  not with an argparse usage error.\n"
            "\n"
            "--require <csv-list> gate names (each must be satisfied):\n"
            "  classification, drafts, boolean, writing, doctrine, dual-review,\n"
            "  approved. An absent or invalid gate fails nonzero, naming it.\n"
        ),
    )
    v.add_argument("--batch", help="resolve queue/records/evidence for this batch slug")
    v.add_argument("--remediation-root",
                   help="root holding <batch>/ trees (default .claude/workspace/remediation)")
    v.add_argument("--queue")
    v.add_argument("--records-dir")
    v.add_argument("--evidence")
    v.add_argument("--require", dest="require",
                   help="comma-separated gate names that must all be satisfied")
    v.add_argument("--require-dual-zero-same-round", action="store_true",
                   help="both lanes report zero on one revision digest and round")
    v.add_argument("--require-latest-terminal-review", action="store_true",
                   help="terminal dual-zero is the latest relevant review event")
    v.add_argument("--require-post-terminal-doctrine-reread", action="store_true",
                   help="doctrine reread strictly after the terminal dual-zero")
    v.add_argument("--require-post-reread-approval", action="store_true",
                   help="approval strictly after the doctrine reread on the same digest")
    v.add_argument("--require-approval-binding", action="store_true",
                   help="approval binds the same digest strictly after the reread")
    v.add_argument("--require-phase3-eligible", action="store_true",
                   help="all gates satisfied on one revision")
    v.add_argument("--require-live-model-lists", action="store_true",
                   help="each selected model occurs exactly once in its recorded catalog")
    v.add_argument("--require-model-usability-probes", action="store_true",
                   help="each lane's read-only usability probe exited 0")
    v.add_argument("--exclude-review-family", dest="exclude_review_family",
                   help="comma-separated model families no reviewer may name")
    v.add_argument("--exclude-review-provider", dest="exclude_review_provider",
                   help="a provider no reviewer lane may name")
    v.add_argument("--deny-reviewer", dest="deny_reviewer",
                   help="a reviewer lane that must not appear in evidence")
    v.add_argument("--deny-approval", action="store_true",
                   help="fail if any approval evidence is present")
    v.set_defaults(func=cmd_validate)

    s = sub.add_parser("seal", help="compute and print a revision tree digest")
    s.add_argument("--revision-dir", required=True)
    s.set_defaults(func=cmd_seal)

    ie = sub.add_parser("import-evidence", help="import schema-valid ordered evidence")
    ie.add_argument("--evidence", required=True)
    ie.add_argument("--out", required=True)
    ie.set_defaults(func=cmd_import_evidence)

    st = sub.add_parser("status", help="print evidence-derived gate status as JSON")
    st.add_argument("--evidence", required=True)
    st.set_defaults(func=cmd_status)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (HarnessError, BackupError, PathSafetyError, SchemaError, EvidenceError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
