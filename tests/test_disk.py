"""Tests for clayde.disk — best-effort disk-usage alert."""

from pathlib import Path

import clayde.disk
from clayde.config import Settings
from clayde.disk import check_disk_and_alert


def _settings(**over) -> Settings:
    base = dict(
        disk_alert_enabled=True,
        disk_alert_threshold_pct=85,
        disk_alert_path="/data",
        disk_alert_cooldown_s=21600,
    )
    base.update(over)
    return Settings(_env_file=None, **base)


def _patch_usage(monkeypatch, *, total, used):
    free = total - used
    monkeypatch.setattr(
        clayde.disk.shutil, "disk_usage", lambda _p: (total, used, free)
    )


def _capture_sends(monkeypatch) -> list:
    sent = []
    monkeypatch.setattr(
        clayde.disk, "_send", lambda settings, **kw: sent.append(kw)
    )
    return sent


class TestCheckDiskAndAlert:
    def test_under_threshold_no_alert(self, monkeypatch, tmp_path):
        _patch_usage(monkeypatch, total=100, used=50)
        sent = _capture_sends(monkeypatch)
        pct = check_disk_and_alert(_settings(), state_path=tmp_path / "s.json")
        assert pct == 50
        assert sent == []

    def test_over_threshold_alerts_once(self, monkeypatch, tmp_path):
        _patch_usage(monkeypatch, total=100, used=91)
        sent = _capture_sends(monkeypatch)
        pct = check_disk_and_alert(
            _settings(), state_path=tmp_path / "s.json", now=1000.0
        )
        assert pct == 91
        assert len(sent) == 1
        assert sent[0]["usage_pct"] == 91

    def test_within_cooldown_suppressed(self, monkeypatch, tmp_path):
        _patch_usage(monkeypatch, total=100, used=91)
        sent = _capture_sends(monkeypatch)
        sf = tmp_path / "s.json"
        check_disk_and_alert(_settings(), state_path=sf, now=1000.0)
        check_disk_and_alert(_settings(), state_path=sf, now=1000.0 + 3600)
        assert len(sent) == 1  # second within 6h cooldown -> suppressed

    def test_after_cooldown_realerts(self, monkeypatch, tmp_path):
        _patch_usage(monkeypatch, total=100, used=91)
        sent = _capture_sends(monkeypatch)
        sf = tmp_path / "s.json"
        check_disk_and_alert(_settings(), state_path=sf, now=1000.0)
        check_disk_and_alert(_settings(), state_path=sf, now=1000.0 + 21600 + 1)
        assert len(sent) == 2

    def test_disabled_skips(self, monkeypatch, tmp_path):
        _patch_usage(monkeypatch, total=100, used=99)
        sent = _capture_sends(monkeypatch)
        pct = check_disk_and_alert(
            _settings(disk_alert_enabled=False), state_path=tmp_path / "s.json"
        )
        assert pct is None
        assert sent == []

    def test_send_uses_shared_ntfy_helper(self, monkeypatch):
        calls = []

        async def fake_send_ntfy(**kw):
            calls.append(kw)

        monkeypatch.setattr(clayde.disk, "send_ntfy", fake_send_ntfy)
        clayde.disk._send(_settings(), usage_pct=91, free_gb=4.2)
        assert len(calls) == 1
        assert calls[0]["success"] is False
        assert calls[0]["topic"] == _settings().ntfy_topic
        assert "91%" in calls[0]["title"]

    def test_usage_error_swallowed(self, monkeypatch, tmp_path):
        def boom(_p):
            raise OSError("no such path")

        monkeypatch.setattr(clayde.disk.shutil, "disk_usage", boom)
        sent = _capture_sends(monkeypatch)
        pct = check_disk_and_alert(_settings(), state_path=tmp_path / "s.json")
        assert pct is None
        assert sent == []
