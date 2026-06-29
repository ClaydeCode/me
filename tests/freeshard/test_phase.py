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

@pytest.mark.parametrize("ci", ["waiting", "requested"])
def test_extra_pending_strings_wait(ci):
    assert derive_phase(pr_open=True, ci_status=ci, max_is_reviewer=False, fix_attempts=0) == Phase.CI_WAIT

@pytest.mark.parametrize("ci", ["action_required", "stale"])
def test_extra_failed_strings_fix(ci):
    assert derive_phase(pr_open=True, ci_status=ci, max_is_reviewer=False, fix_attempts=0) == Phase.CI_FIX

def test_unknown_status_is_safe_wait_not_green():
    assert derive_phase(pr_open=True, ci_status="bogus", max_is_reviewer=True, fix_attempts=0) == Phase.CI_WAIT

def test_fix_attempts_above_cap_means_manual_verify():
    assert derive_phase(pr_open=True, ci_status="failure", max_is_reviewer=False, fix_attempts=5) == Phase.MANUAL_VERIFY

def test_no_pr_dominates_regardless_of_other_inputs():
    assert derive_phase(pr_open=False, ci_status="failure", max_is_reviewer=True, fix_attempts=9) == Phase.IMPLEMENT

@pytest.mark.parametrize("ci", ["neutral", "skipped"])
def test_success_variants_handoff_without_reviewer(ci):
    assert derive_phase(pr_open=True, ci_status=ci, max_is_reviewer=False, fix_attempts=0) == Phase.HANDOFF

# ---------------------------------------------------------------------------
# Fix #1: ci_required=False skips all CI states and goes straight to handoff
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("ci", [None, "pending", "failure", "success", "bogus"])
def test_no_ci_required_pr_open_no_reviewer_means_handoff(ci):
    assert derive_phase(
        pr_open=True, ci_status=ci, max_is_reviewer=False, fix_attempts=0,
        ci_required=False,
    ) == Phase.HANDOFF

@pytest.mark.parametrize("ci", [None, "pending", "failure", "success"])
def test_no_ci_required_pr_open_with_reviewer_means_awaiting_merge(ci):
    assert derive_phase(
        pr_open=True, ci_status=ci, max_is_reviewer=True, fix_attempts=0,
        ci_required=False,
    ) == Phase.AWAITING_MERGE

def test_no_ci_required_no_pr_still_implement():
    assert derive_phase(
        pr_open=False, ci_status=None, max_is_reviewer=False, fix_attempts=0,
        ci_required=False,
    ) == Phase.IMPLEMENT


# ---------------------------------------------------------------------------
# Attempt cap: ESCALATE when branch-commit count hits the cap
# ---------------------------------------------------------------------------

def test_no_pr_attempts_at_cap_means_escalate():
    assert derive_phase(
        pr_open=False, ci_status=None, max_is_reviewer=False, fix_attempts=0,
        attempts=20, attempt_cap=20,
    ) == Phase.ESCALATE


def test_no_pr_attempts_below_cap_means_implement():
    assert derive_phase(
        pr_open=False, ci_status=None, max_is_reviewer=False, fix_attempts=0,
        attempts=19, attempt_cap=20,
    ) == Phase.IMPLEMENT


def test_pr_ci_failed_attempts_at_cap_means_manual_verify():
    """Backstop: attempt cap overrides fix_cap for CI_FIX → MANUAL_VERIFY."""
    assert derive_phase(
        pr_open=True, ci_status="failure", max_is_reviewer=False,
        fix_attempts=0, fix_cap=2,
        attempts=20, attempt_cap=20,
    ) == Phase.MANUAL_VERIFY


def test_ci_success_at_cap_still_handoff():
    """Cap must not override terminal/waiting phases — CI green → HANDOFF regardless."""
    assert derive_phase(
        pr_open=True, ci_status="success", max_is_reviewer=False,
        fix_attempts=0, attempts=20, attempt_cap=20,
    ) == Phase.HANDOFF


def test_ci_success_at_cap_with_reviewer_still_awaiting_merge():
    assert derive_phase(
        pr_open=True, ci_status="success", max_is_reviewer=True,
        fix_attempts=0, attempts=20, attempt_cap=20,
    ) == Phase.AWAITING_MERGE


def test_ci_pending_at_cap_still_ci_wait():
    """attempts cap must NOT override a pending CI — still wait."""
    assert derive_phase(pr_open=True, ci_status="queued", max_is_reviewer=False,
                        fix_attempts=0, attempts=20, attempt_cap=20) == Phase.CI_WAIT


def test_ci_not_required_at_cap_still_handoff():
    """attempts cap must NOT override the no-CI early handoff."""
    assert derive_phase(pr_open=True, ci_status=None, max_is_reviewer=False,
                        fix_attempts=0, ci_required=False,
                        attempts=20, attempt_cap=20) == Phase.HANDOFF
