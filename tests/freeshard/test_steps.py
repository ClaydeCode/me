"""Tests for freeshard phase handlers (steps.py)."""
from pathlib import Path
from unittest.mock import MagicMock, patch

from clayde.freeshard import steps


def _make_issue(title="Do a thing", body="issue body"):
    issue = MagicMock()
    issue.title = title
    issue.body = body
    return issue


def _make_comment(login, body):
    c = MagicMock()
    c.user.login = login
    c.body = body
    return c


# ---------------------------------------------------------------------------
# run_implement
# ---------------------------------------------------------------------------

@patch("clayde.freeshard.steps.local_verify", return_value=(True, ""))
@patch("clayde.freeshard.steps.invoke_claude")
@patch("clayde.freeshard.steps.add_worktree")
@patch("clayde.freeshard.steps.create_pull_request", return_value="https://x/pull/9")
@patch("clayde.freeshard.steps.find_open_pr", return_value=None)
@patch("clayde.freeshard.steps.fetch_issue_comments", return_value=[])
@patch("clayde.freeshard.steps.fetch_issue")
@patch("clayde.freeshard.steps._push_branch")
def test_implement_opens_pr_when_verify_green(
    mock_push, mock_fi, mock_fic, mock_find, mock_pr, mock_wt, mock_claude, mock_v,
):
    mock_claude.return_value = MagicMock(output='{"summary":"did it"}', cost_eur=0.0)
    mock_fi.return_value = _make_issue()
    mock_wt.return_value = Path("/tmp/wt")
    steps.run_implement(MagicMock(), "o", "r", 7, "main")
    mock_pr.assert_called_once()
    mock_push.assert_called_once()


@patch("clayde.freeshard.steps.local_verify", return_value=(False, "tests failed"))
@patch("clayde.freeshard.steps.invoke_claude")
@patch("clayde.freeshard.steps.add_worktree")
@patch("clayde.freeshard.steps.create_pull_request")
@patch("clayde.freeshard.steps.find_open_pr", return_value=None)
@patch("clayde.freeshard.steps.fetch_issue_comments", return_value=[])
@patch("clayde.freeshard.steps.fetch_issue")
@patch("clayde.freeshard.steps._push_branch")
def test_implement_no_pr_when_verify_red(
    mock_push, mock_fi, mock_fic, mock_find, mock_pr, mock_wt, mock_claude, mock_v,
):
    mock_claude.return_value = MagicMock(output='{"summary":"x"}', cost_eur=0.0)
    mock_fi.return_value = _make_issue()
    mock_wt.return_value = Path("/tmp/wt")
    steps.run_implement(MagicMock(), "o", "r", 7, "main")
    mock_pr.assert_not_called()
    mock_push.assert_not_called()


@patch("clayde.freeshard.steps.local_verify", return_value=(True, ""))
@patch("clayde.freeshard.steps.invoke_claude")
@patch("clayde.freeshard.steps.add_worktree")
@patch("clayde.freeshard.steps.create_pull_request", return_value="https://x/pull/9")
@patch("clayde.freeshard.steps.find_open_pr", return_value=None)
@patch("clayde.freeshard.steps.fetch_issue_comments", return_value=[])
@patch("clayde.freeshard.steps.fetch_issue")
@patch("clayde.freeshard.steps._push_branch")
def test_implement_pr_body_includes_closes(
    mock_push, mock_fi, mock_fic, mock_find, mock_pr, mock_wt, mock_claude, mock_v,
):
    mock_claude.return_value = MagicMock(output='{"summary":"added feature"}', cost_eur=0.0)
    mock_fi.return_value = _make_issue(title="My feature")
    mock_wt.return_value = Path("/tmp/wt")
    steps.run_implement(MagicMock(), "o", "r", 42, "main")
    _, kwargs = mock_pr.call_args
    assert "Closes #42" in kwargs["body"]


@patch("clayde.freeshard.steps.local_verify", return_value=(True, ""))
@patch("clayde.freeshard.steps.invoke_claude")
@patch("clayde.freeshard.steps.add_worktree")
@patch("clayde.freeshard.steps.create_pull_request", return_value="https://x/pull/9")
@patch("clayde.freeshard.steps.find_open_pr", return_value=None)
@patch("clayde.freeshard.steps.fetch_issue_comments", return_value=[])
@patch("clayde.freeshard.steps.fetch_issue")
@patch("clayde.freeshard.steps._push_branch")
def test_implement_pr_body_includes_summary_when_parseable(
    mock_push, mock_fi, mock_fic, mock_find, mock_pr, mock_wt, mock_claude, mock_v,
):
    mock_claude.return_value = MagicMock(output='{"summary":"did the thing"}', cost_eur=0.0)
    mock_fi.return_value = _make_issue()
    mock_wt.return_value = Path("/tmp/wt")
    steps.run_implement(MagicMock(), "o", "r", 7, "main")
    _, kwargs = mock_pr.call_args
    assert "did the thing" in kwargs["body"]


