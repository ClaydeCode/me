"""Repo classification: coreness + verify profile. Pure, no I/O."""

CORE_REPOS = frozenset({"freeshard", "freeshard-controller"})

# Repos whose changes require a running shard to verify. Empty for now;
# populated when a non-core repo is found to need a live shard (Group B).
_NEEDS_SHARD = frozenset()


def is_non_core(repo: str) -> bool:
    return repo not in CORE_REPOS


def verify_profile(repo: str) -> str:
    if repo in _NEEDS_SHARD:
        return "needs-shard"
    return "tests-only"
