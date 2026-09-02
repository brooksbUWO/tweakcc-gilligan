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
import threading

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

# Real upstream sources whose commit subjects carry the supported Claude Code
# version (the author syncs by commit, not by release, so commit subjects are
# the version truth). Keyed by repo_name as used in ensure_repo.
UPSTREAM_PROBES = {
    "unnerfcc": ("https://github.com/lukehutch/unnerfcc.git", "main"),
}

# Matches upstream sync-commit subjects like "sync to Claude Code v2.1.235",
# "sync prompts to Claude Code v2.1.222", and "support Claude Code v2.1.258"
# (upstream has no releases; the subject is the only version signal).
SYNC_SUBJECT_RE = re.compile(r"(?:sync(?:\s+prompts)?\s+to|support)\s+Claude Code\s+v(\d+\.\d+\.\d+)", re.IGNORECASE)


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

def run_cmd(cmd, cwd=None, env=None, timeout=600, shell=False, capture_label=None):
    log(f"Running: {cmd if isinstance(cmd, str) else ' '.join(cmd)}")
    proc = None
    try:
        proc = subprocess.Popen(
            cmd, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, shell=shell
        )
        register_pid(proc.pid)
        stdout, stderr = proc.communicate(timeout=timeout)
        if capture_label:
            # Persist the patcher's full per-item accounting in the log. The
            # summary "[OK] applied successfully" alone hides per-override
            # failures (silent-failure class; observed 2026-08-30: a one-item
            # startup banner with no way to reconstruct which overrides failed).
            log(f"----- BEGIN {capture_label} -----\n{stdout}\n----- END {capture_label} -----")
            if stderr.strip():
                log(f"----- BEGIN {capture_label} (stderr) -----\n{stderr}\n----- END {capture_label} (stderr) -----")
        if proc.returncode != 0:
            log(f"Command exited with code {proc.returncode}")
            if not capture_label:
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

# A lock older than this is treated as orphaned regardless of its PID: a
# watchdog kill (os._exit(3)) skips remove_lock(), and PID reuse can make an
# orphaned lock's PID read as a live unrelated process, permanently blocking
# runs. Age is the tiebreaker; the ceiling comfortably exceeds any real run.
STALE_LOCK_SECONDS = 4 * 3600


def acquire_lock():
    GILLIGAN_DIR.mkdir(parents=True, exist_ok=True)
    if LOCK_FILE.exists():
        try:
            lock_data = json.loads(LOCK_FILE.read_text(encoding="utf-8"))
            pid = lock_data.get("pid")
            lock_ts = lock_data.get("timestamp")
            if isinstance(lock_ts, (int, float)) and (time.time() - lock_ts) > STALE_LOCK_SECONDS:
                age_h = (time.time() - lock_ts) / 3600
                log(f"  [WARN] Removing stale install.lock (age {age_h:.1f} h, PID {pid}); "
                    f"a lock this old is an orphan from a killed run, not a live install.")
            elif sys.platform == "win32":
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

