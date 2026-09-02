# tweakcc-gilligan

Customize the Claude Code you have installed on your own machine, with one command sequence, on
Windows, Linux, and macOS.

Claude Code ships with prompt text that tells it to be brief and do the minimum, plus a default cap
on reasoning effort. tweakcc-gilligan rewrites those shipped prompt strings in your locally installed
binary and adds UX features. As a result, Claude Code works thoroughly instead of minimally. The tool
runs the full patch chain for you: `tweakcc-fixed` for code features, `unnerfcc` for the prompt
rewrites, and `lobotomized-claude-code` for system-reminder overrides. You do not edit the binary by
hand. The tool changes only the copy installed on your machine.

![tweakcc session start header](assets/tweakcc-session-start.png)

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
2.1.235 (Claude Code)      # example: your Claude Code version
2.7.38 (tweakcc-fixed)     # example: the tweakcc-fixed patch version
```

The second line only appears after a successful patch.

## Getting started

The patch runs in two stages. Stage 1 is safe inside a Claude Code session. Stage 2 must run outside
Claude Code, because a running session locks the binary file.

### Stage 1: prepare (safe inside a Claude Code session)

The prepare stage runs preflight checks (Node 20+, disk space, running processes) and syncs the
patcher repositories. Then it records the target version, builds the patchers, fills the system
reminders, and writes the external apply script:

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

Four checks must pass: the dual version lines, the sentinels from the three content sources, and
the apply accounting. The accounting check reads the newest install log. That log must hold the
full per-item output of both patchers, with zero failure markers. The verifier also writes a copy
of its own output to `~/.tweakcc-gilligan/logs/`.

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
  [PASS] apply accounting: last apply log holds full accounting with no failure markers

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
- `check_version_intersection.py`: Reports the TARGET (the newest Claude Code release that both
  patcher upstreams support) and the READINESS of the local catalogs. The last output line is a
  paste-ready `npm install` command for the target.
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

- [lukehutch/unnerfcc](https://github.com/lukehutch/unnerfcc): Prompt rewrite engine and Bun section packer.
- [skrabe/tweakcc-fixed](https://github.com/skrabe/tweakcc-fixed): Binary patcher for Claude Code features and UX customizations.
- [skrabe/lobotomized-claude-code](https://github.com/skrabe/lobotomized-claude-code): System-reminder override prompt set.

## License

MIT License. See [LICENSE](LICENSE) for details.
