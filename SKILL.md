---
name: tweakcc-gilligan
description: "Patch Claude Code on Windows or Unix with tweakcc-fixed and unnerfcc in one step. Mechanizes the verified patch sequence, fetches LCC system-reminder overrides at runtime, verifies binary boot and content sentinels, and manages ~/.tweakcc-gilligan/. Use when installing or reapplying tweakcc or unnerfcc patches to Claude Code."
license: MIT
metadata:
  version: 1.1.0
---

# tweakcc-gilligan: Dual-Patcher Skill for Claude Code

tweakcc-gilligan automates the dual-patching workflow for Claude Code binaries on Windows, Linux, and macOS. It combines `tweakcc-fixed` (code patches, `/clear-screen`, session memory, empty system-reminder suppression) with `unnerfcc` (system prompt un-nerfing, reasoning effort cap removal) and populates system-reminder overrides from `lobotomized-claude-code`.

## Quick Start

### Stage 1: Prepare the Setup (Safe inside active Claude Code session)

Run the preparer to check preflights, sync the patcher repositories, determine and record the target Claude Code version, populate system reminders, and generate the external apply script:

```bash
python scripts/install.py --prepare
```

The prepare stage syncs the repositories first and computes the greatest common supported version from the freshly synced catalogs, so the recorded target is never derived from stale clones. It records the target to `~/.tweakcc-gilligan/target_version.txt`; the apply stage requires that record. Failures are loud: a version-source repository that cannot be brought up to date with its remote, or a version check that cannot produce a result, stops the prepare with a remediation message instead of recording a stale or missing target.

The prepare log also reports whether the tracked `unnerfcc` branch is in sync with its real upstream, read from the upstream's "sync to Claude Code vX.Y.Z" commit subjects. This line is advisory only; the catalog intersection decides the target. When it warns that the upstream has synced to a newer version than the tracked branch supports, the tracked fork branch needs updating before that newer version can be targeted.

### Stage 2: Apply Binary Patches (Outside active Claude Code session)

Because a running `claude.exe` or active session locks the executable, run the generated external script with all Claude Code sessions closed:

On Windows:
```cmd
%USERPROFILE%\.tweakcc-gilligan\apply-external.bat
```

On Linux/macOS:
```bash
~/.tweakcc-gilligan/apply-external.sh
```

Alternatively, invoke `install.py` with `--apply` directly in a shell outside Claude Code:

```bash
python scripts/install.py --apply
```

The apply stage accepts only the version the prepare stage recorded. When `target_version.txt` is missing or unreadable, or the reset to the stock version fails, the apply stops with a remediation message rather than patching an unknown binary; re-run `--prepare` first.

### Stage 3: Verify the Patched Binary

Verify that dual version lines are printed and sentinels from all three content sources exist:

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

Every script accepts `--max-seconds <n>` (a hard wall-clock ceiling; default 1800 for `install.py`, 120 for the others) and `--watchdog-probe <n>` (a diagnostic idle used to test the ceiling). Exit codes: 0 success, 1 failure with a logged reason, 2 usage error (invalid flag values), 3 terminated at the wall-clock ceiling. A run killed at the ceiling can leave `install.lock` behind; the next run removes a lock older than four hours on its own, so no manual cleanup is needed. The contract is pinned by the black-box suite in `tests/test_termination_contract.py`:

```bash
python -m unittest discover -s tests
```

## Runtime Layout

- `~/.tweakcc-gilligan/` (Override via `TWEAKCC_GILLIGAN_HOME` environment variable):
  - `repos/`: Working clones of `tweakcc-fixed`, `unnerfcc`, and `lobotomized-claude-code`, fast-forwarded to their remotes on every prepare.
  - `logs/`: Timestamped installation logs and active PID tracking.
  - `manifest.json`: Installation record, target binary path, and commit SHAs.
  - `target_version.txt`: The Claude Code version the prepare stage recorded and the apply stage requires.
  - `install.lock`: Active operation lockfile; self-clears when older than four hours.
- `~/.tweakcc/`:
  - `config.json`: Configuration for `tweakcc-fixed` (`ccInstallationPath`).
  - `system-reminders/`: Live system-reminder override `.md` files.

## Resetting Claude Code to Stock

To return the binary to its un-modified published state:

```bash
npm install -g @anthropic-ai/claude-code@<version>
```