def ensure_repo(repo_name, git_url, branch=None, version_source=False, pin_ref=None):
    # pin_ref pins the repo to an exact commit/tag and DISABLES the
    # fast-forward-to-remote step. Each tweakcc-fixed release patches ONE Bun
    # binary format (2.8.0+ = the CC 2.1.246+ code-split format; v2.7.38 and
    # earlier = the old single-module format). A mismatch throws "claude module
    # not found in any of the binary modules". 452f15a ("prompts: catalogue CC
    # 2.1.258") carries the code-split extractor (890c928) plus the 2.1.258
    # prompt catalog and patch re-anchors, matching the 2.1.258 target. Pinning
    # here keeps prepare from advancing tweakcc-fixed past the binary format
    # the target actually uses.
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
        if pin_ref:
            # reset --hard, not just checkout: force the worktree to exactly the
            # pinned commit. A bare checkout onto a dirty tree leaves local edits
            # in place, so the build would compile non-pinned source while
            # `rev-parse HEAD` still reports the pinned ref (a silent divergence
            # on a version_source repo). Fresh clone is clean here, but reset is
            # kept for symmetry with the existing-clone path and defence in depth.
            try:
                run_cmd(["git", "-C", str(target), "checkout", pin_ref], timeout=30)
                run_cmd(["git", "-C", str(target), "reset", "--hard", pin_ref], timeout=30)
                log(f"  [OK] {repo_name} pinned to {pin_ref}")
            except Exception as e:
                die(f"Could not pin freshly cloned {repo_name} to {pin_ref} ({e}).",
                    f"Delete {target} and re-run --prepare to get a fresh clone at {pin_ref}.")
    elif pin_ref:
        # Existing clone with a pin: fetch so the ref is present, then
        # hard-reset the worktree to the pinned commit/tag. Deliberately NO
        # fast-forward-to-remote: advancing to origin HEAD is exactly what the
        # pin prevents (it would drag tweakcc-fixed back to 2.8.0 and reintroduce
        # the 2.1.246-only extractor).
        log(f"Updating {repo_name} (pinned to {pin_ref})...")
        try:
            run_cmd(["git", "-C", str(target), "fetch", "origin", "--tags"], timeout=60)
        except Exception as e:
            log(f"  [WARN] fetch failed for pinned {repo_name} ({e}); using existing objects.")
        try:
            run_cmd(["git", "-C", str(target), "checkout", pin_ref], timeout=30)
            # reset --hard forces the worktree to the pinned commit even when the
            # clone is already ON that commit but DIRTY. An install run leaves
            # CRLF/LF churn in tracked files (documented in CLAUDE.md), so a bare
            # checkout would build modified source while the logs and rev-parse
            # HEAD both report the pinned ref. reset --hard makes the pin exact.
            run_cmd(["git", "-C", str(target), "reset", "--hard", pin_ref], timeout=30)
            log(f"  [OK] {repo_name} pinned to {pin_ref}")
        except Exception as e:
            die(f"Could not pin {repo_name} to {pin_ref} ({e}).",
                f"Delete {target} and re-run --prepare to get a fresh clone at {pin_ref}.")
    else:
        log(f"Updating {repo_name}...")
        try:
            run_cmd(["git", "-C", str(target), "fetch", "origin"], timeout=30)
            target_branch = branch or "main"
            # The runtime clone is disposable (source is edited only in its
            # dev location). The pre-flight below rewrites tracked files in
            # it (sync-version + apply-unnerfs replay), so a dirty worktree is
            # the normal state on the next run; discard it before the
            # fast-forward instead of failing on it.
            # A run interrupted mid-git leaves .git/index.lock behind; nothing
            # else runs git in this disposable clone, so the lock is stale.
            stale_lock = target / ".git" / "index.lock"
            if stale_lock.exists():
                stale_lock.unlink()
                log(f"  [OK] {repo_name}: removed stale .git/index.lock")
            dirty = run_cmd(["git", "-C", str(target), "status", "--porcelain"], timeout=30).strip()
            if dirty:
                run_cmd(["git", "-C", str(target), "checkout", "-q", "--", "."], timeout=60)
                run_cmd(["git", "-C", str(target), "clean", "-fdq"], timeout=60)
                log(f"  [OK] {repo_name}: discarded local replay output in the runtime clone "
                    f"({len(dirty.splitlines())} path(s))")
            try:
                run_cmd(["git", "-C", str(target), "checkout", target_branch], timeout=30)
            except Exception:
                # The master fallback exists only for default-branch detection
                # (main vs master). An EXPLICITLY requested branch must exist;
                # silently landing on master could sync catalogs from the wrong
                # branch and record a target version from the wrong source.
                if branch:
                    raise
                target_branch = "master"
                run_cmd(["git", "-C", str(target), "checkout", target_branch], timeout=30)
            # Fast-forward to the fetched remote head. Without this the fetch is
            # inert: the local branch silently pins its old commit (observed 143
            # commits stale), which poisons the version-intersection result while
            # the log still reports the repo as "ready".
            try:
                behind = run_cmd(["git", "-C", str(target), "rev-list", "--count",
                                  f"{target_branch}..origin/{target_branch}"], timeout=30).strip()
                run_cmd(["git", "-C", str(target), "merge", "--ff-only",
                         f"origin/{target_branch}"], timeout=30)
                if behind not in ("", "0"):
                    log(f"  [OK] {repo_name} fast-forwarded {behind} commit(s) to origin/{target_branch}")
            except Exception as e:
                # A version-source repo feeds the greatest-common-version target computation; a clone
                # that cannot reach the fetched remote head would silently record
                # a stale target, which is the exact failure this sync exists to
                # prevent. Fatal for version sources, warn-and-continue otherwise.
                # (A dirty worktree also fails --ff-only, not only true divergence.)
                if version_source:
                    die(f"{repo_name} could not fast-forward to origin/{target_branch} ({e}). "
                        f"This repo's catalogs decide the target Claude Code version, so a "
                        f"stale or diverged clone must not proceed.",
                        f"Inspect {target}: commit/stash or discard local changes, or delete the "
                        f"clone directory and re-run --prepare to get a fresh clone.")
                log(f"  [WARN] {repo_name} could NOT fast-forward to origin/{target_branch} ({e}); "
                    f"local branch has diverged or is dirty — resolve manually. "
                    f"Continuing with the existing (possibly stale) checkout.")
        except Exception as e:
            if version_source:
                die(f"Fetch failed for version-source repo {repo_name} ({e}); its catalogs "
                    f"decide the target Claude Code version, so prepare cannot continue on "
                    f"a possibly stale clone.",
                    "Check network/GitHub access and re-run --prepare.")
            log(f"  [WARN] Fetch failed for {repo_name} ({e}); using existing checkout.")

    sha = "unknown"
    try:
        sha = run_cmd(["git", "-C", str(target), "rev-parse", "HEAD"]).strip()
    except Exception:
        pass

    log(f"  [OK] {repo_name} ready at commit {sha[:10]}")

    # Upstream sync-version probe: when the repo has a declared real upstream,
    # compare the newest "sync to Claude Code vX.Y.Z" commit subject upstream
    # against the newest one reachable from the local HEAD. The author ships
    # syncs as commits without releases, so commit subjects are the version
    # truth; a gap here means the tracked branch needs updating and no
    # amount of re-running prepare can see past it.
    probe = UPSTREAM_PROBES.get(repo_name)
    if probe:
        upstream_url, upstream_branch = probe
        try:
            # Fetch by URL into FETCH_HEAD: no persistent named remote is created
            # or trusted, so a user-configured "upstream" remote pointing at some
            # other URL can never be silently compared while the log names this one.
            run_cmd(["git", "-C", str(target), "fetch", upstream_url, upstream_branch], timeout=60)
            up_subjects = run_cmd(["git", "-C", str(target), "log", "FETCH_HEAD",
                                   "--pretty=%s", "-100"], timeout=30)
            local_subjects = run_cmd(["git", "-C", str(target), "log", "HEAD",
                                      "--pretty=%s", "-200"], timeout=30)

            def newest_sync_version(subjects):
                # Highest semver among matched sync subjects, not first-in-log:
                # a revert or a hotfix onto an old line would otherwise report a
                # lower version as current.
                versions = SYNC_SUBJECT_RE.findall(subjects)
                if not versions:
                    return None
                return max(versions, key=lambda v: [int(x) for x in v.split(".")])

            up_ver = newest_sync_version(up_subjects)
            local_ver = newest_sync_version(local_subjects)
            def semver_key(v):
                return [int(x) for x in v.split(".")]

            if up_ver and local_ver and semver_key(up_ver) > semver_key(local_ver):
                log(f"  [WARN] {repo_name}: real upstream ({upstream_url}) has synced to "
                    f"Claude Code v{up_ver} (per commit subjects), but the tracked branch "
                    f"supports only v{local_ver}. The tracked branch needs updating "
                    f"before newer Claude Code versions can be targeted.")
            elif up_ver and local_ver and semver_key(local_ver) > semver_key(up_ver):
                log(f"  [OK] {repo_name}: tracked branch (v{local_ver}) is AHEAD of the real "
                    f"upstream (v{up_ver} per commit subjects); no update needed.")
            elif up_ver and not local_ver:
                log(f"  [WARN] {repo_name}: upstream reports sync to v{up_ver}, but no sync "
                    f"commit subject was found on the local branch; cannot compare.")
            elif up_ver:
                log(f"  [OK] {repo_name}: in sync with real upstream (both at v{up_ver} per commit subjects)")
            else:
                log(f"  [WARN] {repo_name}: no 'sync to Claude Code vX.Y.Z' commit subject found "
                    f"in the newest 100 upstream commits; upstream may have changed its "
                    f"commit conventions — probe inconclusive.")
        except Exception as e:
            log(f"  [WARN] {repo_name}: upstream sync-version probe failed ({e}); "
                f"continuing without upstream comparison.")

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

