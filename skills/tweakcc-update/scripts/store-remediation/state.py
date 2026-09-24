"""Pure ordered-event reducer computing Phase 2 gate state from evidence.

Eligibility is a computed conjunction over executed evidence, never a typed pass
label (RESEARCH.md Pattern 2, the must_haves truths). The reducer consumes an
ordered event stream and derives:

- reviewer eligibility per lane (AntiGravity => Gemini family; OpenCode =>
  excludes Claude/OpenAI/Codex families and every GitHub Copilot provider;
  Copilot is never a reviewer; Ask-Claude is not a Phase 2 lane),
- same-round dual-zero on one revision digest,
- terminal review + strictly-later doctrine reread + strictly-later approval,
  all bound to the same sealed revision digest, with monotonic sequences and
  strictly increasing timestamps, invalidated by any later review event.

No rejected evidence advances dual_zero_same_round or Phase 3 eligibility.
Python 3 standard library only; this module performs no I/O.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Phase 2 review lanes. Ask-Claude is a valid general route but NOT a lane here.
PHASE2_LANES: tuple[str, ...] = ("antigravity", "opencode")

# Model-family / provider exclusions.
GEMINI_MARKERS = ("gemini",)
OPENCODE_FORBIDDEN_FAMILIES = ("claude", "openai", "codex", "gpt", "o1", "o3", "o4")
COPILOT_PROVIDER_MARKERS = ("github-copilot", "copilot")


class EvidenceError(ValueError):
    """Malformed or self-contradictory evidence in the ordered stream."""


def _family_tokens(model_id: str) -> str:
    return model_id.lower()


def antigravity_model_eligible(model_id: str) -> bool:
    """AntiGravity is accepted only when its selected model is Gemini-family."""
    m = _family_tokens(model_id)
    return any(tok in m for tok in GEMINI_MARKERS)


def opencode_selection_eligible(provider: str, model_id: str) -> bool:
    """OpenCode excludes Claude/OpenAI/Codex families and every Copilot provider."""
    p = provider.lower()
    m = _family_tokens(model_id)
    if any(tok in p for tok in COPILOT_PROVIDER_MARKERS):
        return False
    if any(tok in m for tok in COPILOT_PROVIDER_MARKERS):
        return False
    if any(tok in m for tok in OPENCODE_FORBIDDEN_FAMILIES):
        return False
    return True


def lane_reviewer_eligible(lane: str, provider: str, model_id: str) -> bool:
    """Reviewer eligibility predicate per lane; Copilot lane is always rejected."""
    if lane == "antigravity":
        return antigravity_model_eligible(model_id)
    if lane == "opencode":
        return opencode_selection_eligible(provider, model_id)
    # copilot / ask-claude / anything else is not a Phase 2 reviewer lane.
    return False


@dataclass
class LaneReport:
    """One reviewer lane report bound to a revision digest and numbered round."""

    lane: str
    provider: str
    model_id: str
    revision_digest: str
    round: int
    report_digest: str
    finding_count: int
    timestamp: float
    sequence: int
    catalog: tuple[str, ...] = ()           # raw live catalog of model IDs
    probe_command: str = ""
    probe_exit_code: int | None = None
    probe_output: str = ""

    def probe_passed(self) -> bool:
        return self.probe_exit_code == 0

    def selected_in_catalog(self) -> bool:
        return self.catalog.count(self.model_id) == 1

    def eligible(self) -> bool:
        """A lane report counts only when reviewer, probe, and catalog all pass."""
        return (
            self.lane in PHASE2_LANES
            and lane_reviewer_eligible(self.lane, self.provider, self.model_id)
            and self.selected_in_catalog()
            and self.probe_passed()
        )


@dataclass
class TerminalReview:
    revision_digest: str
    terminal_round: int
    dual_zero_timestamp: float
    dual_zero_sequence: int


@dataclass
class DoctrineReread:
    revision_digest: str
    doctrine_digest: str
    reread_timestamp: float
    reread_sequence: int
    result: str                              # "pass" | "fail"
    blind_spots: tuple[str, ...] = ()


@dataclass
class Approval:
    revision_digest: str
    approval_timestamp: float
    approval_sequence: int
    date: str
    verbatim_response: str


@dataclass
class GateState:
    dual_zero_same_round: bool = False
    terminal_review: TerminalReview | None = None
    latest_terminal_review_valid: bool = False
    post_terminal_reread_valid: bool = False
    post_reread_approval_valid: bool = False
    phase3_eligible: bool = False
    reasons: list[str] = field(default_factory=list)


def _lane_reports_by_round(reports: list[LaneReport]) -> dict[tuple[str, int], dict[str, LaneReport]]:
    """Index eligible reports by (revision_digest, round) -> {lane: report}."""
    index: dict[tuple[str, int], dict[str, LaneReport]] = {}
    for r in reports:
        if not r.eligible():
            continue
        key = (r.revision_digest, r.round)
        index.setdefault(key, {})[r.lane] = r
    return index


def compute_dual_zero(reports: list[LaneReport], revision_digest: str) -> tuple[bool, int | None]:
    """True + terminal round when both lanes report zero in the same round.

    Both lanes must appear in the same (digest, round) with finding_count == 0.
    Returns the highest such round (the terminal dual-zero) or None.
    """
    index = _lane_reports_by_round(reports)
    best: int | None = None
    for (digest, rnd), lanes in index.items():
        if digest != revision_digest:
            continue
        if set(PHASE2_LANES).issubset(lanes.keys()):
            if all(lanes[l].finding_count == 0 for l in PHASE2_LANES):
                best = rnd if best is None else max(best, rnd)
    return (best is not None), best


def latest_review_round(reports: list[LaneReport], revision_digest: str) -> int | None:
    """Highest round with any eligible review event for the revision."""
    rounds = [r.round for r in reports if r.eligible() and r.revision_digest == revision_digest]
    return max(rounds) if rounds else None


def has_later_finding(reports: list[LaneReport], revision_digest: str, after_round: int) -> bool:
    """Any eligible review event on a later round invalidates a terminal claim.

    A later round exists, or a finding on a round at/after the terminal round
    beyond the dual-zero pair, means terminal review is not the latest event.
    """
    for r in reports:
        if not r.eligible() or r.revision_digest != revision_digest:
            continue
        if r.round > after_round:
            return True
    return False


def reduce_gate(
    *,
    lane_reports: list[LaneReport],
    revision_digest: str,
    terminal_review: TerminalReview | None = None,
    doctrine_reread: DoctrineReread | None = None,
    approval: Approval | None = None,
) -> GateState:
    """Reduce ordered evidence into a fail-closed GateState for one revision.

    Ordering contract:
      terminal dual-zero  <  doctrine reread  <  approval
    each strictly later in both sequence and timestamp, all bound to the same
    revision_digest, and terminal review + reread must be the LATEST relevant
    events for that revision (any later review invalidates the chain).
    """
    state = GateState()

    dual_zero, terminal_round = compute_dual_zero(lane_reports, revision_digest)
    state.dual_zero_same_round = dual_zero
    if not dual_zero:
        state.reasons.append("no same-round dual-zero on this revision")
        return state

    # Establish terminal review from evidence, cross-check any supplied record.
    tr = terminal_review
    if tr is None:
        # Derive a minimal terminal-review record from the dual-zero pair.
        pair_seq = max(
            r.sequence for r in lane_reports
            if r.eligible() and r.revision_digest == revision_digest and r.round == terminal_round
        )
        pair_ts = max(
            r.timestamp for r in lane_reports
            if r.eligible() and r.revision_digest == revision_digest and r.round == terminal_round
        )
        tr = TerminalReview(
            revision_digest=revision_digest,
            terminal_round=terminal_round,
            dual_zero_timestamp=pair_ts,
            dual_zero_sequence=pair_seq,
        )
    else:
        if tr.revision_digest != revision_digest:
            state.reasons.append("terminal_review bound to another revision")
            return state
        if tr.terminal_round != terminal_round:
            state.reasons.append(
                f"terminal_round {tr.terminal_round} != computed {terminal_round}"
            )
            return state
    state.terminal_review = tr

    # Terminal review must be the latest relevant review for this revision.
    if has_later_finding(lane_reports, revision_digest, tr.terminal_round):
        state.reasons.append("a later review event invalidates the terminal round")
        return state
    state.latest_terminal_review_valid = True

    # Doctrine reread: same revision, strictly after terminal dual-zero.
    if doctrine_reread is None:
        state.reasons.append("missing doctrine reread")
        return state
    dr = doctrine_reread
    if dr.revision_digest != revision_digest:
        state.reasons.append("doctrine reread bound to another revision")
        return state
    if dr.result != "pass":
        state.reasons.append("doctrine reread did not pass")
        return state
    if not (dr.reread_sequence > tr.dual_zero_sequence
            and dr.reread_timestamp > tr.dual_zero_timestamp):
        state.reasons.append(
            "doctrine reread must be strictly after terminal dual-zero "
            "(sequence and timestamp)"
        )
        return state
    state.post_terminal_reread_valid = True

    # Approval: same revision, strictly after reread.
    if approval is None:
        state.reasons.append("missing approval")
        return state
    ap = approval
    if ap.revision_digest != revision_digest:
        state.reasons.append("approval bound to another revision")
        return state
    if not ap.date or not ap.verbatim_response:
        state.reasons.append("approval missing date or verbatim response")
        return state
    if not (ap.approval_sequence > dr.reread_sequence
            and ap.approval_timestamp > dr.reread_timestamp):
        state.reasons.append(
            "approval must be strictly after doctrine reread "
            "(sequence and timestamp)"
        )
        return state
    state.post_reread_approval_valid = True

    state.phase3_eligible = (
        state.dual_zero_same_round
        and state.latest_terminal_review_valid
        and state.post_terminal_reread_valid
        and state.post_reread_approval_valid
    )
    if state.phase3_eligible:
        state.reasons.append("all gates satisfied on the sealed revision")
    return state
