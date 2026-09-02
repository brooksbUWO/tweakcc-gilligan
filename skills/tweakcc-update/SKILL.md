---
name: tweakcc-update
description: "Use when installing, applying, re-applying, or updating the tweakcc-fixed and unnerfcc patches to Claude Code, or when the user says \"tweakcc\", \"unnerfcc\", \"un-nerf Claude Code\", \"patch Claude Code\", \"apply-external.bat\", \"tweakcc-gilligan\", or reports the apply failing with errors like \"claude module not found in any of the binary modules\" or a BUN_FORMAT_INCOMPATIBLE / struct-size ambiguity. Also use to reset Claude Code to a stock version before patching. Scoped to this project."
license: MIT
allowed-tools: Bash(python ${CLAUDE_SKILL_DIR}/scripts/*)
metadata:
  version: 1.4.1
---

# tweakcc-gilligan: Dual-Patcher Skill for Claude Code

tweakcc-gilligan customizes the user's own locally installed Claude Code binary on Windows, Linux, and macOS in two stages. It applies `tweakcc-fixed` (code patches, `/clear-screen`, session memory, empty system-reminder suppression) and `unnerfcc` (rewrites of the prompt text strings shipped inside the product binary; raises the default reasoning-effort configuration). It also fills system-reminder overrides from `lobotomized-claude-code`. Everything here operates on plain text strings shipped in the installed product on the user's machine; nothing reads, extracts, or infers model internals.

## Core rules (read first)

| # | Rule |
|---|---|
| 1 | Run `--prepare` before `--apply`. `--apply` does not build; it needs the dist that `--prepare` produces. A bare `--apply` on a fresh clone dies with "dist/index.mjs missing". |
| 2 | The apply runs OUTSIDE Claude Code with ALL sessions closed. A running `claude.exe` locks the binary. |
| 3 | The extractor commit must match the target binary format (see [references/version-update.md](references/version-update.md), binary-format section). Wrong format = "claude module not found in any of the binary modules" (tweakcc-fixed) or "BUN_FORMAT_INCOMPATIBLE / struct-size ambiguity" (unnerfcc). |
| 4 | One clone per repo. Never create a second copy, snapshot, branch-named dir, or zip of a repo. Duplicates are the top failure source: edits land in one copy, get committed from another, and drift. |
| 5 | Never hand-edit the runtime clones in `~/.tweakcc-gilligan/repos/`. They are disposable; `--prepare` rebuilds them. Edit and commit source only in its one dev location, then let the installer clone fresh from the remote. |
| 6 | Do not use git worktrees. Work in the main checkout. |
| 7 | A new CC version is a MECHANICAL re-patch ([references/version-update.md](references/version-update.md)): the rewrites are already decided and encoded as rules; only stock anchors move. Changing un-nerf CONTENT is a separate job (the "Remediation" section): recipe-driven, run through the GSD phases with `gsd-verifier` gating each one; never force past a failed gate. Never mix the two jobs: mixing them is how a 1-hour patch became a 9-day milestone. |
| 8 | This skill lives in two locations that must stay byte-identical: the DEV source `tweakcc-gilligan/skills/tweakcc-update/` and the INSTALLED copy `.claude/skills/tweakcc-update/`. After any edit, copy to the other side and run `python scripts/test_skill_mirror_sync.py` (exit 0). |

## Three tasks: pick one

| Task | When | Go to |
|---|---|---|
| Re-apply existing rules to the binary | Rules are already encoded; patch a fresh or reset binary | "Re-apply to the binary" below |
| Update to a new Claude Code version | A newer CC version dropped; same rewrites, new anchors | [references/version-update.md](references/version-update.md) |
| Change the un-nerfs themselves | A concept is missing, wrong, or newly adopted in the doctrine | "Remediation" below |

## Version update (new CC version)

The full runbook is [references/version-update.md](references/version-update.md): version
check (APPLICABLE vs RESULT), the remediation-only version gate, closing the readiness gap
(engine sync, Windows support check, sentinel lists, seeding both AI steps, `upgrade.sh`
flags including `--jobs` and `--ack-removed`, re-anchoring drifted rules, shadowed rules),
revalidating the derived artifacts (a new prompt-store round, the concept map re-keyed to
it, the doctrine, alignment and coverage gates green), then apply, verify, and a
behavioral spot-check from a fresh process. The update is not done while the store round
or the map still names the previous version. It also holds the tweakcc-fixed
binary-format table and the recognition precondition for mapping against a new version.

## Remediation (changing the un-nerfs themselves)

Out of scope for a version update. When un-nerf CONTENT changes (new doctrine concept,
wrong rewrite, missing coverage), the method is recipe-concept-prompt-mapping (most recent
version in `D:/Data/Programs/AI/Claude/recipes/`): recognition-first against the LIVE
binary's loaded prompts, per-concept per-file coverage with the `body-invariant` state, and
verification by behavior. See the "Recognition precondition" section of
[references/version-update.md](references/version-update.md) for the new-version case. The
rules dict in `apply-unnerfs.py` is the single source of truth for patch content; the
doctrine (`notes/tweakcc-goals-concepts-*.md`) is the coverage denominator; the concept-map
status is recorded in `.claude/workspace/prompt-store/CLAUDE.md`. For mapping dispatches,
use [references/concept-map-dispatch-prompt.md](references/concept-map-dispatch-prompt.md).
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
| G4 Reanchor | build the store round for the target version, revalidate the map rows against it, run the doctrine, coverage and alignment gates, then reanchor | doctrine and alignment gates exit 0, the coverage gate names only rows this milestone will author, then `apply-unnerfs.py --check` reports 0 FAILED / 0 MISSING |
| G5 Apply | `install.py --prepare`; close CC; `apply-external.bat`; `verify.py` | dual version lines + three content sentinels + apply accounting |
| G6 Behavioral verify | plan then execute the verification phase | each applied batch shows un-nerfed BEHAVIOR against the LIVE prompt baseline, not just string presence vs stock |

A phase whose `gsd-verifier` returns `gaps_found` re-plans within that same phase (GSD's
gap-closure), then re-executes and re-verifies. Never force past a gate, edit a recorded
seal digest, or mark a phase complete by hand.

## Re-apply to the binary

| Step | Action | Verify before moving on |
|---|---|---|
| 1 | Run `python scripts/install.py --prepare` (safe inside a CC session). | Log shows `tweakcc-fixed pinned to 452f15a`, `Recorded target version @<ver>`, and a dist build. On Windows it also verifies or creates `~/.local/bin/python3.bat`. |
| 2 | Close ALL Claude Code sessions (terminal and editor). | No `claude.exe` running. |
| 3 | Run `%USERPROFILE%\.tweakcc-gilligan\apply-external.bat` (Windows) or `~/.tweakcc-gilligan/apply-external.sh` (Unix). | `tweakcc-fixed applied successfully` then `unnerfcc applied successfully`. |
| 4 | Confirm the result. | `verify.py` prints dual version lines and all three content sources PASS. |

## STOP: if the apply fails, diagnose before re-running

| Symptom | Cause | Fix |
|---|---|---|
| `dist/index.mjs missing` | `--apply` ran without a completed `--prepare`. | Run `--prepare` first. |
| `claude module not found in any of the binary modules` | tweakcc-fixed commit does not match the target binary format. | See the binary-format section of [references/version-update.md](references/version-update.md); the pin (`452f15a`) must match the target. |
| `BUN_FORMAT_INCOMPATIBLE` / cannot determine module struct size | unnerfcc's parser hit an ambiguous Bun layout. | This is a real unnerfcc bug; fix `engine/bun-binary.mjs` in the unnerfcc dev repo, do not chase it in the installer. |
| `unrecognized binary format (neither ELF nor 64-bit Mach-O)` on Windows | An upstream engine sync clobbered the fork's PE support (upstream parses ELF/Mach-O only). | Re-port PE support into `engine/bun-binary.mjs` (runbook step 3's Windows-support check names the pieces). |
| Unpack succeeds, then `Is a directory` / EISDIR in classify or gen-catalog | Pipeline scripts are older than the engine interface (code-split engine unpacks to a directory; old scripts expect one cli.js). | Sync `upgrade.sh` and `scripts/*.mjs` from upstream too, then re-apply the fork's Windows deltas (runbook step 3). |
| Windows: pipeline "parses" a tiny script instead of the binary, or endless "Select an app" pickers | `claude` resolved to npm's sh shim, or `python3` resolved to a non-executable shim. | `win_resolve_shim` must be present in `upgrade.sh`/`install.sh`; `~/.local/bin/python3.bat` must exist (`--prepare` creates it). |
| install.py aborts with `tweakcc-fixed reported N per-item failure(s)` | A real code-patch failure (`patch: <name>: failed to ...` at column 0 of the patcher output). | Read the named patch in the log; fix it in tweakcc-fixed, not the installer. Free-text prompt descriptions are not matched. |
| install.py aborts with `unnerfcc reported N per-item failure(s)` and the log shows `[LOST] <id>: couldNotFind` | A rule's stock text is absent from the bundle, usually because a tweakcc-fixed override `shadows:` that surface. | Runbook step 3, "shadowed rules": remove the rule, reset its `.md` to stock, leave a NO RULE comment. |

Do NOT "fix" a failed apply by editing `unnerfcc` blindly, rebuilding, deleting `dist/`, or hunting the minified error. First read the symptom row above; the cause is almost always the format pin or a skipped `--prepare`.

## Quick Start

### Stage 1: Prepare the Setup (Safe inside active Claude Code session)

Run the preparer. It checks preflights, verifies the Windows `python3.bat` shim, syncs the patcher repositories, records the target Claude Code version, fills system reminders, and generates the external apply script:

```bash
python scripts/install.py --prepare
```

The prepare stage syncs the repositories first. Then it computes the greatest common supported version from the fresh catalogs, so the recorded target never comes from stale clones. It records the target to `~/.tweakcc-gilligan/target_version.txt`. The apply stage requires that record. Failures are loud. If a version-source repository cannot sync with its remote, or a version check cannot produce a result, the prepare stops with a remediation message. It does not record a stale or missing target.

The prepare log also reports whether the tracked `unnerfcc` branch is in sync with its real upstream, read from the upstream's commit subjects. This line is advisory only. The catalog intersection decides the target.

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

Both patchers exit 0 even when individual items fail, so install.py classifies their output itself. tweakcc-fixed failures are the column-0 lines `patch: <name>: failed to ...`, `Error: ...`, `\u2716 Error ...`; its per-prompt description lines are free text and are never matched. unnerfcc failures are `[FAILED`, `[LOST]`, `UN-NERF(S) FAILED TO SPLICE`, `Rules FAILED : N`, `Missing files : N`. Version-conditional skips (`Could not find system prompt`, macOS-only prompts on Windows) are counted in one WARN line, not treated as failures.

### Stage 3: Verify the Patched Binary

```bash
python scripts/verify.py
```

Four checks, all must pass: dual version lines, sentinels from all three content sources
(`senior-engineer standard` for unnerfcc, `+ tweakcc v` for tweakcc-fixed, the claudeMd
context lead-in for system-reminders; each is absent from a stock binary), and apply
accounting (the most recent install log must hold both patchers' full per-item output with
zero failure markers, classified by the same patterns install.py uses). The accounting check
is the completeness oracle; a screenshot of a past session's startup banner is NOT one. The
startup banner lists only tweakcc-fixed's customized `.md` prompt files; unnerfcc's rules
patch the binary directly and never appear there. verify.py tees its own output to
`~/.tweakcc-gilligan/logs/verify_<timestamp>.log`.

## Additional Commands

- **Check Supported Version Intersection**:
  ```bash
  python scripts/check_version_intersection.py
  ```
  Two labeled lines: `APPLICABLE` is the newest version the local clones can patch now
  (minimum of the fork catalog and the pinned tweakcc-fixed catalog); `RESULT` is the
  greatest common version the two upstreams support (commit subjects and catalog filenames,
  higher signal wins). When both upstream signals resolved, the final output line is the
  paste-ready `npm install -g @anthropic-ai/claude-code@<target>` command; surface it to the
  user VERBATIM. When an upstream could not be read, the script prints `RESULT-UNCERTAIN`
  (a local-catalog fallback that may understate the target), exits 1, prints no install
  line, and `--prepare` stops rather than record it; re-run when GitHub is reachable.
  The READINESS line reports whether APPLICABLE has caught up to RESULT; a lag is closed by
  the version update runbook.
- **Skill mirror check**:
  ```bash
  python scripts/test_skill_mirror_sync.py
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
  - `repos/`: Working clones of `tweakcc-fixed`, `unnerfcc`, and `lobotomized-claude-code`. `unnerfcc` and `lobotomized-claude-code` fast-forward to their remotes on every prepare. `tweakcc-fixed` does NOT. `pin_ref` in `prepare_stage` pins it to `452f15a` (CC 2.1.258 catalog, code-split extractor). Thus prepare cannot advance it to a release whose extractor does not match the target binary format.
  - `logs/`: Timestamped installation logs and active PID tracking.
  - `manifest.json`: Installation record, target binary path, and commit SHAs.
  - `target_version.txt`: The Claude Code version the prepare stage recorded and the apply stage requires.
  - `install.lock`: Active operation lockfile. It self-clears when older than four hours.
- `~/.tweakcc/`:
  - `config.json`: Configuration for `tweakcc-fixed` (`ccInstallationPath`).
  - `system-reminders/`: Live system-reminder override `.md` files. An override with a `shadows: <id>` frontmatter line replaces that prompt surface before unnerfcc runs (see the runbook's shadowed-rules item).
- `~/.local/bin/python3.bat` (Windows): the `python3` shim `--prepare` verifies or creates.

## Resetting Claude Code to Stock

To return the binary to its un-modified published state:

```bash
npm install -g @anthropic-ai/claude-code@<version>
```