def ensure_python3_shim():
    """Windows only. install.sh and upgrade.sh spawn `python3`; no python3.exe
    exists on Windows, and a bare extensionless `python3` shim gets ShellExecuted
    into an endless "Select an app" picker. A `python3.bat` in ~/.local/bin is
    what cmd/CreateProcess PATH lookups resolve instead. Verify it, create it
    when absent, and warn when the directory is not on PATH."""
    if os.name != "nt":
        return
    bin_dir = pathlib.Path.home() / ".local" / "bin"
    shim = bin_dir / "python3.bat"
    if shim.exists():
        log(f"  [OK] python3 shim present: {shim}")
    else:
        bin_dir.mkdir(parents=True, exist_ok=True)
        shim.write_text("@echo off\r\nrem Windows-side python3 shim (written by tweakcc-update install.py --prepare)\r\npython %*\r\n", encoding="ascii")
        log(f"  [OK] python3 shim created: {shim}")
    path_entries = [e.strip().lower().rstrip("\\") for e in os.environ.get("PATH", "").split(os.pathsep)]
    if str(bin_dir).lower().rstrip("\\") not in path_entries:
        log(f"  [WARN] {bin_dir} is not on PATH; Windows-side python3 spawns in install.sh/upgrade.sh will fail until it is.")


