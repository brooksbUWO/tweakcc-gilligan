# tweakcc-gilligan

Un-nerf Claude Code and customize its binary with one command sequence, on Windows, Linux, and macOS.

Claude Code ships with system prompts that tell it to be brief and do the minimum, plus a cap on
reasoning effort. tweakcc-gilligan patches the installed binary to remove those limits and add UX
features, so Claude Code works thoroughly instead of minimally. It runs the full patch chain for you:
`tweakcc-fixed` for code features, `unnerfcc` for the prompt un-nerfs, and `lobotomized-claude-code`
for system-reminder overrides. You do not edit the binary by hand.

![tweakcc session start header](assets/tweakcc-session-start.png)

- **Un-nerfed prompts.** Removes the "be brief, do the minimum" directives and the reasoning-effort cap.
- **One safe chain.** Applies both patchers in the verified order (`tweakcc-fixed` first, `unnerfcc` second).
- **Windows PE and Unix.** Native `claude.exe` (PE) unpack and repack, plus ELF and Mach-O.
- **Three content sources.** Code features, prompt un-nerfs, and reminder overrides, bound in one pass.
- **Runtime isolation.** All work stays under `~/.tweakcc-gilligan/` with PID tracking and timestamped logs.
- **Reversible.** `claude --version` prints two lines when the patch is live; reset to stock is one command.

A patched binary reports both versions, so you can confirm the patch in one command:

```text
$ claude --version
2.1.235 (Claude Code)      # example: your Claude Code version
2.7.38 (tweakcc-fixed)     # example: the tweakcc-fixed patch version
```

The second line only appears after a successful patch.

## Getting started

The patch runs in two stages. Stage 1 is safe inside a Claude Code session. Stage 2 must run outside
Claude Code, because a running session locks the binary file.

### Stage 1: prepare (safe inside a Claude Code session)

Preparation runs preflight checks (Node 20+, disk space, running processes), checks version-catalog
alignment, syncs the patcher repositories, builds the patchers, fills the system reminders, and
generates the external apply script:

```bash
python skills/tweakcc-update/scripts/install.py --prepare
```

### Stage 2: apply (outside Claude Code)

Close every Claude Code terminal and editor session. Then run the generated external script:

- Windows: `%USERPROFILE%\.tweakcc-gilligan\apply-external.bat`
- Unix: `~/.tweakcc-gilligan/apply-external.sh`

Or run the installer directly in an external shell:

```bash
python skills/tweakcc-update/scripts/install.py --apply
```

### Stage 3: verify

Confirm the dual version lines and the sentinels for all three content sources:

```bash
python skills/tweakcc-update/scripts/verify.py
```

A successful run ends like this (example output):

```text
=== tweakcc-gilligan Verification ===
claude --version output:
2.1.235 (Claude Code)
2.7.38 (tweakcc-fixed)
  [PASS] Dual version lines present (Claude Code + tweakcc-fixed)
  [PASS] unnerfcc: present
  [PASS] tweakcc-fixed: present
  [PASS] system-reminders: present

Verification SUCCESS: Dual version lines and all three content sources present.
```

## Command reference

- `install.py --prepare`: Syncs the patcher repositories, records the target Claude Code version to
  `~/.tweakcc-gilligan/target_version.txt`, fills the system reminders, builds the patchers, and
  generates the external apply script. A stale version-source clone stops the run.
- `install.py --apply`: Resets Claude Code to the recorded version (it refuses to run without that
  record), then applies the tweakcc-fixed and unnerfcc patches to the installed binary.
- `verify.py`: Confirms the dual version output from `claude --version` and the markers from all three
  content sources in the binary.
- `check_version_intersection.py`: Reads the local catalog clones (falling back to GitHub) and npm to
  find the greatest common supported release.
- `install.py --clean-backup`: Removes poisoned tweakcc backup files.
- `test_termination_contract.py`: Runs the black-box suite that pins the scripts' termination contract
  (`--max-seconds` ceiling, exit codes 0, 1, 2, 3).

All scripts live in `skills/tweakcc-update/scripts/`.

## Resetting to stock

To return the binary to its un-modified published state, reinstall Claude Code:

```bash
npm install -g @anthropic-ai/claude-code@<version>
```

## Credits and upstream projects

tweakcc-gilligan builds on these upstream projects:

- [lukehutch/unnerfcc](https://github.com/lukehutch/unnerfcc): Prompt un-nerfing engine and Bun section packer.
- [skrabe/tweakcc-fixed](https://github.com/skrabe/tweakcc-fixed): Binary patcher for Claude Code features and UX customizations.
- [skrabe/lobotomized-claude-code](https://github.com/skrabe/lobotomized-claude-code): System-reminder override prompt set.

## License

MIT License. See [LICENSE](LICENSE) for details.
