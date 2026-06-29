from unittest.mock import MagicMock

from clayde.github import get_ci_status, is_reviewer_assigned, count_fix_commits


def test_get_ci_status_maps_combined_state():
    g = MagicMock()
    commit = g.get_repo.return_value.get_commit.return_value
    commit.get_combined_status.return_value.state = "success"
    commit.get_check_runs.return_value = []
    assert get_ci_status(g, "o", "r", "abc") == "success"


def test_is_reviewer_assigned_true_when_login_present():
    g = MagicMock()
    pr = g.get_repo.return_value.get_pull.return_value
    rev = MagicMock(); rev.login = "maxtepkasper"
    pr.get_review_requests.return_value = ([rev], [])
    assert is_reviewer_assigned(g, "o", "r", 5, "maxtepkasper")


def test_count_fix_commits_counts_prefix():
    g = MagicMock()
    c1 = MagicMock(); c1.commit.message = "fix(ci): retry"
    c2 = MagicMock(); c2.commit.message = "feat: thing"
    g.get_repo.return_value.get_commits.return_value = [c1, c2]
    assert count_fix_commits(g, "o", "r", "clayde/issue-1") == 1