def prepare_stage():
    log("=== Stage 1: Preparing tweakcc-gilligan Setup ===")
    preflight_checks(require_no_claude=False)
    ensure_python3_shim()

    # Recipe Step 5: Clean Poisoned Backup
    clean_poisoned_backup()

    # Sync repositories FIRST. The greatest-common-version check below reads the clones' catalogs,
    # so computing it before the sync would record a target from stale clones
    # (observed: a 143-commit-stale tweakcc-fixed pinned the target at an old
    # version while newer catalogs sat unfetched).
    # Pin tweakcc-fixed to 452f15a ("prompts: catalogue CC 2.1.258"): the
    # code-split extractor (CC 2.1.246+ format) with the 2.1.258 catalog and
    # patch re-anchors, matching the 2.1.258 target. See ensure_repo's pin_ref
    # comment and SKILL.md "tweakcc-fixed binary-format compatibility".
    tweakcc_repo, twk_sha = ensure_repo("tweakcc-fixed", "https://github.com/skrabe/tweakcc-fixed.git", version_source=True, pin_ref="452f15a")
    unnerf_repo, unf_sha = ensure_repo("unnerfcc", "https://github.com/brooksbUWO/unnerfcc.git", branch="master", version_source=True)
    lcc_repo, lcc_sha = ensure_repo("lobotomized-claude-code", "https://github.com/skrabe/lobotomized-claude-code.git")

    # Recipe Step 3: Determine the greatest-common-version target (from the now-current clones).
    # A prepare that cannot determine and record the target is a failed prepare:
    # the apply stage would either skip the stock reset or reset to the wrong
    # version, so every miss here dies loudly instead of skipping silently.
    version_check_script = pathlib.Path(__file__).resolve().parent / "check_version_intersection.py"
    if not version_check_script.exists():
        die(f"check_version_intersection.py not found next to install.py ({version_check_script}).",
            "Restore the skill's scripts directory; prepare cannot record a target version without it.")
    log("Checking greatest common supported Claude Code version...")
    try:
        version_check_out = run_cmd([sys.executable, str(version_check_script)])
        log(version_check_out.strip())
    except Exception as e:
        die(f"Greatest-common-version check failed: {e}",
            "Fix the error above and re-run --prepare; the apply stage must not run without a recorded target version.")
    # The RESULT line may carry a parenthetical between the label and "=":
    # "greatest common version (both patchers' upstream support) = 2.1.257".
    m = re.search(r"greatest common version[^=\n]*= (\d+\.\d+\.\d+)", version_check_out)
    if not m:
        die("The greatest-common-version check ran but its output did not contain 'greatest common version ... = <ver>'.",
            f"check_version_intersection.py output format drifted; raw output above. Fix the parser or the script.")
    greatest_common_version = m.group(1)

    # Record the greatest-common-version target for apply stage (do NOT run npm install during prepare)
    target_version_file = GILLIGAN_DIR / "target_version.txt"
    target_version_file.write_text(greatest_common_version, encoding="utf-8")
    log(f"  [OK] Recorded target version @{greatest_common_version} for apply stage")

    # Pre-flight rule-set drift check. install.sh runs apply-unnerfs --check at
    # APPLY time (external, all sessions closed); a single stale rule there aborts
    # the whole apply before the binary splice, so a fresh correct fix reaches
    # nothing and the user round-trips back into a session to fix it. Run the same
    # sync + full-set --check HERE, inside the session, so any drifted stock
    # (e.g. a bare ${} that a new CC version renamed to a named placeholder) fails
    # prepare loudly with apply-unnerfs's own drift diagnosis, cheap to fix, before
    # the external run. Uses the same bash the apply uses so the check sees exactly
    # what the apply will splice.
    log("Pre-flight: syncing system-prompts and checking the full rule set for stock drift...")
    bash_exe = find_bash_executable()
    if bash_exe is None:
        die("bash not found; cannot run the pre-flight rule-set check.",
            "Install Git for Windows (bash) or ensure bash is on PATH, then re-run --prepare.")
    bash_exe = str(bash_exe)  # find_bash_executable returns a Path; run_cmd joins the list to log it
    sync_mjs = (unnerf_repo / "scripts" / "sync-version.mjs").as_posix()
    apply_py = (unnerf_repo / "scripts" / "apply-unnerfs.py").as_posix()
    # Mirror install.sh's own sequence (sync -> apply -> check): --check alone
    # exits 1 on a FRESH sync because every rule "would change" (not-yet-applied),
    # which is not drift. Applying first, THEN checking, isolates true drift: a
    # stale-stock rule reports FAILED (exit 1) while a clean set exits 0.
    # A fresh clone has no node_modules; sync-version.mjs imports gray-matter
    # and dies with ERR_MODULE_NOT_FOUND without it. Install the declared
    # dependencies once, inside the same login shell that resolves node/npm.
    preflight_cmd = (
        f"cd '{unnerf_repo.as_posix()}' && "
        f"if [ ! -d node_modules ]; then npm install --no-audit --no-fund; fi && "
        f"node '{sync_mjs}' '{greatest_common_version}' && "
        f"python3 '{apply_py}' --quiet && "
        f"python3 '{apply_py}' --check --quiet"
    )
    try:
        # -lc (login shell) is required, not -c: the login shell sources the
        # Git-Bash profile that puts node and python3 on PATH. A change to -c
        # would break their resolution and misfire this block as a phantom sync
        # error. This matches the apply stage's own bash invocation.
        run_cmd([bash_exe, "-lc", preflight_cmd])
        log("  [OK] Rule-set pre-flight check clean (0 FAILED); no stale stock.")
    except Exception as e:
        # The compound command can fail for drift (a rule's stock no longer
        # byte-matches the store -> apply-unnerfs reports FAILED) OR for a
        # non-drift reason (sync-version.mjs download/dep error, a timeout).
        # {e} is only the CalledProcessError summary; run_cmd already logged the
        # sub-process STDOUT/STDERR above, so point the user there and let the
        # reported FAILED-rule count, not this message, decide the remediation.
        die(f"Pre-flight sync+apply+check failed ({e}). See the sub-process output logged above "
            f"for the real cause against the {greatest_common_version} store.",
            "If the output above shows one or more FAILED rules, re-anchor each FAILED rule's stock "
            "in unnerfcc/scripts/apply-unnerfs.py to the current store body, commit and push, then "
            "re-run --prepare. If it shows a sync or dependency error instead (not a FAILED rule), "
            "fix that error and re-run --prepare. Do not run the external apply until this check is "
            "clean: install.sh aborts the whole apply on any FAILED rule.")

    # Build tweakcc-fixed. ALWAYS rebuild, never gate on dist/ existing: dist/
    # is gitignored, so a checkout/fast-forward/pin that changes src/ leaves a
    # stale dist/ in place. The old "if not dist_mjs.exists()" gate then ran the
    # leftover build from the PREVIOUS source (e.g. a 2.8.0 dist over pinned
    # v2.7.38 source), silently defeating the pin. Deleting dist/ forces the
    # build to reflect the currently checked-out source.
    dist_mjs = tweakcc_repo / "dist" / "index.mjs"
    dist_dir = tweakcc_repo / "dist"
    log("Building tweakcc-fixed (from current checkout)...")
    if dist_dir.exists():
        shutil.rmtree(dist_dir, ignore_errors=True)
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
    install_py_path = pathlib.Path(__file__).resolve().as_posix()
    verify_py_path = (pathlib.Path(__file__).resolve().parent / "verify.py").as_posix()
    if sys.platform == "win32":
        ext_script = GILLIGAN_DIR / "apply-external.bat"
        script_content = f"""@echo off
echo === Applying tweakcc-fixed and unnerfcc Patches ===
echo Please ensure all Claude Code sessions are closed!
pause
python "{install_py_path}" --apply
if errorlevel 1 (
    echo install.py --apply failed!
    pause
    exit /b 1
)
python "{verify_py_path}"
if errorlevel 1 (
    echo verify.py failed!
    pause
    exit /b 1
)
echo === Patches Applied and Verified Successfully! ===
pause
"""
        ext_script.write_text(script_content, encoding="utf-8")
        log(f"  [OK] Generated external apply script: {ext_script}")
    else:
        ext_script = GILLIGAN_DIR / "apply-external.sh"
        script_content = f"""#!/usr/bin/env bash
set -e
echo "=== Applying tweakcc-fixed and unnerfcc Patches ==="
python3 "{install_py_path}" --apply
python3 "{verify_py_path}"
echo "=== Patches Applied and Verified Successfully! ==="
"""
        ext_script.write_text(script_content, encoding="utf-8")
        ext_script.chmod(0o755)
        log(f"  [OK] Generated external apply script: {ext_script}")

    log("\n=== Stage 1 Complete ===")
    log(f"To complete patching outside Claude Code, run: {ext_script}")

