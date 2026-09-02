#!/usr/bin/env python3
"""
Verification script for tweakcc-gilligan.

Checks that the installed Claude Code binary carries all three patch content sources:
1. unnerfcc prompt un-nerfs ("senior-engineer standard")
2. tweakcc-fixed code patches ("+ tweakcc v")
3. system-reminder overrides ("As you answer the user's questions...")
and verifies that dual version lines are printed by `claude --version`.
"""

import argparse
import os
import re
import sys
import subprocess
import shutil
import pathlib
import threading
import time

# Markers mirrored from install.py: BEGIN/END sentinels prove the apply log
# captured the patchers' per-item accounting; the failure patterns are the
# same per-item markers the installer aborts on.
CAPTURE_SENTINELS = ["BEGIN TWEAKCC-FIXED OUTPUT", "BEGIN UNNERFCC OUTPUT"]
# Import the installer's anchored patterns so both gates classify the patcher
# output identically (an unanchored "failed to " here matched tweakcc-fixed's
# per-prompt description lines and failed a successful apply, 2026-09-01).
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from install import TWEAKCC_FAIL_PATTERNS, UNNERFCC_FAIL_PATTERNS  # noqa: E402
FAIL_PATTERNS = TWEAKCC_FAIL_PATTERNS + UNNERFCC_FAIL_PATTERNS


def check_apply_accounting():
    """Verify the most recent apply log holds full per-item accounting with no
    failure markers. Returns (ok, message). A log without the capture
    sentinels predates the output-capture installer and proves nothing."""
    gilligan_home = pathlib.Path(os.environ.get("TWEAKCC_GILLIGAN_HOME") or pathlib.Path.home())
    log_dir = gilligan_home / ".tweakcc-gilligan" / "logs"
    logs = sorted(log_dir.glob("install_*.log"), key=lambda p: p.stat().st_mtime) if log_dir.exists() else []
    apply_logs = [p for p in logs if "Stage 2: Applying Patches" in p.read_text(encoding="utf-8", errors="replace")]
    if not apply_logs:
        return False, "no apply log found under " + str(log_dir)
    latest = apply_logs[-1]
    text = latest.read_text(encoding="utf-8", errors="replace")
    missing = [s for s in CAPTURE_SENTINELS if s not in text]
    if missing:
        return False, (f"last apply log {latest.name} lacks per-item accounting ({', '.join(missing)}); "
                       "it predates the output-capture installer. Re-run apply-external to record a provable apply.")
    bad = []
    for line in text.splitlines():
        if any(re.search(p, line, re.IGNORECASE) for p in FAIL_PATTERNS):
            bad.append(line.strip())
    if bad:
        return False, f"last apply log {latest.name} carries {len(bad)} failure marker(s), e.g.: {bad[0][:120]}"
    return True, f"last apply log {latest.name} holds full accounting with no failure markers"


def _arm_watchdog(max_seconds: float, probe_seconds: float) -> None:
    """Deterministic termination guard: hard-kill with exit code 3 at the wall-clock ceiling.
    From recipe-skill-script-hardening (threading.Timer + os._exit; signal.alarm is POSIX-only)."""
    if max_seconds <= 0:
        print("error: --max-seconds must be greater than 0", file=sys.stderr)
        sys.exit(2)
    if probe_seconds < 0:
        print("error: --watchdog-probe must be at least 0", file=sys.stderr)
        sys.exit(2)
    timer = threading.Timer(max_seconds, lambda: os._exit(3))
    timer.daemon = True
    timer.start()
    if probe_seconds > 0:
        time.sleep(probe_seconds)

