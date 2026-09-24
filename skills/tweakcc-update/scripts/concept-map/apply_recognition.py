#!/usr/bin/env python3
"""apply_recognition.py: apply a recognition transcript to the concept map
and the claims table.

The translation from a recognition transcript to the canonical concept map
is deterministic work. It also writes the claims table. It lives in code,
not in a row typed by hand.

Usage:
  apply_recognition.py --transcript T --map M --claims C
      [--concepts ID[,ID...]] [--max-seconds N] [--watchdog-probe N]

Exit codes:
  0  the script wrote both files and printed a line that starts with
     APPLIED, or found no change and printed NO CHANGE.
  1  a contract failure. Each failure prints on its own line. Nothing
     is written.
  2  a usage or input error: a missing file, bad JSON, an input over
     5,000,000 bytes, or a bad flag value.
  3  the wall-clock ceiling of --max-seconds fired (default 120).
"""
from __future__ import annotations

import argparse
import copy
import json
import math
import os
import sys
import threading
import time

MAX_INPUT_BYTES = 5_000_000
STATES = {"carries-defect", "conforms"}
PROVENANCE = {"recognition-first", "search-extended"}
FIX_PRESENT = {"true", "false", "unread"}
NEW_BLOCK_KEYS = ("concept_id", "concept", "tag", "markers", "coverage_method",
                  "notes", "governed_files")

# Each table gives one expected JSON type per field of one record kind.
# The script does not type-check a field that is not in a table. Such a
# field does not exist on that kind, or the script only tests whether it
# is true or a set member. A wrong type in that field cannot crash the
# script. The names "string" and "list" mean the Python types str and
# list. The check tests bool before int, because bool is a subclass of
# int. The script loops over each "list" field in these tables. A value
# that is not a list fails here, not in a loop.
TRANSCRIPT_FIELD_TYPES = dict(
    schema="string",
    binary="object",
    session="object",
    store_dir="string",
    seed_store_dir="string",
    seed_dropped="list",
    concepts="list",
)
TRANSCRIPT_SESSION_FIELD_TYPES = dict(
    session_id="string",
    date="string",
    vantage="string",
)
TRANSCRIPT_BLOCK_FIELD_TYPES = dict(
    concept_id="string",
    notes_append="string",
    rows="list",
    dropped="list",
    concept="string",
    tag="string",
    markers="list",
)
TRANSCRIPT_ROW_FIELD_TYPES = dict(
    state="string",
    provenance="string",
    fix_kind="string",
)
# file, marker, quote, and line are all checked downstream
# (_validate_row), which for line also excludes bool from the accepted
# int. They are deliberately not in the table above: a bad value in
# one of them must reach that exit-1 domain check, not this exit-2
# gate.
TRANSCRIPT_DROPPED_FIELD_TYPES = dict(
    file="string",
    reason="string",
)
# binary_module is a plain string. governed is a boolean. concept_id
# is checked apart from this table, as the one named exception to this
# whole schema step: it holds a string or a null.
SEED_ENTRY_FIELD_TYPES = dict(
    binary_module="string",
    governed="boolean",
)
# id, r0002_file, r0002_line, needle, binary_command, binary_excerpt,
# verdict, catalog_recommendation, and reason are all checked
# downstream (_validate_seed), which for r0002_line also excludes bool
# from the accepted int. They are deliberately not in the table above:
# a bad value in one of them must reach that exit-1 domain check, not
# this exit-2 gate.
MAP_FIELD_TYPES = dict(
    schema="string",
    store_dir="string",
    reconciliation="object",
    concepts="list",
)
MAP_RECONCILIATION_FIELD_TYPES = dict(
    dropped_rows="list",
    seed_dropped_verdicts="list",
)
MAP_CONCEPT_FIELD_TYPES = dict(
    concept_id="string",
    concept="string",
    tag="string",
    notes="string",
    markers="list",
    governed_files="list",
)
MAP_GOVERNED_ROW_FIELD_TYPES = dict(
    file="string",
    marker="string",
    fix_kind="string",
    provenance="string",
    fix_present="string",
    delivery_path="string",
    state="string",
    verified="string",
)
MAP_DROPPED_ROW_FIELD_TYPES = dict(
    concept="string",
    file="string",
    reason="string",
)
MAP_SEED_VERDICT_FIELD_TYPES = dict(
    id="string",
    verdict="string",
    governed="boolean",
    catalog_recommendation="string",
    reason="string",
    anchor="string",
    quote="string",
)
CLAIMS_FIELD_TYPES = dict(
    claims="list",
    decisions="list",
)
CLAIM_ENTRY_FIELD_TYPES = dict(
    id="string",
    category="string",
    anchor="string",
    quote="string",
    output="string",
    text="string",
)
DECISION_FIELD_TYPES = dict(
    id="string",
    text="string",
    concept="string",
    file="string",
    state="string",
    depends_on="list",
    seed_id="string",
    verdict="string",
)
# Every element of a decision's depends_on list must be a string.
DECISION_LIST_ITEM_FIELDS = dict(
    depends_on="string",
)

# The script reads these fields directly. It indexes them, loops over
# them, or passes them on. A JSON null in one of these fields gives no
# usable value. The schema check fails it like a value of the wrong type.
# Without this rule, a later step reads the null as a missing field.
# concept_id is left out on purpose: a seed entry's concept_id holds
# null or a string, the one named exception this whole schema step
# carries.
REQUIRED_VALUE_FIELDS = {"concept_id", "file", "reason", "schema", "store_dir",
                       "seed_store_dir", "seed_dropped", "reconciliation",
                       "claims", "decisions", "concepts", "rows", "dropped",
                       "governed_files", "notes", "markers", "session",
                       "dropped_rows", "seed_dropped_verdicts", "depends_on",
                       "text", "concept", "state", "id", "category", "anchor",
                       "output", "quote", "session_id", "date", "vantage",
                       "notes_append", "binary_module", "tag", "marker",
                       "fix_kind", "provenance", "fix_present", "verdict",
                       "seed_id", "delivery_path", "verified",
                       "governed", "catalog_recommendation"}


def _json_type_of(value) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "list"
    if isinstance(value, dict):
        return "object"
    if isinstance(value, (int, float)):
        return "number"
    return "null"


