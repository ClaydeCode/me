import pytest
from clayde.freeshard.phase import Phase, derive_phase

def test_no_pr_means_implement():
    assert derive_phase(pr_open=False, ci_status=None, max_is_reviewer=False, fix_attempts=0) == Phase.IMPLEMENT

@pytest.mark.parametrize("ci", [None, "pending", "queued", "in_progress"])
def test_pr_open_ci_pending_means_wait(ci):
    assert derive_phase(pr_open=True, ci_status=ci, max_is_reviewer=False, fix_attempts=0) == Phase.CI_WAIT

@pytest.mark.parametrize("ci", ["failure", "error", "cancelled", "timed_out"])
def test_pr_open_ci_failed_under_cap_means_fix(ci):
    assert derive_phase(pr_open=True, ci_status=ci, max_is_reviewer=False, fix_attempts=1) == Phase.CI_FIX

def test_ci_failed_at_cap_means_manual_verify():
    assert derive_phase(pr_open=True, ci_status="failure", max_is_reviewer=False, fix_attempts=2) == Phase.MANUAL_VERIFY

def test_ci_green_no_reviewer_means_handoff():
    assert derive_phase(pr_open=True, ci_status="success", max_is_reviewer=False, fix_attempts=0) == Phase.HANDOFF

def test_ci_green_with_reviewer_means_awaiting_merge():
    assert derive_phase(pr_open=True, ci_status="success", max_is_reviewer=True, fix_attempts=0) == Phase.AWAITING_MERGE
