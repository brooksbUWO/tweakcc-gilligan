#!/usr/bin/env python3
"""
Verification script for tweakcc-gilligan.

Checks that the installed Claude Code binary carries all three patch content sources:
1. unnerfcc prompt un-nerfs ("senior-engineer standard")
2. tweakcc-fixed code patches ("clear-screen")
3. system-reminder overrides ("As you answer the user's questions...")
and verifies that dual version lines are printed by `claude --version`.
"""

import os
import sys
import subprocess
import shutil
import pathlib

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
    print("=== tweakcc-gilligan Verification ===")
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
        ("tweakcc-fixed", b"clear-screen"),
        ("system-reminders", b"As you answer the user's questions, you can use the following context:")
    ]

    for source, marker in sentinels:
        if marker in data:
            print(f"  [PASS] {source}: present")
        else:
            print(f"  [FAIL] {source}: MISSING (marker {marker!r} not found)")
            failed = True

    if failed:
        print("\nVerification FAILED: One or more checks failed.")
        return 1

    print("\nVerification SUCCESS: Dual version lines and all three content sources present.")
    return 0

if __name__ == "__main__":
    sys.exit(main())
