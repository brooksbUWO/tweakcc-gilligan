---
name: tweakcc-update
description: "Use when installing, applying, re-applying, or updating the tweakcc-fixed and unnerfcc patches to Claude Code, or when the user says \"tweakcc\", \"unnerfcc\", \"un-nerf Claude Code\", \"patch Claude Code\", \"apply-external.bat\", \"tweakcc-gilligan\", or reports the apply failing with errors like \"claude module not found in any of the binary modules\" or a BUN_FORMAT_INCOMPATIBLE / struct-size ambiguity. Also use to reset Claude Code to a stock version before patching. Scoped to this project."
license: MIT
allowed-tools: Bash(python ${CLAUDE_SKILL_DIR}/scripts/*)
metadata:
  version: 1.3.0
---

# tweakcc-gilligan: Dual-Patcher Skill for Claude Code

tweakcc-gilligan customizes the user's own locally installed Claude Code binary on Windows, Linux, and macOS in two stages. It applies `tweakcc-fixed` (code patches, `/clear-screen`, session memory, empty system-reminder suppression) and `unnerfcc` (rewrites of the prompt text strings shipped inside the product binary; raises the default reasoning-effort configuration). It also fills system-reminder overrides from `lobotomized-claude-code`. Everything here operates on plain text strings shipped in the installed product on the user's machine; nothing reads, extracts, or infers model internals.

## Core rules (read first)

| # | Rule |
|---|---|
| 1 | Run `--prepare` before `--apply`. `--apply` does not build; it needs the dist that `--prepare` produces. A bare `--apply` on a fresh clone dies with "dist/index.mjs missing". |
| 2 | The apply runs OUTSIDE Claude Code with ALL sessions closed. A running `claude.exe` locks the binary. |
| 3 | The extractor commit must match the target binary format (see the binary-format section). Wrong format = "claude module not found in any of the binary modules" (tweakcc-fixed) or "BUN_FORMAT_INCOMPATIBLE / struct-size ambiguity" (unnerfcc). |
| 4 | One clone per repo. Never create a second copy, snapshot, branch-named dir, or zip of a repo. Duplicates are the top failure source: edits land in one copy, get committed from another, and drift. |
| 5 | Never hand-edit the runtime clones in `~/.tweakcc-gilligan/repos/`. They are disposable; `--prepare` rebuilds them. Edit and commit source only in its one dev location, then let the installer clone fresh from the remote. |
| 6 | Do not use git worktrees. Work in the main checkout. |
| 7 | A new CC version is a MECHANICAL re-patch (the "Version update runbook" below): the rewrites are already decided and encoded as rules; only stock anchors move. Changing un-nerf CONTENT is a separate job (the "Remediation" section): recipe-driven, run through the GSD phases with `gsd-verifier` gating each one; never force past a failed gate. Never mix the two jobs: mixing them is how a 1-hour patch became a 9-day milestone. |

## Three tasks: pick one

| Task | When | Go to |
|---|---|---|
| Re-apply existing rules to the binary | Rules are already encoded; patch a fresh or reset binary | "Re-apply to the binary" below |
| Update to a new Claude Code version | A newer CC version dropped; same rewrites, new anchors | "Version update runbook" below |
| Change the un-nerfs themselves | A concept is missing, wrong, or newly adopted in the doctrine | "Remediation" below |

## Version update runbook (new CC version)

A version update is diff-and-re-anchor, not a remediation. The rewrites live as rules in
`unnerfcc/scripts/apply-unnerfs.py` and do not change; only their stock anchors move when
upstream rewords a prompt. No milestone, no phases.

1. **Version check.** Run `python scripts/check_version_intersection.py`. The RESULT line is
   the TARGET (minimum of the two upstreams' supported versions). The READINESS line says
   whether the local fork catalogs have caught up.
2. **Version gate.** Compare `claude --version` to the target. If they differ, STOP and give
   the user (a) the one-line reason, (b) the script's paste-ready
   `npm install -g @anthropic-ai/claude-code@<target>` line VERBATIM on its own line, and
   (c) the instruction: close ALL Claude Code sessions, run the command, start a fresh
   session, run `/tweakcc-update` again. Do no prompt work on a wrong-version binary: the
   extraction, the anchor checks, and the session's own loaded context would all be from the
   wrong version.
3. **Close the readiness gap** (only when READINESS reports a lag). In order:
   - If the target crossed a binary-format boundary (see the format table below), the
     fork's engine must support it first: sync `unnerfcc/engine/` from the fork's upstream
     (lukehutch/unnerfcc, which tracks CC releases closely) into the dev clone at
     `unnerfcc/`, engine code only, never prompts. Then update the `pin_ref` in
     `install.py` to the tweakcc-fixed commit matching the format.
   - `cd unnerfcc && ./upgrade.sh` self-extracts the target corpus from the genuine binary
     and builds `data/prompts/prompts-<target>.json`.
   - `python unnerfcc/scripts/apply-unnerfs.py --check` names every rule whose stock anchor
     drifted. Re-anchor those rules (same `unnerf` body, updated `stock` text from the new
     extraction); a rule whose prompt vanished (see `removed.json`) is retired deliberately,
     never left to fail silently.
4. **Apply and verify.** `python scripts/install.py --prepare` (safe in-session); close all
   CC sessions; run `apply-external.bat`; `verify.py` must pass all four checks. Both
   patchers' full per-item accounting is captured automatically in
   `~/.tweakcc-gilligan/logs/` (install_*.log and verify_*.log); no manual output capture.
5. **Behavioral spot-check.** In a fresh session on the patched binary, confirm a few known
   un-nerfs by behavior (recipe rule: string presence is not proof).

## Remediation (changing the un-nerfs themselves)

Out of scope for a version update. When un-nerf CONTENT changes (new doctrine concept,
wrong rewrite, missing coverage), the method is recipe-concept-prompt-mapping (most recent
version in `D:/Data/Programs/AI/Claude/recipes/`): recognition-first against the LIVE
binary's loaded prompts, per-concept per-file coverage with the `body-invariant` state, and
verification by behavior. See "Recognition precondition and version-delta bridge" below for
the new-version case. The rules dict in `apply-unnerfs.py` is the single source of truth
for patch content; the doctrine (`notes/tweakcc-goals-concepts-*.md`) is the coverage
denominator; the concept-map status is recorded in
`.claude/workspace/prompt-store/CLAUDE.md`. For mapping dispatches, use
[references/concept-map-dispatch-prompt.md](references/concept-map-dispatch-prompt.md).
Never author rewrites from a store cold-read, and never gate a rewrite against stock only:
a gate that does not compare against the live prompt green-lights regressions (this
happened; see the 2026-08-28 forensics report).

Remediation-scale work runs through the GSD phases. Run `/gsd-new-milestone` to create a
fresh phase set; do not re-run a prior milestone's phase numbers. Dispatch the GSD skills
and let `gsd-verifier` gate each phase. Do not write ROADMAP or STATE checkboxes; a phase
reaches `[x]` only on a `passed` verdict. Read [references/pipeline.md](references/pipeline.md)
for the full step detail and [references/gates.md](references/gates.md) for the gate
definitions. The milestone's phases carry these seven gates (phase numbers are whatever
`/gsd-new-milestone` assigns):

| Gate | Action | Passes only when |
|---|---|---|
| G0 Sync | `cd unnerfcc && ./upgrade.sh` (self-extract the new stock corpus) | corpus extracted; checksum diff recorded |
| G1 Categorize | plan then execute the categorization phase | `verify-corpus-coverage.sh` exits 0 |
| G2 Remediate | plan then execute the remediation phase | `ste_gate.py` exits 0 for EVERY batch; approvals sealed |
| G3 Encode | plan then execute the encoding phase; `encode_rules.py --all --emit` | `check_encode_coverage.py` exits 0 (every non-retain rewrite has a rule) |
| G4 Reanchor | run the alignment gate, then reanchor against the regenerated store | alignment gate exits 0, then `apply-unnerfs.py --check` reports 0 FAILED / 0 MISSING |
| G5 Apply | `install.py --prepare`; close CC; `apply-external.bat`; `verify.py` | dual version lines + three content sentinels + apply accounting |
| G6 Behavioral verify | plan then execute the verification phase | each applied batch shows un-nerfed BEHAVIOR against the LIVE prompt baseline, not just string presence vs stock |

A phase whose `gsd-verifier` returns `gaps_found` re-plans within that same phase (GSD's
gap-closure), then re-executes and re-verifies. Never force past a gate, edit a recorded
seal digest, or mark a phase complete by hand.

## Re-apply to the binary

| Step | Action | Verify before moving on |
|---|---|---|
| 1 | Run `python scripts/install.py --prepare` (safe inside a CC session). | Log shows `tweakcc-fixed pinned to 2dc353c`, `Recorded target version @<ver>`, and a dist build. |
| 2 | Close ALL Claude Code sessions (terminal and editor). | No `claude.exe` running. |
| 3 | Run `%USERPROFILE%\.tweakcc-gilligan\apply-external.bat` (Windows) or `~/.tweakcc-gilligan/apply-external.sh` (Unix). | `tweakcc-fixed applied successfully` then `unnerfcc applied successfully`. |
| 4 | Confirm the result. | `verify.py` prints dual version lines and all three content sources PASS. |

## STOP: if the apply fails, diagnose before re-running

| Symptom | Cause | Fix |
|---|---|---|
| `dist/index.mjs missing` | `--apply` ran without a completed `--prepare`. | Run `--prepare` first. |
| `claude module not found in any of the binary modules` | tweakcc-fixed commit does not match the target binary format. | See the binary-format section; the pin (`2dc353c`) must match the target. |
| `BUN_FORMAT_INCOMPATIBLE` / cannot determine module struct size | unnerfcc's parser hit an ambiguous Bun layout. | This is a real unnerfcc bug; fix `engine/bun-binary.mjs` in the unnerfcc dev repo, do not chase it in the installer. |

Do NOT "fix" a failed apply by editing `unnerfcc` blindly, rebuilding, deleting `dist/`, or hunting the minified error. First read the symptom row above; the cause is almost always the format pin or a skipped `--prepare`.

## Quick Start

### Stage 1: Prepare the Setup (Safe inside active Claude Code session)

Run the preparer. It checks preflights, syncs the patcher repositories, records the target Claude Code version, fills system reminders, and generates the external apply script:

```bash
python scripts/install.py --prepare
```

The prepare stage syncs the repositories first. Then it computes the greatest common supported version from the fresh catalogs, so the recorded target never comes from stale clones. It records the target to `~/.tweakcc-gilligan/target_version.txt`. The apply stage requires that record. Failures are loud. If a version-source repository cannot sync with its remote, or a version check cannot produce a result, the prepare stops with a remediation message. It does not record a stale or missing target.

The prepare log also reports whether the tracked `unnerfcc` branch is in sync with its real upstream. It reads this from the upstream's "sync to Claude Code vX.Y.Z" commit subjects. This line is advisory only. The catalog intersection decides the target. The line can warn that the upstream synced to a newer version than the tracked branch supports. Then you must update the tracked branch before you can target that newer version.

### tweakcc-fixed binary-format compatibility (read before you change the version logic)

`unnerfcc` sets the target version. Its prompt catalog moves slower than the `tweakcc-fixed` catalog. Each `tweakcc-fixed` release patches one Claude Code Bun binary format. A prompt catalog file (`data/prompts/prompts-<ver>.json`) in `tweakcc-fixed` does not prove the checked-out code can patch that binary. The catalog and the extractor are separate. Match the extractor to the target binary format, not the catalog.

The checked-out `tweakcc-fixed` commit must match the target binary format:

- Target 2.1.241 or less (OLD single-module Bun format): use `tweakcc-fixed` at `2dc353c` (v2.7.38) or earlier.
- Target 2.1.246 or more (CODE-SPLIT Bun format): use `tweakcc-fixed` at `890c928` or later.

A format mismatch fails the apply. The extractor finds no claude module and stops with this error:

```
Error: Could not extract JS from native binary: ...claude.exe (claude module not found in any of the binary modules)
```

When you see that error, run `git -C ~/.tweakcc-gilligan/repos/tweakcc-fixed log -1 --oneline`. Compare the commit against the target format. The cause is the format mismatch. Do not edit `unnerfcc`, rebuild, or delete `dist/`.

The `pin_ref` argument in `install.py` `prepare_stage` (currently `2dc353c`, v2.7.38, OLD
single-module format) enforces the match. Change the pin ONLY together with the engine-sync
step of the runbook: a 2.1.246+ target needs BOTH the fork's `unnerfcc/engine/` synced to a
code-split-capable state AND the pin moved to `890c928` or later. Moving the pin alone
trades one format error for the other.

### Recognition precondition and version-delta bridge (mapping against a NEW CC version)

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

### Stage 2: Apply Binary Patches (Outside active Claude Code session)

A running `claude.exe` or active session locks the executable. Close all Claude Code sessions, then run the generated external script:

On Windows:
```cmd
%USERPROFILE%\.tweakcc-gilligan\apply-external.bat
```

On Linux/macOS:
```bash
~/.tweakcc-gilligan/apply-external.sh
```

Or invoke `install.py` with `--apply` directly in a shell outside Claude Code:

```bash
python scripts/install.py --apply
```

The apply stage accepts only the version the prepare stage recorded. If `target_version.txt` is missing or unreadable, or the reset to the stock version fails, the apply stops with a remediation message. It does not patch an unknown binary. Re-run `--prepare` first.

### Stage 3: Verify the Patched Binary

```bash
python scripts/verify.py
```

Four checks, all must pass: dual version lines, sentinels from all three content sources,
and apply accounting (the most recent install log must hold both patchers' full per-item
output with zero failure markers). The accounting check is the completeness oracle; a
screenshot of a past session's startup banner is NOT one (the banner's item list
legitimately changes across versions). verify.py tees its own output to
`~/.tweakcc-gilligan/logs/verify_<timestamp>.log`.

## Additional Commands

- **Check Supported Version Intersection**:
  ```bash
  python scripts/check_version_intersection.py
  ```
  The RESULT line is the greatest common version: the MINIMUM of the versions the two patcher
  projects' upstreams support, read from their commit subjects ("sync to Claude Code vX.Y.Z",
  "prompts: catalogue CC X.Y.Z"). The final output line is the paste-ready
  `npm install -g @anthropic-ai/claude-code@<target>` command; surface it to the user VERBATIM.
  The READINESS line reports whether the local fork catalogs have caught up to the target; a lag
  is closed by installing the target, then `upgrade.sh` (extract the new corpus) and re-anchoring
  the rules. The local catalog intersection is readiness information, never the target.
- **Clear Poisoned Backup Snapshot**:
  ```bash
  python scripts/install.py --clean-backup
  ```

## Termination Guarantee and Exit Codes

Every script accepts `--max-seconds <n>` (a hard wall-clock ceiling: default 1800 for `install.py`, 120 for the others) and `--watchdog-probe <n>` (a diagnostic idle that tests the ceiling). Exit codes: 0 success, 1 failure with a logged reason, 2 usage error (invalid flag values), 3 terminated at the wall-clock ceiling. A run killed at the ceiling can leave `install.lock` behind. The next run removes a lock older than four hours. No manual cleanup is necessary. The black-box suite in `scripts/test_termination_contract.py` pins the contract:

```bash
python scripts/test_termination_contract.py
```

## Runtime Layout

- `~/.tweakcc-gilligan/` (Override via `TWEAKCC_GILLIGAN_HOME` environment variable):
  - `repos/`: Working clones of `tweakcc-fixed`, `unnerfcc`, and `lobotomized-claude-code`. `unnerfcc` and `lobotomized-claude-code` fast-forward to their remotes on every prepare. `tweakcc-fixed` does NOT. `pin_ref` in `prepare_stage` pins it to `2dc353c` (v2.7.38). Thus prepare cannot advance it to a release whose extractor does not match the target binary format (see "tweakcc-fixed binary-format compatibility").
  - `logs/`: Timestamped installation logs and active PID tracking.
  - `manifest.json`: Installation record, target binary path, and commit SHAs.
  - `target_version.txt`: The Claude Code version the prepare stage recorded and the apply stage requires.
  - `install.lock`: Active operation lockfile. It self-clears when older than four hours.
- `~/.tweakcc/`:
  - `config.json`: Configuration for `tweakcc-fixed` (`ccInstallationPath`).
  - `system-reminders/`: Live system-reminder override `.md` files.

## Resetting Claude Code to Stock

To return the binary to its un-modified published state:

```bash
npm install -g @anthropic-ai/claude-code@<version>
```

## Core rules (restated)

| # | Rule |
|---|---|
| 1 | Run `--prepare` before `--apply`. A bare `--apply` on a fresh clone dies with "dist/index.mjs missing". |
| 2 | The apply runs OUTSIDE Claude Code with ALL sessions closed. |
| 3 | The extractor commit must match the target binary format. Wrong format = "claude module not found" or "BUN_FORMAT_INCOMPATIBLE". |
| 4 | One clone per repo. Never create a second copy, snapshot, branch-named dir, or zip. |
| 5 | Never hand-edit the runtime clones in `~/.tweakcc-gilligan/repos/`; edit and commit source in its one dev location, then let the installer clone fresh. |
| 6 | Do not use git worktrees. |
| 7 | A new CC version is a mechanical re-patch (Version update runbook); changing un-nerf content is Remediation (recipe-driven, GSD-gated, live-binary recognition; never force past a failed gate). Never mix the two. |
