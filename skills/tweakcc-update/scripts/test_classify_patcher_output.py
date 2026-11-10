"""classify_patcher_output: the unnerfcc summary counter is not a skipped item."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from install import classify_patcher_output  # noqa: E402

SUMMARY_OK = "    patched=239 runs=291 unchanged=4870 couldNotFind=0 skipped=0 lost=0 dupSites=1 residual=0"
SUMMARY_SKIP = "    patched=239 runs=291 unchanged=4870 couldNotFind=0 skipped=2 lost=0 dupSites=1 residual=0"


def test_zero_skipped_counter_is_not_a_skip():
    bad, skipped, not_found = classify_patcher_output("unnerfcc", SUMMARY_OK)
    assert bad == [] and skipped == [] and not_found == 0


def test_nonzero_skipped_counter_is_surfaced():
    _bad, skipped, _nf = classify_patcher_output("unnerfcc", SUMMARY_SKIP)
    assert len(skipped) == 1 and "skipped=2" in skipped[0]


def test_lost_marker_is_a_failure():
    bad, _s, _nf = classify_patcher_output(
        "unnerfcc", "[LOST] system-reminder-task-tools-reminder: couldNotFind\n" + SUMMARY_OK)
    assert len(bad) == 1 and "[LOST]" in bad[0]


def test_already_unnerfed_is_not_a_skip():
    _bad, skipped, _nf = classify_patcher_output("unnerfcc", "Rules skipped   : 265  (already un-nerfed; idempotent)")
    assert skipped == []


# --- tweakcc-fixed: the benign "missing system prompts" error must not be fatal.
# tweakcc-fixed prints "✖ Error downloading system prompts:" when its release
# ships no stock prompts for the target CC version, but treats it as NON-FATAL
# (skips the system-prompt customizations, then exits 0). This project applies the
# system-prompt un-nerfs through unnerfcc, not tweakcc-fixed, so that line must be
# classified as a skip (WARN), not a per-item failure. A real tweakcc-fixed error
# must still be fatal (2026-11-10: the benign line aborted a 2.1.321 apply before
# the unnerfcc layer ran, leaving the binary un-nerf-free).

TWEAKCC_MISSING_PROMPTS = "✖ Error downloading system prompts:"
TWEAKCC_REAL_ERROR = "✖ Error applying inline blob to cli.js"


def test_tweakcc_missing_system_prompts_is_not_fatal():
    bad, skipped, _nf = classify_patcher_output("tweakcc-fixed", TWEAKCC_MISSING_PROMPTS)
    assert bad == []
    assert any("downloading system prompts" in s for s in skipped)


def test_tweakcc_skipping_line_is_surfaced_as_skip():
    line = "⚠ System prompts not available - skipping system prompt customizations"
    bad, skipped, _nf = classify_patcher_output("tweakcc-fixed", line)
    assert bad == []
    assert len(skipped) == 1


def test_tweakcc_real_error_is_still_fatal():
    bad, _skipped, _nf = classify_patcher_output("tweakcc-fixed", TWEAKCC_REAL_ERROR)
    assert len(bad) == 1
    assert "applying inline blob" in bad[0]


def test_tweakcc_error_colon_line_is_still_fatal():
    """A column-0 'Error: ...' line (no cross glyph) stays fatal; the
    missing-prompts exemption is scoped only to the cross-glyph variant."""
    bad, _skipped, _nf = classify_patcher_output("tweakcc-fixed", "Error: failed to read cli.js")
    assert len(bad) == 1
