#!/usr/bin/env python3
"""Build a materialize queue CSV from a reconstructed stock prompt directory.

Lists every ``<id>.md`` basename in a stock directory and writes a queue CSV
with the header ``file,batch`` that ``remediate.py materialize`` consumes. The
queue's ``file`` column obeys remediate.py read_queue's contract: no name may
contain a directory separator or be a bare dot name (``.``, ``..``), so a queue
row can never redirect a copy outside the stock directory.

Path-safety (reject_link) and the wall-clock watchdog are ported verbatim from
../store-remediation/hashing.py and the project's canonical-paths gate. Python 3
standard library only.

Exit codes:
  0  the queue was written; row count equals the .md count in --prompts-dir.
  1  an error occurred (missing dir, unsafe filename, filesystem safety failure).
  2  usage error (missing or bad flag).
  3  the wall-clock watchdog fired.
"""

from __future__ import annotations

import argparse
import csv
import os
import stat
import sys
import threading
import time
from pathlib import Path


class PathSafetyError(ValueError):
    """A path itself is a symlink or Windows reparse point."""


# --- ported from hashing.py (do NOT re-invent; CLAUDE.md reuse, ponytail rung 2) ---

def reject_link(path: Path) -> None:
    """Raise if path itself is a symlink or Windows reparse point.

    A reparse point (junction, symlink) can redirect a read or write outside the
    intended tree. lstat does not follow the link, so we inspect the entry
    itself. Missing entries pass here; existence is the caller's concern.
    """
    p = Path(path)
    try:
        info = p.lstat()
    except FileNotFoundError:
        return
    if stat.S_ISLNK(info.st_mode):
        raise PathSafetyError(f"refusing to follow symlink: {p}")
    reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    attrs = getattr(info, "st_file_attributes", 0)
    if attrs & reparse:
        raise PathSafetyError(f"refusing to follow reparse point: {p}")


# --- watchdog (recipe-skill-script-hardening-1.0.0 [A001.1], verbatim) ---

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


# --- queue construction ---

def _is_unsafe_name(name: str) -> bool:
    """True when name would fail remediate.py read_queue's path-safety check."""
    return "/" in name or "\\" in name or name in ("", ".", "..")


def collect_md_rows(prompts_dir: Path, batch: str) -> list[dict[str, str]]:
    """Return one {file, batch} row per ``*.md`` basename in prompts_dir.

    Raises on an unsafe basename (a name a queue row must never carry) so the
    caller stops before a bad name reaches materialize. The stock directory
    itself is checked for link/reparse status first.
    """
    reject_link(prompts_dir)
    if not prompts_dir.is_dir():
        raise ValueError(f"prompts dir is not a directory: {prompts_dir}")
    rows: list[dict[str, str]] = []
    for entry in sorted(prompts_dir.iterdir()):
        if entry.is_dir():
            continue
        name = entry.name
        if not name.endswith(".md"):
            continue
        if _is_unsafe_name(name):
            raise ValueError(f"unsafe filename in stock dir: {name!r}")
        rows.append({"file": name, "batch": batch})
    return rows


def write_queue(rows: list[dict[str, str]], out_path: Path) -> None:
    """Write rows to out_path as a CSV with header file,batch (loud replace)."""
    reject_link(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if out_path.exists():
        # Loud replace: state that a prior queue is being overwritten.
        print(f"note: replacing existing queue {out_path}", file=sys.stderr)
    with out_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=["file", "batch"])
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _run(prompts_dir: Path, out_path: Path, batch: str) -> int:
    rows = collect_md_rows(prompts_dir, batch)
    if not rows:
        print(f"error: no .md files found in {prompts_dir}", file=sys.stderr)
        return 1
    write_queue(rows, out_path)
    print(f"wrote {len(rows)} queue rows to {out_path} (batch {batch})")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="build_queue.py",
        description="Build a file,batch materialize queue CSV from a stock .md dir.",
    )
    parser.add_argument("--prompts-dir", required=True,
                        help="Directory holding the reconstructed <id>.md stock files")
    parser.add_argument("--out", required=True, help="Output queue CSV path")
    parser.add_argument("--batch", default="binary-faithful",
                        help="Batch name written into every row (default: binary-faithful)")
    parser.add_argument("--max-seconds", type=float, default=120.0, dest="max_seconds",
                        help="Hard wall-clock ceiling; exit 3 when it fires (default: 120)")
    parser.add_argument("--watchdog-probe", type=float, default=0.0, dest="watchdog_probe",
                        help="Test-only: idle this many seconds after arming the watchdog (default: 0)")
    args = parser.parse_args(argv)
    _arm_watchdog(args.max_seconds, args.watchdog_probe)

    try:
        return _run(Path(args.prompts_dir), Path(args.out), args.batch)
    except (PathSafetyError, ValueError, OSError) as err:
        print(f"error: {err}", file=sys.stderr)
        return 1


def _self_check() -> None:
    """Assert-based self-check on a tiny temp dir: one good .md -> one row; a
    name with a separator raises."""
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        (d / "good-prompt.md").write_text("body\n", encoding="utf-8")
        (d / "ignored.txt").write_text("x\n", encoding="utf-8")
        rows = collect_md_rows(d, "binary-faithful")
        assert rows == [{"file": "good-prompt.md", "batch": "binary-faithful"}], rows

        # A name containing a separator must raise (read_queue would reject it).
        try:
            _is_unsafe_name("sub/evil.md")
            assert _is_unsafe_name("sub/evil.md") is True
            assert _is_unsafe_name("a\\b.md") is True
            assert _is_unsafe_name("..") is True
            assert _is_unsafe_name("good-prompt.md") is False
        except AssertionError:
            raise

        # Round-trip the queue file and confirm the header + row.
        out = d / "queue.csv"
        write_queue(rows, out)
        text = out.read_text(encoding="utf-8")
        assert text.splitlines()[0] == "file,batch", text.splitlines()[0]
        assert "good-prompt.md,binary-faithful" in text, text
    print("self-check OK")


if __name__ == "__main__":
    if len(sys.argv) == 2 and sys.argv[1] == "--self-check":
        _self_check()
        raise SystemExit(0)
    raise SystemExit(main())
