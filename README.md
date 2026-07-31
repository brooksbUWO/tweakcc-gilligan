# tweakcc-gilligan

Dual-patcher skill and automated pipeline for Claude Code on Windows and Unix.

tweakcc-gilligan mechanizes the complete patch sequence for customizing Claude Code binaries. It combines `tweakcc-fixed` (code patches, `/clear-screen`, session memory, empty system-reminder suppression) with `unnerfcc` (system prompt un-nerfing, reasoning effort cap removal) and populates system-reminder overrides from `lobotomized-claude-code`.

![tweakcc session start header](assets/tweakcc-session-start.png)

## Architecture & Staged Workflow

Patching a binary cannot occur while an active Claude Code session holds the executable file open. tweakcc-gilligan implements a three-stage execution workflow to enforce this boundary cleanly:

### Stage 1: Preparation (Safe inside active Claude Code session)
Run the preparer to run preflight checks (Node 20+, disk space, running processes), check version catalog alignment, sync git repositories, build patchers, populate system reminders, and generate the external apply script:

```bash
python scripts/install.py --prepare
```

### Stage 2: Apply Binary Patches (Outside active Claude Code session)
Run the generated external script with all Claude Code terminal and editor sessions closed:

- **Windows**: `%USERPROFILE%\.tweakcc-gilligan\apply-external.bat`
- **Unix**: `~/.tweakcc-gilligan/apply-external.sh`

Or invoke the installer directly in an external shell:

```bash
python scripts/install.py --apply
```

### Stage 3: Verification
Confirm that dual version lines and sentinels for all three content sources are present in the installed binary:

```bash
python scripts/verify.py
```

## Highlights

- **Unified Dual-Patcher Chain**: Applies code patches and prompt un-nerfs in the verified safe order (tweakcc-fixed first, unnerfcc second).
- **Windows PE & Unix Support**: Native PE (`claude.exe`) unpack/repack support currently ships via the [brooksbUWO/unnerfcc](https://github.com/brooksbUWO/unnerfcc/tree/windows-pe-support) `windows-pe-support` branch; an upstream PR ([lukehutch/unnerfcc PR #1](https://github.com/lukehutch/unnerfcc/pull/1)) is open, and the installer will switch to upstream once merged.
- **Three Content Sources**: Binds code features from `tweakcc-fixed`, prompt un-nerfs from `unnerfcc`, and reminder overrides from `lobotomized-claude-code`.
- **Runtime Isolation**: Operates under `~/.tweakcc-gilligan/` with process tree safety, PID tracking, and timestamped logs.

## Command Reference

- `python scripts/install.py --prepare`: Prepares patcher repositories, populates system reminders, builds binaries, and generates the external apply script.
- `python scripts/install.py --apply`: Applies tweakcc-fixed and unnerfcc patches to the installed Claude Code binary.
- `python scripts/verify.py`: Confirms dual version output (`claude --version`) and verifies markers from all three content sources in the executable.
- `python scripts/check_version_intersection.py`: Queries GitHub and local catalogs to find the greatest common supported release.
- `python scripts/install.py --clean-backup`: Removes poisoned tweakcc backup files if needed.

## Resetting to Stock

To return the binary to its un-modified published state:

```bash
npm install -g @anthropic-ai/claude-code@<version>
```

## Credits & Upstream Projects

tweakcc-gilligan builds on the work of the following upstream projects:
- [lukehutch/unnerfcc](https://github.com/lukehutch/unnerfcc): Prompt un-nerfing engine and Bun section packer.
- [skrabe/tweakcc-fixed](https://github.com/skrabe/tweakcc-fixed): Binary patcher for Claude Code features and UX customizations.
- [skrabe/lobotomized-claude-code](https://github.com/skrabe/lobotomized-claude-code): System-reminder override prompt set.

## License

MIT License. See [LICENSE](LICENSE) for details.
