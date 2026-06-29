from unittest.mock import MagicMock

from clayde.github import get_ci_status, is_reviewer_assigned, count_fix_commits


def test_get_ci_status_maps_combined_state():
    g = MagicMock()
    commit = g.get_repo.return_value.get_commit.return_value
    commit.get_combined_status.return_value.state = "success"
    commit.get_combined_status.return_value.total_count = 1
    commit.get_check_runs.return_value = []
    assert get_ci_status(g, "o", "r", "abc") == "success"


def test_is_reviewer_assigned_true_when_login_present():
    g = MagicMock()
    pr = g.get_repo.return_value.get_pull.return_value
    rev = MagicMock(); rev.login = "maxtepkasper"
    pr.get_review_requests.return_value = ([rev], [])
    assert is_reviewer_assigned(g, "o", "r", 5, "maxtepkasper")


def test_count_fix_commits_counts_branch_unique_only():
    g = MagicMock()
    c1 = MagicMock(); c1.commit.message = "fix(ci): retry"
    c2 = MagicMock(); c2.commit.message = "feat: thing"
    g.get_repo.return_value.compare.return_value.commits = [c1, c2]
    assert count_fix_commits(g, "o", "r", "clayde/issue-1", "main") == 1
    g.get_repo.return_value.compare.assert_called_once_with("main", "clayde/issue-1")


def test_count_fix_commits_excludes_base_history():
    """A fix(ci): commit reachable only via base branch must not be counted."""
    g = MagicMock()
    branch_commit = MagicMock(); branch_commit.commit.message = "feat: new thing"
    # compare() returns only the branch-unique commit — base history not included
    g.get_repo.return_value.compare.return_value.commits = [branch_commit]
    assert count_fix_commits(g, "o", "r", "clayde/issue-1", "main") == 0


def test_get_ci_status_failure_dominates_success():
    """Check-run conclusion 'failure' dominates combined status 'success'."""
    g = MagicMock()
    commit = g.get_repo.return_value.get_commit.return_value
    commit.get_combined_status.return_value.state = "success"
    commit.get_combined_status.return_value.total_count = 1
    run = MagicMock()
    run.status = "completed"
    run.conclusion = "failure"
    commit.get_check_runs.return_value = [run]
    assert get_ci_status(g, "o", "r", "abc") == "failure"


def test_get_ci_status_error_normalized_to_failure():
    """Combined status 'error' is normalized to failure."""
    g = MagicMock()
    commit = g.get_repo.return_value.get_commit.return_value
    commit.get_combined_status.return_value.state = "error"
    commit.get_combined_status.return_value.total_count = 1
    commit.get_check_runs.return_value = []
    assert get_ci_status(g, "o", "r", "abc") == "failure"


def test_get_ci_status_pending_when_incomplete_check_run():
    """Pending returned when a check-run is not completed."""
    g = MagicMock()
    commit = g.get_repo.return_value.get_commit.return_value
    commit.get_combined_status.return_value.state = "success"
    commit.get_combined_status.return_value.total_count = 1
    run = MagicMock()
    run.status = "in_progress"
    run.conclusion = None
    commit.get_check_runs.return_value = [run]
    assert get_ci_status(g, "o", "r", "abc") == "pending"


def test_is_reviewer_assigned_true_via_submitted_review():
    """True when reviewer has submitted a review (not just requested)."""
    g = MagicMock()
    pr = g.get_repo.return_value.get_pull.return_value
    pr.get_review_requests.return_value = ([], [])
    rev = MagicMock()
    rev.user = MagicMock()
    rev.user.login = "maxtepkasper"
    pr.get_reviews.return_value = [rev]
    assert is_reviewer_assigned(g, "o", "r", 5, "maxtepkasper")


def test_is_reviewer_assigned_false_neither_request_nor_review():
    """False when login is not in pending requests or submitted reviews."""
    g = MagicMock()
    pr = g.get_repo.return_value.get_pull.return_value
    rev_req = MagicMock()
    rev_req.login = "otheruser"
    pr.get_review_requests.return_value = ([rev_req], [])
    rev = MagicMock()
    rev.user = MagicMock()
    rev.user.login = "another"
    pr.get_reviews.return_value = [rev]
    assert not is_reviewer_assigned(g, "o", "r", 5, "maxtepkasper")


def test_get_ci_status_ignores_empty_combined_status():
    """A repo with zero legacy commit-statuses (total_count=0) reports combined
    state 'pending' by default; green check-runs must NOT be poisoned to pending."""
    from unittest.mock import MagicMock
    from clayde.github import get_ci_status
    g = MagicMock()
    commit = g.get_repo.return_value.get_commit.return_value
    cs = commit.get_combined_status.return_value
    cs.total_count = 0
    cs.state = "pending"
    build = MagicMock(); build.status = "completed"; build.conclusion = "success"
    deploy = MagicMock(); deploy.status = "completed"; deploy.conclusion = "skipped"
    commit.get_check_runs.return_value = [build, deploy]
    assert get_ci_status(g, "o", "r", "sha") == "success"


def test_get_ci_status_respects_real_combined_pending():
    """When commit-statuses DO exist and are pending, still report pending."""
    from unittest.mock import MagicMock
    from clayde.github import get_ci_status
    g = MagicMock()
    commit = g.get_repo.return_value.get_commit.return_value
    cs = commit.get_combined_status.return_value
    cs.total_count = 2
    cs.state = "pending"
    commit.get_check_runs.return_value = []
    assert get_ci_status(g, "o", "r", "sha") == "pending"
