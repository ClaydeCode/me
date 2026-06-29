"""Direct tests for verify.py — profile dispatch, runner discovery, shard lifecycle."""
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

from clayde.freeshard.verify import _verify_with_shard, local_verify


# ---------------------------------------------------------------------------
# profile == "none"
# ---------------------------------------------------------------------------

def test_none_profile_returns_ok_no_subprocess(tmp_path):
    with patch("clayde.freeshard.verify.subprocess.run") as mock_run:
        ok, tail = local_verify("none", tmp_path)
    assert ok is True
    assert tail == ""
    mock_run.assert_not_called()


# ---------------------------------------------------------------------------
# profile == "tests-only" — runner discovery
# ---------------------------------------------------------------------------

def test_tests_only_justfile_runs_just_test(tmp_path):
    (tmp_path / "justfile").write_text("test:\n    echo ok\n")
    proc = MagicMock(returncode=0, stdout="passed\n", stderr="")
    with patch("clayde.freeshard.verify.subprocess.run", return_value=proc) as mock_run:
        ok, tail = local_verify("tests-only", tmp_path)
    assert ok is True
    mock_run.assert_called_once()
    cmd = mock_run.call_args[0][0]
    assert cmd == ["just", "test"]


def test_tests_only_justfile_rc_nonzero_returns_false(tmp_path):
    (tmp_path / "justfile").write_text("test:\n    exit 1\n")
    proc = MagicMock(returncode=1, stdout="", stderr="FAILED\n")
    with patch("clayde.freeshard.verify.subprocess.run", return_value=proc):
        ok, tail = local_verify("tests-only", tmp_path)
    assert ok is False
    assert "FAILED" in tail


def test_tests_only_package_json_runs_npm_test(tmp_path):
    (tmp_path / "package.json").write_text('{"name":"x"}')
    proc = MagicMock(returncode=0, stdout="ok\n", stderr="")
    with patch("clayde.freeshard.verify.subprocess.run", return_value=proc) as mock_run:
        ok, _ = local_verify("tests-only", tmp_path)
    assert ok is True
    cmd = mock_run.call_args[0][0]
    assert cmd == ["npm", "test"]


