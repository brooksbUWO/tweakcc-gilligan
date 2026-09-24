# Version update runbook (new Claude Code version)

A version update is diff-and-re-anchor, not a remediation. The rewrites live as rules in
`unnerfcc/scripts/apply-unnerfs.py`, and they do not change. When upstream rewords a prompt,
only their stock anchors move. A version update has no milestone and no phases. A change to
un-nerf CONTENT is the separate Remediation job in SKILL.md. Never mix the two.

## Steps

1. **Version gate input.** Run `python scripts/check_version_intersection.py`. It prints two
   different numbers on two labeled lines:
   - `APPLICABLE`: the newest version that the local clones can patch NOW. It is the minimum
     of the fork catalog and the local tweakcc-fixed catalog.
   - `RESULT`: the TARGET. It is the minimum of the versions that the two upstreams support.
     The script reads them from the commit subjects AND from the
     `data/prompts/prompts-<ver>.json` file names at each upstream HEAD. The higher signal
     wins, so a new subject wording cannot give a number that is too low.

   The `READINESS` line gives the lag of APPLICABLE behind RESULT. If the script cannot
   read an upstream, it prints `RESULT-UNCERTAIN` in place of `RESULT`. It then exits 1 and
   prints no install line, and `install.py --prepare` stops and records no fallback. When GitHub is
   reachable, run the script again.
2. **Version gate: remediation only.** A mechanical update needs no gate on the running
   binary. `upgrade.sh` fetches the target binary into a temporary npm prefix for the
   extraction, and the apply installs the target itself. The gate is only for
   recognition-first remediation (see "Recognition precondition" below). There, the loaded
   prompts of the session must be the binary that you map. In that case, compare
   `claude --version` to the target. If they are different, STOP and give the user three items:
   - the reason, in one line
   - the paste-ready `npm install -g @anthropic-ai/claude-code@<target>` line of the script,
     VERBATIM, on its own line
   - the instruction: close ALL Claude Code sessions, run the command, start a fresh session,
     and run `/tweakcc-update` again.
