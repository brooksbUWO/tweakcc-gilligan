# Remediation pipeline: extract to verified apply

This pipeline is for REMEDIATION-scale work: un-nerf content changes (new doctrine
concepts, corrected rewrites, coverage gaps). A plain version bump does NOT need it; that
is the SKILL.md "Version update runbook" (check, gate, extract, re-anchor, apply, verify).

A remediation is a new GSD milestone. Run `/gsd-new-milestone` to create the phase set, then plan and execute each phase in order with `/gsd-plan-phase <N>` and `/gsd-execute-phase <N>`, where `<N>` is the number the milestone assigned. `gsd-verifier` gates each phase against its success criteria; a phase reaches `[x]` only on a `passed` verdict. The seven gates below are the success criteria the phases carry. Authoring method inside G2 is recognition-first per recipe-concept-prompt-mapping (never a store cold-read), and every gate that judges a rewrite must compare against the LIVE prompt baseline, not stock alone: a stock-only gate green-lights regressions (2026-08-28 forensics).

## The seven gates

| Gate | Phase role | Script | Passes only when |
|---|---|---|---|
| G0 Sync | pre-phase sync | `unnerfcc/engine/extract-prompts.mjs` (via `upgrade.sh`) | new stock corpus extracted from the binary; checksum diff of changed/added/removed prompts recorded |
| G1 Categorize | categorization phase | `.claude/workspace/scripts/verify-corpus-coverage.sh` | coverage script exits 0 (every corpus file mapped to one batch or the exclusion list) |
| G2 Remediate | remediation phase | `.claude/workspace/scripts/per-batch-remediation/ste_gate.py --revision-dir <rev>` per batch | `ste_gate.py` exits 0 for EVERY batch; Codex review clean; user approval recorded, seal digest matches |
| G3 Encode | encoding phase | `encode_rules.py --all --emit`, then `check_encode_coverage.py` | encoder gates pass (no digest drift); every non-retain rewrite has a rule (coverage check exits 0) |
| G4 Reanchor | encoding phase | `.claude/workspace/scripts/alignment-gate/alignment_gate.py`, then `reanchor_engine.py`, `apply-unnerfs.py --check` | alignment gate exits 0 (no STALE-ANCHOR, and no UNEXPLAINED-DIFF when a live extract is supplied via `--live`), then `apply_unnerfs_check` reports 0 FAILED / 0 MISSING against the genuine binary |
| G5 Apply | encoding phase | `install.py --prepare`; close CC; `apply-external.bat`; `verify.py` | dual version lines print; three content sentinels present |
| G6 Behavioral verify | verification phase | per-batch spot-check in a fresh session | each applied batch shows the un-nerfed text and no stock text |

## Version and format constraints

The target version is the RESULT line of `check_version_intersection.py`: the minimum of
the two patcher projects' UPSTREAM-supported versions. The local fork catalogs are the
READINESS report (what is installable before the gap-closure steps), never the target.

The checked-out `tweakcc-fixed` commit must match the target binary format:

- Target 2.1.241 or less (OLD single-module Bun format): `tweakcc-fixed` at `2dc353c` (v2.7.38) or earlier.
- Target 2.1.246 or more (CODE-SPLIT Bun format): `tweakcc-fixed` at `890c928` or later.

`prepare_stage` pins `tweakcc-fixed` via `pin_ref` (currently `2dc353c`). Move the pin only
together with the runbook's engine-sync step: a 2.1.246+ target needs the fork's
`unnerfcc/engine/` code-split-capable AND the pin at `890c928+`; either alone fails with
the other tool's format error.

## Prompt source

`unnerfcc/engine/extract-prompts.mjs` extracts the stock prompt corpus from each Claude Code binary. The un-nerf rules are this project's own rewrites, encoded in `unnerfcc/scripts/apply-unnerfs.py`. `lukehutch/unnerfcc` is an optional upstream-sync signal, not a prompt source; its PRs were rejected, so prompt updates come from self-extraction, not from copying upstream prompts.

## One clone per repo

`unnerfcc` has one dev copy at `D:/Data/Programs/AI/Claude/Projects/tweakcc/unnerfcc`, tracking `brooksbUWO/unnerfcc`. The installer clones repos fresh into `~/.tweakcc-gilligan/repos/` on every prepare; those clones are disposable and must not be hand-edited. Never create a second copy, snapshot, branch-named directory, or zip of a repo. The encode and reanchor tooling reads the single dev copy; a duplicate lets edits land in one copy and get committed from another.

## When a gate does not pass

Each gate must pass before the next phase runs. A gate that fails means the phase is not done: `gsd-verifier` returns a non-`passed` verdict, the phase stays open, and GSD re-plans and re-executes it within the same phase until the verifier passes. Never edit a recorded seal digest, skip a batch, or mark a phase complete to move past a red gate.

Two failure kinds have a specific fix inside their phase:

- STE failure or seal-digest drift in a batch: the sealed before/after bodies were modified after approval or do not match the binary-faithful store. Re-derive them against the regenerated store, re-seal, re-approve.
- A rule diverging from the genuine binary (reanchor FAILED/MISSING): re-derive it against the regenerated store. An opaque-hoist dead-end the slot-preserving splicer cannot deliver is dispositioned at plan level (drop the rule or use a different override channel) and recorded as a waiver, never silently skipped.

## The alignment gate (runs inside G4, before reanchor)

`.claude/workspace/scripts/alignment-gate/alignment_gate.py` compares, per governed row of the concept map, the stock store body, the un-nerf rules for that slug, and (with `--live <dir>`) a live-extracted body. Produce the rules dump with `python unnerfcc/scripts/apply-unnerfs.py --dump-rules <path>`. Verdicts: `STALE-ANCHOR` (a rule's stock string no longer byte-matches the store: the splicer would silently skip it), `RULE-NOT-APPLIED` (live still equals stock although a rule targets the slug), `UNEXPLAINED-DIFF` (live matches neither stock nor stock-plus-rules: drift). Any failure exits 1 and blocks reanchor. Two-way mode (no `--live`) checks anchoring only and says so loudly; it does not prove the live binary.
