"""Tests for the stateless dispatch loop (tick)."""
from unittest.mock import MagicMock, patch, call

import pytest

from clayde.freeshard import loop
from clayde.freeshard.phase import Phase


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _issue(url, number=7):
    i = MagicMock()
    i.html_url = url
    i.number = number
    return i


def _settings(reviewer="max"):
    s = MagicMock()
    s.fs_reviewer = reviewer
    return s


APP_ISSUE_URL = "https://github.com/FreeshardBase/app-repository/issues/7"
CORE_ISSUE_URL = "https://github.com/FreeshardBase/freeshard/issues/7"
APP_PR_URL = "https://github.com/FreeshardBase/app-repository/pull/42"


# ---------------------------------------------------------------------------
# From the brief: two canonical routing tests
# ---------------------------------------------------------------------------

@patch("clayde.freeshard.loop.run_implement")
@patch("clayde.freeshard.loop.derive_phase", return_value=Phase.IMPLEMENT)
@patch("clayde.freeshard.loop.is_blocked", return_value=False)
@patch("clayde.freeshard.loop.find_open_pr", return_value=None)
@patch("clayde.freeshard.loop.get_default_branch", return_value="main")
@patch("clayde.freeshard.loop.get_assigned_issues")
def test_tick_routes_implement(mock_assigned, _gdb, _fop, _ib, _dp, mock_impl):
    mock_assigned.return_value = [_issue(APP_ISSUE_URL)]
    g = MagicMock()
    n = loop.tick(g, _settings())
    assert n == 1
    mock_impl.assert_called_once_with(g, "FreeshardBase", "app-repository", 7, "main")


@patch("clayde.freeshard.loop.run_implement")
@patch("clayde.freeshard.loop.get_assigned_issues")
def test_tick_skips_core_repo(mock_assigned, mock_impl):
    mock_assigned.return_value = [_issue(CORE_ISSUE_URL)]
    n = loop.tick(MagicMock(), _settings())
    mock_impl.assert_not_called()
    assert n == 0


# ---------------------------------------------------------------------------
# Phase routing: each phase dispatches to the right handler (or no-op)
# ---------------------------------------------------------------------------

def _patched_tick(phase, *, pr_url=None):
    """Run tick with a single non-core issue fixed at `phase`."""
    issue = _issue(APP_ISSUE_URL)
    g = MagicMock()
    # If PR is open, make get_pull().head.sha return something
    if pr_url:
        g.get_repo.return_value.get_pull.return_value.head.sha = "abc123"

    patches = {
        "clayde.freeshard.loop.get_assigned_issues": MagicMock(return_value=[issue]),
        "clayde.freeshard.loop.is_blocked": MagicMock(return_value=False),
        "clayde.freeshard.loop.find_open_pr": MagicMock(return_value=pr_url),
        "clayde.freeshard.loop.get_default_branch": MagicMock(return_value="main"),
        "clayde.freeshard.loop.get_ci_status": MagicMock(return_value="failure"),
        "clayde.freeshard.loop.is_reviewer_assigned": MagicMock(return_value=False),
        "clayde.freeshard.loop.count_fix_commits": MagicMock(return_value=0),
        "clayde.freeshard.loop.derive_phase": MagicMock(return_value=phase),
        "clayde.freeshard.loop.run_implement": MagicMock(),
        "clayde.freeshard.loop.run_ci_fix": MagicMock(),
        "clayde.freeshard.loop.run_handoff": MagicMock(),
        "clayde.freeshard.loop.run_manual_verify": MagicMock(),
        "clayde.freeshard.loop._ci_failure_summary": MagicMock(return_value="checks failed"),
    }

    with patch.multiple("clayde.freeshard.loop", **{k.split(".")[-1]: v for k, v in patches.items()}):
        n = loop.tick(g, _settings())

    return n, patches, g


@pytest.mark.parametrize("phase,handler_key,pr_url,call_args", [
    (Phase.IMPLEMENT, "clayde.freeshard.loop.run_implement", None,
     ("FreeshardBase", "app-repository", 7, "main")),
    (Phase.CI_FIX, "clayde.freeshard.loop.run_ci_fix", APP_PR_URL,
     ("FreeshardBase", "app-repository", 7, "main", "checks failed")),
    (Phase.HANDOFF, "clayde.freeshard.loop.run_handoff", APP_PR_URL,
     ("FreeshardBase", "app-repository", 7, 42, "max")),
    (Phase.MANUAL_VERIFY, "clayde.freeshard.loop.run_manual_verify", APP_PR_URL,
     ("FreeshardBase", "app-repository", 7, 42, "max")),
])
def test_phase_routes_to_correct_handler(phase, handler_key, pr_url, call_args):
    n, patches, g = _patched_tick(phase, pr_url=pr_url)
    assert n == 1
    patches[handler_key].assert_called_once_with(g, *call_args)