def test_tests_only_pyproject_runs_uv_pytest(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[tool.pytest]\n")
    proc = MagicMock(returncode=0, stdout="1 passed\n", stderr="")
    with patch("clayde.freeshard.verify.subprocess.run", return_value=proc) as mock_run:
        ok, _ = local_verify("tests-only", tmp_path)
    assert ok is True
    cmd = mock_run.call_args[0][0]
    assert cmd == ["uv", "run", "pytest"]


def test_tests_only_justfile_beats_package_json(tmp_path):
    (tmp_path / "justfile").write_text("test:\n    echo ok\n")
    (tmp_path / "package.json").write_text('{"name":"x"}')
    (tmp_path / "pyproject.toml").write_text("[tool.pytest]\n")
    proc = MagicMock(returncode=0, stdout="ok\n", stderr="")
    with patch("clayde.freeshard.verify.subprocess.run", return_value=proc) as mock_run:
        local_verify("tests-only", tmp_path)
    cmd = mock_run.call_args[0][0]
    assert cmd == ["just", "test"]


def test_tests_only_package_json_beats_pyproject(tmp_path):
    (tmp_path / "package.json").write_text('{"name":"x"}')
    (tmp_path / "pyproject.toml").write_text("[tool.pytest]\n")
    proc = MagicMock(returncode=0, stdout="ok\n", stderr="")
    with patch("clayde.freeshard.verify.subprocess.run", return_value=proc) as mock_run:
        local_verify("tests-only", tmp_path)
    cmd = mock_run.call_args[0][0]
    assert cmd == ["npm", "test"]


def test_tests_only_no_runner_returns_ok_no_subprocess(tmp_path):
    with patch("clayde.freeshard.verify.subprocess.run") as mock_run:
        ok, tail = local_verify("tests-only", tmp_path)
    assert ok is True
    assert tail == ""
    mock_run.assert_not_called()


def test_tests_only_tail_truncated_to_2000(tmp_path):
    (tmp_path / "justfile").write_text("test:\n    echo ok\n")
    long_output = "x" * 5000
    proc = MagicMock(returncode=0, stdout=long_output, stderr="")
    with patch("clayde.freeshard.verify.subprocess.run", return_value=proc):
        ok, tail = local_verify("tests-only", tmp_path)
    assert ok is True
    assert len(tail) == 2000
    assert tail == long_output[-2000:]


# ---------------------------------------------------------------------------
# profile == "needs-shard"
# ---------------------------------------------------------------------------

def test_needs_shard_no_config_raises(tmp_path):
    with pytest.raises(RuntimeError, match="Phase 2 deferral"):
        local_verify("needs-shard", tmp_path, repo="some-repo")


def test_needs_shard_no_repo_arg_raises(tmp_path):
    with pytest.raises(RuntimeError, match="Phase 2 deferral"):
        local_verify("needs-shard", tmp_path)


# ---------------------------------------------------------------------------
# _verify_with_shard — compose lifecycle
# ---------------------------------------------------------------------------

def _make_proc(returncode=0, stdout="", stderr=""):
    p = MagicMock()
    p.returncode = returncode
    p.stdout = stdout
    p.stderr = stderr
    return p


def test_shard_order_up_command_down(tmp_path):
    compose_file = "/path/to/docker-compose.yml"
    command = ["pytest", "tests/integration"]
    calls_seen = []

    def fake_run(cmd, **kwargs):
        calls_seen.append(list(cmd))
        if cmd[0] == "docker":
            return _make_proc()
        return _make_proc(stdout="ok\n")

    with patch("clayde.freeshard.verify.subprocess.run", side_effect=fake_run):
        ok, tail = _verify_with_shard(tmp_path, compose_file, command)

    assert ok is True
    assert calls_seen[0] == ["docker", "compose", "-f", compose_file, "up", "-d"]
    assert calls_seen[1] == command
    assert calls_seen[2] == ["docker", "compose", "-f", compose_file, "down", "-v"]


def test_shard_down_runs_even_when_command_raises(tmp_path):
    compose_file = "/path/to/docker-compose.yml"
    command = ["pytest"]
    down_called = []

    def fake_run(cmd, **kwargs):
        if "up" in cmd:
            return _make_proc()
        if "down" in cmd:
            down_called.append(True)
            return _make_proc()
        raise RuntimeError("command exploded")

    with patch("clayde.freeshard.verify.subprocess.run", side_effect=fake_run):
        with pytest.raises(RuntimeError, match="command exploded"):
            _verify_with_shard(tmp_path, compose_file, command)

    assert down_called, "docker compose down must run even when command raises"


def test_shard_compose_file_passed_to_up_and_down(tmp_path):
    compose_file = "/custom/compose.yml"
    command = ["echo", "hi"]
    seen_compose_args = []

    def fake_run(cmd, **kwargs):
        if "docker" in cmd:
            seen_compose_args.append(cmd[3])  # -f <file>: index 3
            return _make_proc()
        return _make_proc(stdout="hi\n")

    with patch("clayde.freeshard.verify.subprocess.run", side_effect=fake_run):
        _verify_with_shard(tmp_path, compose_file, command)

    assert seen_compose_args == [compose_file, compose_file]


def test_shard_command_failure_returns_false(tmp_path):
    compose_file = "/path/to/docker-compose.yml"
    command = ["pytest"]

    def fake_run(cmd, **kwargs):
        if "docker" in cmd:
            return _make_proc()
        return _make_proc(returncode=1, stderr="FAILED\n")

    with patch("clayde.freeshard.verify.subprocess.run", side_effect=fake_run):
        ok, tail = _verify_with_shard(tmp_path, compose_file, command)

    assert ok is False
    assert "FAILED" in tail
