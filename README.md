# tweakcc-gilligan

Customize the Claude Code you have installed on your own machine, with one command sequence, on
Windows, Linux, and macOS. The result is a Claude Code that works thoroughly instead of minimally,
with better writing quality. Other patchers each cover one part of the job. tweakcc-gilligan runs
the full three-source patch chain in the verified order, logs every item, and verifies the result.

## Goal, reason, and object

**Goal.** Claude Code that works thoroughly instead of minimally, with better writing quality.

**Why we patch.** The shipped binary carries prompt text that tells the model to be brief and do the
minimum. It also carries a default cap on reasoning effort. Neither is a user setting. The only way
to change them on an installed copy is to rewrite the strings in the local binary.

**What we patch.** The shipped prompt strings and system reminders in the locally installed Claude
Code binary: `claude.exe` on Windows, ELF and Mach-O elsewhere. Three upstream sources are applied
in one chain: `tweakcc-fixed` for code features, `unnerfcc` for prompt rewrites, and
`lobotomized-claude-code` for system-reminder overrides. You do not edit the binary by hand. Only
the copy on your machine changes.

![tweakcc session start header](assets/tweakcc-session-start.png)

The screenshot shows the session-start header of a patched Claude Code 2.1.220 with tweakcc-fixed 2.7.13. The version numbers in the header change with each release; the layout is what to look for.

- **Thorough by default.** Rewrites the "be brief, do the minimum" directives and raises the default reasoning-effort setting.
- **One safe chain.** Applies both patchers in the verified order (`tweakcc-fixed` first, `unnerfcc` second).
- **Windows PE and Unix.** Native `claude.exe` (PE) unpack and repack, plus ELF and Mach-O.
- **Three content sources.** Code features, prompt rewrites, and reminder overrides, applied in one pass.
- **Runtime isolation.** All work stays under `~/.tweakcc-gilligan/` with PID tracking and timestamped logs.
- **Logged runs.** Every run logs the full per-item result of each patcher. Verification fails if any override failed.
- **Reversible.** `claude --version` prints two lines when the patch is live. Reset to stock is one command.

A patched binary reports both versions. One command shows that the patch is live:

```text
$ claude --version
2.1.258 (Claude Code)      # example: your Claude Code version
2.8.4 (tweakcc-fixed)      # example: the tweakcc-fixed patch version
```

The second line only appears after a successful patch.

## Getting started

The patch runs in three stages. Run the chain after you install Claude Code, and again after each
Claude Code update, because an update replaces the patched binary.

### Prerequisites

- Node 20 or newer, Python 3, and Git on the PATH. On Windows, Git for Windows: the apply stage runs
  the `unnerfcc` installer through Git Bash.
- About 1 GB of free disk space on the home-directory drive for the patcher repositories and the build.
- For stage 2: every Claude Code session closed, in terminals and in editors. A running session
  locks the binary file.

All commands run from the root of this repository. The scripts live in `skills/tweakcc-update/scripts/`.

### Stage 1: prepare (safe inside a Claude Code session)

1. Run the preparer:

   ```bash
   python skills/tweakcc-update/scripts/install.py --prepare
   ```

2. Read the log. The preparer runs preflight checks (Node 20+, disk space, running processes) and
   syncs the patcher repositories. Then it records the target version, builds the patchers, fills
   the system reminders, and writes the external apply script.

**Expected outcome:** The log ends with `=== Stage 1 Complete ===`. The target version is recorded
in `~/.tweakcc-gilligan/target_version.txt`. The external apply script exists.

### Stage 2: apply (outside Claude Code)

1. Close every Claude Code terminal and editor session.
2. Run the generated external script:
   - Windows: `%USERPROFILE%\.tweakcc-gilligan\apply-external.bat`
   - Unix: `~/.tweakcc-gilligan/apply-external.sh`

   Or run the installer directly in an external shell:

   ```bash
   python skills/tweakcc-update/scripts/install.py --apply
   ```

**Expected outcome:** The log shows `tweakcc-fixed applied successfully` and then
`unnerfcc applied successfully`. The apply stage resets Claude Code to the recorded version first.
It refuses to run without that record.

### Stage 3: verify

1. Run the verifier:

   ```bash
   python skills/tweakcc-update/scripts/verify.py
   ```