# Per-item failure markers in patcher output. Exit code 0 from a patcher does
# NOT prove every override landed: tweakcc-fixed exits 0 while individual
# prompt overrides fail or skip. These patterns turn per-item failures into a
# loud abort instead of a silently incomplete patch.
# Anchored to line start: tweakcc-fixed prints one free-text description line
# per prompt file ("...subscription failed to arm...") and an unanchored
# "failed to " matched those descriptions as failures (2026-09-01: 5 false
# positives aborted a successful 2.1.258 apply). Real failures are
# "patch: <name>: failed to ...", "Error: ...", "\u2716 Error ..." at column 0
# (descriptions are indented, so lines are matched unstripped).
TWEAKCC_FAIL_PATTERNS = [r"^patch: .*: failed to ", r"^inline-blob: failed", r"^Error: ", r"^\u2716 Error",
                         r"^Failed to read markdown file", r"\[FAILED"]
# Version-conditional skips worth surfacing (not the free-text descriptions).
TWEAKCC_SKIP_PATTERN = r"\bskipping\b|^Skipped \d+ up-to-date|^Unresolved placeholder"
TWEAKCC_NOTFOUND_PATTERN = r"^Could not find system prompt"  # hundreds per run (platform-conditional prompts); counted, not listed
# "[LOST] <id>: couldNotFind" is patch-prompts.mjs reporting an un-nerf that never
# reached the bundle; install.sh still exits 0 (2026-09-01: one lost rule passed
# this gate as "applied successfully").
UNNERFCC_FAIL_PATTERNS = [r"\[FAILED", r"\[LOST\]", r"UN-NERF\(S\) FAILED TO SPLICE",
                         r"Rules FAILED\s*:\s*[1-9]", r"Missing files\s*:\s*[1-9]"]


