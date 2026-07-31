#!/usr/bin/env python3
"""
tweakcc-gilligan installer script.

Mechanizes the verified dual-patcher setup sequence (tweakcc-fixed + unnerfcc) cross-platform.
Supports staged execution (--prepare, --apply, --verify, --clean-backup) to respect the hard boundary
that patching must occur outside an active Claude Code session.
"""

import os
import sys
import json
import re
import time
import shutil
import urllib.request
import subprocess
import pathlib
import datetime
import argparse

# --- Constants & Configuration -----------------------------------------------

# Home for tweakcc-gilligan workspace
GILLIGAN_HOME = pathlib.Path(os.environ.get("TWEAKCC_GILLIGAN_HOME") or pathlib.Path.home())
GILLIGAN_DIR = GILLIGAN_HOME / ".tweakcc-gilligan"
REPOS_DIR = GILLIGAN_DIR / "repos"
LOGS_DIR = GILLIGAN_DIR / "logs"
MANIFEST_FILE = GILLIGAN_DIR / "manifest.json"
LOCK_FILE = GILLIGAN_DIR / "install.lock"

# TWEAKCC_HOME ALWAYS targets the user's real home directory
REAL_HOME = pathlib.Path.home()
TWEAKCC_HOME = REAL_HOME / ".tweakcc"
SYS_REMINDERS_DIR = TWEAKCC_HOME / "system-reminders"

BUILTIN_SKILLS = [
    "batch", "claude-api", "claude-in-chrome", "debug", "design", "design-sync",
    "explain-usage", "loop", "plan-artifact", "run", "run-skill-generator",
    "schedule", "setup-cowork", "update-config", "whiteboard", "workshop"
]

LOG_FILE = None
PID_REGISTRY = []

# --- Logging & Process Helpers -----------------------------------------------

def log(msg):
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    formatted = f"[{timestamp}] {msg}"
    print(formatted)
    if LOG_FILE:
        try:
            with open(LOG_FILE, "a", encoding="utf-8") as f:
                f.write(formatted + "\n")
        except Exception:
            pass

def die(msg, remediation=None):
    log(f"ERROR: {msg}")
    if remediation:
        log(f"REMEDIATION: {remediation}")
    remove_lock()
    sys.exit(1)

def register_pid(pid):
    PID_REGISTRY.append(pid)
    try:
        pid_file = LOGS_DIR / "active_pids.json"
        pid_file.write_text(json.dumps(PID_REGISTRY), encoding="utf-8")
    except Exception:
        pass

def kill_process_tree(proc):
    if not proc or proc.poll() is not None:
        return
    try:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True)
        else:
            proc.terminate()
            time.sleep(0.5)
            if proc.poll() is None:
                proc.kill()
    except Exception as e:
        log(f"Warning: Exception while terminating process PID {proc.pid}: {e}")

def run_cmd(cmd, cwd=None, env=None, timeout=600, shell=False):
    log(f"Running: {cmd if isinstance(cmd, str) else ' '.join(cmd)}")
    proc = None
    try:
        proc = subprocess.Popen(
            cmd, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, shell=shell
        )
        register_pid(proc.pid)
        stdout, stderr = proc.communicate(timeout=timeout)
        if proc.returncode != 0:
            log(f"Command exited with code {proc.returncode}")
            log(f"STDOUT:\n{stdout}")
            log(f"STDERR:\n{stderr}")
            raise subprocess.CalledProcessError(proc.returncode, cmd, output=stdout, stderr=stderr)
        return stdout
    except subprocess.TimeoutExpired:
        log(f"Command timed out after {timeout} seconds")
        if proc:
            kill_process_tree(proc)
        raise
    except Exception as e:
        if proc:
            kill_process_tree(proc)
        raise e

# --- Lockfile Management -----------------------------------------------------

