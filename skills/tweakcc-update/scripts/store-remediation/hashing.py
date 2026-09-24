"""Byte-level SHA-256 hashing and verified copy for the remediation harness.

Every mutation in Phase 2 copies affected files into a backup and proves the
copy is byte-identical to the source before any change proceeds (CONTEXT.md
"Review, Backup, and Approval Gates"; RESEARCH.md Pitfall 8). Hashing reads raw
bytes, never newline-converted text, so CRLF/LF churn cannot mask a real
difference. Python 3 standard library only.
"""

from __future__ import annotations

import os
import stat
from hashlib import file_digest
from pathlib import Path

HEX64 = 64  # a lowercase SHA-256 hexdigest is exactly 64 hex characters


class BackupError(RuntimeError):
    """A backup copy could not be proven byte-identical to its source."""


class PathSafetyError(ValueError):
    """A resolved path escaped its allowed root, or was a link/reparse point."""


def sha256_file(path: Path) -> str:
    """Return the lowercase SHA-256 hexdigest of a file's raw bytes."""
    with Path(path).open("rb") as stream:
        return file_digest(stream, "sha256").hexdigest()


def is_valid_sha256(value: str) -> bool:
    """True when value is exactly 64 lowercase hex characters."""
    if not isinstance(value, str) or len(value) != HEX64:
        return False
    return all(c in "0123456789abcdef" for c in value)


def reject_link(path: Path) -> None:
    """Raise if path itself is a symlink or Windows reparse point.

    A reparse point (junction, symlink) can redirect a backup read or write
    outside the allowlisted tree. lstat does not follow the link, so we inspect
    the entry itself. Missing entries pass here; existence is checked by callers.
    """
    p = Path(path)
    try:
        info = p.lstat()
    except FileNotFoundError:
        return
    if stat.S_ISLNK(info.st_mode):
        raise PathSafetyError(f"refusing to follow symlink: {p}")
    # Windows reparse points (junctions) set FILE_ATTRIBUTE_REPARSE_POINT.
    reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    attrs = getattr(info, "st_file_attributes", 0)
    if attrs & reparse:
        raise PathSafetyError(f"refusing to follow reparse point: {p}")


def resolve_within(root: Path, candidate: Path) -> Path:
    """Resolve candidate and require it stays under root; reject traversal/links.

    Both root and candidate are fully resolved (symlinks collapsed) and compared
    by path parts, not string prefix, so a sibling like ``root-evil`` cannot pass
    as if under ``root``. The candidate's own entry is checked for link/reparse
    status before resolution so a link cannot smuggle the target elsewhere.
    """
    root = Path(root).resolve()
    cand = Path(candidate)
    reject_link(cand)
    resolved = cand.resolve()
    if resolved != root and root not in resolved.parents:
        raise PathSafetyError(
            f"path escapes allowed root: {resolved} not under {root}"
        )
    return resolved


def copy_verified(source: Path, destination: Path) -> str:
    """Copy source to destination and prove the copy is byte-identical.

    Uses exclusive creation for the destination so a prior backup file is never
    silently overwritten. Returns the shared SHA-256 digest. Raises BackupError
    on any digest mismatch (the mutation must not proceed).
    """
    source = Path(source)
    destination = Path(destination)
    reject_link(source)
    before = sha256_file(source)
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive create + copy the raw bytes: no overwrite, no metadata reliance.
    data = source.read_bytes()
    fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    try:
        with os.fdopen(fd, "wb") as out:
            out.write(data)
    except Exception:
        # Leave a partial file behind only if close/write raced; callers treat
        # any BackupError as fatal and stop before mutation regardless.
        raise
    after = sha256_file(destination)
    if before != after:
        raise BackupError(
            f"backup digest mismatch: {source} ({before}) -> "
            f"{destination} ({after})"
        )
    return after