def find_claude_binary():
    env_path = os.environ.get("TWEAKCC_CC_INSTALLATION_PATH")
    if env_path and os.path.isfile(env_path):
        return pathlib.Path(env_path)
    
    launcher = shutil.which("claude")
    if launcher:
        real_launcher = pathlib.Path(launcher).resolve()
        if real_launcher.suffix.lower() == ".exe":
            return real_launcher
        npm_exe = real_launcher.parent / "node_modules" / "@anthropic-ai" / "claude-code" / "bin" / "claude.exe"
        if npm_exe.exists():
            return npm_exe
        return real_launcher

    user_profile = os.environ.get("USERPROFILE") or os.environ.get("HOME")
    if user_profile:
        win_npm = pathlib.Path(user_profile) / "AppData" / "Roaming" / "npm" / "node_modules" / "@anthropic-ai" / "claude-code" / "bin" / "claude.exe"
        if win_npm.exists():
            return win_npm

    return None

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-seconds", type=float, default=120.0, dest="max_seconds",
                        help="Hard wall-clock ceiling in seconds; the process exits with code 3 when it fires (default: 120)")
    parser.add_argument("--watchdog-probe", type=float, default=0.0, dest="watchdog_probe",
                        help="Diagnostic: idle this many seconds after arming the watchdog (default: 0)")
    args = parser.parse_args()
    _arm_watchdog(args.max_seconds, args.watchdog_probe)

    # Tee all output to a timestamped log next to the install logs, so an
    # external run leaves its verification verdict on disk (no manual
    # copy-paste of console output into notes files).
    import datetime

    class _Tee:
        def __init__(self, stream, path):
            self._stream = stream
            try:
                self._f = open(path, "a", encoding="utf-8")
            except Exception:
                self._f = None
        def write(self, s):
            self._stream.write(s)
            if self._f:
                try:
                    self._f.write(s)
                    self._f.flush()
                except Exception:
                    pass
        def flush(self):
            self._stream.flush()

    gilligan_home = pathlib.Path(os.environ.get("TWEAKCC_GILLIGAN_HOME") or pathlib.Path.home())
    log_dir = gilligan_home / ".tweakcc-gilligan" / "logs"
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        verify_log = log_dir / f"verify_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
        sys.stdout = _Tee(sys.stdout, verify_log)
    except Exception:
        verify_log = None

    print("=== tweakcc-gilligan Verification ===")
    if verify_log:
        print(f"(output logged to {verify_log})")
    failed = False

    # 1. Check version output (MUST carry dual version lines)
    try:
        ver_res = subprocess.run(["claude", "--version"], capture_output=True, text=True, timeout=15)
        ver_output = ver_res.stdout.strip()
        print("claude --version output:\n" + ver_output)
        version_lines = [line.strip() for line in ver_output.splitlines() if line.strip()]
        if len(version_lines) >= 2:
            print("  [PASS] Dual version lines present (Claude Code + tweakcc-fixed)")
        else:
            print("  [FAIL] Fewer than two version lines printed by claude --version (tweakcc-fixed patch missing)")
            failed = True
    except Exception as e:
        print(f"  [FAIL] Error executing claude --version: {e}")
        failed = True

    # 2. Resolve binary file for content greps
    bin_path = find_claude_binary()
    if not bin_path or not bin_path.exists():
        print("  [FAIL] Could not resolve installed Claude Code binary path.")
        return 1

    print(f"Target binary: {bin_path}")
    
    try:
        data = bin_path.read_bytes()
    except Exception as e:
        print(f"  [FAIL] Error reading binary file: {e}")
        return 1

    sentinels = [
        ("unnerfcc", b"senior-engineer standard"),
        # "+ tweakcc v" is spliced into the header by the patches-applied-indication
        # patch; stock never contains it. (The old "clear-screen" marker occurs
        # twice in stock, so it passed on an unpatched binary; observed 2026-09-01.)
        ("tweakcc-fixed", b"+ tweakcc v"),
        ("system-reminders", b"As you answer the user's questions, you can use the following context:")
    ]

    for source, marker in sentinels:
        if marker in data:
            print(f"  [PASS] {source}: present")
        else:
            print(f"  [FAIL] {source}: MISSING (marker {marker!r} not found)")
            failed = True

    # 4. Apply accounting: sentinel greps prove three strings, not completeness.
    # The last apply log must hold the patchers' full per-item output with zero
    # failure markers; that is the completeness oracle (a screenshot is not).
    ok, msg = check_apply_accounting()
    if ok:
        print(f"  [PASS] apply accounting: {msg}")
    else:
        print(f"  [FAIL] apply accounting: {msg}")
        failed = True

    if failed:
        print("\nVerification FAILED: One or more checks failed.")
        return 1

    print("\nVerification SUCCESS: Dual version lines and all three content sources present.")
    return 0

if __name__ == "__main__":
    sys.exit(main())