def _check_record_types(record: dict, table: dict, errors: list, label: str) -> None:
    """Check the fields of one record against one field-type table. The
    check skips a field that is not in the record. A JSON null passes,
    except in a field of REQUIRED_VALUE_FIELDS. There, a null fails like
    a wrong type. Each failure adds one message with the label and the
    field. The function never raises an exception."""
    for field, expected in table.items():
        if field not in record:
            continue
        value = record[field]
        if value is None and field not in REQUIRED_VALUE_FIELDS:
            continue
        if _json_type_of(value) != expected:
            errors.append(f"{label}: {field!r} must be a {expected}")


def _check_list_item_types(record: dict, table: dict, errors: list, label: str) -> None:
    """Check every element of each list field named in table. The check
    skips a field that is missing or not a list. table maps a field name
    to the one JSON type every element of that list must hold."""
    for field, expected in table.items():
        value = record.get(field)
        if not isinstance(value, list):
            continue
        for index, item in enumerate(value):
            if _json_type_of(item) != expected:
                errors.append(f"{label}: {field}[{index}] must be a {expected}")


def _check_required_present(record: dict, fields: tuple, errors: list, label: str) -> None:
    """Check that each field named in fields exists in record, present or
    absent, not merely non-null. Call this only for a field the script
    indexes directly later (record["field"]). Without this check, a
    missing field reaches that index and raises. Each failure adds one
    message with the label and the field. The function never raises."""
    for field in fields:
        if field not in record:
            errors.append(f"{label}: missing required field {field!r}")


def _check_seed_concept_id(entry: dict, errors: list, label: str) -> None:
    """A seed entry's concept_id is the one field this schema step lets
    hold two JSON types: null (not governed by any block) or a string
    (the concept it was folded into). Any other type fails."""
    if "concept_id" not in entry:
        return
    value = entry["concept_id"]
    if value is None or isinstance(value, str):
        return
    errors.append(f"{label}: 'concept_id' must be a string or null")


def _schema_error(transcript: dict, cmap: dict, claims: dict) -> str | None:
    """Check the JSON type of each field that the script reads. The
    check covers the transcript, the map, and the claims table, at
    every depth: each container field, each object field, and every
    element of each list. Call it after the three JSON files load and
    after _shape_error. At that point, each list holds only objects, so
    each record is a dict. The function returns the first usage-error
    message. When each field has the correct type, it returns None."""
    errors: list = []
    _check_record_types(transcript, TRANSCRIPT_FIELD_TYPES, errors, "transcript")
    session = transcript.get("session")
    if isinstance(session, dict):
        _check_record_types(session, TRANSCRIPT_SESSION_FIELD_TYPES, errors, "transcript.session")
    for block in transcript.get("concepts") or []:
        if not isinstance(block, dict):
            continue
        cid = block.get("concept_id")
        _check_required_present(block, ("concept_id",), errors, f"transcript.concepts[{cid}]")
        _check_record_types(block, TRANSCRIPT_BLOCK_FIELD_TYPES, errors, f"transcript.concepts[{cid}]")
        _check_list_item_types(block, dict(markers="string"), errors, f"transcript.concepts[{cid}]")
        for row in (block.get("rows") if isinstance(block.get("rows"), list) else []):
            if isinstance(row, dict):
                _check_record_types(row, TRANSCRIPT_ROW_FIELD_TYPES, errors,
                                    f"transcript.concepts[{cid}].rows")
        for entry in (block.get("dropped") if isinstance(block.get("dropped"), list) else []):
            if isinstance(entry, dict):
                _check_record_types(entry, TRANSCRIPT_DROPPED_FIELD_TYPES, errors,
                                    f"transcript.concepts[{cid}].dropped")
    for entry in (transcript.get("seed_dropped") if isinstance(transcript.get("seed_dropped"), list) else []):
        if isinstance(entry, dict):
            _check_record_types(entry, SEED_ENTRY_FIELD_TYPES, errors, "transcript.seed_dropped")
            _check_seed_concept_id(entry, errors, "transcript.seed_dropped")

    _check_record_types(cmap, MAP_FIELD_TYPES, errors, "map")
    reconciliation = cmap.get("reconciliation")
    if isinstance(reconciliation, dict):
        _check_record_types(reconciliation, MAP_RECONCILIATION_FIELD_TYPES, errors, "map.reconciliation")
        for entry in (reconciliation.get("dropped_rows")
                      if isinstance(reconciliation.get("dropped_rows"), list) else []):
            if isinstance(entry, dict):
                _check_record_types(entry, MAP_DROPPED_ROW_FIELD_TYPES, errors,
                                    "map.reconciliation.dropped_rows")
        for entry in (reconciliation.get("seed_dropped_verdicts")
                      if isinstance(reconciliation.get("seed_dropped_verdicts"), list) else []):
            if isinstance(entry, dict):
                _check_required_present(entry, ("id",), errors,
                                        "map.reconciliation.seed_dropped_verdicts")
                _check_record_types(entry, MAP_SEED_VERDICT_FIELD_TYPES, errors,
                                    "map.reconciliation.seed_dropped_verdicts")
                _check_seed_concept_id(entry, errors, "map.reconciliation.seed_dropped_verdicts")
    for concept in (cmap.get("concepts") if isinstance(cmap.get("concepts"), list) else []):
        if not isinstance(concept, dict):
            continue
        cid = concept.get("concept_id")
        _check_record_types(concept, MAP_CONCEPT_FIELD_TYPES, errors, f"map.concepts[{cid}]")
        _check_list_item_types(concept, dict(markers="string"), errors, f"map.concepts[{cid}]")
        for row in (concept.get("governed_files") if isinstance(concept.get("governed_files"), list) else []):
            if isinstance(row, dict):
                _check_record_types(row, MAP_GOVERNED_ROW_FIELD_TYPES, errors,
                                    f"map.concepts[{cid}].governed_files")

    _check_record_types(claims, CLAIMS_FIELD_TYPES, errors, "claims")
    for entry in (claims.get("claims") if isinstance(claims.get("claims"), list) else []):
        if isinstance(entry, dict):
            _check_required_present(entry, ("id",), errors, "claims.claims")
            _check_record_types(entry, CLAIM_ENTRY_FIELD_TYPES, errors, "claims.claims")
    for entry in (claims.get("decisions") if isinstance(claims.get("decisions"), list) else []):
        if isinstance(entry, dict):
            _check_required_present(entry, ("id",), errors, "claims.decisions")
            _check_record_types(entry, DECISION_FIELD_TYPES, errors, "claims.decisions")
            _check_list_item_types(entry, DECISION_LIST_ITEM_FIELDS, errors, "claims.decisions")

    return errors[0] if errors else None


