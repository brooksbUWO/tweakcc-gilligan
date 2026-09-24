#!/usr/bin/env python3
"""check_claims.py: gate a deliverable on claim provenance.

The input is a JSON file. It has a "claims" list and an optional "decisions"
list. An optional --prose file adds a prose scan.

Exit codes:
  0  pass
  1  gate failed. The tool prints each violation.
  2  usage error or bad input.
  3  the run hit the --max-seconds ceiling.

Claim categories:
  observation      Needs an "anchor". The anchor takes one of three forms.
                   Form one is "<path>:<line>". The file and the line must
                   exist. An optional "quote" must occur within 2 lines of
                   that line. Form two is "cmd: <command>". It needs a
                   non-empty "output". Form three is "url: <url>". It needs
                   a "saved" local file.
  inference        Needs a "from" list of claim ids. If every ancestor in
                   that list is an observation, the claim is grounded.
  prior            Needs no anchor. The gate allows it. No other claim or
                   decision can depend on it.
  example-attribute
                   One attribute of a cited source artifact. It needs
                   "source", "attribute", and "kind". "kind" is essential or
                   incidental. If it carries a resolvable "anchor", the
                   attribute is grounded like an observation. A row that a
                   decision depends on also needs "breaks_if_absent" (the
                   test) and "verdict" (adopted or rejected).

Decisions: each "depends_on" id must be grounded. A decision that names a
source artifact in "cites" needs enumerated attributes for that source in
the table.

Prose: a sentence with an absolute quantifier or a process claim ("I read",
"verified") needs a [c<n>] tag that names a grounded claim.
"""
import argparse
import json
import math
import os
import re
import sys
import threading
import time

MAX_INPUT_BYTES = 2_000_000
CATEGORIES = {"observation", "inference", "prior", "example-attribute"}
QUANTIFIERS = r"\b(every|never|always|all|none|must|cannot|therefore|regardless|impossible|only)\b"
PROCESS = r"\b(I|we) (read|reviewed|verified|checked|opened|confirmed|tested)\b|\b(was|were) (verified|checked|reviewed|confirmed|tested)\b"
TAG = re.compile(r"\[(c\d+)\]")

# Each table gives one expected JSON type per field of one record kind.
# This check skips a field that is not in a table. Such a field does not
# exist on that kind, or a later rule checks it and names its record.
# The names "string" and "list" mean the Python types str and list. The
# check tests bool before int, because bool is a subclass of int.
CLAIM_FIELD_TYPES = dict([
    ("id", "string"),
    ("category", "string"),
    ("anchor", "string"),
    ("quote", "string"),
    ("output", "string"),
    ("saved", "string"),
    ("from", "list"),
    ("source", "string"),
    ("attribute", "string"),
    ("kind", "string"),
    ("breaks_if_absent", "string"),
    ("verdict", "string"),
    ("text", "string"),
])
DECISION_FIELD_TYPES = dict([
    ("id", "string"),
    ("depends_on", "list"),
    ("cites", "list"),
    ("concept", "string"),
    ("file", "string"),
    ("seed_id", "string"),
    ("state", "string"),
    ("text", "string"),
    ("verdict", "string"),
])

# The script reads these fields directly to decide grounding, identity,
# and dependency. A JSON null in one of these fields gives no usable
# value. The schema check fails it like a value of the wrong type.
# Without this rule, a later step reads the null as a missing field.
REQUIRED_VALUE_FIELDS = {"id", "category", "anchor", "quote", "output", "saved", "depends_on",
                          "text", "concept", "file", "seed_id", "state", "verdict"}


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


# Each of these decision fields is a list. Every element of that list
# must be a string. This table names each such field once, so the walk
# below covers every list element, not only the list's own type.
DECISION_LIST_ITEM_FIELDS = {"depends_on": "string", "cites": "string"}

# An inference claim's "from" list holds only claim ids. Every element
# must be a string. This is the last untyped list in this script.
CLAIM_LIST_ITEM_FIELDS = {"from": "string"}

# The script indexes these claim fields directly (c["field"]), outside
# the by_id lookup that filters out a claim with a missing "id". A
# claim missing one of these fields must fail here, before that direct
# index runs.
CLAIM_REQUIRED_PRESENT_FIELDS = {"id"}