def check_patcher_output(name, output, fail_patterns):
    """Die loudly when a patcher's output carries per-item failure markers.
    'skipped' lines are surfaced as warnings but do not abort: some skips are
    version-conditional by design; a failure marker never is."""
    bad = []
    skipped = []
    not_found = 0
    for line in output.splitlines():
        s = line.rstrip()
        if any(re.search(p, s, re.IGNORECASE) for p in fail_patterns):
            bad.append(s)
        elif name == "tweakcc-fixed" and re.search(TWEAKCC_NOTFOUND_PATTERN, s):
            not_found += 1
        elif re.search(TWEAKCC_SKIP_PATTERN if name == "tweakcc-fixed" else r"\bskipped\b", s, re.IGNORECASE) and not re.search(r"already un-nerfed", s, re.IGNORECASE):
            skipped.append(s)
    for s in skipped:
        log(f"  [WARN] {name} skipped item: {s}")
    if not_found:
        log(f"  [WARN] {name}: {not_found} prompt override(s) not found in cli.js (platform-conditional; full list in this log)")
    if bad:
        for s in bad:
            log(f"  [FAIL] {name}: {s}")
        die(f"{name} reported {len(bad)} per-item failure(s); the patch is INCOMPLETE.",
            "Read the failure lines above (full output is in this log). Fix the named overrides/rules, then re-run the apply. Do not treat this binary as fully patched.")


