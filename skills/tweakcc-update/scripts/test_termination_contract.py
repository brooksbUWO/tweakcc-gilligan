"""Black-box termination-contract tests for the tweakcc-gilligan scripts.

These tests treat check_version_intersection.py, verify.py, and install.py as
opaque subprocesses. They assert ONLY the externally observable termination
contract (exit codes, stderr presence, --help text, and wall-clock kill time).
They never import or read the implementation source.

Contract pinned (from the hardening recipe):
  1. Kill path: watchdog probe with --max-seconds 1 exits code 3 in < 5 s.
  2. Range validation: --max-seconds 0 and --watchdog-probe -1 exit code 2 with
     non-empty stderr.
  3. --help mentions both --max-seconds and --watchdog-probe, exits 0.
  4. check_version_intersection.py at default flags exits 0 or 1 (never 2/3) and
     emits either a "RESULT: greatest common version =" line or an ERROR line.

Run:
  python -m unittest discover -s <this dir> -v
"""

import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

# Directory layout: this file lives in <repo>/tests, scripts live in <repo>/scripts.
_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_TESTS_DIR)
_SCRIPTS_DIR = os.path.join(_REPO_ROOT, "scripts")

CHECK_SCRIPT = os.path.join(_SCRIPTS_DIR, "check_version_intersection.py")
VERIFY_SCRIPT = os.path.join(_SCRIPTS_DIR, "verify.py")
INSTALL_SCRIPT = os.path.join(_SCRIPTS_DIR, "install.py")

# All three scripts, for the parametrized contract tests.
ALL_SCRIPTS = (CHECK_SCRIPT, VERIFY_SCRIPT, INSTALL_SCRIPT)

# Hard cap on every subprocess: a hang must surface as a test FAILURE (via
# TimeoutExpired), never as an indefinitely blocked run.
SUBPROCESS_TIMEOUT = 60

# Wall-clock budget the kill path must beat (contract item 1).
KILL_DEADLINE_SECONDS = 5.0


def _run(args, extra_env=None):
    """Invoke a script as a subprocess. Returns (returncode, stdout, stderr).

    Always passes timeout=SUBPROCESS_TIMEOUT so a hang raises TimeoutExpired,
    which unittest reports as an error/failure rather than blocking forever.
    """
    env = os.environ.copy()
    if extra_env:
        env.update(extra_env)
    cmd = [sys.executable] + args
    proc = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=SUBPROCESS_TIMEOUT,
        env=env,
        universal_newlines=True,  # text mode; py3 stdlib-portable spelling
    )
    return proc.returncode, proc.stdout, proc.stderr


def _install_env():
    """Fresh TWEAKCC_GILLIGAN_HOME temp dir so install.py never touches real state.

    Returns (env_dict, tempdir_path). Caller must remove the tempdir.
    """
    tmp = tempfile.mkdtemp(prefix="tweakcc_gilligan_test_")
    return {"TWEAKCC_GILLIGAN_HOME": tmp}, tmp


def _kill_args_for(script):
    """The lightest watchdog-probe invocation per script (contract item 1)."""
    if script == INSTALL_SCRIPT:
        return [script, "--clean-backup", "--watchdog-probe", "10",
                "--max-seconds", "1"]
    return [script, "--watchdog-probe", "10", "--max-seconds", "1"]


class TestScriptsExist(unittest.TestCase):
    """Guard: the target scripts must be present for the contract to be testable."""

    def test_scripts_present(self):
        for script in ALL_SCRIPTS:
            self.assertTrue(
                os.path.isfile(script),
                "target script missing: %s" % script,
            )


class TestKillPath(unittest.TestCase):
    """Contract 1: watchdog probe + --max-seconds 1 => exit 3 in < 5 s."""

    def _assert_kill(self, script):
        args = _kill_args_for(script)
        extra_env = None
        tmp = None
        if script == INSTALL_SCRIPT:
            extra_env, tmp = _install_env()
        try:
            start = time.monotonic()
            rc, out, err = _run(args, extra_env=extra_env)
            elapsed = time.monotonic() - start
        finally:
            if tmp is not None:
                shutil.rmtree(tmp, ignore_errors=True)

        self.assertEqual(
            rc, 3,
            "%s kill path expected exit 3, got %r\nSTDOUT:\n%s\nSTDERR:\n%s"
            % (os.path.basename(script), rc, out, err),
        )
        self.assertLess(
            elapsed, KILL_DEADLINE_SECONDS,
            "%s kill path took %.3f s, must be < %.1f s"
            % (os.path.basename(script), elapsed, KILL_DEADLINE_SECONDS),
        )

    def test_check_version_intersection_kill(self):
        self._assert_kill(CHECK_SCRIPT)

    def test_verify_kill(self):
        self._assert_kill(VERIFY_SCRIPT)

    def test_install_kill(self):
        self._assert_kill(INSTALL_SCRIPT)


