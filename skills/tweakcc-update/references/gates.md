# Verification gates

Two gates make the ROADMAP success criteria executable and fail-closed. Both run inside their GSD phase and both are also standalone scripts a verifier can run.

## Encode-coverage gate (Phase 3, "0 missing rules")

Every approved behavioral un-nerf must be present as a rule in the live `apply-unnerfs.py`.

```
python .claude/skills/tweakcc-update/scripts/check_encode_coverage.py
```

Exit 0: every non-retain rewrite is encoded. Exit 1: one or more approved un-nerfs are unencoded, or a batch could not be verified. Exit 2: usage or config error. Exit 3: wall-clock ceiling.

It imports `encode_rules.encode_batch`, so the set of prompts that should have a rule is computed with the encoder's exact predicate: a rule is expected only when the record disposition is not `retain` and the after-body differs from the before-body (raw bytes, frontmatter stripped). Each expected slug is checked against the rule ids `apply-unnerfs.py --dump-rules` exposes. A batch whose sealed revision fails the encoder's own fail-closed gate (digest drift, missing bodies) is a batch-level FAIL, listed by name, not a crash.

A digest-drift FAIL means the sealed approved after-bodies were modified after approval. The phase is not done: re-derive the sealed bodies against the regenerated store, re-seal, and re-approve within the phase. Do not edit the recorded digest to force a pass.

## STE gate (Phase 2 minimum bar)

Every batch's approved revision must be STE-clean.

```
python .claude/workspace/scripts/store-remediation/ste_gate.py --revision-dir <batch>/<rev>
```

Run it over all eight batches. Exit 0: all prompts clean or prose-free exempt. Exit 1: any unexplained STE violation, named with file and offending text. Exit 2: usage or config. Exit 3: ceiling.

It scans `<rev>/prompts/after/*.md`. A prose-bearing body must have zero `ste_lint` violations after blanking each preserved span from `<rev>/writing-quality/ste.json`. A failing batch keeps the phase open until the batch is re-derived clean and re-approved.

## Reanchor gate (Phase 3, binary-faithful)

The encoded rules must apply against the genuine binary with no loss.

```
python unnerfcc/scripts/apply-unnerfs.py --check
```

`0 FAILED / 0 MISSING` is the pass. A rule whose sealed before-body diverges from the genuine binary (slot-count or prose divergence) is re-derived against the regenerated store within the phase. Opaque-hoist dead-ends the slot-preserving splicer cannot reach are dispositioned at plan level and recorded as waivers.

## How the gates gate

The skill does not write ROADMAP or STATE checkboxes. It dispatches `/gsd-plan-phase N` then `/gsd-execute-phase N`. Inside `gsd-execute-phase`, `gsd-verifier` checks the phase `must_haves` against the codebase and writes `NN-VERIFICATION.md`. Only a `passed` verdict reaches `gsd_run query phase.complete N`, which flips the checkbox to `[x]`. A skill that writes the checkbox directly bypasses the only success-criteria check GSD has.