def apply_stage():
    log("=== Stage 2: Applying Patches to Binary ===")
    preflight_checks(require_no_claude=True)

    # Recipe Step 4: Reset Target Version to Stock
    greatest_common_version = None
    target_version_file = GILLIGAN_DIR / "target_version.txt"
    if target_version_file.exists():
        try:
            greatest_common_version = target_version_file.read_text(encoding="utf-8").strip()
        except Exception:
            pass

    if not greatest_common_version:
        # No recompute fallback here, deliberately: the apply stage patches the
        # binary against the repository state the PREPARE stage staged, so the
        # target version must be the one prepare recorded. Recomputing now could
        # silently pick a different version than the staged artifacts were
        # prepared for (catalogs may have advanced since), defeating the staged
        # handoff. A missing record means prepare did not complete; fail loudly.
        die("No recorded target Claude Code version (target_version.txt missing or unreadable).",
            "Run python scripts/install.py --prepare first; the apply stage only accepts the version prepare recorded.")

    log(f"Resetting Claude Code to stock version @{greatest_common_version}...")
    try:
        npm_cmd = shutil.which("npm") or "npm"
        run_cmd([npm_cmd, "install", "-g", f"@anthropic-ai/claude-code@{greatest_common_version}"], timeout=300, shell=(sys.platform == "win32"))
        log(f"  [OK] Reset Claude Code to stock @{greatest_common_version}")
    except Exception as e:
        die(f"Stock reset to @{greatest_common_version} failed: {e}",
            "Patching on top of an unknown or already-patched binary is unsafe. Fix npm/network and re-run the apply.")

    tweakcc_repo = REPOS_DIR / "tweakcc-fixed"
    unnerf_repo = REPOS_DIR / "unnerfcc"
    dist_mjs = tweakcc_repo / "dist" / "index.mjs"

    if not dist_mjs.exists():
        die("tweakcc-fixed dist/index.mjs missing.", "Run python scripts/install.py --prepare first.")

    # Apply tweakcc-fixed
    log("Applying tweakcc-fixed code patches...")
    twk_out = run_cmd(["node", str(dist_mjs), "--apply"], timeout=180,
                      capture_label="TWEAKCC-FIXED OUTPUT")
    check_patcher_output("tweakcc-fixed", twk_out, TWEAKCC_FAIL_PATTERNS)
    log("  [OK] tweakcc-fixed applied successfully (per-item accounting logged, no failure markers)")

    # Apply unnerfcc
    log("Applying unnerfcc prompt un-nerfs...")
    if sys.platform == "win32":
        git_bash = find_bash_executable()
        if not git_bash or not git_bash.exists():
            die("Git Bash not found.", "Install Git for Windows or check bash installation path.")
        bash_cmd = f"cd '{unnerf_repo.as_posix()}' && ./install.sh"
        unf_out = run_cmd([str(git_bash), "-lc", bash_cmd], timeout=600,
                          capture_label="UNNERFCC OUTPUT")
    else:
        unf_out = run_cmd(["./install.sh"], cwd=str(unnerf_repo), timeout=600,
                          capture_label="UNNERFCC OUTPUT")
    check_patcher_output("unnerfcc", unf_out, UNNERFCC_FAIL_PATTERNS)
    log("  [OK] unnerfcc applied successfully (per-item accounting logged, no failure markers)")

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
    parser.add_argument("--max-seconds", type=float, default=1800.0, dest="max_seconds",
                        help="Hard wall-clock ceiling in seconds; the process exits with code 3 when it fires (default: 1800)")
    parser.add_argument("--watchdog-probe", type=float, default=0.0, dest="watchdog_probe",
                        help="Diagnostic: idle this many seconds after arming the watchdog (default: 0)")
    args = parser.parse_args()
    _arm_watchdog(args.max_seconds, args.watchdog_probe)

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
