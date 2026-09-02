#!/usr/bin/env python3
"""
Fail loudly when the two required copies of the tweakcc-update skill drift.

The skill lives in two mandatory locations that must stay byte-identical
(project CLAUDE.md hard rule 8): the DEV source inside the plugin repo
(tweakcc-gilligan/skills/tweakcc-update/) and the INSTALLED copy Claude Code
loads (.claude/skills/tweakcc-update/). They were synced by hand (cp, then
diff -rq); this test makes drift a failing check instead of a silent one.

Usage: python test_skill_mirror_sync.py   (exit 0 in sync, 1 on drift, 2 if
the mirrors cannot be located).
"""

import argparse
import filecmp
import os
import pathlib
import sys
import threading
import time

IGNORE = {"__pycache__"}


def _arm_watchdog(max_seconds: float, probe_seconds: float) -> None:
    """Deterministic termination guard: hard-kill with exit code 3 at the wall-clock ceiling.
    From recipe-skill-script-hardening (threading.Timer + os._exit; signal.alarm is POSIX-only)."""
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


def find_project_root(start: pathlib.Path):
    for d in [start, *start.parents]:
        if (d / "CLAUDE.md").exists() and (d / "tweakcc-gilligan").is_dir() and (d / ".claude" / "skills").is_dir():
            return d
    return None


def _relative_files(root: pathlib.Path):
    """Every file under root as a relative POSIX path, skipping IGNORE directories."""
    files = set()
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in IGNORE]
        for f in filenames:
            files.add(pathlib.Path(dirpath, f).relative_to(root).as_posix())
    return files


def drift(dev: pathlib.Path, installed: pathlib.Path):
    """Return a list of human-readable drift lines (empty when byte-identical).
    Byte comparison per file (filecmp.cmp with shallow=False): a same-size,
    same-mtime file with different content is still drift."""
    out = []
    left, right = _relative_files(dev), _relative_files(installed)
    for rel in sorted(left - right):
        out.append(f"only in dev:       {rel}")
    for rel in sorted(right - left):
        out.append(f"only in installed: {rel}")
    for rel in sorted(left & right):
        if not filecmp.cmp(dev / rel, installed / rel, shallow=False):
            out.append(f"differs:           {rel}")
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-seconds", type=float, default=120.0, dest="max_seconds",
                        help="Hard wall-clock ceiling in seconds; the process exits with code 3 when it fires (default: 120)")
    parser.add_argument("--watchdog-probe", type=float, default=0.0, dest="watchdog_probe",
                        help="Diagnostic: idle this many seconds after arming the watchdog (default: 0)")
    args = parser.parse_args()
    _arm_watchdog(args.max_seconds, args.watchdog_probe)
    root = find_project_root(pathlib.Path(__file__).resolve().parent)
    if root is None:
        print("error: cannot locate the tweakcc project root (CLAUDE.md + tweakcc-gilligan/ + .claude/skills/)", file=sys.stderr)
        return 2
    dev = root / "tweakcc-gilligan" / "skills" / "tweakcc-update"
    installed = root / ".claude" / "skills" / "tweakcc-update"
    for d in (dev, installed):
        if not d.is_dir():
            print(f"error: skill mirror missing: {d}", file=sys.stderr)
            return 2
    lines = drift(dev, installed)
    if lines:
        print(f"FAIL: skill mirrors drifted ({len(lines)} difference(s)):")
        for line in lines:
            print("  " + line)
        print("Copy the intended side over the other, then re-run (CLAUDE.md hard rule 8).")
        return 1
    print(f"PASS: {dev} and {installed} are byte-identical")
    return 0


if __name__ == "__main__":
    sys.exit(main())
