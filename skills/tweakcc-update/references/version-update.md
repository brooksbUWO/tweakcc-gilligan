# Version update runbook (new Claude Code version)

A version update is diff-and-re-anchor, not a remediation. The rewrites live as rules in
`unnerfcc/scripts/apply-unnerfs.py` and do not change; only their stock anchors move when
upstream rewords a prompt. No milestone, no phases. Changing un-nerf CONTENT is the
separate Remediation job in SKILL.md; never mix the two.

## Steps

1. **Version check.** Run `python scripts/check_version_intersection.py`. It prints two
   distinct numbers on two labeled lines:
   - `APPLICABLE`: the newest version the local clones can patch NOW (minimum of the fork
     catalog and the local tweakcc-fixed catalog).
   - `RESULT`: the TARGET (minimum of the two upstreams' supported versions, read from
     commit subjects AND from the `data/prompts/prompts-<ver>.json` filenames at each
     upstream HEAD; the higher signal wins so a new subject wording cannot under-report).
   The `READINESS` line says whether APPLICABLE has caught up to RESULT. When an upstream
   could not be read, the script prints `RESULT-UNCERTAIN` instead of `RESULT`, exits 1,
   prints no install line, and `install.py --prepare` stops rather than record the fallback;
   re-run once GitHub is reachable.
2. **Version gate: remediation only.** A mechanical update needs no gate on the running
   binary: `upgrade.sh` fetches the target binary into a temporary npm prefix for the
   extraction, and the apply installs the target itself. The gate applies only to
   recognition-first remediation (see "Recognition precondition" below), where the
   session's loaded prompts must be the binary being mapped. In that case compare
   `claude --version` to the target; if they differ, STOP and give the user (a) the
   one-line reason, (b) the script's paste-ready
   `npm install -g @anthropic-ai/claude-code@<target>` line VERBATIM on its own line, and
   (c) the instruction: close ALL Claude Code sessions, run the command, start a fresh
   session, run `/tweakcc-update` again.