@pytest.mark.parametrize("phase", [Phase.CI_WAIT, Phase.AWAITING_MERGE])
def test_noop_phases_do_not_call_handlers(phase):
    n, patches, _g = _patched_tick(phase, pr_url=APP_PR_URL)
    assert n == 1  # still counted as processed
    patches["clayde.freeshard.loop.run_implement"].assert_not_called()
    patches["clayde.freeshard.loop.run_ci_fix"].assert_not_called()
    patches["clayde.freeshard.loop.run_handoff"].assert_not_called()
    patches["clayde.freeshard.loop.run_manual_verify"].assert_not_called()


# ---------------------------------------------------------------------------
# Blocked issues are skipped (not counted)
# ---------------------------------------------------------------------------

@patch("clayde.freeshard.loop.run_implement")
@patch("clayde.freeshard.loop.is_blocked", return_value=True)
@patch("clayde.freeshard.loop.get_assigned_issues")
def test_tick_skips_blocked_issue(mock_assigned, _ib, mock_impl):
    mock_assigned.return_value = [_issue(APP_ISSUE_URL)]
    n = loop.tick(MagicMock(), _settings())
    assert n == 0
    mock_impl.assert_not_called()


# ---------------------------------------------------------------------------
# A handler raising does not abort the tick (other issues still processed)
# ---------------------------------------------------------------------------

@patch("clayde.freeshard.loop.derive_phase", return_value=Phase.IMPLEMENT)
@patch("clayde.freeshard.loop.is_blocked", return_value=False)
@patch("clayde.freeshard.loop.find_open_pr", return_value=None)
@patch("clayde.freeshard.loop.get_default_branch", return_value="main")
@patch("clayde.freeshard.loop.get_assigned_issues")
@patch("clayde.freeshard.loop.run_implement")
def test_handler_exception_does_not_abort_loop(mock_impl, mock_assigned, *_):
    issue_a = _issue("https://github.com/FreeshardBase/repo-a/issues/1", number=1)
    issue_b = _issue("https://github.com/FreeshardBase/repo-b/issues/2", number=2)
    mock_assigned.return_value = [issue_a, issue_b]

    # First call raises; second call succeeds
    mock_impl.side_effect = [RuntimeError("boom"), None]

    n = loop.tick(MagicMock(), _settings())
    # First issue errored (not counted), second succeeded (counted)
    assert n == 1
    assert mock_impl.call_count == 2


# ---------------------------------------------------------------------------
# Multiple non-core, non-blocked issues are all counted
# ---------------------------------------------------------------------------

@patch("clayde.freeshard.loop.derive_phase", return_value=Phase.IMPLEMENT)
@patch("clayde.freeshard.loop.is_blocked", return_value=False)
@patch("clayde.freeshard.loop.find_open_pr", return_value=None)
@patch("clayde.freeshard.loop.get_default_branch", return_value="main")
@patch("clayde.freeshard.loop.get_assigned_issues")
@patch("clayde.freeshard.loop.run_implement")
def test_multiple_issues_all_processed(mock_impl, mock_assigned, *_):
    mock_assigned.return_value = [
        _issue("https://github.com/FreeshardBase/repo-a/issues/1", number=1),
        _issue("https://github.com/FreeshardBase/repo-b/issues/2", number=2),
        _issue("https://github.com/FreeshardBase/repo-c/issues/3", number=3),
    ]
    n = loop.tick(MagicMock(), _settings())
    assert n == 3
    assert mock_impl.call_count == 3


# ---------------------------------------------------------------------------
# Mixed: core + blocked + valid → only valid counted
# ---------------------------------------------------------------------------

@patch("clayde.freeshard.loop.run_implement")
@patch("clayde.freeshard.loop.derive_phase", return_value=Phase.IMPLEMENT)
@patch("clayde.freeshard.loop.find_open_pr", return_value=None)
@patch("clayde.freeshard.loop.get_default_branch", return_value="main")
@patch("clayde.freeshard.loop.get_assigned_issues")
def test_mixed_issues_only_valid_counted(mock_assigned, _gdb, _fop, _dp, mock_impl):
    core_issue = _issue(CORE_ISSUE_URL, number=1)
    app_issue = _issue(APP_ISSUE_URL, number=7)

    def blocked_side_effect(g, owner, repo, number):
        return number == 1  # block only issue #1 (but it's core anyway)

    mock_assigned.return_value = [core_issue, app_issue]

    with patch("clayde.freeshard.loop.is_blocked", side_effect=blocked_side_effect):
        n = loop.tick(MagicMock(), _settings())

    assert n == 1
    mock_impl.assert_called_once()


# ---------------------------------------------------------------------------
# Minor A: PR items are skipped silently (no error log, no routing)
# ---------------------------------------------------------------------------

@patch("clayde.freeshard.loop.get_assigned_issues")
def test_pr_item_skipped_silently(mock_assigned):
    pr_item = MagicMock()
    pr_item.html_url = "https://github.com/FreeshardBase/app-repository/pull/42"
    mock_assigned.return_value = [pr_item]
    n = loop.tick(MagicMock(), _settings())
    assert n == 0


