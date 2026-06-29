from clayde.freeshard.repos import is_non_core, verify_profile


def test_core_repos_excluded():
    assert not is_non_core("freeshard")
    assert not is_non_core("freeshard-controller")


def test_non_core_repos_included():
    for r in ("landing-page", "app-repository", "documentation", "web-terminal"):
        assert is_non_core(r)


def test_profile_defaults_to_tests_only():
    assert verify_profile("app-repository") == "tests-only"


def test_documentation_has_no_tests():
    assert verify_profile("documentation") == "none"
