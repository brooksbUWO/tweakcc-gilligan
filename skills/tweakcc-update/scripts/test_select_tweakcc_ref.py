#!/usr/bin/env python3
"""Tests for install.select_tweakcc_ref: the tweakcc-fixed checkout is DERIVED
from the target Claude Code version, never a hard-coded commit.

Rule A: the newest release tag whose newest data/prompts catalog IS the target
(that release catalogued the target binary, so its extractor parsed that binary
format). Fallback: the last commit on origin/main that touched the target's
catalog file. No catalog at all: ValueError.

The fixture is a bare "origin" plus a clone, so origin/main exists exactly as
it does in the runtime clone. stdlib unittest only.
"""

import pathlib
import re
import subprocess
import sys
import tempfile
import unittest

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import install  # noqa: E402  (import is side-effect free: constants only)


def git(repo, *args):
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True,
                                   stderr=subprocess.STDOUT).strip()


def add_catalog(repo, version, msg=None, tag=None):
    d = repo / "data" / "prompts"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"prompts-{version}.json").write_text(f'{{"msg": "{msg}"}}', encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", msg or f"prompts: catalogue CC {version}")
    if tag:
        git(repo, "tag", tag)
    return git(repo, "rev-parse", "HEAD")


class SelectTweakccRef(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = pathlib.Path(cls.tmp.name)
        origin = root / "origin.git"
        subprocess.check_output(["git", "init", "-q", "--bare", "-b", "main", str(origin)])
        work = root / "work"
        subprocess.check_output(["git", "clone", "-q", str(origin), str(work)], stderr=subprocess.STDOUT)
        git(work, "config", "user.email", "t@example.invalid")
        git(work, "config", "user.name", "t")
        git(work, "checkout", "-q", "-b", "main")
        cls.sha = {}
        cls.sha["2.1.230"] = add_catalog(work, "2.1.230")
        cls.sha["2.1.235"] = add_catalog(work, "2.1.235", tag="v2.7.32")
        cls.sha["2.1.241"] = add_catalog(work, "2.1.241", tag="v2.7.38")
        cls.sha["2.1.246"] = add_catalog(work, "2.1.246", tag="v2.8.0")
        # Re-touch of an OLD catalog inside the new era; the tag rule must still
        # pick the release whose newest catalog is the target, not this commit.
        cls.sha["retouch-235"] = add_catalog(work, "2.1.235", msg="prompts: re-anchor 2.1.235")
        cls.sha["2.1.250"] = add_catalog(work, "2.1.250")  # catalogued, never released
        git(work, "push", "-q", "origin", "main", "--tags")
        cls.clone = root / "clone"
        subprocess.check_output(["git", "clone", "-q", str(origin), str(cls.clone)], stderr=subprocess.STDOUT)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_release_whose_newest_catalog_is_target(self):
        self.assertEqual(install.select_tweakcc_ref(self.clone, "2.1.235"), ("v2.7.32", self.sha["2.1.235"]))
        self.assertEqual(install.select_tweakcc_ref(self.clone, "2.1.241"), ("v2.7.38", self.sha["2.1.241"]))
        self.assertEqual(install.select_tweakcc_ref(self.clone, "2.1.246"), ("v2.8.0", self.sha["2.1.246"]))

    def test_fallback_last_commit_touching_catalog(self):
        label, sha = install.select_tweakcc_ref(self.clone, "2.1.250")
        self.assertEqual(sha, self.sha["2.1.250"])
        self.assertIn("2.1.250", label)

    def test_never_catalogued_target_raises(self):
        with self.assertRaises(ValueError):
            install.select_tweakcc_ref(self.clone, "2.1.999")

    def test_no_hard_coded_pin_in_source(self):
        src = (HERE / "install.py").read_text(encoding="utf-8")
        self.assertNotIn("pin_ref", src)
        self.assertIsNone(re.search(r"\b(452f15a|2dc353c)\b", src))


if __name__ == "__main__":
    unittest.main()
