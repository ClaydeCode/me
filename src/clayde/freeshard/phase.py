"""Stateless phase derivation for the Freeshard execution loop.

Phase is a pure function of GitHub-observable facts — no local store.
"""
from enum import StrEnum

_CI_PENDING = {None, "pending", "queued", "in_progress", "waiting", "requested"}
_CI_FAILED = {"failure", "error", "cancelled", "timed_out", "action_required", "stale"}


class Phase(StrEnum):
    IMPLEMENT = "implement"            # no open PR — fresh or partial branch
    CI_WAIT = "ci_wait"               # PR open, CI still running
    CI_FIX = "ci_fix"                 # PR open, CI failed, under fix cap
    MANUAL_VERIFY = "manual_verify"   # PR open, CI failed, fix cap reached
    HANDOFF = "handoff"               # PR open, CI green, Max not yet reviewer
    AWAITING_MERGE = "awaiting_merge" # PR open, CI green, Max is reviewer


def derive_phase(*, pr_open: bool, ci_status: str | None,
                 max_is_reviewer: bool, fix_attempts: int, fix_cap: int = 2) -> Phase:
    if not pr_open:
        return Phase.IMPLEMENT
    if ci_status in _CI_PENDING:
        return Phase.CI_WAIT
    if ci_status in _CI_FAILED:
        return Phase.MANUAL_VERIFY if fix_attempts >= fix_cap else Phase.CI_FIX
    # any other value ("success") → green
    return Phase.AWAITING_MERGE if max_is_reviewer else Phase.HANDOFF
