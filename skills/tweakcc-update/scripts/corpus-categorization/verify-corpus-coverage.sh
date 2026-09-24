#!/usr/bin/env bash
#
# verify-corpus-coverage.sh
#
# Coverage-check for the corpus categorization CSV. Recomputes the denominator
# from the pinned system-prompts directory at runtime (never hardcoded) and runs
# four consistency assertions over the CSV. Prints a RESULT line and exits 0 only
# when all four pass; on any failure it names the failing assertion on stderr and
# exits non-zero. Loud-fail shape modeled on check-version-intersection.py.
#
# Assertions:
#   1. data-row count equals the corpus file count
#   2. no duplicate value in the file column
#   3. no row with assigned_by=hand has an empty basis
#   4. the set of corpus file names equals the set of CSV file names (diff empty)
#
# Paths are resolved relative to this script so it runs from any working directory.

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Walk up from this script to the project's .claude/workspace, so the script
# works from the skill or from the workspace.
WORKSPACE_DIR=""
d="$SCRIPT_DIR"
while [ "$d" != "/" ] && [ -n "$d" ]; do
  if [ -d "$d/.claude/workspace" ]; then WORKSPACE_DIR="$d/.claude/workspace"; break; fi
  d="$(dirname "$d")"
done
[ -n "$WORKSPACE_DIR" ] || { echo "ASSERTION FAILED: no .claude/workspace above $SCRIPT_DIR" >&2; exit 1; }
# The corpus version comes from --version <x.y.z>. Without the flag, use the
# highest version under repos/pi-bald, compared as numbers.
CORPUS_ROOT="$WORKSPACE_DIR/repos/pi-bald"
VERSION=""
if [ "${1:-}" = "--version" ]; then
  [ -n "${2:-}" ] || { echo "usage: $0 [--version <x.y.z>]" >&2; exit 2; }
  VERSION="$2"
elif [ -n "${1:-}" ]; then
  echo "usage: $0 [--version <x.y.z>]" >&2; exit 2
else
  VERSION="$(find "$CORPUS_ROOT" -mindepth 1 -maxdepth 1 -type d -name 'claude-code-system-prompts-*' -printf '%f\n' 2>/dev/null \
    | sed 's/^claude-code-system-prompts-//' | sort -t. -k1,1n -k2,2n -k3,3n | tail -n 1)"
  [ -n "$VERSION" ] || { echo "ASSERTION FAILED: no claude-code-system-prompts-* directory in $CORPUS_ROOT" >&2; exit 1; }
fi
echo "corpus version: $VERSION"
CORPUS_DIR="$CORPUS_ROOT/claude-code-system-prompts-$VERSION/system-prompts"
CSV="$WORKSPACE_DIR/remediation/corpus-categorization-1.0.0.csv"

fail() {
  echo "ASSERTION FAILED: $1" >&2
  exit 1
}

# Resolve inputs loudly before any accounting (local-first, no-guess).
if [ ! -d "$CORPUS_DIR" ]; then
  fail "corpus directory not found: $CORPUS_DIR"
fi
if [ ! -f "$CSV" ]; then
  fail "CSV not found: $CSV"
fi

# Denominator computed from the directory at runtime. Never hardcode the count.
CORPUS_COUNT="$(find "$CORPUS_DIR" -type f -printf '%f\n' | wc -l | tr -d '[:space:]')"

# Empty-directory guard: fail loudly rather than report 100 percent of zero.
if [ "$CORPUS_COUNT" -eq 0 ]; then
  fail "corpus directory listing returned zero files; refusing to report coverage of zero"
fi

# Data rows = every CSV line after the header. cut on the first comma isolates the
# quoted file column; strip the surrounding double quotes so names compare cleanly.
CSV_FILE_COL="$(tail -n +2 "$CSV" | cut -d, -f1 | sed 's/^"//; s/"$//')"
DATA_ROW_COUNT="$(printf '%s\n' "$CSV_FILE_COL" | grep -c . || true)"

# Assertion 1: data-row count equals corpus file count.
if [ "$DATA_ROW_COUNT" -ne "$CORPUS_COUNT" ]; then
  fail "row count mismatch: CSV has $DATA_ROW_COUNT data rows, corpus has $CORPUS_COUNT files"
fi

# Assertion 2: no duplicate file value.
DUPES="$(printf '%s\n' "$CSV_FILE_COL" | sort | uniq -d)"
if [ -n "$DUPES" ]; then
  fail "duplicate file value(s) in CSV file column: $(printf '%s' "$DUPES" | tr '\n' ' ')"
fi

# Assertion 3: no row with assigned_by=hand has an empty basis.
# Columns per D-08: file,class,batch,basis,assigned_by,existing_override,token_count
# basis is column 4, assigned_by is column 5. Awk parses quoted CSV fields.
EMPTY_BASIS_HAND="$(tail -n +2 "$CSV" | awk '
  {
    # Split on comma. Fields may be quoted; strip quotes for the columns we test.
    n = split($0, f, ",")
    basis = f[4]; assigned = f[5]
    gsub(/^"|"$/, "", basis)
    gsub(/^"|"$/, "", assigned)
    if (assigned == "hand" && basis == "") print NR
  }')"
if [ -n "$EMPTY_BASIS_HAND" ]; then
  fail "row(s) with assigned_by=hand and empty basis at data line(s): $(printf '%s' "$EMPTY_BASIS_HAND" | tr '\n' ' ')"
fi

# Assertion 4: corpus file-name set equals CSV file-name set (diff empty).
CORPUS_SET="$(find "$CORPUS_DIR" -type f -printf '%f\n' | sort)"
CSV_SET="$(printf '%s\n' "$CSV_FILE_COL" | sort)"
SET_DIFF="$(diff <(printf '%s\n' "$CORPUS_SET") <(printf '%s\n' "$CSV_SET"))"
if [ -n "$SET_DIFF" ]; then
  echo "ASSERTION FAILED: corpus file set does not equal CSV file set" >&2
  echo "$SET_DIFF" >&2
  exit 1
fi

echo "RESULT: coverage verified. $DATA_ROW_COUNT CSV data rows equal $CORPUS_COUNT corpus files; no duplicate file, no empty basis on hand rows, corpus-set equals CSV-set."
exit 0