def acquire_lock():
    GILLIGAN_DIR.mkdir(parents=True, exist_ok=True)
    if LOCK_FILE.exists():
        try:
            lock_data = json.loads(LOCK_FILE.read_text(encoding="utf-8"))
            pid = lock_data.get("pid")
            if sys.platform == "win32":
                res = subprocess.run(["tasklist", "/FI", f"PID eq {pid}"], capture_output=True, text=True)
                if str(pid) in res.stdout:
                    die(f"Another install process is running (PID {pid}).", "Wait for active install to finish or remove ~/.tweakcc-gilligan/install.lock.")
            else:
                os.kill(pid, 0)
                die(f"Another install process is running (PID {pid}).", "Wait for active install to finish or remove ~/.tweakcc-gilligan/install.lock.")
        except Exception:
            pass
    LOCK_FILE.write_text(json.dumps({"pid": os.getpid(), "timestamp": time.time()}), encoding="utf-8")

def remove_lock():
    try:
        if LOCK_FILE.exists():
            LOCK_FILE.unlink()
    except Exception:
        pass

# --- Preflight Checks --------------------------------------------------------

def preflight_checks(require_no_claude=True):
    log("Running preflight checks...")

    # 1. Node.js Version Check (>= 20)
    node_cmd = shutil.which("node")
    if not node_cmd:
        die("Node.js not found on PATH.", "Install Node.js 20 or newer.")
    try:
        node_ver_str = subprocess.check_output([node_cmd, "--version"], text=True).strip()
        m = re.search(r"v(\d+)\.", node_ver_str)
        if m and int(m.group(1)) < 20:
            die(f"Node.js version {node_ver_str} is below required v20.", "Upgrade Node.js to version 20 or newer.")
        log(f"  [OK] Node.js version: {node_ver_str}")
    except Exception as e:
        log(f"  Warning: Could not parse Node.js version: {e}")

    # 2. Disk Space Check (~1 GB free)
    try:
        total, used, free = shutil.disk_usage(GILLIGAN_DIR)
        free_mb = free / (1024 * 1024)
        if free_mb < 1000:
            die(f"Insufficient disk space: {free_mb:.1f} MB available, ~1000 MB required.", "Free up disk space on the home directory drive.")
        log(f"  [OK] Disk space: {free_mb:.1f} MB free")
    except Exception as e:
        log(f"  Warning: Disk space check failed: {e}")

    # 3. Check for running claude processes if applying
    if require_no_claude:
        if sys.platform == "win32":
            try:
                res = subprocess.run(["tasklist", "/FI", "IMAGENAME eq claude.exe"], capture_output=True, text=True)
                if "claude.exe" in res.stdout:
                    die("A Claude Code session (claude.exe) is currently running.", "Close all active Claude Code terminal and editor sessions before applying binary patches.")
            except Exception:
                pass
        else:
            try:
                res = subprocess.run(["pgrep", "-f", "claude"], capture_output=True, text=True)
                if res.returncode == 0 and res.stdout.strip():
                    die("A Claude Code process is currently running.", "Close all active Claude Code sessions before applying binary patches.")
            except Exception:
                pass
        log("  [OK] No running claude processes detected")

    # 4. Check for native installer layout out-of-scope conflict
    native_versions = REAL_HOME / ".local" / "share" / "claude" / "versions"
    if native_versions.exists():
        dirs = [d for d in native_versions.iterdir() if d.is_dir()]
        if len(dirs) > 1:
            die(f"Multiple Claude Code installs detected under {native_versions}.",
                f"Delete obsolete version directories under {native_versions} so npm global install remains authoritative.")

    # 5. Tool Prerequisites
    for tool in ["node", "git"]:
        if not shutil.which(tool):
            die(f"Required command '{tool}' not found on PATH.", f"Install {tool} and ensure it is on PATH.")
    
    pnpm = shutil.which("pnpm")
    if not pnpm:
        log("  [WARN] pnpm not found on PATH. Will attempt fallback to npx pnpm.")

    log("  [OK] Tool prerequisites present")