def _arm_watchdog(max_seconds: float, probe_seconds: float) -> None:
    # Order matters. A non-finite value fails its own check first. That
    # way nan and inf get a clear message. They do not fall through to a
    # range check that is also true for them.
    if math.isnan(max_seconds) or math.isinf(max_seconds):
        print("error: --max-seconds must be a finite number", file=sys.stderr)
        sys.exit(2)
    if math.isnan(probe_seconds) or math.isinf(probe_seconds):
        print("error: --watchdog-probe must be a finite number", file=sys.stderr)
        sys.exit(2)
    if max_seconds <= 0:
        print("error: --max-seconds must be greater than 0", file=sys.stderr)
        sys.exit(2)
    if probe_seconds < 0:
        print("error: --watchdog-probe must be at least 0", file=sys.stderr)
        sys.exit(2)
    # threading.Timer and time.sleep both hand their delay to a wait call.
    # That call rejects a value past this ceiling. It raises OverflowError
    # on a background thread, not the caller. So the check happens here,
    # before the timer starts.
    ceiling = threading.TIMEOUT_MAX
    if max_seconds > ceiling:
        print(f"error: --max-seconds must be at most {ceiling}", file=sys.stderr)
        sys.exit(2)
    if probe_seconds > ceiling:
        print(f"error: --watchdog-probe must be at most {ceiling}", file=sys.stderr)
        sys.exit(2)
    timer = threading.Timer(max_seconds, lambda: os._exit(3))
    timer.daemon = True
    timer.start()
    if probe_seconds > 0:
        time.sleep(probe_seconds)


def _read(path: str) -> str:
    if os.path.getsize(path) > MAX_INPUT_BYTES:
        print(f"error: {path} exceeds {MAX_INPUT_BYTES} bytes", file=sys.stderr)
        sys.exit(2)
    with open(path, encoding="utf-8", errors="replace") as fh:
        return fh.read()


def _reject_nan_family(value: str):
    # json.loads calls parse_constant only for NaN, Infinity, and
    # -Infinity. Raising here turns each one into a clean load error.
    raise ValueError(f"the constant {value} is not allowed")


def _reject_duplicate_keys(pairs):
    # json.loads calls object_pairs_hook once per JSON object, at every
    # depth, with the raw key-value pairs in file order. A duplicate key
    # in that list is a defect this loader must catch, not silently keep
    # the last value for.
    seen = set()
    for key, _ in pairs:
        if key in seen:
            raise ValueError(f"duplicate key {key!r}")
        seen.add(key)
    return dict(pairs)


def _read_json_strict(path: str):
    """Read path as strict UTF-8 bytes and parse as strict JSON. Rejects
    a bad byte, NaN/Infinity/-Infinity at any depth, and a duplicate key
    at any depth. Raises ValueError with a message naming the file."""
    if os.path.getsize(path) > MAX_INPUT_BYTES:
        raise ValueError(f"{path} exceeds {MAX_INPUT_BYTES} bytes")
    with open(path, "rb") as fh:
        raw = fh.read()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as e:
        raise ValueError(f"{path} is not valid UTF-8: {e}") from e
    try:
        return json.loads(text, parse_constant=_reject_nan_family,
                          object_pairs_hook=_reject_duplicate_keys)
    except ValueError as e:
        raise ValueError(f"{path} is not valid JSON: {e}") from e


def _check_unicode_clean(data, label: str, path: str) -> None:
    # A JSON escape of a lone surrogate parses into a string this
    # process cannot re-encode as UTF-8. It can appear in a key or a
    # value, at any depth. json.dumps walks the whole parsed structure,
    # so one call here checks every key and every value in one pass.
    try:
        json.dumps(data, ensure_ascii=False).encode("utf-8")
    except UnicodeEncodeError:
        print(f"error: {label} ({path}) holds a lone surrogate that is not valid Unicode",
              file=sys.stderr)
        sys.exit(2)


def _load_json(path: str, label: str) -> dict:
    if not os.path.isfile(path):
        print(f"error: no such file {path}", file=sys.stderr)
        sys.exit(2)
    try:
        data = _read_json_strict(path)
    except ValueError as e:
        print(f"error: invalid JSON in {label} ({path}): {e}", file=sys.stderr)
        sys.exit(2)
    _check_unicode_clean(data, label, path)
    return data


def _write_json_atomic(path: str, data: dict) -> None:
    """Write JSON at indent 1, with non-ASCII characters kept as-is, plus
    one trailing newline. The write goes through a temp file in the target
    directory and os.replace. A reader never sees a half-written file."""
    text = json.dumps(data, indent=1, ensure_ascii=False) + "\n"
    directory = os.path.dirname(os.path.abspath(path)) or "."
    fd, tmp_path = _mkstemp_in(directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        os.replace(tmp_path, path)
    except BaseException:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise


def _mkstemp_in(directory: str):
    import tempfile
    return tempfile.mkstemp(prefix=".apply_recognition-", dir=directory)


class GateInternalError(Exception):
    """The gate itself did not run: its temp file creation or write
    step failed, or check_claims.py did not start. This is not a
    verdict on new_claims. The caller reports one clean line and exits
    1, with no write."""


def _check_claims_gate(new_claims: dict, claims_path: str) -> list:
    """Run check_claims.py against new_claims before this script writes
    anything. The check runs on a temp file in the claims directory, so
    check_claims.py resolves anchors the same way as a written claims
    file. The temp file is removed before this function returns, on
    every path, success or failure, including a failed write. Its
    prefix is .apply_recognition_gate-. A watchdog kill can leave this
    file behind. _clean_stale_temp_files removes it on the next run
    that exits 0. The function returns the gate's stdout violation
    lines. An empty list means the gate passed. A failure to create
    the temp file, write it, or start check_claims.py raises
    GateInternalError instead of it returning a verdict."""
    import subprocess
    import tempfile
    claims_dir = os.path.dirname(os.path.abspath(claims_path)) or "."
    try:
        fd, tmp_path = tempfile.mkstemp(prefix=".apply_recognition_gate-", dir=claims_dir)
    except OSError as e:
        raise GateInternalError(f"could not create the gate temp file: {e}") from e
    try:
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(json.dumps(new_claims, indent=1, ensure_ascii=False) + "\n")
        except OSError as e:
            # os.fdopen failed before it took ownership of fd, so the
            # descriptor is still open. Close it here so the finally
            # block's os.remove does not hit a locked file on Windows.
            try:
                os.close(fd)
            except OSError:
                pass
            raise GateInternalError(f"could not write the gate temp file: {e}") from e
        check_script = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                    "..", "anchored-claims", "check_claims.py")
        try:
            result = subprocess.run([sys.executable, check_script, tmp_path],
                                    capture_output=True, text=True)
        except OSError as e:
            raise GateInternalError(f"could not start check_claims.py: {e}") from e
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
    if result.returncode == 0:
        return []
    lines = [ln for ln in result.stdout.splitlines() if ln.strip()]
    if not lines:
        lines = [ln for ln in result.stderr.splitlines() if ln.strip()]
    return lines or ["check_claims.py rejected the new claims table"]