**Expected outcome:** Four checks pass: the dual version lines, the sentinels from the three
content sources, and the apply accounting. The accounting check reads the newest install log. That
log must hold the full per-item output of both patchers, with zero failure markers. The verifier
also writes a copy of its own output to `~/.tweakcc-gilligan/logs/`. A successful run ends like
this (example output):

```text
=== tweakcc-gilligan Verification ===
(output logged to C:\Users\<you>\.tweakcc-gilligan\logs\verify_20260902_140202.log)
claude --version output:
2.1.258 (Claude Code)
2.8.4 (tweakcc-fixed)
  [PASS] Dual version lines present (Claude Code + tweakcc-fixed)
Target binary: D:\Data\Programs\npm\node_modules\@anthropic-ai\claude-code\bin\claude.exe
  [PASS] unnerfcc: present
  [PASS] tweakcc-fixed: present
  [PASS] system-reminders: present
  [PASS] apply accounting: last apply log install_20260902_003516.log holds full accounting (3 capture block(s)) with no failure markers

Verification SUCCESS: Dual version lines and all three content sources present.
```

### Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `dist/index.mjs missing` | `--apply` ran without a completed `--prepare`. | Run `--prepare` first. |
| `claude module not found in any of the binary modules` | The `tweakcc-fixed` commit does not match the target binary format. | See the binary-format section of `skills/tweakcc-update/references/version-update.md`. |
| `BUN_FORMAT_INCOMPATIBLE` | The `unnerfcc` parser hit an ambiguous Bun layout. | Fix `engine/bun-binary.mjs` in the `unnerfcc` repository, not the installer. |
| `[FAIL] Fewer than two version lines` | The `tweakcc-fixed` patch is missing from the binary. | Run stage 2 again with every Claude Code session closed. |
| `[FAIL] <source>: MISSING` | One content source did not land in the binary. | Read the newest install log in `~/.tweakcc-gilligan/logs/` for that source. |
| `[FAIL] apply accounting` | The newest install log lacks per-item accounting or carries a failure marker. | Read the named log line, correct the cause, then run stage 2 again. |

The full symptom table is in `skills/tweakcc-update/SKILL.md`.

## Command reference

- `install.py --prepare`: Syncs the patcher repositories and records the target Claude Code version
  to `~/.tweakcc-gilligan/target_version.txt`. Then it fills the system reminders, builds the
  patchers, and generates the external apply script. A stale version-source clone stops the run.
- `install.py --apply`: Resets Claude Code to the recorded version. It refuses to run without that
  record. Then it applies the `tweakcc-fixed` and `unnerfcc` patches to the installed binary.
- `verify.py`: Runs the four checks. These are the dual version lines from `claude --version` and
  the sentinels from all three content sources in the binary. The fourth check is the apply
  accounting from the newest install log.
- `check_version_intersection.py`: Reports the TARGET (the newest Claude Code release that both
  patcher upstreams support) and the READINESS of the local catalogs. The last output line is a
  paste-ready `npm install` command for the target.
- `install.py --clean-backup`: Removes poisoned tweakcc backup files.
- `test_termination_contract.py`: Runs the black-box suite that pins the termination contract of the
  scripts (`--max-seconds` ceiling, exit codes 0, 1, 2, 3).

All scripts live in `skills/tweakcc-update/scripts/`.

## Resetting to stock

To return the binary to its un-modified published state, reinstall Claude Code:

```bash
npm install -g @anthropic-ai/claude-code@<version>
```

## The name

tweakcc-gilligan takes its name from a parody of the Gilligan's Island theme song, posted to
r/ClaudeCode: [Just sit right back and you'll hear a tale](https://www.reddit.com/r/ClaudeCode/comments/1vbrrm2/just_sit_right_back_and_youll_hear_a_tale_the/).
The verses are in [gilligan-ballad.md](gilligan-ballad.md).

## Credits and upstream projects

tweakcc-gilligan builds on these upstream projects:

- [lukehutch/unnerfcc](https://github.com/lukehutch/unnerfcc): Prompt rewrite engine and Bun section packer.
- [skrabe/tweakcc-fixed](https://github.com/skrabe/tweakcc-fixed): Binary patcher for Claude Code features and UX customizations.
- [skrabe/lobotomized-claude-code](https://github.com/skrabe/lobotomized-claude-code): System-reminder override prompt set.

## License

MIT License. See [LICENSE](LICENSE) for details.
