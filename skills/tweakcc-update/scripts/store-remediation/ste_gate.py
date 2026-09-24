#!/usr/bin/env python3
"""REM-06 STE enforcement gate.

Scans <revision-dir>/prompts/after/*.md and decides, per prompt, whether
ASD-STE100 Simplified Technical English was really applied:

  1. PROSE-FREE EXEMPT: a body with zero sentence-form prose (pure code / JSON /
     data) auto-passes. "Sentence-form prose" is ste_lint.sentences() on the
     code-stripped body. Zero sentences means nothing to lint.
  2. PROSE-BEARING must be STE-CLEAN: zero ste_lint violations, EXCEPT a
     violation whose offending text lies inside a preserved-verbatim span
     recorded in <revision-dir>/writing-quality/ste.json. Exemption is applied
     by blanking each preserved span out of the body, then re-linting the
     residue: a violation that survives the blanking is unexplained and FAILS.
  3. LOUD FAILURE: per-item PASS / FAIL / EXEMPT lines, each FAIL names the
     file, the ste_lint rule bucket, and quotes the offending text. Pass / fail
     / exempt counts are summarized. No silent partial success.

Exit codes: 0 all clean or exempt, 1 one or more unexplained violations,
2 usage / config error, 3 terminated at the wall-clock ceiling.

Termination guard per recipe-skill-script-hardening [R001]. Loud per-item
accounting per recipe-skill-script-loud-replace [R001] Step 6.

Python 3 standard library only, plus ste_lint loaded by file path.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import sys
import threading
import time
from pathlib import Path

# ste_lint.py ships with the simple-english plugin, in its evals/ folder.
# Load it by file path from the newest installed plugin version, so the gate
# works from any cwd. The STE hook finds the linter the same way.
_PLUGIN_ROOT = Path.home() / ".claude" / "plugins" / "cache" / "simple-english" / "simple-english"


def _newest_ste_lint() -> Path:
    """Return evals/ste_lint.py of the newest installed simple-english
    plugin version. If no version holds the file, return the path under
    the plugin root, and the load step then reports it."""
    def version_key(p: Path):
        return [int(x) if x.isdigit() else 0 for x in p.parent.parent.name.split(".")]
    found = sorted(_PLUGIN_ROOT.glob("*/evals/ste_lint.py"), key=version_key)
    return found[-1] if found else _PLUGIN_ROOT / "evals" / "ste_lint.py"


_STE_LINT_PATH = _newest_ste_lint()


def _load_ste_lint(path: Path):
    spec = importlib.util.spec_from_file_location("ste_lint", str(path))
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load ste_lint from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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


# Per-bucket offending-text extractors, mirroring ste_lint's own regexes so a
# FAIL can quote the exact tokens. sentence_over_limit has no token. The whole
# over-limit sentence is the offender, handled separately in evaluate().
def _bucket_offenders(ste, body: str) -> dict:
    offenders = {}
    for bucket, rx in (
        ("banned_modal", ste.BANNED_MODALS),
        ("perfect_tense", ste.PERFECT),
        ("contraction", ste.CONTRACTION),
        ("ing_clause", ste.ING_CLAUSE),
        ("latin_abbrev", ste.LATIN),
        ("slop_word", ste.SLOP),
    ):
        hits = [m.group(0) for m in rx.finditer(body)]
        if hits:
            offenders[bucket] = hits
    if ";" in body:
        offenders["semicolon"] = [";"]
    return offenders


def strip_frontmatter(body: str) -> str:
    """Drop a single leading HTML-comment frontmatter block (stock prompt
    metadata: name/description/ccVersion/variables). STE governs the
    model-facing BODY only, never the frontmatter. The block must be at the
    very start of the file: a line beginning with '<!--' through the line
    ending with '-->'. Anything else is left untouched."""
    m = re.match(r"^\s*<!--.*?-->[ \t]*\r?\n?", body, re.DOTALL)
    return body[m.end():] if m else body


def evaluate(ste, name: str, body: str, spans: list) -> dict:
    """Classify one prompt. Returns {verdict, detail} where verdict is one of
    PASS, FAIL, EXEMPT. Preserved spans are blanked before linting so a
    violation inside a quoted span is explained away."""
    body = strip_frontmatter(body)
    # Rule 1: prose-free bodies auto-pass as proven-exempt.
    if not ste.sentences(ste.strip_code(body)):
        return {"verdict": "EXEMPT", "detail": "no sentence-form prose"}

    # Rule 2 exemption: blank each preserved span out of the body, literally.
    residue = body
    for span in spans:
        if span:
            residue = residue.replace(span, " ")

    result = ste.lint(residue, "procedural")
    counts = result["violations"]
    if result["violations_total"] == 0:
        return {"verdict": "PASS", "detail": "0 STE violations"}

    # Build a loud diagnostic: each firing bucket with quoted offending text.
    stripped = ste.strip_code(residue)
    offenders = _bucket_offenders(ste, stripped)
    lines = []
    for bucket, n in counts.items():
        if n <= 0:
            continue
        if bucket == "sentence_over_limit":
            long_sents = [
                s for s in ste.sentences(stripped)
                if len(s.split()) > ste.LIMITS["procedural"]
            ]
            quote = long_sents[0] if long_sents else "(sentence exceeds word limit)"
            lines.append(f"    {bucket} x{n}: {quote!r}")
        else:
            quoted = offenders.get(bucket, [])
            uniq = list(dict.fromkeys(quoted))
            lines.append(f"    {bucket} x{n}: {', '.join(repr(t) for t in uniq) or '(no token)'}")
    return {"verdict": "FAIL", "detail": "\n".join(lines)}


def load_spans(revision_dir: Path) -> dict:
    """Read writing-quality/ste.json -> {filename: [preserved span, ...]}.
    Absent file means no preserved spans. A malformed file is a config error."""
    ste_json = revision_dir / "writing-quality" / "ste.json"
    if not ste_json.is_file():
        return {}
    try:
        data = json.loads(ste_json.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise ValueError(f"cannot read {ste_json}: {e}")
    spans = data.get("preserved_spans", {})
    if not isinstance(spans, dict):
        raise ValueError(f"{ste_json}: 'preserved_spans' must be an object")
    return spans


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--revision-dir", dest="revision_dir",
                        help="Revision directory holding prompts/after/*.md and optional writing-quality/ste.json")
    parser.add_argument("--max-seconds", type=float, default=120.0, dest="max_seconds",
                        help="Hard wall-clock ceiling in seconds; exit 3 when it fires (default: 120)")
    parser.add_argument("--watchdog-probe", type=float, default=0.0, dest="watchdog_probe",
                        help="Diagnostic: idle this many seconds after arming the watchdog (default: 0)")
    args = parser.parse_args()
    _arm_watchdog(args.max_seconds, args.watchdog_probe)

    if not args.revision_dir:
        print("error: --revision-dir is required", file=sys.stderr)
        return 2
    revision_dir = Path(args.revision_dir)
    if not revision_dir.is_dir():
        print(f"error: revision dir does not exist: {revision_dir}", file=sys.stderr)
        return 2

    after = revision_dir / "prompts" / "after"
    if not after.is_dir():
        print(f"error: no prompts/after directory under {revision_dir}", file=sys.stderr)
        return 2

    try:
        ste = _load_ste_lint(_STE_LINT_PATH)
    except Exception as e:
        print(f"error: cannot load ste_lint: {e}", file=sys.stderr)
        return 2

    try:
        span_map = load_spans(revision_dir)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    prompts = sorted(after.glob("*.md"))
    passed, failed, exempt = [], [], []
    for md in prompts:
        try:
            body = md.read_text(encoding="utf-8")
        except OSError as e:
            print(f"FAIL {md.name}: cannot read: {e}")
            failed.append(md.name)
            continue
        res = evaluate(ste, md.name, body, span_map.get(md.name, []))
        v = res["verdict"]
        if v == "PASS":
            print(f"PASS {md.name}: {res['detail']}")
            passed.append(md.name)
        elif v == "EXEMPT":
            print(f"EXEMPT {md.name}: {res['detail']}")
            exempt.append(md.name)
        else:
            print(f"FAIL {md.name}: unexplained STE violation(s)")
            print(res["detail"])
            failed.append(md.name)

    print(f"\nsummary: {len(passed)} pass, {len(failed)} fail, {len(exempt)} exempt "
          f"({len(prompts)} prompt(s) scanned)")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
