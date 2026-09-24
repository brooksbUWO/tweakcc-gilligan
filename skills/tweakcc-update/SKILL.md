---
name: tweakcc-update
description: "Use to install, apply, re-apply, or update the tweakcc-fixed and unnerfcc patches to Claude Code. Use when the user says \"tweakcc\", \"unnerfcc\", \"un-nerf Claude Code\", \"patch Claude Code\", \"apply-external.bat\", or \"tweakcc-gilligan\". Use when the apply fails with \"claude module not found in any of the binary modules\" or a BUN_FORMAT_INCOMPATIBLE / struct-size ambiguity. Also use to reset Claude Code to a stock version before a patch. Scoped to this project."
license: MIT
allowed-tools: Bash(python ${CLAUDE_SKILL_DIR}/scripts/*)
metadata:
  version: 1.7.0
---

# tweakcc-gilligan: Dual-Patcher Skill for Claude Code

tweakcc-gilligan customizes the locally installed Claude Code binary of the user on Windows, Linux, and macOS. It works in two stages:

- It applies `tweakcc-fixed`: code patches, `/clear-screen`, session memory, and suppression of empty system-reminders.
- It applies `unnerfcc`: rewrites of the prompt text strings in the product binary. It also raises the default reasoning-effort configuration.

It also fills system-reminder overrides from `lobotomized-claude-code`. All of this work is on plain text strings in the installed product on the machine of the user. Nothing reads, extracts, or infers model internals.

**Portability warning:** only the Claude Code CLI enforces `allowed-tools`.
Agent SDKs ignore it. If you load this skill through the Python or TypeScript SDK,
copy these restrictions into `ClaudeAgentOptions` (Python) or `Options`
(TypeScript). If you do not, the skill runs with all tools.

## Core rules (read first)

| # | Rule |
|---|---|
| 1 | Run `--prepare` before `--apply`. `--apply` does not build. It needs the dist that `--prepare` makes. A bare `--apply` on a fresh clone stops with "dist/index.mjs missing". |
| 2 | The apply runs OUTSIDE Claude Code with ALL sessions closed. A running `claude.exe` locks the binary. |
| 3 | The extractor commit must match the target binary format (see the binary-format section of [references/version-update.md](references/version-update.md)). A wrong format gives "claude module not found in any of the binary modules" (tweakcc-fixed) or "BUN_FORMAT_INCOMPATIBLE / struct-size ambiguity" (unnerfcc). |
| 4 | One clone per repo. Never make a second copy, snapshot, branch-named directory, or zip of a repo. Duplicates are the top source of failures. An edit lands in one copy, a commit goes out from another, and the copies drift. |
| 5 | Never edit the runtime clones in `~/.tweakcc-gilligan/repos/` by hand. They are disposable, and `--prepare` builds them again. Edit and commit source only in its one dev location. Then let the installer clone fresh from the remote. |
| 6 | Do not use git worktrees. Work in the main working copy. |
| 7 | A new CC version is a MECHANICAL re-patch ([references/version-update.md](references/version-update.md)). The rewrites are already decided and encoded as rules. Only the stock anchors move. A change to un-nerf CONTENT is a separate job (the "Remediation" section). That job follows a recipe and runs the seven remediation gates in order. Never force past a failed gate. Never mix the two jobs. A mix of the two made a 1-hour patch into a 9-day job. |
| 8 | This skill is in two locations that must stay byte-identical: the DEV source `tweakcc-gilligan/skills/tweakcc-update/` and the INSTALLED copy `.claude/skills/tweakcc-update/`. After each edit, copy the change to the other side. Then run `python scripts/test_skill_mirror_sync.py`, which must exit 0. |

## Before the first tool call

State a plan before the first tool call. A stated plan is compared with the gate table. An unstated plan gets a reason after the fact.

```
[PLAN]
Task: <re-apply | version update | remediation>
Target CC version: <from check_version_intersection.py RESULT, or "not yet known">
Stage: <prepare | apply | verify | gate G0..G6>
Proof before moving on: <the exact log line, exit code, or verify.py output that ends this stage>
```

## Three tasks: pick one

| Task | When | Go to |
|---|---|---|
| Re-apply existing rules to the binary | The rules are already encoded. You patch a fresh or reset binary. | "Re-apply to the binary" below |
| Update to a new Claude Code version | A newer CC version is out. The rewrites stay the same, and the anchors are new. | [references/version-update.md](references/version-update.md) |
| Change the un-nerfs themselves | A concept is missing, wrong, or new in the doctrine. | "Remediation" below |

## Version update (new CC version)

The full runbook is [references/version-update.md](references/version-update.md). It has these parts:

- the version gate input (APPLICABLE and RESULT) and the version gate for remediation only
- the parts that close the readiness gap:
  - the engine sync and the Windows support test
  - the sentinel lists and the seeds for both AI steps
  - the `upgrade.sh` flags, with `--jobs` and `--ack-removed`
  - the re-anchor of drifted rules and the shadowed rules
- the new validation of the derived artifacts: a new prompt-store round, the concept map keyed to that round, and green doctrine, alignment, and coverage gates
- the apply, the verify, and a behavior test from a fresh process

The update is not done while the store round or the map still names the previous version. The runbook also holds the tweakcc-fixed binary-format table and the recognition precondition for a map against a new version.

## Remediation (changing the un-nerfs themselves)

This section applies only inside the tweakcc project working copy. The gate scripts are in the `scripts/` directory of this skill. They read data that exist only in that workspace: `.claude/workspace/remediation/`, `.claude/workspace/prompt-store/`, and `D:/Data/Programs/AI/Claude/recipes/`. A user who installed the published plugin in a different location has none of those paths. That user must use the "Re-apply to the binary" task and the install steps of the version-update runbook.

A remediation is out of scope for a version update. When un-nerf CONTENT changes (a new doctrine concept, a wrong rewrite, missing coverage), the method is recipe-concept-prompt-mapping. Use its most recent version in `D:/Data/Programs/AI/Claude/recipes/`. The method has three parts:

- recognition-first against the prompts that the LIVE binary loads
- coverage for each concept and each file, with the `body-invariant` state
- verification by behavior

For the new-version case, see the "Recognition precondition" section of [references/version-update.md](references/version-update.md). For a map dispatch, use [references/concept-map-dispatch-prompt.md](references/concept-map-dispatch-prompt.md).

### Remediation rules

| Rule | Why |
|---|---|
| `unnerfcc/rules/<id>.json` (one file for each rule id) is the single source of truth for patch content. `apply-unnerfs.py` loads and applies it. | The rules moved to one JSON file for each id on 2026-09-02. `apply-unnerfs.py` is the loader, not the store. |
| Write rewrites only from the prompts that the LIVE binary loads. Never write them from a cold read of the store. | A cold-read map was found invalid in 2026-08, and the work was done again. |
| Gate a rewrite against the live prompt, never against stock only. | A gate against stock only lets regressions pass (2026-08-28 forensics). |
| The doctrine (`notes/tweakcc-goals-concepts-*.md`) is the coverage denominator. The status of the concept map is in `.claude/workspace/prompt-store/CLAUDE.md`. | Both are the recorded state that a gate compares against. |

Run the seven gates below in order. Each gate must exit 0 before the next gate runs. For the full step detail, read [references/pipeline.md](references/pipeline.md). For the gate definitions, read [references/gates.md](references/gates.md).

| Gate | Action | Passes only when |
|---|---|---|
| G0 Sync | `cd unnerfcc && ./upgrade.sh` (self-extract the new stock corpus) | the corpus is extracted, and a sha256 diff is recorded |
| G1 Categorize | put each corpus file into one batch or into the exclusion list | `verify-corpus-coverage.sh` exits 0 |
| G2 Remediate | write, review, and seal the rewrites of each batch | `ste_gate.py` exits 0 for EVERY batch, and the approvals are sealed |
| G3 Encode | run `encode_rules.py --all --emit` | `check_encode_coverage.py` exits 0 (each non-retain rewrite has a rule) |
| G4 Reanchor | build the store round for the target version, and compare the map rows with it. Run the doctrine, coverage, and alignment gates. Then reanchor. | the doctrine and alignment gates exit 0, and the coverage gate names only rows that this remediation will write. Then `apply-unnerfs.py --check` reports 0 FAILED and 0 MISSING. |
| G5 Apply | `install.py --prepare`, close CC, `apply-external.bat`, `verify.py` | the two version lines, the three content sentinels, and the apply record all pass |
| G6 Behavioral verify | test the behavior of each applied batch in a fresh session | each applied batch shows un-nerfed BEHAVIOR against the LIVE prompt baseline, not only string presence against stock |

Before you write or review an un-nerf rewrite in G2, you MUST invoke the Skill tool:
Skill(skill="simple-english:simple-english"). This is a blocking requirement. Follow the
instructions the skill returns. `ste_gate.py` loads the linter of the same plugin, so the
simple-english plugin must be installed.

If a gate fails, fix the cause and run that gate again. Never force past a gate, edit a recorded seal digest, or mark a gate as passed by hand.

## Re-apply to the binary

| Step | Action | Proof before you go on |
|---|---|---|
| 1 | Run `python scripts/install.py --prepare`. It is safe inside a CC session. | The log shows `Recorded target version @<ver>`, `tweakcc-fixed checked out at <tag> (<sha>) for CC <ver>`, and a dist build. On Windows, it also makes sure that `~/.local/bin/python3.bat` exists. |
| 2 | Close ALL Claude Code sessions (terminal and editor). | No `claude.exe` runs. |
| 3 | Run `%USERPROFILE%\.tweakcc-gilligan\apply-external.bat` (Windows) or `~/.tweakcc-gilligan/apply-external.sh` (Unix). | `tweakcc-fixed applied successfully`, and then `unnerfcc applied successfully`. |
| 4 | Verify the result. | `verify.py` prints the two version lines, and all three content sources PASS. |

## STOP: if the apply fails, find the cause before you run it again

| Symptom | Cause | Fix |
|---|---|---|
| `dist/index.mjs missing` | `--apply` ran without a completed `--prepare`. | Run `--prepare` first. |
| `claude module not found in any of the binary modules` | The tweakcc-fixed commit does not match the target binary format. | See the binary-format section of [references/version-update.md](references/version-update.md). `--prepare` selects the release that catalogued the target. So this row now has two causes. The `checked out at` line in the prepare log came from an earlier `--prepare`, so run it again. Or `tweakcc-fixed` catalogued the target again on a different format. |
| `BUN_FORMAT_INCOMPATIBLE` / cannot determine module struct size | The unnerfcc parser found an ambiguous Bun layout. | This is a real unnerfcc bug. Fix `engine/bun-binary.mjs` in the unnerfcc dev repo. Do not look for it in the installer. |
| `unrecognized binary format (neither ELF nor 64-bit Mach-O)` on Windows | An upstream engine sync removed the PE support of the fork. The upstream engine parses only ELF and Mach-O. | Port the PE support into `engine/bun-binary.mjs` again. The Windows support part of runbook step 3 names the parts. |
| The unpack succeeds, and then `Is a directory` / EISDIR occurs in classify or gen-catalog | The pipeline scripts are older than the engine interface. The code-split engine unpacks to a directory, and the old scripts expect one cli.js. | Sync `upgrade.sh` and `scripts/*.mjs` from upstream too. Then apply the Windows deltas of the fork again (runbook step 3). |
| Windows: the pipeline parses a small script in place of the binary, or "Select an app" pickers open without end | `claude` resolved to the sh shim of npm, or `python3` resolved to a shim that cannot run. | `win_resolve_shim` must be in `upgrade.sh` and `install.sh`. `~/.local/bin/python3.bat` must exist (`--prepare` creates it). |
| install.py stops with `tweakcc-fixed reported N per-item failure(s)` | A real code-patch failure (`patch: <name>: failed to ...` at column 0 of the patcher output). | Read the named patch in the log. Fix it in tweakcc-fixed, not in the installer. The script does not match free-text prompt descriptions. |
| install.py stops with `unnerfcc reported N per-item failure(s)`, and the log shows `[LOST] <id>: couldNotFind` | The stock text of a rule is not in the bundle. Usually a tweakcc-fixed override `shadows:` that surface. | Runbook step 3, "shadowed rules": remove the rule, set its `.md` back to stock, and put a NO RULE comment. From 1.4.2, `--prepare` refuses to make the apply script while such a rule exists. So this row applies only to an apply script from an older prepare. |

Do NOT "fix" a failed apply with blind edits to `unnerfcc` or a rebuild. Do not delete `dist/`, and do not search through the minified error. First read the symptom row above. The cause is almost always the format pin or a skipped `--prepare`.

## Quick Start

### Stage 1: Prepare the setup (safe inside an active Claude Code session)

Run the preparer. It does these steps:

- It runs the preflight tests.
- It makes sure that the Windows `python3.bat` shim exists.
- It syncs the patcher repositories.
- It records the target Claude Code version.
- It fills the system reminders.
- It makes the external apply script.

```bash
python scripts/install.py --prepare
```

The prepare stage syncs the repositories first. Then it calculates the greatest common supported version from the fresh catalogs, so the recorded target never comes from stale clones. It records the target to `~/.tweakcc-gilligan/target_version.txt`. The apply stage requires that record. Failures are loud. If a version-source repository cannot sync with its remote, the prepare stops with a remediation message. If the version gate cannot give a result, the prepare also stops. It does not record a stale or missing target.

The prepare log also gives the sync state of the tracked `unnerfcc` branch against its real upstream. It reads that from the commit subjects of the upstream. This line is for information only. The catalog intersection decides the target.

### Stage 2: Apply the binary patches (outside an active Claude Code session)

A running `claude.exe` or an active session locks the executable. Close all Claude Code sessions, and then run the external script:

On Windows:
```cmd
%USERPROFILE%\.tweakcc-gilligan\apply-external.bat
```

On Linux/macOS:
```bash
~/.tweakcc-gilligan/apply-external.sh
```

Or run `install.py` with `--apply` directly in a shell outside Claude Code:

```bash
python scripts/install.py --apply
```

The apply stage accepts only the version that the prepare stage recorded. The apply stops with a remediation message in three cases: `target_version.txt` is missing, it cannot be read, or the reset to the stock version fails. It does not patch an unknown binary. Run `--prepare` again first.

Both patchers exit 0 for a run with failed items. So install.py classifies their output itself:

- tweakcc-fixed failures are the column-0 lines `patch: <name>: failed to ...`, `Error: ...`, and `\u2716 Error ...`. Its description lines for each prompt are free text, and the script never matches them.
- unnerfcc failures are `[FAILED`, `[LOST]`, `UN-NERF(S) FAILED TO SPLICE`, `Rules FAILED : N`, and `Missing files : N`.
- Skips that depend on the version (`Could not find system prompt`, prompts for macOS only on Windows) go into one WARN line. They are not failures.

### Stage 3: Verify the patched binary

```bash
python scripts/verify.py
```

There are four tests, and all must pass:

- the two version lines
- the sentinels from all three content sources: `senior-engineer standard` for unnerfcc, `+ tweakcc v` for tweakcc-fixed, and the claudeMd context lead-in for system-reminders. A stock binary has none of them.
- the apply record: the most recent install log must hold the full output of both patchers for each item, with zero failure markers. The test classifies them with the same patterns that install.py uses.

The apply record is the completeness oracle. A screenshot of the startup banner of a past session is NOT one. The startup banner lists only the customized `.md` prompt files of tweakcc-fixed. The unnerfcc rules patch the binary directly, and they never show there. verify.py writes a copy of its own output to `~/.tweakcc-gilligan/logs/verify_<timestamp>.log`.

## Additional Commands

- **Supported version intersection**:
  ```bash
  python scripts/check_version_intersection.py
  ```
  The script prints two labeled lines:
  - `APPLICABLE` is the newest version that the local clones can patch now. It is the minimum of the fork catalog and the local tweakcc-fixed catalog.
  - `RESULT` is the greatest common version that the two upstreams support. It comes from the commit subjects and the catalog file names, and the higher signal wins.

  When both upstream signals resolve, the last output line is the paste-ready `npm install -g @anthropic-ai/claude-code@<target>` command. Give it to the user VERBATIM. If the script cannot read an upstream, it prints `RESULT-UNCERTAIN`. That value comes from the local catalogs and can be lower than the real target. The script then exits 1 and prints no install line, and `--prepare` stops and records nothing. When GitHub is reachable, run the script again. The READINESS line gives the lag of APPLICABLE behind RESULT. The version update runbook closes that lag.
- **Skill mirror test**:
  ```bash
  python scripts/test_skill_mirror_sync.py
  ```
- **Clear a poisoned backup snapshot**:
  ```bash
  python scripts/install.py --clean-backup
  ```

## Termination Guarantee and Exit Codes

Each script accepts `--max-seconds <n>`, a hard wall-clock ceiling. The default is 1800 for `install.py` and 120 for the others. Each script also accepts `--watchdog-probe <n>`, a diagnostic idle that tests the ceiling. The exit codes are: 0 success, 1 failure with a logged reason, 2 usage error (flag values that are not valid), and 3 stopped at the wall-clock ceiling. A run that stops at the ceiling can leave `install.lock` behind. The next run removes a lock that is older than four hours, so you do not need to clean up by hand. The black-box suite in `scripts/test_termination_contract.py` pins the contract:

```bash
python scripts/test_termination_contract.py
```

## Runtime Layout

- `~/.tweakcc-gilligan/` (the `TWEAKCC_GILLIGAN_HOME` environment variable overrides it):
  - `repos/`: the working clones of `tweakcc-fixed`, `unnerfcc`, and `lobotomized-claude-code`. All three fast-forward to their remotes on each prepare. Then `tweakcc-fixed` goes to the release that catalogued the recorded target (see the binary-format section of the runbook). So its extractor matches the target binary format.
  - `logs/`: the timestamped installation logs and the active PID record.
  - `manifest.json`: the installation record. It holds the installed-at time and the `tweakcc-fixed` and `unnerfcc` commit SHAs. It also holds the `claude` launcher path that `shutil.which("claude")` resolves. On Windows, that path is the npm `claude.CMD` shim, not the patched `claude.exe`. `verify.py` prints the resolved binary.
  - `target_version.txt`: the Claude Code version that the prepare stage recorded and that the apply stage requires.
  - `install.lock`: the lock file of the active operation. When it is older than four hours, the next run removes it.
- `~/.tweakcc/`:
  - `config.json`: the configuration for `tweakcc-fixed` (`ccInstallationPath`).
  - `system-reminders/`: the live system-reminder override `.md` files. An override with a `shadows: <id>` line in its front matter replaces that prompt surface before unnerfcc runs (see the shadowed-rules item of the runbook).
- `~/.local/bin/python3.bat` (Windows): the `python3` shim that `--prepare` makes sure of or creates.

## Resetting Claude Code to Stock

To put the binary back to its un-modified published state, run:

```bash
npm install -g @anthropic-ai/claude-code@<version>
```

## Core rules (restated)

| # | Rule |
|---|---|
| 1 | Run `--prepare` before `--apply`. `--apply` does not build. It needs the dist that `--prepare` makes. A bare `--apply` on a fresh clone stops with "dist/index.mjs missing". |
| 2 | The apply runs OUTSIDE Claude Code with ALL sessions closed. A running `claude.exe` locks the binary. |
| 3 | The extractor commit must match the target binary format (see the binary-format section of [references/version-update.md](references/version-update.md)). A wrong format gives "claude module not found in any of the binary modules" (tweakcc-fixed) or "BUN_FORMAT_INCOMPATIBLE / struct-size ambiguity" (unnerfcc). |
| 4 | One clone per repo. Never make a second copy, snapshot, branch-named directory, or zip of a repo. Duplicates are the top source of failures. An edit lands in one copy, a commit goes out from another, and the copies drift. |
| 5 | Never edit the runtime clones in `~/.tweakcc-gilligan/repos/` by hand. They are disposable, and `--prepare` builds them again. Edit and commit source only in its one dev location. Then let the installer clone fresh from the remote. |
| 6 | Do not use git worktrees. Work in the main working copy. |
| 7 | A new CC version is a MECHANICAL re-patch ([references/version-update.md](references/version-update.md)). The rewrites are already decided and encoded as rules. Only the stock anchors move. A change to un-nerf CONTENT is a separate job (the "Remediation" section). That job follows a recipe and runs the seven remediation gates in order. Never force past a failed gate. Never mix the two jobs. A mix of the two made a 1-hour patch into a 9-day job. |
| 8 | This skill is in two locations that must stay byte-identical: the DEV source `tweakcc-gilligan/skills/tweakcc-update/` and the INSTALLED copy `.claude/skills/tweakcc-update/`. After each edit, copy the change to the other side. Then run `python scripts/test_skill_mirror_sync.py`, which must exit 0. |
