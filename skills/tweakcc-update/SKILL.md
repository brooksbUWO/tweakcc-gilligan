---
name: tweakcc-update
description: "Use when installing, applying, re-applying, or updating the tweakcc-fixed and unnerfcc patches to Claude Code, or when the user says \"tweakcc\", \"unnerfcc\", \"un-nerf Claude Code\", \"patch Claude Code\", \"apply-external.bat\", \"tweakcc-gilligan\", or reports the apply failing with errors like \"claude module not found in any of the binary modules\" or a BUN_FORMAT_INCOMPATIBLE / struct-size ambiguity. Also use to reset Claude Code to a stock version before patching. Scoped to this project."
license: MIT
allowed-tools: Bash(python ${CLAUDE_SKILL_DIR}/scripts/*)
metadata:
  version: 1.2.0
---

# tweakcc-gilligan: Dual-Patcher Skill for Claude Code

tweakcc-gilligan patches Claude Code binaries on Windows, Linux, and macOS in two stages. It applies `tweakcc-fixed` (code patches, `/clear-screen`, session memory, empty system-reminder suppression) and `unnerfcc` (system prompt un-nerfing, reasoning effort cap removal). It also fills system-reminder overrides from `lobotomized-claude-code`.

## Core rules (read first)

| # | Rule |
|---|---|
| 1 | Run `--prepare` before `--apply`. `--apply` does not build; it needs the dist that `--prepare` produces. A bare `--apply` on a fresh clone dies with "dist/index.mjs missing". |
| 2 | The apply runs OUTSIDE Claude Code with ALL sessions closed. A running `claude.exe` locks the binary. |
| 3 | The extractor commit must match the target binary format (see the binary-format section). Wrong format = "claude module not found in any of the binary modules" (tweakcc-fixed) or "BUN_FORMAT_INCOMPATIBLE / struct-size ambiguity" (unnerfcc). |
| 4 | One clone per repo. Never create a second copy, snapshot, branch-named dir, or zip of a repo. Duplicates are the top failure source: edits land in one copy, get committed from another, and drift. |
| 5 | Never hand-edit the runtime clones in `~/.tweakcc-gilligan/repos/`. They are disposable; `--prepare` rebuilds them. Edit and commit source only in its one dev location, then let the installer clone fresh from the remote. |
| 6 | Do not use git worktrees. Work in the main checkout. |
| 7 | A new-version update runs through the GSD phases. Every approved un-nerf must be encoded (`check_encode_coverage.py` exits 0) and every batch STE-clean before the patch ships. Let `gsd-verifier` gate each phase; never mark a phase complete by hand or force past a failed gate. |

## Two tasks: pick one

| Task | When | Go to |
|---|---|---|
| Re-apply existing rules to the binary | Rules are already encoded; patch a fresh or reset binary | "Re-apply to the binary" below |
| Update to a new Claude Code version | A newer CC version dropped; new prompts need remediation before patching | "Full update pipeline" below |

## Full update pipeline (new CC version)

A new CC version is a new milestone. Run `/gsd-new-milestone` to create a fresh phase set; do not re-run a prior milestone's phase numbers. Dispatch the GSD skills and let `gsd-verifier` gate each phase. Do not write ROADMAP or STATE checkboxes; a phase reaches `[x]` only on a `passed` verdict. Read [references/pipeline.md](references/pipeline.md) for the full step detail and [references/gates.md](references/gates.md) for the gate definitions. For the remediation phase's concept-to-prompt mapping dispatches, use the proven template at [references/concept-map-dispatch-prompt.md](references/concept-map-dispatch-prompt.md).

The milestone's phases carry these seven gates (phase numbers are whatever `/gsd-new-milestone` assigns):

| Gate | Action | Passes only when |
|---|---|---|
| G0 Sync | `cd unnerfcc && ./upgrade.sh` (self-extract the new stock corpus) | corpus extracted; checksum diff recorded |
| G1 Categorize | plan then execute the categorization phase | `verify-corpus-coverage.sh` exits 0 |
| G2 Remediate | plan then execute the remediation phase | `ste_gate.py` exits 0 for EVERY batch; approvals sealed |
| G3 Encode | plan then execute the encoding phase; `encode_rules.py --all --emit` | `check_encode_coverage.py` exits 0 (every non-retain rewrite has a rule) |
| G4 Reanchor | run the alignment gate, then reanchor against the regenerated store | alignment gate exits 0, then `apply-unnerfs.py --check` reports 0 FAILED / 0 MISSING |
| G5 Apply | `install.py --prepare`; close CC; `apply-external.bat`; `verify.py` | dual version lines + three content sentinels |
| G6 Behavioral verify | plan then execute the verification phase | each applied batch shows un-nerfed text, no stock text |

A phase whose `gsd-verifier` returns `gaps_found` re-plans within that same phase (GSD's gap-closure), then re-executes and re-verifies. Never force past a gate, edit a recorded seal digest, or mark a phase complete by hand.

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

Until `unnerfcc` reaches 2.1.246, the target stays at 2.1.235 or less. Thus `tweakcc-fixed` must stay at v2.7.38 or earlier. The `pin_ref="2dc353c"` argument in `install.py` `prepare_stage` enforces this. Do not remove the pin until `unnerfcc` supports 2.1.246.

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

Make sure that the tool prints dual version lines and that sentinels from all three content sources exist:

```bash
python scripts/verify.py
```

## Additional Commands

- **Check Supported Version Intersection**:
  ```bash
  python scripts/check_version_intersection.py
  ```
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
| 7 | A new-version update runs through the GSD phases; every approved un-nerf must be encoded and every batch STE-clean before the patch ships. Let `gsd-verifier` gate each phase; never force past a failed gate. |
