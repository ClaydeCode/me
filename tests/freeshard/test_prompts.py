"""Tests for Freeshard prompt templates."""

from clayde.prompts import render_template


def test_fs_implement_includes_branch_and_tests():
    out = render_template(
        "fs_implement.j2",
        number=1,
        title="T",
        owner="o",
        repo="r",
        body="b",
        discussion_text="",
        repo_path="/wt",
        branch_name="clayde/issue-1",
    )
    assert "clayde/issue-1" in out and "test" in out.lower()


def test_fs_ci_fix_includes_log_and_prefix_rule():
    out = render_template(
        "fs_ci_fix.j2",
        number=1,
        owner="o",
        repo="r",
        repo_path="/wt",
        branch_name="clayde/issue-1",
        ci_log="boom",
    )
    assert "fix(ci):" in out and "boom" in out
