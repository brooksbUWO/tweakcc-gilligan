#!/usr/bin/env python3
"""Phase 12 authoring gate: the G2 bar for one authored store revision.

`ste_gate.py` proves STE compliance only. This gate proves nine more items
per row of an authored revision: placeholder parity (STE-04), the splice
frame (D-22), the code exemption, the carry-forward of every un-nerf point
(D-06, D-25), the glossary (D-02), the commit-header rule (STE-02), and twin
consistency. Two revision-wide items, rowset (STE-01) and per-prompt fit
(D-01), run only with no `--files` scope.

See <gate_contract> of .planning/phases/12-ste-clean-authoring/12-01-PLAN.md
for the full contract text. This module implements it item for item.

Exit codes: 0 every item passes, 1 one or more items fail, 2 usage or input
error found before any item runs, 3 the watchdog fired.

Python 3 standard library only, plus three imports: `strip_frontmatter` from
ste_gate.py, `require_same_placeholders` from schema.py, and
`_read_json_strict` / `_arm_watchdog` from check_claims.py. The gate writes
no file.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
_ANCHORED_CLAIMS = _HERE.parent / "anchored-claims"
if str(_ANCHORED_CLAIMS) not in sys.path:
    sys.path.insert(0, str(_ANCHORED_CLAIMS))

from ste_gate import strip_frontmatter  # noqa: E402
from schema import require_same_placeholders, SchemaError  # noqa: E402
from check_claims import _read_json_strict, _arm_watchdog  # noqa: E402


class GateUsageError(ValueError):
    """A usage or input error found before any gate item runs (exit 2)."""


# --- text-part helpers (code parts vs. prose) --------------------------------

_FENCE = re.compile(r"```.*?```", re.DOTALL)
_INLINE_CODE = re.compile(r"`[^`\n]+`")
_PLACEHOLDER_UNIT = re.compile(r"(?:</?)?(?:\$\{[^}]*\})+>?")
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")
_HEAD_MARKER = re.compile(r"^(?:[^\w\s]+|\d+\.)[ \t]*")
_TAIL_MARKER_CHARS = (":", ",", "(", "```")
_HEADER_LINE = re.compile(r"^[a-z]+(\([^()\s]*\))?!?: \S")
_LIST_MARKER = re.compile(r"^(?:[-*]|\d+\.) +")
_STOP_WORDS = frozenset(("the and for with that this from into over than then are was were been "
                         + "its not only each every any you your our can must does did has have had "
                         + "but nor yet all also just very they them their there here when what "
                         + "which while who whom whose why how out off per via").split())


def text_units(text: str) -> list[str]:
    """Split text into units. A unit ends at the sentence pattern and at each
    line break. The gate removes a leading list marker from each unit. A list
    marker is a dash, an asterisk, or a number with a period, and a space
    follows it. The gate drops an empty unit."""
    units = []
    for line in text.splitlines():
        for part in _SENTENCE_SPLIT.split(line):
            part = _LIST_MARKER.sub("", part.strip())
            if part:
                units.append(part)
    return units


def unit_key(unit: str) -> str:
    """The compare form of a unit. Whitespace runs become one space, and the
    letters become lowercase."""
    return " ".join(unit.split()).lower()


def content_forms(text: str) -> set:
    """The first 5 letters of each content word of text. A content word is a
    lowercase run of 3 or more letters that is not a stop word."""
    words = re.findall(r"[a-z]+", text.lower())
    return {w[:5] for w in words if len(w) >= 3 and w not in _STOP_WORDS}


def fenced_blocks(body: str) -> list[str]:
    """Return the inner text of each fenced block (without the backticks)."""
    out = []
    for m in _FENCE.finditer(body):
        inner = m.group(0)
        inner = inner[3:-3] if len(inner) >= 6 else ""
        out.append(inner)
    return out


def inline_code_spans(body: str, *, min_words: int = 1) -> list[str]:
    """Return the inner text of each inline code span outside a fence, with
    at least min_words words."""
    prose_only = _FENCE.sub(lambda m: " " * len(m.group(0)), body)
    out = []
    for m in _INLINE_CODE.finditer(prose_only):
        inner = m.group(0)[1:-1]
        if len(inner.split()) >= min_words:
            out.append(inner)
    return out


def strip_code_parts(body: str) -> str:
    """The prose of a body: the frontmatter block and code parts replaced by
    one space each. The glossary and header items read only model-facing
    prose, never the stock frontmatter that item_frame holds byte-identical
    (name/description/ccVersion/variables can restate a glossary phrase,
    for example a prompt titled with the exact stock line a rewrite must
    drop)."""
    residue = strip_frontmatter(body)
    residue = _FENCE.sub(" ", residue)
    residue = _INLINE_CODE.sub(" ", residue)
    return residue


def ordered_placeholder_units(text: str) -> list[str]:
    """Placeholder units: touching ${...} runs and their angle-bracket
    markup form one unit, per <gate_contract> item 1."""
    return [m.group(0) for m in _PLACEHOLDER_UNIT.finditer(text)]


def phrase_pattern(phrase: str) -> re.Pattern:
    """A case-insensitive phrase match. A whitespace run in the phrase
    matches any whitespace run. A word char or hyphen must not sit right
    before the match; for a phrase ending in a word char, a word char or
    hyphen must not sit right after."""
    parts = [re.escape(p) for p in re.split(r"\s+", phrase.strip())]
    core = r"\s+".join(parts)
    ends_word = bool(re.search(r"[A-Za-z0-9_]$", phrase.strip()))
    lookbehind = r"(?<![A-Za-z0-9_-])"
    lookahead = r"(?![A-Za-z0-9_-])" if ends_word else ""
    return re.compile(lookbehind + core + lookahead, re.IGNORECASE)


def phrase_search(phrase: str, text: str):
    return phrase_pattern(phrase).search(text)


# --- loaders -------------------------------------------------------------


def load_glossary(path: Path) -> dict:
    """{"schema": 1, "terms": {<id>: {"text", "means", "variants"}}}. Usage
    errors: no term, an empty text, a duplicate text, a variant that is not
    a non-empty string, or a variant that matches inside any canonical
    text."""
    try:
        data = _read_json_strict(str(path))
    except ValueError as e:
        raise GateUsageError(f"cannot read glossary {path}: {e}")
    if "terms" not in data:
        raise GateUsageError(f"{path}: missing 'terms'")
    terms = data.get("terms")
    if not isinstance(terms, dict):
        raise GateUsageError(f"{path}: 'terms' must be an object")
    seen_text = {}
    for term_id, entry in terms.items():
        if not isinstance(entry, dict):
            raise GateUsageError(f"{path}: term {term_id!r} must be an object")
        text = entry.get("text")
        if not isinstance(text, str) or not text.strip():
            raise GateUsageError(f"{path}: term {term_id!r} has no non-empty 'text'")
        if text in seen_text:
            raise GateUsageError(
                f"{path}: duplicate term text {text!r} in {term_id!r} and {seen_text[text]!r}")
        seen_text[text] = term_id
        variants = entry.get("variants", [])
        if not isinstance(variants, list):
            raise GateUsageError(f"{path}: term {term_id!r} 'variants' must be a list")
        for v in variants:
            if not isinstance(v, str) or not v.strip():
                raise GateUsageError(
                    f"{path}: term {term_id!r} has a variant that is not a non-empty string")
    for term_id, entry in terms.items():
        for v in entry.get("variants", []):
            for other_id, other in terms.items():
                if phrase_search(v, other["text"]):
                    raise GateUsageError(
                        f"{path}: variant {v!r} of {term_id!r} matches inside "
                        f"canonical text of {other_id!r}")
    return data


def load_contract(path: Path, before_dir: Path) -> dict:
    """{"schema": 1, "required": {row: [term_id]}, "ordered": {...},
    "forbidden": {...}, "header_statement": {"rows", "term", "mentions"}}.
    A row name absent from prompts/before/ is a usage error, and so is an
    unknown term id. Term-id validity is checked by the caller, which has
    the glossary in hand."""
    try:
        data = _read_json_strict(str(path))
    except ValueError as e:
        raise GateUsageError(f"cannot read contract {path}: {e}")
    for key in ("required", "ordered", "forbidden"):
        section = data.get(key, {})
        if not isinstance(section, dict):
            raise GateUsageError(f"{path}: {key!r} must be an object")
        for row in section:
            if not (before_dir / row).is_file():
                raise GateUsageError(f"{path}: {key}.{row!r} is not a row in prompts/before/")
    header_statement = data.get("header_statement", {})
    if not isinstance(header_statement, dict):
        raise GateUsageError(f"{path}: 'header_statement' must be an object")
    for row in header_statement.get("rows", []) or []:
        if not (before_dir / row).is_file():
            raise GateUsageError(
                f"{path}: header_statement.rows contains {row!r}, not a row in prompts/before/")
    return data


def validate_contract_terms(contract: dict, glossary_terms: dict, path: Path) -> None:
    for key in ("required", "ordered"):
        for row, term_ids in (contract.get(key, {}) or {}).items():
            for tid in term_ids:
                if tid not in glossary_terms:
                    raise GateUsageError(f"{path}: {key}.{row!r} names unknown term id {tid!r}")
    hs = contract.get("header_statement", {}) or {}
    term = hs.get("term")
    if term is not None and term not in glossary_terms:
        raise GateUsageError(f"{path}: header_statement.term names unknown term id {term!r}")


# --- rule entries and points (carry-forward, D-06/D-25) --------------------


def load_all_rule_files(rules_dir: Path) -> dict:
    """{stem: parsed rule JSON} for every *.json under rules_dir."""
    out = {}
    for p in sorted(rules_dir.glob("*.json")):
        try:
            out[p.stem] = _read_json_strict(str(p))
        except ValueError as e:
            raise GateUsageError(f"cannot read rule file {p}: {e}")
    return out


def rule_entries_for_row(row: str, stock_body: str, rules_dir: Path, all_rules: dict) -> list:
    """Every entry of <rules>/<stem>.json, plus every entry of any rule file
    whose stock lines (joined by newline, stripped) occur in stock_body
    (D-25). Returns a list of (rule_id, entry_index, entry_dict)."""
    stem = row[:-3] if row.endswith(".md") else row
    out = []
    own = all_rules.get(stem)
    if own is not None:
        rid = own.get("id", stem)
        for i, entry in enumerate(own.get("rules", [])):
            out.append((rid, i, entry))
    own_ids = {rid for rid, _, _ in out}
    for other_stem, other in sorted(all_rules.items()):
        if other_stem == stem:
            continue
        rid = other.get("id", other_stem)
        if rid in own_ids:
            continue
        for i, entry in enumerate(other.get("rules", [])):
            stock_lines = entry.get("stock", [])
            joined = "\n".join(stock_lines).strip()
            if joined and joined in stock_body:
                out.append((rid, i, entry))
    return out


def points_for_row(entries: list) -> list:
    """Derive the added/removed points of a row's rule entries, per
    <gate_contract> item 4. Returns a list of dicts:
    {"id", "kind" ("+"/"-"), "line", "entry_index", "rule_id"}."""
    points = []
    for rid, entry_index, entry in entries:
        stock_lines = [ln for ln in entry.get("stock", [])]
        unnerf_lines = [ln for ln in entry.get("unnerf", [])]
        stock_nonblank = {ln for ln in stock_lines if ln.strip()}
        unnerf_nonblank = {ln for ln in unnerf_lines if ln.strip()}
        for line_no, line in enumerate(unnerf_lines):
            if line.strip() and line not in stock_nonblank:
                points.append({
                    "id": f"{rid}#{entry_index}+{line_no}",
                    "kind": "+",
                    "line": line,
                    "entry_index": entry_index,
                    "rule_id": rid,
                })
        for line_no, line in enumerate(stock_lines):
            if line.strip() and line not in unnerf_nonblank:
                points.append({
                    "id": f"{rid}#{entry_index}-{line_no}",
                    "kind": "-",
                    "line": line,
                    "entry_index": entry_index,
                    "rule_id": rid,
                })
    return points


# --- row items ---------------------------------------------------------


def item_placeholders(row: str, stock_body: str, after_body: str) -> tuple[bool, str]:
    try:
        require_same_placeholders(stock_body, after_body)
    except SchemaError as e:
        return False, str(e)
    before_units = ordered_placeholder_units(stock_body)
    after_units = ordered_placeholder_units(after_body)
    if before_units != after_units:
        return False, f"placeholder unit sequence changed: before={before_units} after={after_units}"
    return True, f"{len(after_units)} placeholder unit(s) unchanged"


def item_frame(row: str, stock_body: str, after_body: str) -> tuple[bool, str]:
    stock_fm_end = len(stock_body) - len(strip_frontmatter(stock_body))
    after_fm_end = len(after_body) - len(strip_frontmatter(after_body))
    stock_fm = stock_body[:stock_fm_end]
    after_fm = after_body[:after_fm_end]
    if stock_fm != after_fm:
        return False, "frontmatter block changed"

    stock_body_text = stock_body[stock_fm_end:]
    after_body_text = after_body[after_fm_end:]

    stock_lead = re.match(r"^\s*", stock_body_text).group(0)
    after_lead = re.match(r"^\s*", after_body_text).group(0)
    if stock_lead != after_lead:
        return False, "leading whitespace run changed"
    stock_trail = re.search(r"\s*$", stock_body_text).group(0)
    after_trail = re.search(r"\s*$", after_body_text).group(0)
    if stock_trail != after_trail:
        return False, "trailing whitespace run changed"

    stock_core = stock_body_text.strip("\n\r\t ") if stock_lead or stock_trail else stock_body_text
    # Use the text after leading whitespace for marker detection, per contract.
    stock_after_lead = stock_body_text[len(stock_lead):]
    after_after_lead = after_body_text[len(after_lead):]

    head_m = _HEAD_MARKER.match(stock_after_lead)
    head_detail = "no head marker"
    if head_m:
        marker = head_m.group(0)
        if not after_after_lead.startswith(marker):
            return False, f"head marker {marker!r} not kept at the same place"
        head_detail = f"head marker {marker!r} kept"

    stock_no_trail = stock_body_text[:len(stock_body_text) - len(stock_trail)] if stock_trail else stock_body_text
    after_no_trail = after_body_text[:len(after_body_text) - len(after_trail)] if after_trail else after_body_text
    tail_detail = "no tail marker"
    for marker in _TAIL_MARKER_CHARS:
        if stock_no_trail.endswith(marker):
            if not after_no_trail.endswith(marker):
                return False, f"tail marker {marker!r} not kept at the end"
            tail_detail = f"tail marker {marker!r} kept"
            break

    return True, f"{head_detail}; {tail_detail}"


def item_code(row: str, stock_body: str, after_body: str, old_unnerf_text: str) -> tuple[bool, str]:
    allowed_sources = stock_body + "\n" + old_unnerf_text
    fences = fenced_blocks(after_body)
    for fence in fences:
        if fence.strip() and fence not in allowed_sources:
            return False, f"new fenced block not found in stock or old un-nerf text: {fence[:80]!r}"
    spans = inline_code_spans(after_body, min_words=4)
    for span in spans:
        if span not in allowed_sources:
            return False, f"new inline code span (4+ words) not found in stock or old un-nerf text: {span!r}"
    return True, f"{len(fences)} fenced block(s), {len(spans)} long inline span(s) all traced to stock or old un-nerf text"


def _is_quote_list(new) -> bool:
    return (isinstance(new, list) and bool(new)
            and all(isinstance(q, str) and q for q in new))


def load_ledger(revision_dir: Path, row: str) -> dict | None:
    """Read and check the ledger of one row. No ledger file gives None.
    A shape error is an input error with the ledger path in the message.
    The shape errors are these: the ledger is not an object, "points" is
    not a list, or a point is not an object. An "id" that is not a string
    or a duplicate id is also a shape error. An "old" that is not a string
    is a shape error too. The last shape error is a "new" that is not one
    non-empty string or a non-empty list of non-empty strings."""
    stem = row[:-3] if row.endswith(".md") else row
    ledger_path = revision_dir / "carry-forward" / f"{stem}.json"
    if not ledger_path.is_file():
        return None
    try:
        data = _read_json_strict(str(ledger_path))
    except ValueError as e:
        raise GateUsageError(f"cannot read ledger {ledger_path}: {e}")
    if not isinstance(data, dict):
        raise GateUsageError(f"{ledger_path}: the ledger must be an object")
    points = data.get("points", [])
    if not isinstance(points, list):
        raise GateUsageError(f"{ledger_path}: 'points' must be a list")
    seen = set()
    for it in points:
        if not isinstance(it, dict):
            raise GateUsageError(f"{ledger_path}: point {it!r} is not an object")
        iid = it.get("id")
        if not isinstance(iid, str):
            raise GateUsageError(f"{ledger_path}: point has no string 'id': {it!r}")
        if iid in seen:
            raise GateUsageError(f"{ledger_path}: duplicate point id {iid!r}")
        seen.add(iid)
        if not isinstance(it.get("old"), str):
            raise GateUsageError(f"{ledger_path}: point {iid!r} has no string 'old'")
        new = it.get("new")
        if not ((isinstance(new, str) and new) or _is_quote_list(new)):
            raise GateUsageError(
                f"{ledger_path}: point {iid!r} 'new' must be a non-empty string "
                f"or a non-empty list of non-empty strings")
    return data


def item_carry_forward(row: str, after_body: str, after_stripped: str, points: list,
                       ledger: dict | None) -> tuple[bool, str]:
    """Prove each added point in the stripped body, and prove that each
    removed point is gone. load_ledger checks the shape of the ledger first.

    For an added point to pass, these rules must be true. Each quote of the
    point has 4 or more words. With whitespace runs as one space, each
    quote occurs in the stripped body. The quotes hold a match for 60
    percent or more of the old content words, and the percent rounds down.
    For a match, the first 5 letters of two content words must be equal.

    If its line is a whole line of the rewrite, a removed point fails. If a
    unit of 6 or more words of its line is a unit of the stripped body, the
    removed point also fails. The unit compare ignores letter case and
    whitespace runs. If the same unit is also a unit of an added line of
    the same rule entry, that unit is exempt from this unit test. The un-nerf
    text keeps such a unit on purpose. An added line of a different entry
    does not make a unit exempt."""
    added = [p for p in points if p["kind"] == "+"]
    removed = [p for p in points if p["kind"] == "-"]

    by_id = {it["id"]: it for it in (ledger or {}).get("points", [])}
    fm_lines = after_body[:len(after_body) - len(after_stripped)].count("\n")

    known_ids = {p["id"] for p in added}
    lines = []
    ok = True
    for iid in by_id:
        if iid not in known_ids:
            ok = False
            lines.append(f"unknown ledger id: {iid!r}")

    for p in added:
        pid = p["id"]
        it = by_id.get(pid)
        if it is None:
            ok = False
            lines.append(f"    {pid}: absent from ledger")
            continue
        if it["old"] != p["line"]:
            ok = False
            lines.append(f"    {pid}: ledger 'old' does not match the point line")
            continue
        quotes = [it["new"]] if isinstance(it["new"], str) else it["new"]
        point_ok = True
        line_no = None
        for quote in quotes:
            words = quote.split()
            if len(words) < 4:
                point_ok = False
                lines.append(f"    {pid}: quote under 4 words: {quote!r}")
                continue
            m = re.search(r"\s+".join(map(re.escape, words)), after_stripped)
            if m is None:
                point_ok = False
                lines.append(f"    {pid}: quote not found in the body: {' '.join(words)!r}")
            elif line_no is None:
                line_no = fm_lines + after_stripped[:m.start()].count("\n") + 1
        old_forms = content_forms(it["old"])
        new_forms = content_forms(" ".join(quotes))
        matched = len(old_forms & new_forms)
        percent = matched * 100 // len(old_forms) if old_forms else 100
        if percent < 60:
            point_ok = False
            lines.append(f"    {pid}: coverage {percent} percent of the old content words, "
                         f"{matched} of {len(old_forms)}, under 60 percent")
        if point_ok:
            lines.append(f"    {pid}: kept at line {line_no}")
        else:
            ok = False

    body_units = {unit_key(u) for u in text_units(after_stripped)}
    # The units of the added lines, one set for each rule entry.
    kept_units: dict = {}
    for p in added:
        entry = kept_units.setdefault((p["rule_id"], p["entry_index"]), set())
        entry.update(unit_key(u) for u in text_units(p["line"]))
    for p in removed:
        stripped_point = p["line"].strip()
        exempt = kept_units.get((p["rule_id"], p["entry_index"]), set())
        found = [u for u in text_units(p["line"])
                 if len(u.split()) >= 6 and unit_key(u) in body_units
                 and unit_key(u) not in exempt]
        if any(body_line.strip() == stripped_point for body_line in after_body.splitlines()):
            ok = False
            lines.append(f"    {p['id']}: removed stock line still present as a whole line")
        elif found:
            ok = False
            lines.append(f"    {p['id']}: removed unit still present: {found[0]!r}")
        else:
            lines.append(f"    {p['id']}: absent")

    detail = f"{len(added)} added point(s), {len(removed)} removed point(s)\n" + "\n".join(lines)
    return ok, detail


def item_glossary(row: str, after_body: str, glossary: dict, contract: dict) -> tuple[bool, str]:
    prose = strip_code_parts(after_body)
    terms = glossary.get("terms", {})

    for term_id, entry in terms.items():
        for variant in entry.get("variants", []):
            m = phrase_search(variant, prose)
            if m:
                return False, f"forbidden variant {variant!r} of term {term_id!r} found: {m.group(0)!r}"

    required = (contract.get("required", {}) or {}).get(row, [])
    for tid in required:
        text = terms[tid]["text"]
        if not phrase_search(text, prose):
            return False, f"required term {tid!r} ({text!r}) not found"

    ordered = (contract.get("ordered", {}) or {}).get(row, [])
    last_pos = -1
    for tid in ordered:
        text = terms[tid]["text"]
        m = phrase_search(text, prose)
        if not m:
            return False, f"ordered term {tid!r} ({text!r}) not found"
        if m.start() < last_pos:
            return False, f"ordered term {tid!r} ({text!r}) is out of order"
        last_pos = m.start()

    forbidden = (contract.get("forbidden", {}) or {}).get(row, [])
    for phrase in forbidden:
        m = phrase_search(phrase, prose)
        if m:
            return False, f"forbidden phrase {phrase!r} found: {m.group(0)!r}"

    return True, f"{len(required)} required, {len(ordered)} ordered, {len(forbidden)} forbidden term(s) all satisfied"


def item_header(row: str, stock_body: str, after_body: str, contract: dict,
                 glossary_terms: dict) -> tuple[bool, str]:
    def header_lines(text: str) -> list[str]:
        out = []
        for line in text.splitlines():
            candidate = line.lstrip()
            candidate = re.sub(r"^(?:[-*]|\d+\.)\s*", "", candidate)
            candidate = candidate.lstrip("`'\" ")
            if _HEADER_LINE.match(candidate):
                out.append(line)
        return out

    stock_headers = header_lines(stock_body)
    after_headers = header_lines(after_body)
    if stock_headers != after_headers:
        return False, f"header lines changed: before={stock_headers!r} after={after_headers!r}"

    hs = contract.get("header_statement", {}) or {}
    rows = hs.get("rows", []) or []
    if row in rows:
        term_id = hs.get("term")
        mentions = hs.get("mentions", []) or []
        canonical = glossary_terms[term_id]["text"] if term_id else None
        prose = strip_code_parts(after_body)
        for sent in _SENTENCE_SPLIT.split(prose):
            if not sent.strip():
                continue
            mentioned = any(phrase_search(m, sent) for m in mentions)
            if mentioned and canonical and not phrase_search(canonical, sent):
                return False, (f"sentence mentions the header without the term "
                                f"{term_id!r}: {sent.strip()[:120]!r}")

    return True, f"{len(stock_headers)} header line(s) in stock, {len(after_headers)} in rewrite"


def item_twins(row: str, stock_body: str, after_body: str, twin_row: str | None,
                twin_after_body: str | None, in_scope: bool) -> tuple[bool, str]:
    """after_body and twin_after_body are the stripped rewrites. The
    frontmatter of each row stays its own, so the item compares the bodies
    only. If the stripped rewrites are byte-identical, the twin pair passes."""
    if twin_row is None:
        return True, "no twin"
    if not in_scope:
        return False, f"twin {twin_row!r} is not in scope"
    if after_body != twin_after_body:
        return False, f"twin {twin_row!r} has a different rewrite body"
    return True, f"twin {twin_row!r} has a byte-identical rewrite"


def item_rowset(before_names: set, after_names: set, queue_names: set) -> tuple[bool, str]:
    if not before_names or not after_names or not queue_names:
        return False, "an empty set: before={}, after={}, queue={}".format(
            len(before_names), len(after_names), len(queue_names))
    if before_names != after_names or before_names != queue_names:
        return False, (f"name sets differ: before={sorted(before_names)} "
                        f"after={sorted(after_names)} queue={sorted(queue_names)}")
    return True, f"{len(before_names)} row(s) match across before/after/queue"


def item_per_prompt_fit(rows_prose: dict, glossary_terms: dict, twin_groups: dict) -> tuple[bool, str]:
    """rows_prose: {row: prose}. twin_groups: {row: representative_row}.
    A twin pair counts as one row.

    The item counts one unit, not one term. text_units splits the prose at
    the sentence pattern and at each line break, and it removes a list
    marker. To count, a unit must have 8 or more words and a match of a
    canonical glossary text. For the comparison, runs of whitespace become
    one space, the ends lose their whitespace, and letter case is ignored.
    When one such unit is in 3 or more rows, the item fails."""
    counts: dict[str, set] = {}
    for row, prose in rows_prose.items():
        rep = twin_groups.get(row, row)
        for sent in text_units(prose):
            if len(sent.split()) < 8:
                continue
            if any(phrase_search(e["text"], sent) for e in glossary_terms.values()):
                counts.setdefault(unit_key(sent), set()).add(rep)
    failures = sorted((key, sorted(reps)) for key, reps in counts.items() if len(reps) >= 3)
    if failures:
        parts = [f"{key[:80]!r} in {reps}" for key, reps in failures]
        return False, "term sentence(s) repeated across 3+ rows: " + "; ".join(parts)
    return True, "no term sentence repeats across 3 or more rows"


# --- main --------------------------------------------------------------


ROW_ITEM_NAMES = (
    "placeholders", "frame", "code", "carry-forward", "glossary", "header", "twins",
)
REVISION_ITEM_NAMES = ("rowset", "per-prompt-fit")


def _parse_files(files_arg: str | None, before_names: set) -> list[str] | None:
    if files_arg is None:
        return None
    names = [f for f in files_arg.split(",") if f]
    for name in names:
        if "/" in name or "\\" in name:
            raise GateUsageError(f"--files entry {name!r} has a path separator")
        if name not in before_names:
            raise GateUsageError(f"--files entry {name!r} is not a row in prompts/before/")
    return names


def _find_twins(before: dict) -> dict:
    """before: {row: stock_body}. Returns {row: twin_row} for every row that
    has exactly one sibling with a byte-identical stripped stock body. The
    frontmatter block is removed first, so rows with other frontmatter can
    be twins. A group of 3+ identical stripped stock bodies pairs each with
    the first other member found."""
    by_body: dict[str, list[str]] = {}
    for row, body in before.items():
        by_body.setdefault(strip_frontmatter(body), []).append(row)
    twins = {}
    for rows in by_body.values():
        if len(rows) < 2:
            continue
        rows = sorted(rows)
        for i, row in enumerate(rows):
            other = rows[(i + 1) % len(rows)]
            twins[row] = other
    return twins


def run_gate(revision_dir: Path, rules_dir: Path, glossary_path: Path,
             files_arg: str | None, list_points: bool) -> tuple[int, list[str]]:
    lines: list[str] = []

    before_dir = revision_dir / "prompts" / "before"
    after_dir = revision_dir / "prompts" / "after"
    contract_path = revision_dir / "contract.json"
    batch_path = revision_dir / "batch.json"

    if not before_dir.is_dir():
        raise GateUsageError(f"no prompts/before directory under {revision_dir}")
    if not after_dir.is_dir():
        raise GateUsageError(f"no prompts/after directory under {revision_dir}")
    if not contract_path.is_file():
        raise GateUsageError(f"missing {contract_path}")
    if not batch_path.is_file():
        raise GateUsageError(f"missing {batch_path}")

    before_names = {p.name for p in before_dir.glob("*.md")}
    after_names = {p.name for p in after_dir.glob("*.md")}

    glossary = load_glossary(glossary_path)
    glossary_terms = glossary["terms"]
    contract = load_contract(contract_path, before_dir)
    validate_contract_terms(contract, glossary_terms, contract_path)

    try:
        batch_data = _read_json_strict(str(batch_path))
    except ValueError as e:
        raise GateUsageError(f"cannot read {batch_path}: {e}")
    queue = batch_data.get("queue", [])
    if not isinstance(queue, list):
        raise GateUsageError(f"{batch_path}: 'queue' must be a list")
    queue_names = {q.get("file") for q in queue if isinstance(q, dict)}

    scope_names = _parse_files(files_arg, before_names)
    scope = sorted(scope_names) if scope_names is not None else sorted(before_names & after_names)

    all_rules = load_all_rule_files(rules_dir)

    bodies_before = {r: (before_dir / r).read_text(encoding="utf-8")
                      for r in before_names if (before_dir / r).is_file()}
    bodies_after = {r: (after_dir / r).read_text(encoding="utf-8")
                     for r in after_names if (after_dir / r).is_file()}

    twins = _find_twins(bodies_before)

    if list_points:
        for row in scope:
            if row not in bodies_before:
                continue
            entries = rule_entries_for_row(row, bodies_before[row], rules_dir, all_rules)
            for p in points_for_row(entries):
                lines.append(f"{row} {p['id']} {p['kind']} {p['line']}")
        return 0, lines

    # Check the shape of every ledger in scope before any item runs.
    ledgers = {row: load_ledger(revision_dir, row) for row in scope}

    passed = 0
    failed = 0

    rows_prose = {}
    stripped_after = {r: strip_frontmatter(b) for r, b in bodies_after.items()}

    for row in scope:
        if row not in bodies_before:
            raise GateUsageError(f"row {row!r} has no stock body under prompts/before/")
        if row not in bodies_after:
            for item in ROW_ITEM_NAMES:
                lines.append(f"FAIL {item} {row}: no rewrite found under prompts/after/")
                failed += 1
            continue

        stock_body = bodies_before[row]
        after_body = bodies_after[row]
        rows_prose[row] = strip_code_parts(after_body)

        entries = rule_entries_for_row(row, stock_body, rules_dir, all_rules)
        points = points_for_row(entries)
        old_unnerf_text = "\n".join(
            "\n".join(entry.get("unnerf", [])) for _, _, entry in entries
        )
        results = []
        results.append(("placeholders", item_placeholders(row, stock_body, after_body)))
        results.append(("frame", item_frame(row, stock_body, after_body)))
        results.append(("code", item_code(row, stock_body, after_body, old_unnerf_text)))
        results.append(("carry-forward", item_carry_forward(
            row, after_body, stripped_after[row], points, ledgers[row])))
        results.append(("glossary", item_glossary(row, after_body, glossary, contract)))
        results.append(("header", item_header(row, stock_body, after_body, contract, glossary_terms)))

        twin_row = twins.get(row)
        twin_after_body = stripped_after.get(twin_row) if twin_row else None
        twin_in_scope = twin_row in scope if twin_row else True
        results.append(("twins", item_twins(row, stock_body, stripped_after[row], twin_row,
                                             twin_after_body, twin_in_scope)))

        for item, (ok, detail) in results:
            if ok:
                lines.append(f"PASS {item} {row}: {detail}")
                passed += 1
            else:
                lines.append(f"FAIL {item} {row}: {detail}")
                failed += 1

    revision_scope = files_arg is None
    if revision_scope:
        ok, detail = item_rowset(before_names, after_names, queue_names)
        if ok:
            lines.append(f"PASS rowset: {detail}")
            passed += 1
        else:
            lines.append(f"FAIL rowset: {detail}")
            failed += 1

        twin_groups = {}
        for row, twin_row in twins.items():
            rep = min(row, twin_row)
            twin_groups[row] = rep
        ok, detail = item_per_prompt_fit(rows_prose, glossary_terms, twin_groups)
        if ok:
            lines.append(f"PASS per-prompt-fit: {detail}")
            passed += 1
        else:
            lines.append(f"FAIL per-prompt-fit: {detail}")
            failed += 1

    n_rows = len(scope)
    lines.append(f"summary: {passed} pass, {failed} fail ({n_rows} row(s) in scope)")
    lines.append("GATE PASSED" if failed == 0 else "GATE FAILED")
    return (0 if failed == 0 else 1), lines


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                      formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--revision-dir", dest="revision_dir",
                        help="Revision directory holding prompts/before, prompts/after, "
                             "contract.json, batch.json, and carry-forward/")
    parser.add_argument("--rules-dir", dest="rules_dir",
                        help="Directory of unnerfcc-shaped rule JSON files")
    parser.add_argument("--glossary", dest="glossary",
                        help="Glossary JSON file")
    parser.add_argument("--files", dest="files", default=None,
                        help="Comma-separated list of bare filenames to scope the run to")
    parser.add_argument("--list-points", dest="list_points", action="store_true",
                        help="Print one line per point for each row in scope, then exit 0")
    parser.add_argument("--max-seconds", type=float, default=120.0, dest="max_seconds",
                        help="Hard wall-clock ceiling in seconds; exit 3 when it fires (default: 120)")
    parser.add_argument("--watchdog-probe", type=float, default=0.0, dest="watchdog_probe",
                        help="Diagnostic: idle this many seconds after arming the watchdog (default: 0)")
    args = parser.parse_args()
    _arm_watchdog(args.max_seconds, args.watchdog_probe)

    if not args.revision_dir:
        print("error: --revision-dir is required", file=sys.stderr)
        return 2
    if not args.rules_dir:
        print("error: --rules-dir is required", file=sys.stderr)
        return 2
    if not args.glossary:
        print("error: --glossary is required", file=sys.stderr)
        return 2

    revision_dir = Path(args.revision_dir)
    rules_dir = Path(args.rules_dir)
    glossary_path = Path(args.glossary)

    if not revision_dir.is_dir():
        print(f"error: revision dir does not exist: {revision_dir}", file=sys.stderr)
        return 2
    if not rules_dir.is_dir():
        print(f"error: rules dir does not exist: {rules_dir}", file=sys.stderr)
        return 2
    if not glossary_path.is_file():
        print(f"error: glossary file does not exist: {glossary_path}", file=sys.stderr)
        return 2

    try:
        code, lines = run_gate(revision_dir, rules_dir, glossary_path,
                                args.files, args.list_points)
    except GateUsageError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    for line in lines:
        print(line)
    return code


if __name__ == "__main__":
    sys.exit(main())