def _clean_stale_temp_files(*directories: str) -> None:
    """Remove a leftover .apply_recognition- or .apply_recognition_gate-
    file in each directory. Such a file is a write temp file or a gate
    temp file that an earlier run's watchdog killed before it removed
    the file. Call this only on a successful exit, after every
    write this run makes has already landed. A missing directory or a
    file removed by another process in the meantime is not an error."""
    seen: set = set()
    for directory in directories:
        directory = os.path.abspath(directory)
        if directory in seen or not os.path.isdir(directory):
            continue
        seen.add(directory)
        for name in os.listdir(directory):
            if name.startswith(".apply_recognition-") or name.startswith(".apply_recognition_gate-"):
                try:
                    os.remove(os.path.join(directory, name))
                except OSError:
                    pass


def _current_text(path: str) -> str:
    if not os.path.isfile(path):
        return ""
    with open(path, encoding="utf-8") as fh:
        return fh.read()


# --- validation -------------------------------------------------------


def _validate_grounding(label: str, raw_file, line, raw_quote, base_dir: str,
                         errors: list) -> bool:
    """Check that a file, line, and quote ground one record inside
    base_dir. This holds for a row's file and for a seed's r0002_file,
    under the same rule. A file name must be a non-empty, unpadded bare
    name inside base_dir. It must carry no leading or trailing
    whitespace, no absolute path, and no slash or backslash. A padded
    name fails here. The script never writes a file name that differs
    from the name it checked. The line must exist in the file. The
    quote must occur in the window of up to 2 lines before and 2 lines
    after that line, joined with single spaces. The function returns
    whether the record grounds."""
    ok = True
    if raw_file is not None and not isinstance(raw_file, str):
        errors.append(f"{label}: file must be a string")
        raw_file = None
        ok = False
    f = raw_file or ""
    padded = f != f.strip()
    f_stripped = f.strip()
    # A file value must be a bare name inside base_dir. This rule rejects
    # a padded name, an absolute path, and a path that holds a slash or a
    # backslash. The wrong path shape fails this rule, regardless of
    # whether the file exists on disk. This rule keeps every governed
    # file inside the one base_dir the map and the claims table both
    # anchor against.
    file_shape_ok = (bool(f_stripped) and not padded and not os.path.isabs(f_stripped)
                      and "/" not in f_stripped and "\\" not in f_stripped)
    file_exists = file_shape_ok and os.path.isfile(os.path.join(base_dir, f_stripped))
    if not file_shape_ok or not file_exists:
        errors.append(f"{label} :: {f_stripped or '<empty>'}: file not found in store_dir")
        ok = False
    raw_quote_value = raw_quote
    if raw_quote_value is not None and not isinstance(raw_quote_value, str):
        errors.append(f"{label} :: {f_stripped}: quote must be a string")
        raw_quote_value = None
        ok = False
    quote = (raw_quote_value or "").strip()
    if not quote:
        errors.append(f"{label} :: {f_stripped}: quote must not be empty")
        ok = False
    line_ok = isinstance(line, int) and not isinstance(line, bool) and line >= 1
    if file_exists and line_ok and quote:
        ok = _validate_line_and_window(label, f_stripped, line, quote, base_dir, errors) and ok
    return ok


def _validate_row(concept_id: str, row: dict, store_dir: str, errors: list) -> bool:
    ok = True
    raw_marker = row.get("marker")
    if raw_marker is not None and not isinstance(raw_marker, str):
        errors.append(f"{concept_id}: marker must be a string")
        raw_marker = None
        ok = False
    if not (raw_marker or "").strip():
        errors.append(f"{concept_id}: missing marker")
        ok = False
    if row.get("state") not in STATES:
        errors.append(f"{concept_id}: state must be one of {sorted(STATES)}")
        ok = False
    if row.get("provenance") not in PROVENANCE:
        errors.append(f"{concept_id}: provenance must be one of {sorted(PROVENANCE)}")
        ok = False
    line = row.get("line")
    # bool is a subclass of int in Python. True and False must be excluded
    # by name. isinstance(line, int) alone lets a boolean line pass.
    if not isinstance(line, int) or isinstance(line, bool) or line < 1:
        errors.append(f"{concept_id}: line must be 1 or more")
        ok = False
    ok = _validate_grounding(concept_id, row.get("file"), line, row.get("quote"),
                              store_dir, errors) and ok
    return ok


def _validate_line_and_window(label: str, f: str, line: int, quote: str,
                               base_dir: str, errors: list) -> bool:
    """A row's line must exist in the file. Its quote must occur in the
    window of up to 2 lines before and 2 lines after that line. The
    window text joins with single spaces. On failure, each error names
    the file."""
    with open(os.path.join(base_dir, f), encoding="utf-8", errors="replace") as fh:
        lines = fh.read().splitlines()
    if line > len(lines):
        errors.append(f"{label} :: {f}: line {line} is beyond the file's {len(lines)} lines")
        return False
    window = " ".join(lines[max(0, line - 3): line + 2])
    if quote not in window:
        errors.append(f"{label} :: {f}: quote not found within 2 lines of {f}:{line}")
        return False
    return True


