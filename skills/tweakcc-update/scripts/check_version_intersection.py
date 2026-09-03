#!/usr/bin/env python3
"""
Compute the greatest common supported Claude Code version across catalogs.

Goal: Identify the highest version number supported by both unnerfcc and tweakcc-fixed prompt catalogs.

Prefers local clone catalog files when present to avoid GitHub API rate limits.
Fails loudly (exit code 1) if either catalog set cannot be resolved.
"""

import argparse
import json
import os
import re
import sys
import threading
import time
import urllib.request
import urllib.error
import pathlib


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

def fetch_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "tweakcc-gilligan/1.0"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))

def parse_versions(names):
    versions = set()
    for name in names:
        m = re.search(r"prompts-(\d+\.\d+\.\d+)\.json$", name)
        if m:
            versions.add(m.group(1))
    return versions

def sort_versions(ver_list):
    def key_fn(v):
        return [int(x) for x in v.split(".")]
    return sorted(ver_list, key=key_fn)

def get_tweakcc_versions():
    # Check local clone first
    script_dir = pathlib.Path(__file__).resolve().parent
    gilligan_home = pathlib.Path(os.environ.get("TWEAKCC_GILLIGAN_HOME") or pathlib.Path.home())
    # The installer clones tweakcc-fixed into the runtime workspace
    # (~/.tweakcc-gilligan/repos/tweakcc-fixed). --prepare fast-forwards it to
    # origin/main before this check runs, then checks out the release that
    # catalogued the chosen target (install.select_tweakcc_ref), so the local
    # catalog set here is the full upstream set. The GitHub API fallback below
    # reads the same set when no clone exists yet.
    candidate_dirs = [
        gilligan_home / ".tweakcc-gilligan" / "repos" / "tweakcc-fixed" / "data" / "prompts",
    ]
    for local_dir in candidate_dirs:
        if local_dir.exists():
            names = [f.name for f in local_dir.glob("prompts-*.json")]
            if names:
                return parse_versions(names)
    
    # Fallback to GitHub API
    try:
        data = fetch_json("https://api.github.com/repos/skrabe/tweakcc-fixed/contents/data/prompts")
        return parse_versions([item["name"] for item in data if isinstance(item, dict) and "name" in item])
    except Exception as e:
        sys.stderr.write(f"ERROR: Failed to fetch tweakcc-fixed catalogs: {e}\n")
        return None

def get_unnerfcc_versions():
    # Check local clone first
    script_dir = pathlib.Path(__file__).resolve().parent
    gilligan_home = pathlib.Path(os.environ.get("TWEAKCC_GILLIGAN_HOME") or pathlib.Path.home())
    # The installer clones unnerfcc into the runtime workspace
    # (~/.tweakcc-gilligan/repos/unnerfcc); that is the only local catalog
    # source. If it is absent (a fresh run before prepare), fall through to the
    # GitHub API below.
    candidate_dirs = [
        gilligan_home / ".tweakcc-gilligan" / "repos" / "unnerfcc" / "data" / "prompts",
    ]
    for local_dir in candidate_dirs:
        if local_dir.exists():
            names = [f.name for f in local_dir.glob("prompts-*.json")]
            if names:
                return parse_versions(names)

    # Fallback to GitHub API on brooksbUWO/unnerfcc PE branch
    try:
        data = fetch_json("https://api.github.com/repos/brooksbUWO/unnerfcc/contents/data/prompts?ref=main")
        return parse_versions([item["name"] for item in data if isinstance(item, dict) and "name" in item])
    except Exception as e:
        sys.stderr.write(f"ERROR: Failed to fetch unnerfcc catalogs from brooksbUWO/unnerfcc (ref=main): {e}\n")
        return None

UPSTREAM_COMMIT_SPECS = [
    # (repo, subject pattern naming a supported CC version)
    # Upstream has no releases; its commit subjects name the version, in
    # three wordings so far: "sync to Claude Code vX", "sync prompts to
    # Claude Code vX", and (since 2.1.258) "support Claude Code vX".
    ("lukehutch/unnerfcc", re.compile(r"(?:sync(?:\s+prompts)?\s+to|support)\s+Claude Code\s+v(\d+\.\d+\.\d+)", re.IGNORECASE)),
    ("skrabe/tweakcc-fixed", re.compile(r"\bCC v?(\d+\.\d+\.\d+)\b")),
]

def _vkey(v):
    return [int(x) for x in v.split(".")]


def get_upstream_catalog_versions(repo):
    """Second signal: catalog filenames (data/prompts/prompts-<ver>.json) at the
    upstream HEAD. Independent of commit-subject wording, which has already
    changed once ("sync to" -> "support"); a fourth wording would otherwise
    silently under-report the target. Returns a set (empty on fetch failure)."""
    try:
        data = fetch_json(f"https://api.github.com/repos/{repo}/contents/data/prompts")
        return parse_versions([item["name"] for item in data if isinstance(item, dict) and "name" in item])
    except Exception as e:
        sys.stderr.write(f"WARNING: could not list {repo} data/prompts ({e}); catalog signal unavailable.\n")
        return set()


