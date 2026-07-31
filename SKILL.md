---
name: tweakcc-gilligan
description: "Patch Claude Code on Windows or Unix with tweakcc-fixed and unnerfcc in one step. Mechanizes the verified patch sequence, fetches LCC system-reminder overrides at runtime, verifies binary boot and content sentinels, and manages ~/.tweakcc-gilligan/. Use when installing or reapplying tweakcc or unnerfcc patches to Claude Code."
license: MIT
metadata:
  version: 1.0.0
---

# tweakcc-gilligan: Dual-Patcher Skill for Claude Code

tweakcc-gilligan automates the dual-patching workflow for Claude Code binaries on Windows, Linux, and macOS. It combines `tweakcc-fixed` (code patches, `/clear-screen`, session memory, empty system-reminder suppression) with `unnerfcc` (system prompt un-nerfing, reasoning effort cap removal) and populates system-reminder overrides from `lobotomized-claude-code`.

## Quick Start

### Stage 1: Prepare the Setup (Safe inside active Claude Code session)

Run the preparer to check preflights, verify version catalog alignment, sync patcher repositories, populate system reminders, and generate the external apply script:

```bash
python scripts/install.py --prepare
```

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

## Runtime Layout

- `~/.tweakcc-gilligan/` (Override via `TWEAKCC_GILLIGAN_HOME` environment variable):
  - `repos/`: Working clones of `tweakcc-fixed`, `unnerfcc`, and `lobotomized-claude-code`.
  - `logs/`: Timestamped installation logs and active PID tracking.
  - `manifest.json`: Installation record, target binary path, and commit SHAs.
  - `install.lock`: Active operation lockfile.
- `~/.tweakcc/`:
  - `config.json`: Configuration for `tweakcc-fixed` (`ccInstallationPath`).
  - `system-reminders/`: Live system-reminder override `.md` files.

## Resetting Claude Code to Stock

To return the binary to its un-modified published state:

```bash
npm install -g @anthropic-ai/claude-code@<version>
```