3. **Close the readiness gap** (only when READINESS reports a lag). In order:
   - If the target crossed a binary-format boundary (see the format table below), sync CODE
     from the fork's upstream (lukehutch/unnerfcc) into the dev clone at `unnerfcc/`. The
     sync set is `engine/` PLUS the pipeline scripts that consume the engine's interface:
     `upgrade.sh`, `install.sh`, `scripts/*.mjs`, `scripts/package*.json`. Never sync prompt
     or rule content: `scripts/apply-unnerfs.py`, `system-prompts/`, `data/prompts/`. An
     engine-only sync strands the pipeline on the old interface. The code-split engine
     unpacks to a directory. The old scripts expect one cli.js and die with EISDIR ("Is a
     directory") after a good unpack. `install.py --prepare` then selects the `tweakcc-fixed`
     release that catalogued the target on its own (binary-format section below).
   - After each engine sync, make sure that the fork's Windows support survived. Upstream's
     engine parses ELF and Mach-O only. `grep -c "findBunSectionPE\|repackPE"
     engine/bun-binary.mjs` must be nonzero. If it is zero, re-port the Windows pieces: the
     PE `.bun` section parse, `repackPE` (node-lief), FileAlignment padding tolerance in the
     size-header check, and `B:/~BUN/root/...` drive-letter module names in the struct
     validator and `moduleRelPath`. Also make sure that `win_resolve_shim` is present in
     `upgrade.sh` and `install.sh`. On Windows, `claude` on PATH is npm's sh shim, and
     readlink cannot see through it. Without the helper, the pipeline parses the shim script
     as the binary.
   - After each engine sync, re-check the fork's sentinel lists. The fork's rules emit their
     own phrases ("senior-engineer standard", "never trade away rigor, depth, or
     correctness", "Spawn agents whenever parallel investigation", "investigate thoroughly,
     then be direct", "Complete what was asked thoroughly and correctly"); upstream's
     "thorough, clear, and rich with explanation" is not one of them. The lists live in
     `install.sh` (sentinel verify and `is_unnerfed`), `upgrade.sh` (same two), and
     `engine/patch-prompts.mjs` (`UNNERF_SENTINELS`). An upstream sync that restores the
     upstream phrase warns "sentinel missing" on every run.
   - Seed BOTH AI steps from upstream BEFORE you run upgrade.sh. Upstream already
     classified AND named the target's prompts. Without both seeds, the run re-does hours
     of AI work: classify on the strings, then relabel on ~2400 anonymous prompts.
     1. Classify seed: merge upstream's `data/string-catalog.json` into the local one
        (union keyed by sha256, local entries win). Copy the target's
        `data/bucket-analysis-<ver>.json`. Result: only Windows-only strings remain to
        classify (131 at 2.1.257).
     2. Relabel seed: build a MERGED carry-forward catalog with
        `.claude/workspace/scripts/unnerfcc-seed/merge-seed-catalog.mjs`: every entry of
        the fork's previous catalog (fork ids win) plus the upstream entries for the
        target (`git show <upstream sync commit>:data/prompts/prompts-<target>.json`)
        whose hash and id are both new. Never replace the fork catalog with upstream's:
        rules are keyed to fork slug ids, and the fork relabeled some ids. Pass the
        merged file with `--seed`. Result: relabel names only the prompts that no
        catalog has named.
   - `cd unnerfcc && ./upgrade.sh --version <target> --seed <merged.json> --no-bucket-analyze --jobs 4 --yes`
     extracts the target corpus from the genuine binary and builds
     `data/prompts/prompts-<target>.json`. `--version`, `--seed`, `--no-bucket-analyze`
     and `--yes` are required. Without `--version`, upgrade.sh targets npm latest, not the
     intersection target. Without `--no-bucket-analyze`, upgrade.sh's bucket-analysis step
     has an AI worker author NEW un-nerf rules and merges them into
     `scripts/apply-unnerfs.py`. That is un-nerf content work (SKILL.md rule 7, the
     recipe's cold-read prohibition), never part of a version update. `--jobs N` labels N
     relabel chunks concurrently (about 5 minutes per chunk serially; each chunk is an
     independent job writing its own `labels-NNN.json`). `--seed`, `--no-bucket-analyze`,
     `--jobs` and `--ack-removed` are the fork's additions to upgrade.sh.
   - If the run stops at the catalog gate "N ids removed vs prev, suspiciously large":
     the merged seed holds upstream entries absent from the Windows binary. Confirm the
     removed ids split into upstream-origin (not in the fork's previous catalog) and
     fork-origin (each surfaces as MISSING in `--check`, handled in the re-anchor step).
     Then re-run with `--seed` pointing at the now fully labeled
     `prompts-<target>.json` (copy it out first: the run overwrites it) and
     `--ack-removed <N>` set to the exact count the gate printed. Every id carries, the
     relabel worklist is 0, and the gate passes.
   - Label files written through PowerShell carry a UTF-8 BOM; `relabel.mjs` strips it
     (fork fix).
   - Windows only: `install.py --prepare` verifies `~/.local/bin/python3.bat` (content:
     `python %*`) and creates it when absent, warning if `~/.local/bin` is not on PATH.
     Without it, Windows-side `python3` spawns fail, and PowerShell ShellExecutes a bare
     extensionless `python3` shim, which opens endless "Select an app" pickers.
   - `python unnerfcc/scripts/apply-unnerfs.py --check` names every rule whose stock anchor
     drifted. Re-anchor those rules (same `unnerf` body, updated `stock` text from the new
     extraction). Do the edit with
     `.claude/workspace/scripts/unnerfcc-reanchor/reanchor_rules.py` (AST-positioned,
     count-asserted; ops: reanchor, rekey, retire, add; `--dry-run` first). Write a spec
     builder for the version (example: the 2026-09-02 run's `build_spec_2_1_257.py`) that
     reads each new stock from the store file bytes and asserts it occurs exactly once,
     with placeholder parity on every rewrite. Three drift kinds recur: punctuation only
     (em dash to hyphen), a renamed `${...}` placeholder, and a prompt split into sibling
     fragments (a MISSING file whose text now lives under a new slug: grep the store for
     the stock's first 60 characters; re-key when the stock is present, re-anchor when it
     drifted, and add a rule on the sibling fragment when the un-nerfed span was split).
     A rule whose prompt vanished (see `removed.json`) is retired deliberately,
     never left to fail silently.
   - A rule whose surface tweakcc-fixed SHADOWS (an override in
     `~/.tweakcc/system-reminders/*.md` with a `shadows: <id>` frontmatter line) can never
     find its stock on a real apply, because the override replaces that text before
     unnerfcc runs. It reports `[LOST] <id>: couldNotFind` and install.py aborts the apply
     on that marker. Remove the rule, reset its `.md` body to stock, and leave a
     "NO RULE" comment in the catalog naming the override file (four such cases exist).
     `install.py --prepare` intersects the rule ids with every `shadows:` list in the LCC
     `system-reminders/` and refuses to generate the apply script on any overlap
     (skill 1.4.2; the 2.1.258 apply of 2026-09-01 lost one rule this way).
4. **Revalidate the derived artifacts against the new version.** The re-anchor step only
   touches rule-bearing files. The prompt store round and the concept map are keyed to the
   PREVIOUS extraction and drift silently unless this step runs (2026-09-01: after the
   2.1.258 update the map still pointed at 8 prompts that had left the binary, 17 changed
   bodies, 1 renamed slug, on a 2.1.235 store round). In order, and every gate must exit 0
   before the apply:
   - Build the new store round: `node unnerfcc/scripts/sync-version.mjs <target> --target
     <scratch> --no-manifest`, then `python .claude/workspace/scripts/store-provenance/build_queue.py
     --prompts-dir <scratch> --out <scratch>/queue.csv`, then
     `python .claude/workspace/scripts/store-remediation/remediate.py materialize --queue
     <scratch>/queue.csv --prompts-dir <scratch> --out .claude/workspace/prompt-store --batch
     binary-faithful --revision r<NNNN>`. Delete `<scratch>` afterward (a second copy of a
     store round outside `.backups/` is a rule 9 violation and inflates the repo).
   - Validate every map row against the new round's `batch.json`: a file absent from the
     new round is dropped with a reason in the map's `reconciliation.dropped_rows` (after a
     grep of the new catalog for a renamed successor), a renamed slug is rekeyed, a row whose
     body sha changed is re-read and carries a `reread` note. Then set the map's `store_dir`
     to the new round. Write the change as a one-shot count-asserted script kept in the run
     scratchpad (example: `runs/2026-09-01T2252/scratchpad/migrate_map_r0002.py`).
   - Run the three gates listed in `.claude/workspace/prompt-store/CLAUDE.md`
     (`doctrine_coverage_check.py`, `alignment_gate.py`, `map_coverage_gate.py`) against
     the new round and a fresh `apply-unnerfs.py --dump-rules` output. The coverage gate's
     uncovered rows are the [R002] worklist, not a version-update failure; the alignment and
     doctrine gates must be clean.
5. **Apply and verify.** `python scripts/install.py --prepare` (safe in-session); close all
   CC sessions; run `apply-external.bat`; `verify.py` must pass all four checks. Both
   patchers' full per-item accounting is captured automatically in
   `~/.tweakcc-gilligan/logs/` (install_*.log and verify_*.log); no manual output capture.
6. **Behavioral spot-check.** In a fresh session on the patched binary, confirm a few known
   un-nerfs by behavior (recipe rule: string presence is not proof). A session resumed with
   `-c` from before the apply is not evidence about the current binary; start a new process.

## tweakcc-fixed binary-format compatibility (read before you change the version logic)

`unnerfcc` sets the target version. Its prompt catalog moves slower than the `tweakcc-fixed`
catalog. Each `tweakcc-fixed` release patches one Claude Code Bun binary format. A prompt
catalog file (`data/prompts/prompts-<ver>.json`) in `tweakcc-fixed` does not prove the
checked-out code can patch that binary. The catalog and the extractor are separate. Match
the extractor to the target binary format, not the catalog.

The checked-out `tweakcc-fixed` commit must match the target binary format. Known eras, for
diagnosis only (the selection below needs no era table): 2.1.241 and earlier use the OLD
single-module Bun format (releases up to v2.7.38); 2.1.246 and later use the CODE-SPLIT format
(v2.8.0 and later).

A format mismatch fails the apply. The extractor finds no claude module and stops with this error:

```
Error: Could not extract JS from native binary: ...claude.exe (claude module not found in any of the binary modules)
```

When you see that error, run `git -C ~/.tweakcc-gilligan/repos/tweakcc-fixed log -1 --oneline`
and compare it with the `tweakcc-fixed checked out at <tag> (<sha>) for CC <ver>` line of the
most recent prepare log. The cause is the format mismatch. Do not edit `unnerfcc`, rebuild, or
delete `dist/`.

`--prepare` derives the `tweakcc-fixed` checkout from the target: the newest release tag whose newest `data/prompts/prompts-<ver>.json` is the target (that release catalogued the target binary, so its extractor parsed that format), else the last commit that touched the target's catalog file (`install.select_tweakcc_ref`). There is no hard-coded commit. A target that `tweakcc-fixed` never catalogued stops the prepare with a remediation message.
The checkout, the fork's `unnerfcc/engine/` state, and the target version must agree on one
binary format; the engine-sync step above keeps the engine side current.

## Recognition precondition and version-delta bridge (mapping against a NEW CC version)

Recognition-first mapping (recipe-concept-prompt-mapping, most recent version) reads the
running binary's live prompts. That has a precondition: instant in-context recognition is
fully valid only when the running session IS the binary to be patched. When the target is a
NEW version the session is not running:

- Prompts UNCHANGED from the running version are still recognizable in context; map them by
  recognition as usual.
- NEW or CHANGED prompts are NOT in context. Get them by SELF-EXTRACTION from the genuine
  target binary: install the target version, run `upgrade.sh` (extracts the corpus into
  `unnerfcc/system-prompts/`), then diff STOCK against STOCK: the new "sync to Claude Code
  vX.Y.Z" commit against the previous version's sync commit. Never diff against the working
  tree; after an install/replay it holds the UN-NERFED bodies (rules replayed onto stock),
  so a working-tree diff misreports every un-nerf as an upstream change. STOCK = THE BINARY,
  always.
  The Piebald corpus (`repos/pi-bald/`) is QUARANTINED and never a prompt source
  (REQUIREMENTS.md: it diverged from the genuine binary; that divergence is part of why
  the v3.0 remediation redo exists).

The cleanest path is install-first: take the version gate's paste-ready npm command, start a
fresh session on the target, and the whole live set is recognizable directly while the
extraction diff names what changed.
