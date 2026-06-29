"""Stateless dispatch loop for the Freeshard execution loop.

tick() runs one full pass over all open issues assigned to the bot,
derives the phase from GitHub-observable facts, and routes to the
matching handler. No local state — all phase input comes from GitHub.
"""
import concurrent.futures
import logging

from clayde.claude import is_claude_available
from clayde.config import get_github_client
from clayde.disk import check_disk_and_alert
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
    has_ci_workflows,
    is_blocked,
    is_pull_request_item,
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


def _process_issue(g, settings, issue) -> bool:
    """Process a single issue. Returns True iff the issue was routed (counted)."""
    if is_pull_request_item(issue):
        log.debug("Skipping PR item %s", issue.html_url)
        return False
    try:
        owner, repo, number = parse_issue_url(issue.html_url)
        ref = f"{owner}/{repo}#{number}"

        if not is_non_core(repo):
            log.info("Skipping core repo %s (Phase 3)", ref)
            return False

        if is_blocked(g, owner, repo, number):
            log.info("%s is blocked — skipping", ref)
            return False

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
            fix_attempts = count_fix_commits(g, owner, repo, branch, default_branch)

        try:
            ci_required = has_ci_workflows(g, owner, repo)
        except Exception:
            log.warning(
                "Could not determine CI workflows for %s/%s — defaulting ci_required=True",
                owner, repo,
            )
            ci_required = True
        phase = derive_phase(
            pr_open=pr_open,
            ci_status=ci,
            max_is_reviewer=bool(is_rev),
            fix_attempts=fix_attempts,
            ci_required=ci_required,
        )
        log.info("%s — phase=%s", ref, phase)

        _route(g, owner, repo, number, default_branch, pr_number, head, phase, settings)
        return True

    except Exception as exc:  # noqa: BLE001
        log.exception("Error processing %s: %s", issue.html_url, exc)
        return False


def tick(g, settings) -> int:
    """Process one pass over assigned issues. Returns count of routed issues."""
    issues = list(get_assigned_issues(g))
    parallelism = max(1, int(getattr(settings, "fs_parallelism", 1)))

    if parallelism == 1:
        return sum(1 if _process_issue(g, settings, issue) else 0 for issue in issues)

    processed = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=parallelism) as executor:
        futures = [executor.submit(_process_issue, g, settings, issue) for issue in issues]
        for f in futures:
            try:
                if f.result():
                    processed += 1
            except Exception as exc:  # noqa: BLE001
                log.exception("Unexpected error collecting future result: %s", exc)
    return processed


def run_cycle(settings) -> int:
    """One Freeshard cycle: disk guard + tick. Returns issue count, or -1 if Claude unavailable."""
    try:
        check_disk_and_alert(settings)
    except Exception:
        log.warning("Disk guard check failed — continuing")
    if is_claude_available():
        g = get_github_client()
        return tick(g, settings)
    log.info("usage limit — skipping")
    return -1