def _schema_error(claims_field, decisions_field) -> str | None:
    """Check each claim field and decision field against
    CLAIM_FIELD_TYPES and DECISION_FIELD_TYPES. Call it after the JSON
    loads and before other logic reads a field value. The check skips a
    field that is not in the record, because a later rule reports a
    missing field. A JSON null passes, except in a field of
    REQUIRED_VALUE_FIELDS. There, a null fails like a wrong type. After
    a list field passes its own type check, the walk also checks every
    element of that list against DECISION_LIST_ITEM_FIELDS or
    CLAIM_LIST_ITEM_FIELDS. A claim missing a field named in
    CLAIM_REQUIRED_PRESENT_FIELDS also fails here, because the script
    indexes that field directly later. The function returns the first
    usage-error message. When each field has the correct type, it
    returns None."""
    for c in (claims_field if isinstance(claims_field, list) else []):
        if not isinstance(c, dict):
            continue
        for field in CLAIM_REQUIRED_PRESENT_FIELDS:
            if field not in c:
                return f"error: claim missing required field {field!r}"
        for field, expected in CLAIM_FIELD_TYPES.items():
            if field not in c:
                continue
            value = c[field]
            if value is None and field not in REQUIRED_VALUE_FIELDS:
                continue
            if _json_type_of(value) != expected:
                return f"error: claim {c.get('id')!r}: {field!r} must be a {expected}"
        for field, item_expected in CLAIM_LIST_ITEM_FIELDS.items():
            value = c.get(field)
            if not isinstance(value, list):
                continue
            for index, item in enumerate(value):
                if _json_type_of(item) != item_expected:
                    return (f"error: claim {c.get('id')!r}: {field}[{index}] "
                            f"must be a {item_expected}")
    for d in (decisions_field if isinstance(decisions_field, list) else []):
        if not isinstance(d, dict):
            continue
        for field, expected in DECISION_FIELD_TYPES.items():
            if field not in d:
                continue
            value = d[field]
            if value is None and field not in REQUIRED_VALUE_FIELDS:
                continue
            if _json_type_of(value) != expected:
                return f"error: decision {d.get('id')!r}: {field!r} must be a {expected}"
        for field, item_expected in DECISION_LIST_ITEM_FIELDS.items():
            value = d.get(field)
            if not isinstance(value, list):
                continue
            for index, item in enumerate(value):
                if _json_type_of(item) != item_expected:
                    return (f"error: decision {d.get('id')!r}: {field}[{index}] "
                            f"must be a {item_expected}")
    return None


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


def _split_anchor(anchor: str):
    m = re.match(r"^(.*?):(\d+)$", anchor)
    if not m:
        return None, None
    return m.group(1), int(m.group(2))


def check_observation(c: dict, base: str, out: list) -> bool:
    cid = c["id"]
    anchor = (c.get("anchor") or "").strip()
    if not anchor:
        out.append(f"{cid}: observation without anchor")
        return False
    if anchor.startswith("cmd:"):
        # "cmd:" with nothing after it names no command. Only spaces
        # after it also name no command. Output can be non-empty. This
        # claim still cannot be grounded.
        if not anchor[len("cmd:"):].strip():
            out.append(f"{cid}: cmd anchor without a command")
            return False
        if not (c.get("output") or "").strip():
            out.append(f"{cid}: cmd anchor without output")
            return False
        return True
    if anchor.startswith("url:"):
        # "url:" with nothing after it names no URL. Only spaces after
        # it also name no URL. A saved copy can exist on disk even so.
        # This claim still cannot be grounded.
        if not anchor[len("url:"):].strip():
            out.append(f"{cid}: url anchor without a URL")
            return False
        saved = c.get("saved") or ""
        p = saved if os.path.isabs(saved) else os.path.join(base, saved)
        if not saved or not os.path.isfile(p):
            out.append(f"{cid}: url anchor without an existing saved copy ({saved!r})")
            return False
        return True
    path, line = _split_anchor(anchor)
    if path is None:
        out.append(f"{cid}: anchor {anchor!r} is not <path>:<line>, cmd:, or url:")
        return False
    p = path if os.path.isabs(path) else os.path.join(base, path)
    if not os.path.isfile(p):
        out.append(f"{cid}: anchored file does not exist: {path}")
        return False
    lines = _read(p).splitlines()
    if line < 1 or line > len(lines):
        out.append(f"{cid}: line {line} outside {path} ({len(lines)} lines)")
        return False
    quote = (c.get("quote") or "").strip()
    if quote:
        window = " ".join(lines[max(0, line - 3): line + 2])
        if quote not in window:
            out.append(f"{cid}: quote not found within 2 lines of {path}:{line}")
            return False
    return True


def check_example_attribute(c: dict, base: str, out: list, load_bearing: bool) -> bool:
    cid = c["id"]
    ok = True
    for field in ("source", "attribute", "kind"):
        if not (c.get(field) or "").strip():
            out.append(f"{cid}: example-attribute without {field!r}")
            ok = False
    if c.get("kind") not in (None, "essential", "incidental"):
        out.append(f"{cid}: kind must be 'essential' or 'incidental'")
        ok = False
    if load_bearing:
        # justification is owed only where a decision rests on the attribute
        if not (c.get("breaks_if_absent") or "").strip():
            out.append(f"{cid}: a decision depends on this attribute, so it needs 'breaks_if_absent'")
            ok = False
        if c.get("verdict") not in ("adopted", "rejected"):
            out.append(f"{cid}: a decision depends on this attribute, so it needs verdict adopted|rejected")
            ok = False
    if (c.get("anchor") or "").strip():
        ok = check_observation(c, base, out) and ok
    return ok