def _validate_dropped(concept_id: str, entry: dict, errors: list) -> bool:
    ok = True
    if not (entry.get("file") or "").strip():
        errors.append(f"{concept_id}: a dropped entry needs a non-empty file")
        ok = False
    if not (entry.get("reason") or "").strip():
        errors.append(f"{concept_id} :: {entry.get('file')}: a dropped entry needs a non-empty reason")
        ok = False
    return ok


def _validate_seed(entry: dict, seed_store_dir: str, errors: list) -> bool:
    ok = True
    sid = entry.get("id") if isinstance(entry.get("id"), str) and entry.get("id").strip() else "<no id>"
    label = f"seed {sid}"
    if not isinstance(entry.get("governed"), bool):
        errors.append(f"{label}: governed must be a boolean")
        ok = False
    # concept_id is allowed to be None (not governed by any block).
    # r0002_file, r0002_line, and needle get the same grounding checks as
    # a transcript row's file, line, and quote, below. This loop covers
    # every other required string field.
    for field in ("id", "binary_command", "binary_excerpt",
                  "verdict", "catalog_recommendation", "reason"):
        value = entry.get(field)
        if not isinstance(value, str) or not value.strip():
            errors.append(f"{label}: missing {field!r}")
            ok = False
    line = entry.get("r0002_line")
    # bool is a subclass of int in Python. True and False must be excluded
    # by name. isinstance(line, int) alone lets a boolean line pass.
    if not isinstance(line, int) or isinstance(line, bool) or line < 1:
        errors.append(f"{label}: r0002_line must be 1 or more")
        ok = False
    ok = _validate_grounding(label, entry.get("r0002_file"), line,
                              entry.get("needle"), seed_store_dir, errors) and ok
    return ok


def validate_and_plan(transcript: dict, cmap: dict, claims: dict, store_dir: str,
                       seed_store_dir: str, wanted: set | None) -> tuple[list, dict]:
    """Test every input rule first. It returns errors and a plan. If errors
    is not empty, plan is empty, and the script writes nothing."""
    errors: list = []
    plan = {"blocks": [], "seeds": []}

    version = (transcript.get("binary") or {}).get("claude_version")
    if not isinstance(version, str) or not version.strip():
        errors.append("transcript.binary.claude_version must be a non-empty string")

    date = (transcript.get("session") or {}).get("date")
    if not isinstance(date, str) or not date.strip() or len(date.strip().split()) != 1:
        errors.append("transcript.session.date must be a single non-empty token")

    map_by_id = {c.get("concept_id"): c for c in cmap.get("concepts", [])}
    transcript_blocks = transcript.get("concepts") or []

    for block in transcript_blocks:
        cid = block.get("concept_id")
        if wanted is not None and cid not in wanted:
            continue
        rows = block.get("rows") or []
        dropped = block.get("dropped") or []
        for row in rows:
            _validate_row(cid, row, store_dir, errors)
        for entry in dropped:
            _validate_dropped(cid, entry, errors)
        map_block = map_by_id.get(cid)
        if map_block is None:
            if not (block.get("concept") or "").strip() or not (block.get("tag") or "").strip() \
                    or not block.get("markers"):
                errors.append(f"{cid}: transcript names a block the map lacks, and the block "
                              f"carries no concept, tag, or markers to create one")
                continue
        plan["blocks"].append(block)

    if wanted is None:
        for entry in transcript.get("seed_dropped") or []:
            _validate_seed(entry, seed_store_dir, errors)
        plan["seeds"] = transcript.get("seed_dropped") or []

    if errors:
        return errors, {}
    return [], plan


# --- map application ---------------------------------------------------


def _apply_state_rules(row: dict, state: str, is_new: bool) -> None:
    """Map rules 4 and 5: fix_present and fix_kind by state."""
    if state == "conforms":
        if is_new:
            row["fix_kind"] = "body-invariant"
        row["fix_present"] = "not-applicable"
        return
    # carries-defect
    if is_new:
        row.setdefault("fix_kind", "body-rewrite")
    elif row.get("fix_kind") == "body-invariant":
        row["fix_kind"] = "body-rewrite"
    if row.get("fix_present") not in FIX_PRESENT:
        row["fix_present"] = "false"


def _apply_row(existing: dict | None, trow: dict, session: dict, short_version: str) -> dict:
    """Build the map row for one transcript row, per map rules 2 to 6. If
    the row lacks the "state" key, a dict assignment on that key adds it at
    the end. If the row already has the key, the assignment keeps its
    slot. So rule 3 needs no extra code for key position."""
    is_new = existing is None
    row = copy.deepcopy(existing) if existing else {}
    row["file"] = trow["file"]
    row["marker"] = trow["marker"]
    row["provenance"] = trow["provenance"]
    if is_new:
        if trow.get("fix_kind"):
            row["fix_kind"] = trow["fix_kind"]
        row["delivery_path"] = ("runtime-system-reminders"
                                 if trow.get("fix_kind") == "runtime-notice"
                                 else "binary-splice")
        row["verified"] = (f"main-session recognition {(session.get('date') or '').strip()} on "
                            f"{short_version}")
    _apply_state_rules(row, trow["state"], is_new)
    row["state"] = trow["state"]
    return row


def _binary_short_version(claude_version: str) -> str:
    """The Claude Code version the verified text names: the first
    whitespace-separated token of the trimmed transcript.binary.claude_version,
    for example "2.1.280" from " 2.1.280 (Claude Code)". The caller has
    already checked that claude_version is a non-empty string."""
    return claude_version.strip().split()[0]


