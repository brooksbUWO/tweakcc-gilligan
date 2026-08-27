---
name: tweakcc-gilligan
description: "Patch Claude Code on Windows or Unix with tweakcc-fixed and unnerfcc in one step. Mechanizes the verified patch sequence, fetches LCC system-reminder overrides at runtime, verifies binary boot and content sentinels, and manages ~/.tweakcc-gilligan/. Use when installing or reapplying tweakcc or unnerfcc patches to Claude Code."
license: MIT
metadata:
  version: 1.1.0
---

# tweakcc-gilligan: Dual-Patcher Skill for Claude Code

tweakcc-gilligan patches Claude Code binaries on Windows, Linux, and macOS in two stages. It applies `tweakcc-fixed` (code patches, `/clear-screen`, session memory, empty system-reminder suppression) and `unnerfcc` (system prompt un-nerfing, reasoning effort cap removal). It also fills system-reminder overrides from `lobotomized-claude-code`.

## Quick Start

### Stage 1: Prepare the Setup (Safe inside active Claude Code session)

Run the preparer. It checks preflights, syncs the patcher repositories, records the target Claude Code version, fills system reminders, and generates the external apply script:

```bash
python scripts/install.py --prepare
```

The prepare stage syncs the repositories first. Then it computes the greatest common supported version from the fresh catalogs, so the recorded target never comes from stale clones. It records the target to `~/.tweakcc-gilligan/target_version.txt`. The apply stage requires that record. Failures are loud. If a version-source repository cannot sync with its remote, or a version check cannot produce a result, the prepare stops with a remediation message. It does not record a stale or missing target.

The prepare log also reports whether the tracked `unnerfcc` branch is in sync with its real upstream. It reads this from the upstream's "sync to Claude Code vX.Y.Z" commit subjects. This line is advisory only. The catalog intersection decides the target. The line can warn that the upstream synced to a newer version than the tracked branch supports. Then you must update the tracked fork branch before you can target that newer version.

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
