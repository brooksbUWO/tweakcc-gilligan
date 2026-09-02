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