# ---------------------------------------------------------------------------
# Change 2: ci_required derived from has_ci_workflows
# ---------------------------------------------------------------------------

@patch("clayde.freeshard.loop.run_handoff")
@patch("clayde.freeshard.loop.run_implement")
@patch("clayde.freeshard.loop.has_ci_workflows", return_value=False)
@patch("clayde.freeshard.loop.is_reviewer_assigned", return_value=False)
@patch("clayde.freeshard.loop.get_ci_status", return_value="pending")
@patch("clayde.freeshard.loop.is_blocked", return_value=False)
@patch("clayde.freeshard.loop.find_open_pr", return_value=APP_PR_URL)
@patch("clayde.freeshard.loop.get_default_branch", return_value="main")
@patch("clayde.freeshard.loop.count_fix_commits", return_value=0)
@patch("clayde.freeshard.loop.get_assigned_issues")
def test_no_ci_workflows_open_pr_routes_to_handoff(
    mock_assigned, _cfc, _gdb, _fop, _ib, _gci, _ira, _hwf, mock_impl, mock_handoff,
):
    """Repo without CI workflows → ci_required=False → HANDOFF even with pending CI."""
    mock_assigned.return_value = [_issue(APP_ISSUE_URL, number=7)]
    g = MagicMock()
    g.get_repo.return_value.get_pull.return_value.head.sha = "abc"
    n = loop.tick(g, _settings())
    assert n == 1
    mock_handoff.assert_called_once()
    mock_impl.assert_not_called()


@patch("clayde.freeshard.loop.has_ci_workflows", return_value=True)
@patch("clayde.freeshard.loop.is_reviewer_assigned", return_value=False)
@patch("clayde.freeshard.loop.get_ci_status", return_value="pending")
@patch("clayde.freeshard.loop.is_blocked", return_value=False)
@patch("clayde.freeshard.loop.find_open_pr", return_value=APP_PR_URL)
@patch("clayde.freeshard.loop.get_default_branch", return_value="main")
@patch("clayde.freeshard.loop.count_fix_commits", return_value=0)
@patch("clayde.freeshard.loop.get_assigned_issues")
def test_has_ci_workflows_pending_ci_is_ci_wait(
    mock_assigned, _cfc, _gdb, _fop, _ib, _gci, _ira, _hwf,
):
    """Repo with CI workflows + pending CI → CI_WAIT (no handler called)."""
    mock_assigned.return_value = [_issue(APP_ISSUE_URL, number=7)]
    g = MagicMock()
    g.get_repo.return_value.get_pull.return_value.head.sha = "abc"
    with (
        patch("clayde.freeshard.loop.run_handoff") as mock_handoff,
        patch("clayde.freeshard.loop.run_implement") as mock_impl,
    ):
        n = loop.tick(g, _settings())
    assert n == 1
    mock_handoff.assert_not_called()
    mock_impl.assert_not_called()


# ---------------------------------------------------------------------------
# Resilience: a malformed issue URL must not abort the rest of the tick
# ---------------------------------------------------------------------------

@patch("clayde.freeshard.loop.run_implement")
@patch("clayde.freeshard.loop.derive_phase", return_value=Phase.IMPLEMENT)
@patch("clayde.freeshard.loop.is_blocked", return_value=False)
@patch("clayde.freeshard.loop.find_open_pr", return_value=None)
@patch("clayde.freeshard.loop.get_default_branch", return_value="main")
@patch("clayde.freeshard.loop.get_assigned_issues")
@patch("clayde.freeshard.loop.parse_issue_url")
def test_malformed_issue_url_does_not_abort_tick(
    mock_parse, mock_assigned, _gdb, _fop, _ib, _dp, mock_impl
):
    bad = _issue("bad-url", number=99)
    good = _issue(APP_ISSUE_URL, number=7)
    mock_assigned.return_value = [bad, good]
    mock_parse.side_effect = [ValueError("bad"), ("FreeshardBase", "app-repository", 7)]

    n = loop.tick(MagicMock(), _settings())
    assert n == 1
    mock_impl.assert_called_once()


def test_run_cycle_sets_gh_token_for_git_credential_helper():
    """run_cycle must export GH_TOKEN so the container's `!gh auth git-credential`
    helper can authenticate branch pushes (else push fails with exit 128)."""
    import os
    from unittest.mock import MagicMock, patch
    from clayde.freeshard import loop

    settings = MagicMock(github_token="ghp_testtoken123")
    os.environ.pop("GH_TOKEN", None)
    with patch("clayde.freeshard.loop.check_disk_and_alert"), \
         patch("clayde.freeshard.loop.is_claude_available", return_value=True), \
         patch("clayde.freeshard.loop.get_github_client", return_value=MagicMock()), \
         patch("clayde.freeshard.loop.tick", return_value=0):
        loop.run_cycle(settings)
    assert os.environ.get("GH_TOKEN") == "ghp_testtoken123"
    os.environ.pop("GH_TOKEN", None)