3. **Close the readiness gap.** A lag in READINESS needs this step. Do its parts in this
   order:
   - If the target crossed a binary-format boundary (see the format table below), sync CODE
     from the upstream of the fork (lukehutch/unnerfcc) into the dev clone at `unnerfcc/`.
     The sync set is `engine/` PLUS the pipeline scripts that use the interface of the engine:
     `upgrade.sh`, `install.sh`, `scripts/*.mjs`, and `scripts/package*.json`. Never sync
     prompt or rule content: `scripts/apply-unnerfs.py`, `system-prompts/`, `data/prompts/`.
     A sync of the engine alone leaves the pipeline on the old interface. The code-split
     engine unpacks to a directory. The old scripts expect one cli.js, and they stop with
     EISDIR ("Is a directory") after a good unpack. `install.py --prepare` then selects the
     `tweakcc-fixed` release that catalogued the target (see the binary-format section below).
   - After each engine sync, make sure that the Windows support of the fork is still there.
     The upstream engine parses only ELF and Mach-O. `grep -c "findBunSectionPE\|repackPE"
     engine/bun-binary.mjs` must give a number that is not zero. If it gives zero, port the
     Windows parts again:
     - the parse of the PE `.bun` section
     - `repackPE` (node-lief)
     - the FileAlignment padding tolerance in the size-header test
     - the `B:/~BUN/root/...` drive-letter module names in the struct validator and in
       `moduleRelPath`

     Also make sure that `win_resolve_shim` is in `upgrade.sh` and `install.sh`. On Windows,
     `claude` on PATH is the sh shim of npm, and readlink cannot see through it. Without the
     helper, the pipeline parses the shim script as the binary.
   - After each engine sync, read the sentinel lists of the fork again. The rules of the fork
     emit these phrases of their own:
     - "senior-engineer standard"
     - "never trade away rigor, depth, or correctness"
     - "Spawn agents whenever parallel investigation"
     - "investigate thoroughly, then be direct"
     - "Complete what was asked thoroughly and correctly"

     The upstream phrase "thorough, clear, and rich with explanation" is not one of them. The lists are in
     `install.sh` (the sentinel test and `is_unnerfed`), in `upgrade.sh` (the same two), and
     in `engine/patch-prompts.mjs` (`UNNERF_SENTINELS`). If an upstream sync puts the
     upstream phrase back, each run warns "sentinel missing".
   - Seed BOTH AI steps from upstream BEFORE you run upgrade.sh. Upstream already classified
     AND named the prompts of the target. Without both seeds, the run does hours of AI work
     again: it classifies the strings, and then it relabels about 2400 anonymous prompts.
     1. Classify seed: merge the upstream `data/string-catalog.json` into the local one. Use
        a union keyed by sha256, in which local entries win. Copy the
        `data/bucket-analysis-<ver>.json` of the target. Then only the Windows-only strings
        need a classification (131 at 2.1.257).
     2. Relabel seed: make a MERGED carry-forward catalog with
        `.claude/skills/tweakcc-update/scripts/unnerfcc-seed/merge-seed-catalog.mjs`. The
        catalog holds each entry of the previous catalog of the fork, and fork ids win. It
        also holds each upstream entry for the target whose hash and id are both new
        (`git show <upstream sync commit>:data/prompts/prompts-<target>.json`). Never replace
        the fork catalog with the upstream one. The rules are keyed to the slug ids of the
        fork, and the fork relabeled some ids. Give the merged file with `--seed`. Then the
        relabel step names only the prompts that no catalog named.
   - Run `cd unnerfcc && ./upgrade.sh --version <target> --seed <merged.json> --no-bucket-analyze --jobs 4 --yes`.
     It extracts the target corpus from the genuine binary and builds
     `data/prompts/prompts-<target>.json`. You must give `--version`, `--seed`,
     `--no-bucket-analyze`, and `--yes`:
     - Without `--version`, upgrade.sh targets the npm latest version, not the intersection
       target.
     - Without `--no-bucket-analyze`, the bucket-analysis step of upgrade.sh has an AI worker
       write NEW un-nerf rules and merge them into `scripts/apply-unnerfs.py`. That is un-nerf
       content work (SKILL.md rule 7, the cold-read prohibition of the recipe). It is never
       part of a version update.

     `--jobs N` labels N relabel chunks at the same time. One chunk takes about 5 minutes,
     and each chunk is an independent job that writes its own `labels-NNN.json`. The fork
     added `--seed`, `--no-bucket-analyze`, `--jobs`, and `--ack-removed` to upgrade.sh.
   - If the run stops at the catalog gate "N ids removed vs prev, suspiciously large", the
     merged seed holds upstream entries that are not in the Windows binary. Make sure that the
     removed ids split into two groups:
     - upstream-origin ids, which are not in the previous catalog of the fork
     - fork-origin ids, which each show as MISSING in `--check` (the re-anchor part handles them)

     Then copy the fully labeled `prompts-<target>.json` out, because the run overwrites it.
     Run upgrade.sh again with `--seed` set to that copy and `--ack-removed <N>` set to the
     exact count that the gate printed. Each id carries over, the relabel worklist is 0, and
     the gate passes.
   - PowerShell writes a label file with a UTF-8 BOM. `relabel.mjs` removes the BOM (a fork fix).
   - On Windows only, `install.py --prepare` makes sure that `~/.local/bin/python3.bat`
     exists with the content `python %*`. If the file is absent, it creates it. If
     `~/.local/bin` is not on PATH, it prints a warning. Without the file, a Windows-side
     `python3` spawn fails. PowerShell then runs a bare `python3` shim that has no extension,
     and that opens "Select an app" pickers without end.
   - `python unnerfcc/scripts/apply-unnerfs.py --check` names each rule whose stock anchor
     drifted. Re-anchor those rules: keep the `unnerf` body, and take the new `stock` text
     from the new extraction. Make the edit with
     `.claude/skills/tweakcc-update/scripts/unnerfcc-reanchor/reanchor_rules.py`. The script
     finds positions with the syntax tree and asserts counts. Its operations are reanchor,
     rekey, retire, and add. Run it with `--dry-run` first. For each version, write a spec
     builder in the scratchpad of the run. The builder reads each new stock from the bytes of
     the store file. It asserts that the stock occurs exactly once and that each rewrite keeps
     the same placeholders.

     Three kinds of drift occur again and again:
     - a change of punctuation only (an em dash that became a hyphen)
     - a renamed `${...}` placeholder
     - a prompt that split into sibling fragments. This shows as a MISSING file whose text is
       now under a new slug. Search the store for the first 60 characters of the stock. If
       the stock is there, re-key the rule. If the stock drifted, re-anchor it. If the
       un-nerfed span split, add a rule on the sibling fragment.

     If the prompt of a rule is gone (see `removed.json`), retire the rule on purpose. Never
     let it fail silently.
   - A rule can have a surface that tweakcc-fixed SHADOWS. That is an override in
     `~/.tweakcc/system-reminders/*.md` with a `shadows: <id>` line in the front matter. Such
     a rule can never find its stock on a real apply, because the override replaces that text
     before unnerfcc runs. It reports `[LOST] <id>: couldNotFind`, and install.py stops the
     apply on that marker. Remove the rule, and set its `.md` body back to stock. Put a "NO RULE"
     comment in the catalog that names the override file. Four such cases exist.
     `install.py --prepare` compares the rule ids with each `shadows:` list in the LCC
     `system-reminders/`. If any id is in both, it refuses to make the apply script (skill
     1.4.2). The 2.1.258 apply of 2026-09-01 lost one rule this way.
