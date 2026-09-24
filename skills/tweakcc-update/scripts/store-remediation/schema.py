"""Canonical vocabularies and strict record validators for Phase 2 remediation.

This module owns the discrete values that must appear verbatim in every batch
(RESEARCH.md: canonical in-repo values), the ordered-placeholder guard copied
from the encoder's positional-slot contract (apply-unnerfs.py:1360-1396), and
the JSON-record validators. Validation is fail-closed: unknown fields, unknown
enum values, missing files, and malformed digests all raise loudly. Corpus and
reviewer text is treated as untrusted data and never executed.

Python 3 standard library only.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from hashing import is_valid_sha256

# --- Canonical discrete values (must appear verbatim; RESEARCH.md) -----------

DEFECT_TAGS: tuple[str, ...] = (
    "Over-constraint",
    "Register mismatch",
    "Token economy",
    "Contradictory rules",
    "Silent failure",
    "Process/artifact split",
)

OVERRIDE_DISPOSITIONS: tuple[str, ...] = (
    "retain",
    "merge",
    "supersede",
    "reject",
)

APPLY_STATES: tuple[str, ...] = (
    "applied",
    "skipped",
    "failed",
    "missing",
    "normalized",
)

CONFIDENCE_LABELS: tuple[str, ...] = ("high", "medium", "low")

# The six negative checks a `clean` result must complete, one per defect class.
SIX_CLASS_CHECKS: tuple[str, ...] = DEFECT_TAGS

# Positional placeholder pattern, identical to the encoder's slot guard so a
# Phase 2 draft cannot pass a placeholder sequence the encoder would reject.
_PLACEHOLDER = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)")


class SchemaError(ValueError):
    """A record violated the strict schema (unknown field/value or missing data)."""


def ordered_placeholders(text: str) -> list[str]:
    """Return the ordered list of ``${NAME}`` placeholder names in text."""
    return _PLACEHOLDER.findall(text)


def require_same_placeholders(before: str, after: str) -> None:
    """Raise unless before and after have the exact same ordered placeholders."""
    b = ordered_placeholders(before)
    a = ordered_placeholders(after)
    if b != a:
        raise SchemaError(
            f"placeholder sequence changed: before={b} after={a}"
        )


def _reject_unknown_fields(record: dict[str, Any], allowed: set[str], where: str) -> None:
    extra = set(record) - allowed
    if extra:
        raise SchemaError(f"{where}: unknown field(s): {sorted(extra)}")


def _require(record: dict[str, Any], field: str, where: str) -> Any:
    if field not in record:
        raise SchemaError(f"{where}: missing required field: {field}")
    return record[field]


def validate_class_checks(checks: Any, where: str) -> None:
    """Six per-class checks, one per defect class, each with result + reasoning."""
    if not isinstance(checks, list):
        raise SchemaError(f"{where}: class_checks must be a list")
    names = []
    for i, entry in enumerate(checks):
        if not isinstance(entry, dict):
            raise SchemaError(f"{where}: class_checks[{i}] must be an object")
        _reject_unknown_fields(entry, {"defect", "result", "reasoning"},
                               f"{where}.class_checks[{i}]")
        defect = _require(entry, "defect", f"{where}.class_checks[{i}]")
        if defect not in DEFECT_TAGS:
            raise SchemaError(
                f"{where}.class_checks[{i}]: unknown defect {defect!r}"
            )
        result = _require(entry, "result", f"{where}.class_checks[{i}]")
        if result not in ("negative", "positive"):
            raise SchemaError(
                f"{where}.class_checks[{i}]: result must be negative|positive"
            )
        reasoning = _require(entry, "reasoning", f"{where}.class_checks[{i}]")
        if not isinstance(reasoning, str) or not reasoning.strip():
            raise SchemaError(
                f"{where}.class_checks[{i}]: reasoning must be non-empty"
            )
        names.append(defect)
    if names != list(DEFECT_TAGS):
        raise SchemaError(
            f"{where}: class_checks must cover all six defects in order; got {names}"
        )


def derive_clean(class_checks: list[dict[str, Any]]) -> bool:
    """`clean` is true only when all six class checks are negative."""
    return all(c.get("result") == "negative" for c in class_checks)


def validate_hash_ref(ref: Any, where: str) -> None:
    if not isinstance(ref, dict):
        raise SchemaError(f"{where}: must be an object with path + sha256")
    _reject_unknown_fields(ref, {"path", "sha256"}, where)
    path = _require(ref, "path", where)
    if not isinstance(path, str) or not path:
        raise SchemaError(f"{where}.path: must be a non-empty string")
    digest = _require(ref, "sha256", where)
    if not is_valid_sha256(digest):
        raise SchemaError(f"{where}.sha256: not a 64-char lowercase hex digest")


def validate_prompt_record(record: dict[str, Any]) -> None:
    """Validate one per-prompt classification/draft record.

    A record is complete only when it carries the source filename, six class
    checks, a clean flag consistent with those checks, ordered placeholders, and
    the override disposition. Defective records additionally require complete
    before/after refs, a doctrine map, placeholder equality, a confidence label,
    and a valid disposition. Fail-closed on any deviation.
    """
    where = "record"
    allowed = {
        "filename", "class_checks", "clean", "defect_tags",
        "ordered_placeholders", "existing_override_disposition",
        "before", "after", "doctrine_map", "confidence", "gate_status",
    }
    _reject_unknown_fields(record, allowed, where)

    filename = _require(record, "filename", where)
    if not isinstance(filename, str) or ("/" in filename or "\\" in filename):
        raise SchemaError(f"{where}.filename: must be a bare filename, got {filename!r}")

    class_checks = _require(record, "class_checks", where)
    validate_class_checks(class_checks, where)
    clean = _require(record, "clean", where)
    if not isinstance(clean, bool):
        raise SchemaError(f"{where}.clean: must be a boolean")
    if clean != derive_clean(class_checks):
        raise SchemaError(
            f"{where}.clean: {clean} disagrees with the six class checks"
        )

    disposition = _require(record, "existing_override_disposition", where)
    if disposition not in OVERRIDE_DISPOSITIONS:
        raise SchemaError(
            f"{where}.existing_override_disposition: unknown {disposition!r}"
        )

    placeholders = _require(record, "ordered_placeholders", where)
    if not isinstance(placeholders, list) or not all(isinstance(p, str) for p in placeholders):
        raise SchemaError(f"{where}.ordered_placeholders: must be a list of strings")

    if clean:
        # A clean record must not claim defect tags.
        tags = record.get("defect_tags", [])
        if tags:
            raise SchemaError(f"{where}: clean record must have no defect_tags")
        return

    # Defective record path.
    tags = _require(record, "defect_tags", where)
    if not isinstance(tags, list) or not tags:
        raise SchemaError(f"{where}.defect_tags: defective record needs >=1 tag")
    for t in tags:
        if t not in DEFECT_TAGS:
            raise SchemaError(f"{where}.defect_tags: unknown tag {t!r}")

    before = _require(record, "before", where)
    validate_hash_ref(before, f"{where}.before")
    after = _require(record, "after", where)
    validate_hash_ref(after, f"{where}.after")

    doctrine_map = _require(record, "doctrine_map", where)
    if not isinstance(doctrine_map, list) or not doctrine_map:
        raise SchemaError(f"{where}.doctrine_map: defective record needs mappings")

    confidence = _require(record, "confidence", where)
    if confidence not in CONFIDENCE_LABELS:
        raise SchemaError(f"{where}.confidence: unknown label {confidence!r}")


@dataclass(frozen=True)
class Rule:
    """A structured Boolean rule record for the consistency engine."""

    rule_id: str
    trigger_vars: tuple[str, ...]
    required_behavior: str
    prohibited_behavior: str
    precedence: int
    exceptions: tuple[str, ...]


def validate_rule(record: dict[str, Any]) -> Rule:
    where = "rule"
    allowed = {
        "rule_id", "trigger_vars", "required_behavior",
        "prohibited_behavior", "precedence", "exceptions",
    }
    _reject_unknown_fields(record, allowed, where)
    rule_id = _require(record, "rule_id", where)
    if not isinstance(rule_id, str) or not rule_id:
        raise SchemaError(f"{where}.rule_id: non-empty string required")
    trigger = _require(record, "trigger_vars", where)
    if not isinstance(trigger, list):
        raise SchemaError(f"{where}.trigger_vars: must be a list")
    precedence = _require(record, "precedence", where)
    if not isinstance(precedence, int):
        raise SchemaError(f"{where}.precedence: must be an int")
    exceptions = record.get("exceptions", [])
    return Rule(
        rule_id=rule_id,
        trigger_vars=tuple(trigger),
        required_behavior=str(_require(record, "required_behavior", where)),
        prohibited_behavior=str(_require(record, "prohibited_behavior", where)),
        precedence=precedence,
        exceptions=tuple(exceptions),
    )
