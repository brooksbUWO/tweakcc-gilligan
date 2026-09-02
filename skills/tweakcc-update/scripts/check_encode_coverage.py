#!/usr/bin/env python3
"""Encode-coverage gate: every approved behavioral un-nerf must be present as a
rule in the live apply-unnerfs.py.

This makes Phase 3's ROADMAP success criterion ("every approved rewrite is
encoded ... 0 missing rules") executable and fail-closed. It reuses the
encoder's own predicate (encode_rules.encode_batch), so the set of prompts that
SHOULD have a rule is computed exactly as the encoder computes it: a rule is
expected only when the record's disposition is not "retain" and the after-body
differs from the before-body (raw bytes, frontmatter stripped). Each expected
slug is then checked against the rule ids the live apply-unnerfs.py exposes via
--dump-rules.

Per-slug accounting (recipe-skill-script-loud-replace-1.0.1): every batch and
every expected slug is reported PASS/FAIL; the run exits non-zero listing every
gap. A batch whose sealed revision fails the encoder's own gate (digest drift,
missing bodies) is reported as a batch-level FAIL, not a crash.

Termination guard (recipe-skill-script-hardening-1.0.0). Exit codes:
0 every approved un-nerf is encoded,
1 one or more approved un-nerfs are unencoded, or a batch could not be verified,
2 usage or config error,
3 terminated at the wall-clock ceiling.
"""

import argparse
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

# Locations. ENCODER is the authoritative predicate source; APPLY is the live
# rules file it targets (repathed to the single dev unnerfcc copy).
ENCODER = Path(__file__).resolve().parents[4] / ".claude" / "workspace" / "scripts" / "phase3-encode" / "encode_rules.py"
# Derived like ENCODER: parents[4] is the project root from BOTH skill copies
# (installed .claude/skills/... and dev tweakcc-gilligan/skills/...), so the
# default resolves on any machine that has the unnerfcc clone at the root.
DEFAULT_APPLY = Path(__file__).resolve().parents[4] / "unnerfcc" / "scripts" / "apply-unnerfs.py"


def _arm_watchdog(max_seconds: float, probe_seconds: float) -> None:
    """Deterministic termination guard: hard-kill with exit code 3 at the wall-clock ceiling."""
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


def load_encoder(path: Path):
    """Import encode_rules.py as a module so its predicate is reused verbatim."""
    if not path.is_file():
        print(f"error: encoder not found at {path}", file=sys.stderr)
        sys.exit(2)
    spec = importlib.util.spec_from_file_location("encode_rules", str(path))
    mod = importlib.util.module_from_spec(spec)
    # Register before exec: a frozen @dataclass in the target resolves its own
    # module via sys.modules during class creation (Python 3.14).
    sys.modules["encode_rules"] = mod
    spec.loader.exec_module(mod)
    return mod


def live_rules_map(apply_path: Path) -> dict:
    """Map {id: set of (stock, unnerf) pairs} the live apply-unnerfs.py exposes.

    Dumps to a unique temp file (never stdout "-", which apply-unnerfs.py would
    treat as a literal path and leave behind, and never a fixed name in the repo
    tree, which two runs could race on). The temp file is removed in finally.
    """
    if not apply_path.is_file():
        print(f"error: apply-unnerfs.py not found at {apply_path}", file=sys.stderr)
        sys.exit(2)
    fd, tmp = tempfile.mkstemp(prefix="encode_coverage_dump_", suffix=".json")
    os.close(fd)
    try:
        subprocess.run([sys.executable, str(apply_path), "--dump-rules", tmp],
                       capture_output=True, text=True, timeout=90, check=True)
        data = json.loads(Path(tmp).read_text(encoding="utf-8"))
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass
    m = {}
    for r in data:
        m.setdefault(r["id"], set()).add((r["stock"], r["unnerf"]))
    return m


def _slug(filename: str) -> str:
    return filename[:-3] if filename.endswith(".md") else filename


def expected_rules_per_batch(enc) -> dict:
    """{batch: (expected, batch_error_or_None)} using the encoder's own predicate.

    `expected` is a list of (slug, stock, unnerf) the encoder would emit. A batch
    whose sealed revision fails the encoder's fail-closed gate raises SystemExit
    inside encode_batch; capture it as a batch-level error string so the gate
    reports the batch as unverifiable rather than crashing.
    """
    result = {}
    for batch in enc.BATCHES:
        try:
            rules, _counts = enc.encode_batch(batch)
            expected = [(_slug(r["rule"].filename), r["rule"].stock, r["rule"].unnerf)
                        for r in rules]
            result[batch] = (expected, None)
        except SystemExit as e:
            result[batch] = ([], f"encoder gate failed: {e}")
        except Exception as e:  # a malformed record fails ITS batch, not the run
            result[batch] = ([], f"{type(e).__name__}: {e}")
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", default=str(DEFAULT_APPLY),
                        help="Path to the live apply-unnerfs.py (default: the dev unnerfcc copy)")
    parser.add_argument("--encoder", default=str(ENCODER),
                        help="Path to encode_rules.py (the authoritative predicate)")
    parser.add_argument("--max-seconds", type=float, default=120.0, dest="max_seconds",
                        help="Hard wall-clock ceiling in seconds; exit 3 when it fires (default: 120)")
    parser.add_argument("--watchdog-probe", type=float, default=0.0, dest="watchdog_probe",
                        help="Diagnostic: idle this many seconds after arming the watchdog (default: 0)")
    args = parser.parse_args(argv)
    _arm_watchdog(args.max_seconds, args.watchdog_probe)

    enc = load_encoder(Path(args.encoder))
    live = live_rules_map(Path(args.apply))
    per_batch = expected_rules_per_batch(enc)

    total_expected = 0
    total_encoded = 0
    failures = []  # (batch, slug-or-'*', reason)

    for batch in enc.BATCHES:
        expected, batch_err = per_batch[batch]
        if batch_err is not None:
            print(f"FAIL {batch}: {batch_err}")
            failures.append((batch, "*", batch_err))
            continue
        # A rewrite is encoded only when the live rules for its id contain the
        # exact (stock, unnerf) pair. Id presence alone is a false pass: a rule
        # under the same filename may carry stale bodies, or be a legacy rule.
        encoded = missing = 0
        for slug, stock, unnerf in expected:
            if (stock, unnerf) in live.get(slug, ()):  # id key == slug
                encoded += 1
            else:
                missing += 1
                reason = ("id present but no rule matches the approved stock/unnerf body"
                          if slug in live else "no rule for this id in apply-unnerfs.py")
                print(f"    UNENCODED: {slug} ({reason})")
                failures.append((batch, slug, reason))
        total_expected += len(expected)
        total_encoded += encoded
        status = "PASS" if not missing else f"FAIL {missing} unencoded"
        print(f"{status} {batch}: {encoded}/{len(expected)} expected un-nerfs encoded")

    print(f"\nTOTAL: {total_encoded}/{total_expected} expected behavioral un-nerfs encoded across {len(enc.BATCHES)} batches")
    if failures:
        print(f"{len(failures)} coverage failure(s); the patch would ship incomplete:", file=sys.stderr)
        for batch, slug, reason in failures:
            print(f"  {batch} :: {slug} :: {reason}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