class TestRangeValidation(unittest.TestCase):
    """Contract 2: --max-seconds 0 and --watchdog-probe -1 => exit 2 + stderr."""

    def _run_with_flags(self, script, flags):
        extra_env = None
        tmp = None
        if script == INSTALL_SCRIPT:
            extra_env, tmp = _install_env()
        try:
            return _run([script] + flags, extra_env=extra_env)
        finally:
            if tmp is not None:
                shutil.rmtree(tmp, ignore_errors=True)

    def _assert_reject(self, script, flags):
        rc, out, err = self._run_with_flags(script, flags)
        self.assertEqual(
            rc, 2,
            "%s with %r expected exit 2, got %r\nSTDOUT:\n%s\nSTDERR:\n%s"
            % (os.path.basename(script), flags, rc, out, err),
        )
        self.assertTrue(
            err.strip(),
            "%s with %r expected non-empty stderr, got empty"
            % (os.path.basename(script), flags),
        )

    # --max-seconds 0 rejected by all three.
    def test_check_max_seconds_zero(self):
        self._assert_reject(CHECK_SCRIPT, ["--max-seconds", "0"])

    def test_verify_max_seconds_zero(self):
        self._assert_reject(VERIFY_SCRIPT, ["--max-seconds", "0"])

    def test_install_max_seconds_zero(self):
        self._assert_reject(INSTALL_SCRIPT, ["--max-seconds", "0"])

    # Negative --watchdog-probe rejected by all three.
    def test_check_negative_watchdog_probe(self):
        self._assert_reject(CHECK_SCRIPT, ["--watchdog-probe", "-1"])

    def test_verify_negative_watchdog_probe(self):
        self._assert_reject(VERIFY_SCRIPT, ["--watchdog-probe", "-1"])

    def test_install_negative_watchdog_probe(self):
        self._assert_reject(INSTALL_SCRIPT, ["--watchdog-probe", "-1"])


class TestHelp(unittest.TestCase):
    """Contract 3: --help exits 0 and mentions both flags, all three scripts."""

    def _assert_help(self, script):
        rc, out, err = _run([script, "--help"])
        self.assertEqual(
            rc, 0,
            "%s --help expected exit 0, got %r\nSTDERR:\n%s"
            % (os.path.basename(script), rc, err),
        )
        combined = out + err
        self.assertIn(
            "--max-seconds", combined,
            "%s --help must mention --max-seconds\nOUTPUT:\n%s"
            % (os.path.basename(script), combined),
        )
        self.assertIn(
            "--watchdog-probe", combined,
            "%s --help must mention --watchdog-probe\nOUTPUT:\n%s"
            % (os.path.basename(script), combined),
        )

    def test_check_help(self):
        self._assert_help(CHECK_SCRIPT)

    def test_verify_help(self):
        self._assert_help(VERIFY_SCRIPT)

    def test_install_help(self):
        self._assert_help(INSTALL_SCRIPT)


class TestNormalBehaviorUntouched(unittest.TestCase):
    """Contract 4: check_version_intersection.py at defaults exits 0 or 1 and
    emits either a RESULT line (success) or an ERROR line (failure).

    Tolerant of both outcomes: this may run with or without local catalog
    clones and network, so a clean failure (exit 1 + ERROR) is contract-valid.
    """

    def test_default_run(self):
        rc, out, err = _run([CHECK_SCRIPT])
        self.assertIn(
            rc, (0, 1),
            "default run expected exit 0 or 1, got %r\nSTDOUT:\n%s\nSTDERR:\n%s"
            % (rc, out, err),
        )
        if rc == 0:
            self.assertIn(
                "RESULT: greatest common version", out,
                "exit 0 must print the RESULT line\nSTDOUT:\n%s" % out,
            )
            self.assertIn(
                "npm install -g @anthropic-ai/claude-code@", out,
                "exit 0 must end with the paste-ready install command\nSTDOUT:\n%s" % out,
            )
        else:  # rc == 1
            self.assertIn(
                "ERROR", err,
                "exit 1 must print an ERROR line on stderr\nSTDERR:\n%s" % err,
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