def apply_block(map_block: dict | None, block: dict, store_dir: str, session: dict,
                 short_version: str, dropped_rows_out: list) -> dict:
    """Return the (possibly new) map block after applying one transcript
    block, per map rules 1-8."""
    cid = block["concept_id"]
    rows = block.get("rows") or []
    dropped = block.get("dropped") or []
    by_file = {r["file"]: r for r in rows}
    dropped_files = {d["file"] for d in dropped}

    if map_block is None:
        new_block = {}
        for key in NEW_BLOCK_KEYS:
            if key == "concept_id":
                new_block[key] = cid
            elif key == "concept":
                new_block[key] = block["concept"]
            elif key == "tag":
                new_block[key] = block["tag"]
            elif key == "markers":
                new_block[key] = block["markers"]
            elif key == "coverage_method":
                new_block[key] = "read-verified"
            elif key == "notes":
                new_block[key] = block.get("notes_append", "")
            elif key == "governed_files":
                new_block[key] = []
        map_block = new_block
    else:
        map_block = copy.deepcopy(map_block)
        notes_append = block.get("notes_append") or ""
        if notes_append and notes_append not in (map_block.get("notes") or ""):
            map_block["notes"] = (map_block.get("notes") or "") + f" | {notes_append}"

    existing_rows = map_block.get("governed_files") or []
    new_rows: list = []
    seen_files = set()
    for existing in existing_rows:
        f = existing.get("file")
        if f in seen_files:
            continue
        seen_files.add(f)
        if f in by_file:
            new_rows.append(_apply_row(existing, by_file[f], session, short_version))
        elif f in dropped_files:
            reason = next(d["reason"] for d in dropped if d["file"] == f)
            entry = {"concept": cid, "file": f, "reason": reason}
            if entry not in dropped_rows_out:
                dropped_rows_out.append(entry)
        else:
            # An unaccounted prior row is neither kept nor dropped. The
            # caller tests for this case on every in-scope block. This
            # branch is a defensive backstop, not the primary gate.
            raise ValueError(f"{cid} :: {f}: no transcript row and no drop reason")
    for trow in rows:
        if trow["file"] not in seen_files:
            new_rows.append(_apply_row(None, trow, session, short_version))
            seen_files.add(trow["file"])

    map_block["governed_files"] = new_rows
    return map_block


def _seed_verdict_row(entry: dict) -> dict:
    """Map rule 9: one reconciliation.seed_dropped_verdicts row per seed
    entry, in the field order the rule names."""
    return dict(
        id=entry["id"], verdict=entry["verdict"], governed=entry["governed"],
        concept_id=entry.get("concept_id"),
        catalog_recommendation=entry["catalog_recommendation"], reason=entry["reason"],
        anchor=f"cmd: {entry['binary_command']}", quote=entry["binary_excerpt"],
    )


def apply_map(cmap: dict, transcript: dict, plan_blocks: list, plan_seeds: list,
              store_dir: str) -> dict:
    new_map = copy.deepcopy(cmap)
    new_map.setdefault("reconciliation", {})
    new_map["reconciliation"].setdefault("dropped_rows", [])
    map_by_id = {c.get("concept_id"): i for i, c in enumerate(new_map["concepts"])}
    session = transcript.get("session") or {}
    short_version = _binary_short_version((transcript.get("binary") or {})["claude_version"])
    dropped_out = new_map["reconciliation"]["dropped_rows"]

    for block in plan_blocks:
        cid = block["concept_id"]
        idx = map_by_id.get(cid)
        map_block = new_map["concepts"][idx] if idx is not None else None
        new_block = apply_block(map_block, block, store_dir, session, short_version, dropped_out)
        if idx is not None:
            new_map["concepts"][idx] = new_block
        else:
            new_map["concepts"].append(new_block)
            map_by_id[cid] = len(new_map["concepts"]) - 1

    if plan_seeds:
        by_id = {v["id"]: i for i, v in enumerate(
            new_map["reconciliation"].get("seed_dropped_verdicts", []))}
        verdicts = new_map["reconciliation"].setdefault("seed_dropped_verdicts", [])
        for entry in plan_seeds:
            row = _seed_verdict_row(entry)
            if entry["id"] in by_id:
                verdicts[by_id[entry["id"]]] = row
            else:
                verdicts.append(row)
                by_id[entry["id"]] = len(verdicts) - 1

    return new_map


# --- claims application -------------------------------------------------


def _find_claim(claims_list: list, anchor: str, quote: str | None = None,
                 output: str | None = None, category: str | None = None) -> dict | None:
    for c in claims_list:
        if c.get("anchor") != anchor:
            continue
        if quote is not None and c.get("quote") != quote:
            continue
        if output is not None and c.get("output") != output:
            continue
        if category is not None and c.get("category") != category:
            continue
        return c
    return None


def _next_id(claims_list: list, prefix: str) -> str:
    n = 0
    for c in claims_list:
        cid = c.get("id", "")
        if cid.startswith(prefix) and cid[len(prefix):].isdigit():
            n = max(n, int(cid[len(prefix):]))
    return f"{prefix}{n + 1}"


class StoreNotRelativeError(Exception):
    """No relative path exists from the claims file to store_dir. Two
    paths on different Windows drives raise this error."""


def _claims_relative_store_dir(store_dir: str, claims_path: str) -> str:
    claims_dir = os.path.dirname(os.path.abspath(claims_path))
    try:
        rel = os.path.relpath(os.path.abspath(store_dir), claims_dir)
    except ValueError as e:
        raise StoreNotRelativeError(
            f"no relative path from {claims_dir} to {store_dir}: {e}") from e
    return rel.replace(os.sep, "/")