4. **Revalidate the derived artifacts against the new version.** The re-anchor part touches
   only the files that carry rules. The prompt store round and the concept map are keyed to
   the PREVIOUS extraction. If this step does not run, they drift silently. On 2026-09-01,
   after the 2.1.258 update, the map still pointed at a 2.1.235 store round. It had 8
   prompts that were no longer in the binary, 17 changed bodies, and 1 renamed slug. Do the
   parts in this order. Each gate must exit 0 before the apply:
   - Build the new store round. Run these three commands in order:
     - `node unnerfcc/scripts/sync-version.mjs <target> --target <scratch> --no-manifest`
     - `python .claude/skills/tweakcc-update/scripts/store-provenance/build_queue.py --prompts-dir <scratch> --out <scratch>/queue.csv`
     - `python .claude/skills/tweakcc-update/scripts/store-remediation/remediate.py materialize --queue <scratch>/queue.csv --prompts-dir <scratch> --out .claude/workspace/prompt-store --batch binary-faithful --revision r<NNNN>`

     Then delete `<scratch>`. A second copy of a store round outside `.backups/` breaks rule
     9, and it makes the repo larger.
   - Compare each map row with the `batch.json` of the new round:
     - A file that is not in the new round is dropped. First search the new catalog for a
       renamed successor. Then record the reason in `reconciliation.dropped_rows` of the map.
     - A renamed slug gets the new key.
     - A row whose body sha changed is read again, and it gets a `reread` note.

     Then set the `store_dir` of the map to the new round. Make the change with a one-shot
     script that asserts its counts, and keep the script in the scratchpad of the run.
   - Write each new or changed map row from a recognition transcript with
     `python scripts/concept-map/apply_recognition.py --transcript <transcript> --map <map> --claims <claims>`.
     The script writes the row and its anchored claim. If the map already matches the
     transcript, it prints NO CHANGE. Then run
     `python scripts/anchored-claims/check_claims.py <claims>`. Exit 0 is the only pass.
   - Run the three map gates against the new round and a fresh `apply-unnerfs.py --dump-rules`
     output: `scripts/doctrine-coverage/doctrine_coverage_check.py`,
     `scripts/alignment-gate/alignment_gate.py`, and `scripts/concept-map/map_coverage_gate.py`.
     The alignment gate and the doctrine gate must be clean. The uncovered rows of the
     coverage gate are the [R002] worklist, not a failure of the version update.
