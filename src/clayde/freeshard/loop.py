"""Stateless dispatch loop for the Freeshard execution loop.

tick() runs one full pass over all open issues assigned to the bot,
derives the phase from GitHub-observable facts, and routes to the
matching handler. No local state — all phase input comes from GitHub.
"""
import logging

from clayde.freeshard.phase import Phase, derive_phase
from clayde.freeshard.repos import is_non_core
from clayde.freeshard.steps import (
    run_ci_fix,
    run_handoff,
    run_implement,
    run_manual_verify,
)
from clayde.github import (
    count_fix_commits,
    find_open_pr,
    get_assigned_issues,
    get_ci_status,
    get_default_branch,
    is_blocked,
    is_reviewer_assigned,
    parse_issue_url,
    parse_pr_url,
)

log = logging.getLogger("clayde.freeshard.loop")


def _ci_failure_summary(g, owner: str, repo: str, head: str) -> str:
    """Return a short string listing failing check-run names, best-effort."""
    try:
        commit = g.get_repo(f"{owner}/{repo}").get_commit(head)
        failed = [
            run.name
            for run in commit.get_check_runs()
            if run.status == "completed"
            and run.conclusion in ("failure", "error", "cancelled", "timed_out", "action_required")
        ]
        if failed:
            return "Failing checks: " + ", ".join(failed)
    except Exception as exc:  # noqa: BLE001
        log.warning("Could not fetch CI check-run names (%s)", exc)
    return "CI failed — check logs for details"


def _route(
    g,
    owner: str,
    repo: str,
    number: int,
    default_branch: str,
    pr_number: int | None,
    head: str | None,
    phase: Phase,
    settings,
) -> None:
    ref = f"{owner}/{repo}#{number}"
    if phase is Phase.IMPLEMENT:
        run_implement(g, owner, repo, number, default_branch)
    elif phase is Phase.CI_FIX:
        ci_log = _ci_failure_summary(g, owner, repo, head) if head else "CI failed"
        run_ci_fix(g, owner, repo, number, default_branch, ci_log)
    elif phase is Phase.HANDOFF:
        run_handoff(g, owner, repo, number, pr_number, settings.fs_reviewer)
    elif phase is Phase.MANUAL_VERIFY:
        run_manual_verify(g, owner, repo, number, pr_number, settings.fs_reviewer)
    elif phase is Phase.CI_WAIT:
        log.info("%s — CI_WAIT: waiting for checks to complete", ref)
    elif phase is Phase.AWAITING_MERGE:
        log.info("%s — AWAITING_MERGE: reviewer assigned, waiting for merge", ref)
    else:
        log.warning("%s — unknown phase %s, skipping", ref, phase)


def tick(g, settings) -> int:
    """Process one pass over assigned issues. Returns count of routed issues."""
    processed = 0
    for issue in get_assigned_issues(g):
        try:
            owner, repo, number = parse_issue_url(issue.html_url)
            ref = f"{owner}/{repo}#{number}"

            if not is_non_core(repo):
                log.info("Skipping core repo %s (Phase 3)", ref)
                continue

            if is_blocked(g, owner, repo, number):
                log.info("%s is blocked — skipping", ref)
                continue

            default_branch = get_default_branch(g, owner, repo)
            branch = f"clayde/issue-{number}"
            pr_url = find_open_pr(g, owner, repo, branch)
            pr_open = pr_url is not None

            pr_number: int | None = None
            head: str | None = None
            ci = None
            is_rev = False
            fix_attempts = 0

            if pr_open:
                _, _, pr_number = parse_pr_url(pr_url)
                head = g.get_repo(f"{owner}/{repo}").get_pull(pr_number).head.sha
                ci = get_ci_status(g, owner, repo, head)
                is_rev = is_reviewer_assigned(g, owner, repo, pr_number, settings.fs_reviewer)
                fix_attempts = count_fix_commits(g, owner, repo, branch)

            phase = derive_phase(
                pr_open=pr_open,
                ci_status=ci,
                max_is_reviewer=bool(is_rev),
                fix_attempts=fix_attempts,
            )
            log.info("%s — phase=%s", ref, phase)

            _route(g, owner, repo, number, default_branch, pr_number, head, phase, settings)
            processed += 1

        except Exception as exc:  # noqa: BLE001
            log.exception("Error processing %s: %s", issue.html_url, exc)

    return processed