# --- Git Repo Helpers --------------------------------------------------------

def ensure_repo(repo_name, git_url, branch=None):
    REPOS_DIR.mkdir(parents=True, exist_ok=True)
    target = REPOS_DIR / repo_name
    if not target.exists() or not (target / ".git").exists():
        if target.exists():
            shutil.rmtree(target)
        log(f"Cloning {repo_name} from {git_url}...")
        cmd = ["git", "clone"]
        if branch:
            cmd.extend(["-b", branch])
        cmd.extend([git_url, str(target)])
        run_cmd(cmd, timeout=300)
    else:
        log(f"Updating {repo_name}...")
        try:
            run_cmd(["git", "-C", str(target), "fetch", "origin"], timeout=30)
            target_branch = branch or "main"
            try:
                run_cmd(["git", "-C", str(target), "checkout", target_branch], timeout=30)
            except Exception:
                run_cmd(["git", "-C", str(target), "checkout", "master"], timeout=30)
        except Exception as e:
            log(f"  [WARN] Fetch failed for {repo_name} ({e}); using existing checkout.")

    sha = "unknown"
    try:
        sha = run_cmd(["git", "-C", str(target), "rev-parse", "HEAD"]).strip()
    except Exception:
        pass

    log(f"  [OK] {repo_name} ready at commit {sha[:10]}")
    return target, sha

def find_bash_executable():
    bash_path = shutil.which("bash")
    if bash_path:
        return pathlib.Path(bash_path)
    
    if sys.platform == "win32":
        candidates = [
            pathlib.Path(r"C:\Programs\Programming\Git\bin\bash.exe"),
            pathlib.Path(r"C:\Program Files\Git\bin\bash.exe"),
            pathlib.Path(r"C:\Program Files (x86)\Git\bin\bash.exe")
        ]
        for c in candidates:
            if c.exists():
                return c
    return None

def clean_poisoned_backup():
    log("Checking for poisoned tweakcc backup...")
    backup_file = TWEAKCC_HOME / "native-binary.backup"
    orig_js = TWEAKCC_HOME / "native-claudejs-orig.js"
    removed = False
    if backup_file.exists():
        backup_file.unlink()
        log(f"  [OK] Removed {backup_file}")
        removed = True
    if orig_js.exists():
        orig_js.unlink()
        log(f"  [OK] Removed {orig_js}")
        removed = True
    if not removed:
        log("  [INFO] No tweakcc backup files were present.")

# --- Staged Workflow Actions -------------------------------------------------