5. **Apply and verify.** Run `python scripts/install.py --prepare`, which is safe inside a
   session. Close all CC sessions, and run `apply-external.bat`. `verify.py` must pass all
   four of its tests. The logs in `~/.tweakcc-gilligan/logs/` (install_*.log and
   verify_*.log) keep the full per-item record of both patchers. You do not need to capture
   output by hand.
6. **Behavior test.** In a fresh session on the patched binary, verify some known un-nerfs by
   their behavior. A string in the binary is not proof (a recipe rule). A session that you
   resume with `-c` from before the apply is not evidence about the current binary. Start a
   new process.

## tweakcc-fixed binary-format compatibility (read before you change the version logic)

`unnerfcc` sets the target version. Its prompt catalog moves slower than the `tweakcc-fixed`
catalog. Each `tweakcc-fixed` release patches one Claude Code Bun binary format. A prompt
catalog file (`data/prompts/prompts-<ver>.json`) in `tweakcc-fixed` does not prove that the
code in the working copy can patch that binary. The catalog and the extractor are separate.
Match the extractor to the target binary format, not the catalog.

The `tweakcc-fixed` commit in the working copy must match the target binary format. These
eras help only with a diagnosis, because the selection below needs no era table:
- 2.1.241 and earlier use the OLD single-module Bun format (releases up to v2.7.38).
- 2.1.246 and later use the CODE-SPLIT format (v2.8.0 and later).

A format mismatch makes the apply fail. The extractor finds no claude module and stops with this error:

```
Error: Could not extract JS from native binary: ...claude.exe (claude module not found in any of the binary modules)
```

When you see that error, run `git -C ~/.tweakcc-gilligan/repos/tweakcc-fixed log -1 --oneline`.
Compare the result with the `tweakcc-fixed checked out at <tag> (<sha>) for CC <ver>` line of
the most recent prepare log. The cause is the format mismatch. Do not edit `unnerfcc`,
rebuild, or delete `dist/`.

`--prepare` picks the `tweakcc-fixed` commit from the target. It uses the newest release tag whose newest `data/prompts/prompts-<ver>.json` is the target. That release catalogued the target binary, so its extractor parsed that format. If no such tag exists, it uses the last commit that touched the catalog file of the target (`install.select_tweakcc_ref`). No commit is hard-coded. If `tweakcc-fixed` never catalogued the target, the prepare stops with a remediation message.

The working copy, the state of `unnerfcc/engine/` in the fork, and the target version must
agree on one binary format. The engine-sync part of step 3 keeps the engine current.

## Recognition precondition and version-delta bridge (mapping against a NEW CC version)

Recognition-first mapping (recipe-concept-prompt-mapping, most recent version) reads the live
prompts of the running binary. That has a precondition. Instant recognition in context is
fully valid only for a session that runs the binary that you patch. If the target is a
NEW version that the session does not run, two rules apply:

- A prompt that did not change from the running version is still in context. Map it by
  recognition as usual.
- A NEW or CHANGED prompt is NOT in context. Get it by SELF-EXTRACTION from the genuine
  target binary. Install the target version, and run `upgrade.sh`, which extracts the corpus
  into `unnerfcc/system-prompts/`. Then diff STOCK against STOCK: the new "sync to Claude
  Code vX.Y.Z" commit against the sync commit of the previous version.

Never diff against the working tree. After an install or a replay, it holds the UN-NERFED
bodies (the rules replayed onto stock). A diff of the working tree then reports each un-nerf
as an upstream change. STOCK is THE BINARY, always.

The Piebald corpus (`repos/pi-bald/`) is QUARANTINED, and it is never a prompt source. It
diverged from the genuine binary (REQUIREMENTS.md). That divergence is one reason for the
v3.0 remediation redo.

The best path is to install first. Use the paste-ready npm command of the version gate, and
start a fresh session on the target. Then you can recognize the whole live set directly,
and the extraction diff names what changed.