def apply_claims(claims: dict, transcript: dict, plan_blocks: list, plan_seeds: list,
                  store_dir: str, seed_store_dir: str, claims_path: str) -> dict:
    new_claims = copy.deepcopy(claims)
    new_claims.setdefault("claims", [])
    new_claims.setdefault("decisions", [])

    binary_output = (transcript.get("binary") or {}).get("claude_version", "")
    binary_claim = _find_claim(new_claims["claims"], "cmd: claude --version", output=binary_output,
                                category="observation")
    if binary_claim is None:
        binary_claim = {
            "id": _next_id(new_claims["claims"], "c"),
            "category": "observation",
            "anchor": "cmd: claude --version",
            "output": binary_output,
        }
        new_claims["claims"].append(binary_claim)
    binary_id = binary_claim["id"]

    rel_store = _claims_relative_store_dir(store_dir, claims_path)
    decisions_by_key = {(d.get("concept"), d.get("file")): i
                         for i, d in enumerate(new_claims["decisions"])}
    in_scope_keys = set()

    for block in plan_blocks:
        cid = block["concept_id"]
        for trow in block.get("rows") or []:
            f = trow["file"]
            in_scope_keys.add((cid, f))
            anchor = f"{rel_store}/{f}:{trow['line']}"
            quote = trow["quote"]
            row_claim = _find_claim(new_claims["claims"], anchor, quote=quote,
                                     category="observation")
            if row_claim is None:
                row_claim = {
                    "id": _next_id(new_claims["claims"], "c"),
                    "category": "observation",
                    "anchor": anchor,
                    "quote": quote,
                }
                new_claims["claims"].append(row_claim)
            decision = {
                "id": None,
                "text": f"{f} in {cid} is {trow['state']}, per a main-session recognition.",
                "concept": cid,
                "file": f,
                "state": trow["state"],
                "depends_on": [binary_id, row_claim["id"]],
            }
            key = (cid, f)
            if key in decisions_by_key:
                idx = decisions_by_key[key]
                decision["id"] = new_claims["decisions"][idx]["id"]
                new_claims["decisions"][idx] = decision
            else:
                decision["id"] = _next_id(new_claims["decisions"], "d")
                new_claims["decisions"].append(decision)
                decisions_by_key[key] = len(new_claims["decisions"]) - 1

        # rule 4: a decision of a block in scope whose file is no longer a
        # row leaves the table.
        kept_files = {trow["file"] for trow in block.get("rows") or []}
        new_claims["decisions"] = [
            d for d in new_claims["decisions"]
            if not (d.get("concept") == cid and d.get("file") not in kept_files
                    and (cid, d.get("file")) not in in_scope_keys)
        ]
        # The filter above rebuilds the decisions list. Any index in
        # decisions_by_key from before this point is now stale. Rebuild
        # the lookup here. The next block must match against the real,
        # current indices.
        decisions_by_key = {(d.get("concept"), d.get("file")): i
                             for i, d in enumerate(new_claims["decisions"])}

    rel_seed_store = _claims_relative_store_dir(seed_store_dir, claims_path)
    seed_decisions_by_id = {d.get("seed_id"): i for i, d in enumerate(new_claims["decisions"])
                            if d.get("seed_id")}
    for entry in plan_seeds:
        anchor_a = f"{rel_seed_store}/{entry['r0002_file']}:{entry['r0002_line']}"
        claim_a = _find_claim(new_claims["claims"], anchor_a, quote=entry["needle"],
                               category="observation")
        if claim_a is None:
            claim_a = dict(id=_next_id(new_claims["claims"], "c"), category="observation",
                           anchor=anchor_a, quote=entry["needle"])
            new_claims["claims"].append(claim_a)
        anchor_b = f"cmd: {entry['binary_command']}"
        claim_b = _find_claim(new_claims["claims"], anchor_b, output=entry["binary_excerpt"],
                               category="observation")
        if claim_b is None:
            claim_b = dict(id=_next_id(new_claims["claims"], "c"), category="observation",
                           anchor=anchor_b, output=entry["binary_excerpt"])
            new_claims["claims"].append(claim_b)
        decision = dict(
            id=None,
            text=f"Seed {entry['id']} is {entry['verdict']}, per a main-session recognition.",
            seed_id=entry["id"], verdict=entry["verdict"],
            depends_on=[binary_id, claim_a["id"], claim_b["id"]],
        )
        if entry["id"] in seed_decisions_by_id:
            idx = seed_decisions_by_id[entry["id"]]
            decision["id"] = new_claims["decisions"][idx]["id"]
            new_claims["decisions"][idx] = decision
        else:
            decision["id"] = _next_id(new_claims["decisions"], "d")
            new_claims["decisions"].append(decision)
            seed_decisions_by_id[entry["id"]] = len(new_claims["decisions"]) - 1

    return new_claims


def _shape_error(transcript: dict, cmap: dict, claims: dict) -> str | None:
    """Check every list this script walks for a non-object item. Do this
    before any validation rule that assumes each item is a dict. If every
    shape is clean, return None. Otherwise return the first usage-error
    message found."""
    for item in (transcript.get("concepts") if isinstance(transcript.get("concepts"), list) else []):
        if not isinstance(item, dict):
            return "error: transcript.concepts must hold only JSON objects"
        for row in (item.get("rows") if isinstance(item.get("rows"), list) else []):
            if not isinstance(row, dict):
                return f"error: {item.get('concept_id')}: rows must hold only JSON objects"
        for entry in (item.get("dropped") if isinstance(item.get("dropped"), list) else []):
            if not isinstance(entry, dict):
                return f"error: {item.get('concept_id')}: dropped must hold only JSON objects"
    if "seed_dropped" in transcript and not isinstance(transcript.get("seed_dropped"), list):
        return "error: transcript.seed_dropped must hold only JSON objects"
    for entry in (transcript.get("seed_dropped") if isinstance(transcript.get("seed_dropped"), list) else []):
        if not isinstance(entry, dict):
            return "error: transcript.seed_dropped must hold only JSON objects"
    for item in (cmap.get("concepts") if isinstance(cmap.get("concepts"), list) else []):
        if not isinstance(item, dict):
            return "error: map.concepts must hold only JSON objects"
        for row in (item.get("governed_files") if isinstance(item.get("governed_files"), list) else []):
            if not isinstance(row, dict):
                return f"error: {item.get('concept_id')}: governed_files must hold only JSON objects"
    reconciliation = cmap.get("reconciliation")
    if isinstance(reconciliation, dict):
        for entry in (reconciliation.get("dropped_rows")
                      if isinstance(reconciliation.get("dropped_rows"), list) else []):
            if not isinstance(entry, dict):
                return "error: map.reconciliation.dropped_rows must hold only JSON objects"
        for entry in (reconciliation.get("seed_dropped_verdicts")
                      if isinstance(reconciliation.get("seed_dropped_verdicts"), list) else []):
            if not isinstance(entry, dict):
                return "error: map.reconciliation.seed_dropped_verdicts must hold only JSON objects"
    if "claims" in claims and not isinstance(claims.get("claims"), list):
        return "error: claims.claims must hold only JSON objects"
    for item in (claims.get("claims") if isinstance(claims.get("claims"), list) else []):
        if not isinstance(item, dict):
            return "error: claims.claims must hold only JSON objects"
    if "decisions" in claims and not isinstance(claims.get("decisions"), list):
        return "error: claims.decisions must hold only JSON objects"
    for item in (claims.get("decisions") if isinstance(claims.get("decisions"), list) else []):
        if not isinstance(item, dict):
            return "error: claims.decisions must hold only JSON objects"
    return None


