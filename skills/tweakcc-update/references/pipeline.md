# Remediation pipeline: extract to verified apply

This pipeline is for REMEDIATION-scale work: un-nerf content changes (new doctrine
concepts, corrected rewrites, coverage gaps). A plain version bump does NOT need it. For a
version bump, use the SKILL.md "Version update runbook" (version gate, extract, re-anchor,
apply, verify).

Run the seven gates below in order. Each gate must exit 0 before the next gate runs.

Inside G2, the authoring method is recognition-first per recipe-concept-prompt-mapping. Never do a cold read of the store. Every gate that judges a rewrite must compare it against the LIVE prompt baseline, not against stock alone. A gate that compares against stock only lets regressions pass (2026-08-28 forensics).

## The seven gates

| Gate | Script | Passes only when |
|---|---|---|
| G0 Sync | `unnerfcc/engine/extract-prompts.mjs` (through `upgrade.sh`) | the new stock corpus is extracted from the binary, and a sha256 diff of changed, added, and removed prompts is recorded |
| G1 Categorize | `.claude/skills/tweakcc-update/scripts/corpus-categorization/verify-corpus-coverage.sh [--version <x.y.z>]` (without the flag, it uses the highest corpus version) | the coverage script exits 0: each corpus file maps to one batch or to the exclusion list |
| G2 Remediate | `.claude/skills/tweakcc-update/scripts/store-remediation/ste_gate.py --revision-dir <rev>` for each batch (it needs the simple-english plugin, which ships the linter) | `ste_gate.py` exits 0 for EVERY batch, the Codex review is clean, the user approval is recorded, and the seal digest matches |
| G3 Encode | `encode_rules.py --all --emit`, then `check_encode_coverage.py` | the encoder gates pass with no digest drift, and each non-retain rewrite has a rule (the coverage script exits 0) |
| G4 Reanchor | `.claude/skills/tweakcc-update/scripts/doctrine-coverage/doctrine_coverage_check.py`, `.claude/skills/tweakcc-update/scripts/concept-map/map_coverage_gate.py` (governed-set coverage, names the uncovered rows), `.claude/skills/tweakcc-update/scripts/anchored-claims/check_claims.py` (each map row rests on an anchored claim), `.claude/skills/tweakcc-update/scripts/alignment-gate/alignment_gate.py`, then `reanchor_engine.py`, `apply-unnerfs.py --check` | the alignment gate exits 0 with no STALE-ANCHOR. With a live extract from `--live`, it also reports no UNEXPLAINED-DIFF. Then `apply_unnerfs_check` reports 0 FAILED and 0 MISSING against the genuine binary |
| G5 Apply | `install.py --prepare`, close CC, `apply-external.bat`, `verify.py` | the two version lines print, and the three content sentinels are present |
| G6 Behavioral verify | a behavior test for each batch in a fresh session | each applied batch shows the un-nerfed text and no stock text |

## Version and format constraints

The target version is the RESULT line of `check_version_intersection.py`. It is the minimum of
the versions that the upstreams of the two patcher projects support. The local fork catalogs
give the READINESS report: they show what you can install before the gap-closure steps. They
never give the target.

The `tweakcc-fixed` commit in the working copy must match the binary format of the target. `--prepare` picks the `tweakcc-fixed` commit from the target. It uses the newest release tag whose newest `data/prompts/prompts-<ver>.json` is the target. That release catalogued the target binary, so its extractor parsed that format. If no such tag exists, it uses the last commit that touched the catalog file of the target (`install.select_tweakcc_ref`). No commit is hard-coded. If `tweakcc-fixed` never catalogued the target, the prepare stops with a remediation message.

The `unnerfcc/engine/` of the fork must match the same format (the engine-sync step of the
runbook). A code-split target with an old-format engine fails with the format error of unnerfcc.

## Prompt source

`unnerfcc/engine/extract-prompts.mjs` extracts the stock prompt corpus from each Claude Code binary. The un-nerf rules are the rewrites of this project, encoded in `unnerfcc/scripts/apply-unnerfs.py`. `lukehutch/unnerfcc` is an optional upstream-sync signal, not a prompt source. Its PRs were rejected, so prompt updates come from self-extraction, not from copies of upstream prompts.

## One clone per repo

`unnerfcc` has one dev copy at `D:/Data/Programs/AI/Claude/Projects/tweakcc/unnerfcc`, which tracks `brooksbUWO/unnerfcc`. The installer keeps the runtime clones in `~/.tweakcc-gilligan/repos/`. On each prepare, it fetches `unnerfcc` and `lobotomized-claude-code` and fast-forwards them to their remotes. It resets `tweakcc-fixed` to the pinned commit. If a clone is missing, it makes a fresh clone. Those clones are disposable, and you must not edit them by hand.

Never make a second copy, snapshot, branch-named directory, or zip of a repo. The encode and reanchor tools read the single dev copy. With a duplicate, an edit can land in one copy while a commit goes out from the other.

## When a gate does not pass

Each gate must pass before the next gate runs. If a gate fails, fix the cause and run that gate again. Never edit a recorded seal digest, skip a batch, or mark a gate as passed to get past a red gate.

Two kinds of failure have a specific fix:

- An STE failure or a seal-digest drift in a batch: someone changed the sealed before and after bodies after approval, or they do not match the binary-faithful store. Derive them again against the regenerated store, seal them again, and get a new approval.
- A rule that diverges from the genuine binary (reanchor FAILED or MISSING): derive it again against the regenerated store. The slot-preserving splicer cannot deliver an opaque-hoist dead-end. Disposition that case at plan level: drop the rule or use a different override channel. Record it as a waiver. Never skip it silently.

## The alignment gate (runs inside G4, before reanchor)

`.claude/skills/tweakcc-update/scripts/alignment-gate/alignment_gate.py` compares three bodies for each governed row of the concept map: the stock store body, the un-nerf rules for that slug, and (with `--live <dir>`) a body from a live extract. To make the rules dump, run `python unnerfcc/scripts/apply-unnerfs.py --dump-rules <path>`.

The verdicts are:

- `STALE-ANCHOR`: the stock string of a rule no longer matches the store byte for byte, so the splicer skips that rule silently.
- `RULE-NOT-APPLIED`: a rule targets the slug, but the live body still equals stock.
- `UNEXPLAINED-DIFF`: the live body matches neither stock nor stock plus the rules. This is drift.

Any failure exits 1 and blocks the reanchor. Two-way mode (no `--live`) tests only the anchors, and it says so loudly. It does not prove the live binary.