def run(data: dict, base: str, prose: str | None) -> list:
    out: list = []
    claims = data.get("claims")
    if not isinstance(claims, list) or not claims:
        return ["input has no non-empty 'claims' list"]
    by_id = {}
    for c in claims:
        cid = c.get("id")
        if not cid or cid in by_id:
            out.append(f"claim id missing or duplicate: {cid!r}")
            continue
        by_id[cid] = c
        if c.get("category") not in CATEGORIES:
            out.append(f"{cid}: category must be one of {sorted(CATEGORIES)}")
    # ids a decision rests on: these owe justification, the rest owe only enumeration
    load_bearing = set()
    for d in data.get("decisions") or []:
        load_bearing.update(d.get("depends_on") or [])
    grounded: dict = {}

    def is_grounded(cid: str, stack: tuple) -> bool:
        if cid in grounded:
            return grounded[cid]
        c = by_id.get(cid)
        if c is None:
            return False
        cat = c.get("category")
        if cat == "observation":
            g = check_observation(c, base, out)
        elif cat == "example-attribute":
            g = check_example_attribute(c, base, out, cid in load_bearing)
        elif cat == "prior":
            g = False
        elif cat == "inference":
            src = c.get("from") or []
            if not src:
                out.append(f"{cid}: inference without 'from'")
                g = False
            elif cid in stack:
                out.append(f"{cid}: inference cycle via {' -> '.join(stack + (cid,))}")
                g = False
            else:
                g = True
                for s in src:
                    if s not in by_id:
                        out.append(f"{cid}: from unknown claim {s!r}")
                        g = False
                    elif not is_grounded(s, stack + (cid,)):
                        g = False
        else:
            g = False
        grounded[cid] = g
        return g

    for cid in by_id:
        is_grounded(cid, ())
    sources = {}
    for c in claims:
        if c.get("category") == "example-attribute" and (c.get("source") or "").strip():
            sources.setdefault(c["source"].strip(), []).append(c["id"])
    for d in data.get("decisions") or []:
        did = d.get("id", "?")
        deps = d.get("depends_on") or []
        for src in d.get("cites") or []:
            if not sources.get(src):
                out.append(f"decision {did}: cites source {src!r} with no enumerated attributes")
        if not deps:
            out.append(f"decision {did}: no depends_on")
        for s in deps:
            if s not in by_id:
                out.append(f"decision {did}: depends on unknown claim {s!r}")
            elif not grounded.get(s):
                out.append(f"decision {did}: depends on ungrounded claim {s} ({by_id[s].get('category')})")
    if prose is not None:
        for sent in re.split(r"(?<=[.!?])\s+", prose):
            if not sent.strip():
                continue
            if re.search(QUANTIFIERS, sent, re.I) or re.search(PROCESS, sent):
                tags = TAG.findall(sent)
                if not tags:
                    out.append(f"prose: untagged sentence: {sent.strip()[:120]!r}")
                for t in tags:
                    if not grounded.get(t):
                        out.append(f"prose: [{t}] is not grounded: {sent.strip()[:120]!r}")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Gate a deliverable on claim provenance.")
    ap.add_argument("claims", help="JSON file with claims and decisions")
    ap.add_argument("--prose", help="text or markdown file to scan for untagged assertions")
    ap.add_argument("--base", help="directory anchors are relative to (default: claims file dir)")
    ap.add_argument("--max-seconds", type=float, default=120)
    ap.add_argument("--watchdog-probe", type=float, default=0)
    a = ap.parse_args()
    _arm_watchdog(a.max_seconds, a.watchdog_probe)
    if not os.path.isfile(a.claims):
        print(f"error: no such file {a.claims}", file=sys.stderr)
        return 2
    try:
        data = _read_json_strict(a.claims)
    except ValueError as e:
        print(f"error: invalid JSON: {e}", file=sys.stderr)
        return 2
    try:
        json.dumps(data, ensure_ascii=False).encode("utf-8")
    except UnicodeEncodeError:
        print(f"error: {a.claims} holds a lone surrogate that is not valid Unicode",
              file=sys.stderr)
        return 2
    if not isinstance(data, dict):
        print("error: claims root must be a JSON object", file=sys.stderr)
        return 2
    claims_field = data.get("claims")
    if "claims" in data and (
            not isinstance(claims_field, list)
            or any(not isinstance(c, dict) for c in claims_field)):
        print("error: 'claims' must be a list of JSON objects", file=sys.stderr)
        return 2
    decisions_field = data.get("decisions")
    if "decisions" in data and (
            not isinstance(decisions_field, list)
            or any(not isinstance(d, dict) for d in decisions_field)):
        print("error: 'decisions' must be a list of JSON objects", file=sys.stderr)
        return 2
    schema_error = _schema_error(claims_field, decisions_field)
    if schema_error is not None:
        print(schema_error, file=sys.stderr)
        return 2
    base = a.base or os.path.dirname(os.path.abspath(a.claims))
    prose = None
    if a.prose:
        if not os.path.isfile(a.prose):
            print(f"error: no such file {a.prose}", file=sys.stderr)
            return 2
        prose = _read(a.prose)
    violations = run(data, base, prose)
    if violations:
        print("GATE FAILED")
        for v in violations:
            print(" -", v)
        return 1
    print("GATE PASSED: every decision rests on grounded claims")
    return 0


if __name__ == "__main__":
    sys.exit(main())