def _same_file(path_a: str, path_b: str) -> bool:
    """Tell whether two names refer to one file. When both files exist,
    the function asks the file system with os.path.samefile, which uses
    the case rule of the file system. When a file is missing, samefile
    cannot run. Then the function compares the two absolute paths after
    os.path.normcase."""
    if os.path.exists(path_a) and os.path.exists(path_b):
        try:
            return os.path.samefile(path_a, path_b)
        except OSError:
            pass
    return os.path.normcase(os.path.abspath(path_a)) == os.path.normcase(os.path.abspath(path_b))


def _resolve_store_dir(map_path: str, raw: str) -> str | None:
    """A transcript store_dir can be relative to the map file's directory,
    or relative to the project root (this project's convention). Try the
    map-relative form first, then the raw form. If neither directory
    exists, return None."""
    candidate = os.path.join(os.path.dirname(os.path.abspath(map_path)), raw)
    if os.path.isdir(candidate):
        return candidate
    if os.path.isdir(raw):
        return raw
    return None


# --- main ---------------------------------------------------------------


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--transcript", required=True)
    ap.add_argument("--map", required=True, dest="map_path")
    ap.add_argument("--claims", required=True, dest="claims_path")
    ap.add_argument("--concepts", default=None)
    ap.add_argument("--max-seconds", type=float, default=120, dest="max_seconds")
    ap.add_argument("--watchdog-probe", type=float, default=0, dest="watchdog_probe")
    a = ap.parse_args()
    _arm_watchdog(a.max_seconds, a.watchdog_probe)

    named = [("--transcript", a.transcript), ("--map", a.map_path), ("--claims", a.claims_path)]
    for i in range(len(named)):
        for j in range(i + 1, len(named)):
            name_i, path_i = named[i]
            name_j, path_j = named[j]
            if _same_file(path_i, path_j):
                print(f"error: {name_i} and {name_j} must not name the same file", file=sys.stderr)
                return 2

    transcript = _load_json(a.transcript, "transcript")
    cmap = _load_json(a.map_path, "map")
    claims = _load_json(a.claims_path, "claims")

    if not isinstance(transcript, dict):
        print("error: transcript root must be a JSON object", file=sys.stderr)
        return 2
    if not isinstance(cmap, dict):
        print("error: map root must be a JSON object", file=sys.stderr)
        return 2
    if not isinstance(claims, dict):
        print("error: claims root must be a JSON object", file=sys.stderr)
        return 2

    if not isinstance(cmap.get("concepts"), list) or not cmap["concepts"]:
        print("error: map has no concepts list", file=sys.stderr)
        return 2
    if not isinstance(transcript.get("concepts"), list):
        print("error: transcript has no concepts list", file=sys.stderr)
        return 2

    shape_error = _shape_error(transcript, cmap, claims)
    if shape_error is not None:
        print(shape_error, file=sys.stderr)
        return 2

    schema_error = _schema_error(transcript, cmap, claims)
    if schema_error is not None:
        print(f"error: {schema_error}", file=sys.stderr)
        return 2

    store_dir = _resolve_store_dir(a.map_path, transcript.get("store_dir", ""))
    if store_dir is None:
        print(f"error: store_dir not found: {transcript.get('store_dir')}", file=sys.stderr)
        return 2

    wanted = None
    if a.concepts is not None:
        wanted = {c.strip() for c in a.concepts.split(",") if c.strip()}
        if not wanted:
            print("error: --concepts must name at least one concept id", file=sys.stderr)
            return 2

    # With --concepts, the run processes no seed entry. seed_store_dir
    # stays unresolved and unchecked, so a missing directory named
    # there cannot change the result.
    seed_store_dir = store_dir
    if wanted is None and transcript.get("seed_dropped"):
        seed_store_dir = _resolve_store_dir(a.map_path, transcript.get("seed_store_dir", ""))
        if seed_store_dir is None:
            print(f"error: seed_store_dir not found: {transcript.get('seed_store_dir')}",
                  file=sys.stderr)
            return 2

    errors, plan = validate_and_plan(transcript, cmap, claims, store_dir, seed_store_dir, wanted)
    if errors:
        print("GATE FAILED")
        for e in errors:
            print(" -", e)
        return 1

    try:
        new_map = apply_map(cmap, transcript, plan["blocks"], plan["seeds"], store_dir)
    except ValueError as e:
        print("GATE FAILED")
        print(" -", str(e))
        return 1
    try:
        new_claims = apply_claims(claims, transcript, plan["blocks"], plan["seeds"],
                                  store_dir, seed_store_dir, a.claims_path)
    except StoreNotRelativeError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    old_map_text = _current_text(a.map_path)
    old_claims_text = _current_text(a.claims_path)
    new_map_text = json.dumps(new_map, indent=1, ensure_ascii=False) + "\n"
    new_claims_text = json.dumps(new_claims, indent=1, ensure_ascii=False) + "\n"

    map_dir = os.path.dirname(os.path.abspath(a.map_path)) or "."
    claims_dir = os.path.dirname(os.path.abspath(a.claims_path)) or "."

    no_change = new_map_text == old_map_text and new_claims_text == old_claims_text

    try:
        gate_violations = _check_claims_gate(new_claims, a.claims_path)
    except GateInternalError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    if gate_violations:
        print("GATE FAILED")
        for v in gate_violations:
            print(" -", v)
        return 1

    if no_change:
        _clean_stale_temp_files(map_dir, claims_dir)
        print("NO CHANGE")
        return 0

    try:
        _write_json_atomic(a.claims_path, new_claims)
        _write_json_atomic(a.map_path, new_map)
    except OSError:
        print("error: write failed, run the command again", file=sys.stderr)
        return 1
    _clean_stale_temp_files(map_dir, claims_dir)
    print(f"APPLIED: {len(plan['blocks'])} block(s), {len(plan['seeds'])} seed(s) processed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