def get_upstream_head_support():
    """Newest CC version each upstream supports, from TWO signals: recent commit
    SUBJECTS ("sync to Claude Code vX.Y.Z", "prompts: catalogue CC X.Y.Z") and
    the catalog filenames at HEAD. The higher of the two wins, so a new subject
    wording cannot under-report as long as the catalog file exists.

    The local catalogs only say what the local clones can install NOW;
    upstream support lands long before it lands in this fork's catalog, so the
    catalog intersection alone understates the real ceiling.
    Returns {repo: (version, evidence) | None when neither signal resolves}.
    """
    results = {}
    for repo, pattern in UPSTREAM_COMMIT_SPECS:
        best = None
        best_evidence = None
        try:
            commits = fetch_json(f"https://api.github.com/repos/{repo}/commits?per_page=30")
        except Exception as e:
            sys.stderr.write(f"WARNING: could not read {repo} commit subjects ({e}).\n")
            commits = []
        for c in commits:
            subject = c.get("commit", {}).get("message", "").split("\n", 1)[0]
            m = pattern.search(subject)
            if m and (best is None or _vkey(m.group(1)) > _vkey(best)):
                best, best_evidence = m.group(1), f"subject \"{subject}\""
        if commits and best is None:
            sys.stderr.write(f"WARNING: no CC version found in the last 30 {repo} commit subjects "
                             "(wording drift?); relying on the catalog filename signal.\n")
        cat = get_upstream_catalog_versions(repo)
        if cat:
            cat_best = sort_versions(cat)[-1]
            if best is None or _vkey(cat_best) > _vkey(best):
                best, best_evidence = cat_best, f"catalog data/prompts/prompts-{cat_best}.json"
        if best is None:
            sys.stderr.write(f"WARNING: {repo} upstream HEAD support UNKNOWN (both signals failed); "
                             "do not treat the local result as the ceiling.\n")
            results[repo] = None
        else:
            results[repo] = (best, best_evidence)
    return results

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-seconds", type=float, default=120.0, dest="max_seconds",
                        help="Hard wall-clock ceiling in seconds; the process exits with code 3 when it fires (default: 120)")
    parser.add_argument("--watchdog-probe", type=float, default=0.0, dest="watchdog_probe",
                        help="Diagnostic: idle this many seconds after arming the watchdog (default: 0)")
    args = parser.parse_args()
    _arm_watchdog(args.max_seconds, args.watchdog_probe)

    twk_versions = get_tweakcc_versions()
    unf_versions = get_unnerfcc_versions()

    if twk_versions is None:
        sys.stderr.write("ERROR: Unable to load tweakcc-fixed catalogs.\n")
        return 1

    if unf_versions is None:
        sys.stderr.write("ERROR: Unable to load unnerfcc catalogs.\n")
        return 1

    try:
        npm_data = fetch_json("https://registry.npmjs.org/@anthropic-ai/claude-code/latest")
        npm_latest = npm_data.get("version", "unknown")
    except Exception as e:
        sys.stderr.write(f"Warning: Failed to fetch npm latest: {e}\n")
        npm_latest = "unknown"

    common_versions = sort_versions(twk_versions.intersection(unf_versions))

    print(f"tweakcc-fixed           : {len(twk_versions)} catalogs, newest {sort_versions(twk_versions)[-1] if twk_versions else 'none'}")
    print(f"unnerfcc                : {len(unf_versions)} catalogs, newest {sort_versions(unf_versions)[-1] if unf_versions else 'none'}")
    print(f"npm latest              : {npm_latest}")

    if not common_versions:
        sys.stderr.write("ERROR: No common supported version found between tweakcc-fixed and unnerfcc catalogs.\n")
        return 1

    local_common = common_versions[-1]

    upstream = get_upstream_head_support()
    print("UPSTREAM HEAD (commit subjects + catalog filenames):")
    upstream_versions = []
    for repo, _ in UPSTREAM_COMMIT_SPECS:
        entry = upstream.get(repo)
        if entry:
            ver, evidence = entry
            upstream_versions.append(ver)
            print(f"  {repo:<22}: {ver} ({evidence})")
        else:
            print(f"  {repo:<22}: UNKNOWN (see warning above)")

    # The common version is what BOTH patcher projects support, so take the
    # MINIMUM across the upstreams. The local fork catalogs lag upstream and
    # only describe readiness, never the target.
    certain = len(upstream_versions) == len(UPSTREAM_COMMIT_SPECS)
    if certain:
        target = sort_versions(upstream_versions)[0]
    else:
        target = local_common
        print("WARNING: an upstream is UNKNOWN; falling back to the local catalog")
        print(f"         intersection ({local_common}). This may UNDERSTATE the true target.")

    if [int(x) for x in local_common.split(".")] < [int(x) for x in target.split(".")]:
        print(f"READINESS: local catalogs lag at {local_common} (fork catalog/engine not yet")
        print(f"           synced to {target}). Closing the gap: install {target}, then run")
        print("           upgrade.sh to extract its corpus and re-anchor the rules.")
    else:
        print(f"READINESS: local catalogs are current ({local_common}).")

    # Two distinct numbers, printed on two labeled lines so they cannot be read
    # as one: APPLICABLE is what the local clones can patch NOW (min of the fork
    # catalog and the local tweakcc-fixed catalog); RESULT is the upstream target.
    print(f"APPLICABLE: newest version the local clones can patch now (fork catalog and local tweakcc-fixed catalog) = {local_common}")
    if certain:
        print(f"RESULT: greatest common version (both patchers' upstream support) = {target}")
        print(f"npm install -g @anthropic-ai/claude-code@{target}")
        return 0
    # A different label on purpose: install.py --prepare accepts only "RESULT:"
    # and stops on "RESULT-UNCERTAIN:", so a guess is never recorded as the target.
    print(f"RESULT-UNCERTAIN: local catalog fallback (an upstream could not be read) = {target}")
    print("Re-run when the upstream is reachable before installing a version.")
    return 1

if __name__ == "__main__":
    sys.exit(main())