def prepare_stage():
    log("=== Stage 1: Preparing tweakcc-gilligan Setup ===")
    preflight_checks(require_no_claude=False)

    # Recipe Step 5: Clean Poisoned Backup
    clean_poisoned_backup()

    # Recipe Step 3: Determine GCV Target Version
    gcv_ver = None
    gcv_script = pathlib.Path(__file__).resolve().parent / "check_version_intersection.py"
    if gcv_script.exists():
        log("Checking greatest common supported Claude Code version...")
        try:
            gcv_out = run_cmd([sys.executable, str(gcv_script)])
            log(gcv_out.strip())
            m = re.search(r"greatest common version = (\S+)", gcv_out)
            if m:
                gcv_ver = m.group(1)
        except Exception as e:
            log(f"  Warning: GCV check failed: {e}")

    # Recipe Step 4: Reset Target Version to Stock
    if gcv_ver:
        log(f"Resetting Claude Code to stock version @{gcv_ver}...")
        try:
            npm_cmd = shutil.which("npm") or "npm"
            run_cmd([npm_cmd, "install", "-g", f"@anthropic-ai/claude-code@{gcv_ver}"], timeout=300, shell=(sys.platform == "win32"))
            log(f"  [OK] Reset Claude Code to stock @{gcv_ver}")
        except Exception as e:
            log(f"  Warning: Stock reset failed: {e}")

    # Sync repositories
    tweakcc_repo, twk_sha = ensure_repo("tweakcc-fixed", "https://github.com/skrabe/tweakcc-fixed.git")
    unnerf_repo, unf_sha = ensure_repo("unnerfcc", "https://github.com/brooksbUWO/unnerfcc.git", branch="windows-pe-support")
    lcc_repo, lcc_sha = ensure_repo("lobotomized-claude-code", "https://github.com/skrabe/lobotomized-claude-code.git")

    # Build tweakcc-fixed if needed
    dist_mjs = tweakcc_repo / "dist" / "index.mjs"
    if not dist_mjs.exists():
        log("Building tweakcc-fixed...")
        pnpm = shutil.which("pnpm") or "pnpm"
        run_cmd([pnpm, "install"], cwd=str(tweakcc_repo), timeout=300, shell=(sys.platform == "win32"))
        run_cmd([pnpm, "build"], cwd=str(tweakcc_repo), timeout=180, shell=(sys.platform == "win32"))
        if not dist_mjs.exists():
            die("Failed to build tweakcc-fixed (dist/index.mjs missing).", "Check pnpm build output in logs.")

    # Populate system-reminders from LCC repo
    log("Populating system-reminders directory in real HOME...")
    SYS_REMINDERS_DIR.mkdir(parents=True, exist_ok=True)
    lcc_reminders = lcc_repo / "system-reminders"
    if lcc_reminders.exists():
        copied_count = 0
        for md_file in lcc_reminders.glob("*.md"):
            shutil.copy(md_file, SYS_REMINDERS_DIR / md_file.name)
            copied_count += 1
        log(f"  [OK] Copied {copied_count} reminder override files into {SYS_REMINDERS_DIR}")
    else:
        log("  Warning: LCC system-reminders directory not found.")

    # Configure tweakcc Installation Path
    launcher = shutil.which("claude")
    if not launcher:
        die("Claude Code 'claude' binary not found on PATH.", "Install Claude Code via npm install -g @anthropic-ai/claude-code@<version>.")

    real_bin = pathlib.Path(launcher).resolve()
    if sys.platform == "win32" and real_bin.suffix.lower() != ".exe":
        npm_exe = real_bin.parent / "node_modules" / "@anthropic-ai" / "claude-code" / "bin" / "claude.exe"
        if npm_exe.exists():
            real_bin = npm_exe

    log(f"Configuring tweakcc config.json in {TWEAKCC_HOME}...")
    TWEAKCC_HOME.mkdir(parents=True, exist_ok=True)
    cfg_file = TWEAKCC_HOME / "config.json"
    cfg_data = {}
    if cfg_file.exists():
        try:
            cfg_data = json.loads(cfg_file.read_text(encoding="utf-8"))
        except Exception:
            cfg_data = {}
    cfg_data["ccInstallationPath"] = str(real_bin).replace("\\", "/")
    cfg_file.write_text(json.dumps(cfg_data, indent=2) + "\n", encoding="utf-8")
    log(f"  [OK] Configured ccInstallationPath = {real_bin}")

    # Generate External Apply Script (to respect hard session boundary)
    if sys.platform == "win32":
        ext_script = GILLIGAN_DIR / "apply-external.bat"
        bash_path = find_bash_executable()
        bash_str = str(bash_path) if bash_path else "bash"
        script_content = f"""@echo off
echo === Applying tweakcc-fixed and unnerfcc Patches ===
echo Please ensure all Claude Code sessions are closed!
pause
node "{dist_mjs.as_posix()}" --apply
if errorlevel 1 (
    echo tweakcc-fixed apply failed!
    pause
    exit /b 1
)
"{bash_str}" -lc "cd '{unnerf_repo.as_posix()}' && ./install.sh"
if errorlevel 1 (
    echo unnerfcc apply failed!
    pause
    exit /b 1
)
echo === Patches Applied Successfully! ===
python "{pathlib.Path(__file__).resolve().parent.as_posix()}/verify.py"
pause
"""
        ext_script.write_text(script_content, encoding="utf-8")
        log(f"  [OK] Generated external apply script: {ext_script}")
    else:
        ext_script = GILLIGAN_DIR / "apply-external.sh"
        script_content = f"""#!/usr/bin/env bash
set -e
echo "=== Applying tweakcc-fixed and unnerfcc Patches ==="
node "{dist_mjs.as_posix()}" --apply
( cd "{unnerf_repo.as_posix()}" && ./install.sh )
echo "=== Patches Applied Successfully! ==="
python3 "{pathlib.Path(__file__).resolve().parent.as_posix()}/verify.py"
"""
        ext_script.write_text(script_content, encoding="utf-8")
        ext_script.chmod(0o755)
        log(f"  [OK] Generated external apply script: {ext_script}")

    log("\n=== Stage 1 Complete ===")
    log(f"To complete patching outside Claude Code, run: {ext_script}")

