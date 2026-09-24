# Verification gates

Three gates make the remediation success criteria executable and fail-closed. Each gate is a standalone script.

## Encode-coverage gate (G3, "0 missing rules")

Each approved behavioral un-nerf must be present as a rule in the live `apply-unnerfs.py`.

```
python .claude/skills/tweakcc-update/scripts/check_encode_coverage.py
```

Exit 0: each non-retain rewrite is encoded. Exit 1: one or more approved un-nerfs are not encoded, or the script cannot verify a batch. Exit 2: usage or configuration error. Exit 3: wall-clock ceiling.

It imports `encode_rules.encode_batch`, so it uses the exact predicate of the encoder to find the prompts that must have a rule. A record needs a rule in one case: its disposition is not `retain`, and its after-body differs from its before-body (raw bytes, frontmatter stripped). It compares each expected slug with the rule ids that `apply-unnerfs.py --dump-rules` exposes. A batch whose sealed revision fails the fail-closed gate of the encoder (digest drift, missing bodies) is a FAIL for that batch. The script names the batch and does not crash.

A digest-drift FAIL means that someone changed the sealed approved after-bodies after approval. The work is not done. Derive the sealed bodies again against the regenerated store, seal them again, and get a new approval. Do not edit the recorded digest to force a pass.

## STE gate (G2 minimum bar)

The approved revision of each batch must be STE-clean.

```
python .claude/skills/tweakcc-update/scripts/store-remediation/ste_gate.py --revision-dir <batch>/<rev>
```

Run it over all eight batches. Exit 0: all prompts are clean, or prose-free and exempt. Exit 1: an unexplained STE violation, named with its file and text. Exit 2: usage or configuration. Exit 3: ceiling.

It scans `<rev>/prompts/after/*.md`. A body with prose must have zero `ste_lint` violations after the gate blanks each preserved span from `<rev>/writing-quality/ste.json`. A failed batch blocks the next gate until you derive the batch again, clean, and get a new approval.

## Reanchor gate (G4, binary-faithful)

The encoded rules must apply against the genuine binary with no loss.

```
python unnerfcc/scripts/apply-unnerfs.py --check
```

The pass is `0 FAILED / 0 MISSING`. A rule whose sealed before-body diverges from the genuine binary (a slot-count or prose divergence) must be derived again against the regenerated store. The slot-preserving splicer cannot reach an opaque-hoist dead-end. Disposition each such case in the plan, and record it as a waiver.

## Gate order

Run the gates in order. Each gate must exit 0 before the next gate runs. If a gate fails, fix the cause and run that gate again. Never go past a failed gate, and never edit a recorded seal digest to get a pass.
