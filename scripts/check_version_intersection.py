#!/usr/bin/env python3
"""
Compute the greatest common supported Claude Code version across catalogs.

Goal: Identify the highest version number supported by both unnerfcc and tweakcc-fixed prompt catalogs.

Prefers local clone catalog files when present to avoid GitHub API rate limits.
Fails loudly (exit code 1) if either catalog set cannot be resolved.
"""

import json
import os
import re
import sys
import urllib.request
import urllib.error
import pathlib

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
    candidate_dirs = [
        script_dir.parent / "repos" / "tweakcc-fixed" / "data" / "prompts",
        gilligan_home / ".tweakcc-gilligan" / "repos" / "tweakcc-fixed" / "data" / "prompts",
        script_dir.parent.parent / "repos" / "tweakcc-fixed" / "data" / "prompts",
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
    candidate_dirs = [
        script_dir.parent / "repos" / "unnerfcc" / "data" / "prompts",
        script_dir.parent / "repos" / "unnerfcc-pr" / "data" / "prompts",
        gilligan_home / ".tweakcc-gilligan" / "repos" / "unnerfcc" / "data" / "prompts",
        script_dir.parent.parent / "repos" / "unnerfcc" / "data" / "prompts",
        script_dir.parent.parent / "repos" / "unnerfcc-pr" / "data" / "prompts",
    ]
    for local_dir in candidate_dirs:
        if local_dir.exists():
            names = [f.name for f in local_dir.glob("prompts-*.json")]
            if names:
                return parse_versions(names)

    # Fallback to GitHub API on brooksbUWO/unnerfcc PE branch
    try:
        data = fetch_json("https://api.github.com/repos/brooksbUWO/unnerfcc/contents/data/prompts?ref=windows-pe-support")
        return parse_versions([item["name"] for item in data if isinstance(item, dict) and "name" in item])
    except Exception as e:
        # Retry with lukehutch/unnerfcc upstream
        try:
            data = fetch_json("https://api.github.com/repos/lukehutch/unnerfcc/contents/data/prompts")
            return parse_versions([item["name"] for item in data if isinstance(item, dict) and "name" in item])
        except Exception as e2:
            sys.stderr.write(f"ERROR: Failed to fetch unnerfcc catalogs: {e2}\n")
            return None

def main():
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

    gcv = common_versions[-1]
    print(f"RESULT: greatest common version = {gcv}")
    print(f"        install with: npm install -g @anthropic-ai/claude-code@{gcv}")
    return 0

if __name__ == "__main__":
    sys.exit(main())