def apply_stage():
    log("=== Stage 2: Applying Patches to Binary ===")
    preflight_checks(require_no_claude=True)

    tweakcc_repo = REPOS_DIR / "tweakcc-fixed"
    unnerf_repo = REPOS_DIR / "unnerfcc"
    dist_mjs = tweakcc_repo / "dist" / "index.mjs"

    if not dist_mjs.exists():
        die("tweakcc-fixed dist/index.mjs missing.", "Run python scripts/install.py --prepare first.")

    # Apply tweakcc-fixed
    log("Applying tweakcc-fixed code patches...")
    run_cmd(["node", str(dist_mjs), "--apply"], timeout=180)
    log("  [OK] tweakcc-fixed applied successfully")

    # Apply unnerfcc
    log("Applying unnerfcc prompt un-nerfs...")
    if sys.platform == "win32":
        git_bash = find_bash_executable()
        if not git_bash or not git_bash.exists():
            die("Git Bash not found.", "Install Git for Windows or check bash installation path.")
        bash_cmd = f"cd '{unnerf_repo.as_posix()}' && ./install.sh"
        run_cmd([str(git_bash), "-lc", bash_cmd], timeout=600)
    else:
        run_cmd(["./install.sh"], cwd=str(unnerf_repo), timeout=600)

    log("  [OK] unnerfcc applied successfully")

    # Write Manifest
    launcher = shutil.which("claude")
    manifest = {
        "installed_at": datetime.datetime.now().isoformat(),
        "tweakcc_fixed_sha": run_cmd(["git", "-C", str(tweakcc_repo), "rev-parse", "HEAD"]).strip(),
        "unnerfcc_sha": run_cmd(["git", "-C", str(unnerf_repo), "rev-parse", "HEAD"]).strip(),
        "target_binary": launcher
    }
    MANIFEST_FILE.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    log(f"  [OK] Manifest recorded at {MANIFEST_FILE}")

def main():
    global LOG_FILE
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp_str = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    LOG_FILE = LOGS_DIR / f"install_{timestamp_str}.log"

    parser = argparse.ArgumentParser(description="tweakcc-gilligan installer")
    parser.add_argument("--prepare", action="store_true", help="Stage repos, reminders, config, and external apply script")
    parser.add_argument("--apply", action="store_true", help="Execute binary patchers (must run outside active CC session)")
    parser.add_argument("--verify", action="store_true", help="Run verification check on binary")
    parser.add_argument("--clean-backup", action="store_true", help="Clear poisoned tweakcc backup files")
    args = parser.parse_args()

    acquire_lock()
    try:
        if args.clean_backup:
            clean_poisoned_backup()
        elif args.verify:
            verify_script = pathlib.Path(__file__).resolve().parent / "verify.py"
            sys.exit(subprocess.call([sys.executable, str(verify_script)]))
        elif args.apply:
            apply_stage()
        else:
            prepare_stage()
    finally:
        remove_lock()

if __name__ == "__main__":
    main()