@patch("clayde.freeshard.steps.local_verify", return_value=(True, ""))
@patch("clayde.freeshard.steps.invoke_claude")
@patch("clayde.freeshard.steps.add_worktree")
@patch("clayde.freeshard.steps.create_pull_request", return_value="https://x/pull/9")
@patch("clayde.freeshard.steps.find_open_pr", return_value=None)
@patch("clayde.freeshard.steps.fetch_issue_comments", return_value=[])
@patch("clayde.freeshard.steps.fetch_issue")
@patch("clayde.freeshard.steps._push_branch")
def test_implement_pr_body_no_summary_when_unparseable(
    mock_push, mock_fi, mock_fic, mock_find, mock_pr, mock_wt, mock_claude, mock_v,
):
    mock_claude.return_value = MagicMock(output="not json at all", cost_eur=0.0)
    mock_fi.return_value = _make_issue()
    mock_wt.return_value = Path("/tmp/wt")
    steps.run_implement(MagicMock(), "o", "r", 7, "main")
    mock_pr.assert_called_once()
    _, kwargs = mock_pr.call_args
    assert kwargs["body"] == "Closes #7"


# ---------------------------------------------------------------------------
# run_ci_fix
# ---------------------------------------------------------------------------

@patch("clayde.freeshard.steps._push_branch")
@patch("clayde.freeshard.steps.invoke_claude")
@patch("clayde.freeshard.steps.add_worktree")
def test_ci_fix_invokes_claude_and_pushes(mock_wt, mock_claude, mock_push):
    mock_claude.return_value = MagicMock(output='{"summary":"fixed ci"}', cost_eur=0.0)
    mock_wt.return_value = Path("/tmp/wt")
    steps.run_ci_fix(MagicMock(), "o", "r", 7, "main", "ERROR: test failed\n")
    mock_claude.assert_called_once()
    mock_push.assert_called_once()


@patch("clayde.freeshard.steps._push_branch")
@patch("clayde.freeshard.steps.invoke_claude")
@patch("clayde.freeshard.steps.add_worktree")
def test_ci_fix_passes_ci_log_in_prompt(mock_wt, mock_claude, mock_push):
    mock_claude.return_value = MagicMock(output="{}", cost_eur=0.0)
    mock_wt.return_value = Path("/tmp/wt")
    ci_log = "npm ERR! Test suite failed\n  at expect"
    steps.run_ci_fix(MagicMock(), "o", "r", 7, "main", ci_log)
    prompt_arg = mock_claude.call_args[0][0]
    assert ci_log in prompt_arg


# ---------------------------------------------------------------------------
# run_handoff
# ---------------------------------------------------------------------------

def test_handoff_assigns_reviewer_and_removes_worktree():
    with patch("clayde.freeshard.steps.add_pr_reviewer") as mr, \
         patch("clayde.freeshard.steps.post_comment"), \
         patch("clayde.freeshard.steps.remove_worktree") as rw:
        steps.run_handoff(MagicMock(), "o", "r", 7, 9, "maxtepkasper")
        mr.assert_called_once()
        rw.assert_called_once()


def test_handoff_posts_ready_comment():
    with patch("clayde.freeshard.steps.add_pr_reviewer"), \
         patch("clayde.freeshard.steps.post_comment") as pc, \
         patch("clayde.freeshard.steps.remove_worktree"):
        steps.run_handoff(MagicMock(), "o", "r", 7, 9, "reviewer")
        pc.assert_called_once()
        comment_body = pc.call_args[0][4]
        assert "ready for review" in comment_body


def test_handoff_reviewer_arg_forwarded():
    with patch("clayde.freeshard.steps.add_pr_reviewer") as mr, \
         patch("clayde.freeshard.steps.post_comment"), \
         patch("clayde.freeshard.steps.remove_worktree"):
        g = MagicMock()
        steps.run_handoff(g, "o", "r", 7, 9, "alice")
        mr.assert_called_once_with(g, "o", "r", 9, "alice")


# ---------------------------------------------------------------------------
# run_manual_verify
# ---------------------------------------------------------------------------

@patch("clayde.freeshard.steps.remove_worktree")
@patch("clayde.freeshard.steps.post_comment")
@patch("clayde.freeshard.steps.add_pr_reviewer")
def test_manual_verify_assigns_reviewer_and_comments_and_removes_worktree(
    mock_apr, mock_pc, mock_rw,
):
    g = MagicMock()
    steps.run_manual_verify(g, "o", "r", 7, 9, "reviewer")
    mock_apr.assert_called_once()
    mock_pc.assert_called_once()
    mock_rw.assert_called_once()


@patch("clayde.freeshard.steps.remove_worktree")
@patch("clayde.freeshard.steps.post_comment")
@patch("clayde.freeshard.steps.add_pr_reviewer")
def test_manual_verify_adds_label(mock_apr, mock_pc, mock_rw):
    g = MagicMock()
    issue_obj = MagicMock()
    g.get_repo.return_value.get_issue.return_value = issue_obj
    steps.run_manual_verify(g, "o", "r", 7, 9, "reviewer")
    issue_obj.add_to_labels.assert_called_once_with("manual-verify-required")


@patch("clayde.freeshard.steps.remove_worktree")
@patch("clayde.freeshard.steps.post_comment")
@patch("clayde.freeshard.steps.add_pr_reviewer")
def test_manual_verify_comment_mentions_hands(mock_apr, mock_pc, mock_rw):
    g = MagicMock()
    steps.run_manual_verify(g, "o", "r", 7, 9, "reviewer")
    comment_body = mock_pc.call_args[0][4]
    assert "needs your hands" in comment_body
