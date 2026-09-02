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

import filecmp
import pathlib
import sys

IGNORE = {"__pycache__", ".pytest_cache"}


def find_project_root(start: pathlib.Path):
    for d in [start, *start.parents]:
        if (d / "CLAUDE.md").exists() and (d / "tweakcc-gilligan").is_dir() and (d / ".claude" / "skills").is_dir():
            return d
    return None


def drift(dev: pathlib.Path, installed: pathlib.Path):
    """Return a list of human-readable drift lines (empty when identical)."""
    out = []

    def walk(cmp: filecmp.dircmp, rel: str):
        for n in cmp.left_only:
            if n not in IGNORE:
                out.append(f"only in dev:       {rel}{n}")
        for n in cmp.right_only:
            if n not in IGNORE:
                out.append(f"only in installed: {rel}{n}")
        for n in cmp.diff_files:
            out.append(f"differs:           {rel}{n}")
        for n in cmp.funny_files:
            out.append(f"uncomparable:      {rel}{n}")
        for n, sub in cmp.subdirs.items():
            if n not in IGNORE:
                walk(sub, f"{rel}{n}/")

    walk(filecmp.dircmp(dev, installed, ignore=list(IGNORE)), "")
    return out


def main():
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
