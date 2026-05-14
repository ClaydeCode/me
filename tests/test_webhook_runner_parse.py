"""Tests for extract_notification_payload — last-JSON-block extractor."""

from __future__ import annotations

from clayde.webhook.runner import extract_notification_payload


def test_extracts_last_json_block():
    result = '''
    Working on it.
    ```json
    {"title": "stale", "body": "old", "success": false}
    ```
    Done.
    ```json
    {"title": "saved", "body": "wrote inbox/x.md", "success": true}
    ```
    '''
    p = extract_notification_payload(result)
    assert p.title == "saved"
    assert p.body == "wrote inbox/x.md"
    assert p.success is True


def test_fallback_on_missing_block():
    result = "I did things but forgot the JSON."
    p = extract_notification_payload(result)
    assert p.title == "Pebble: done (no summary)"
    assert p.body == "I did things but forgot the JSON."
    assert p.success is True


def test_fallback_on_malformed_json():
    result = "stuff\n```json\n{not valid json}\n```"
    p = extract_notification_payload(result)
    assert p.title == "Pebble: done (no summary)"
    assert p.success is True


def test_clamps_overlong_fields():
    long = "x" * 500
    result = f'```json\n{{"title": "{long}", "body": "{long}", "success": true}}\n```'
    p = extract_notification_payload(result)
    assert len(p.title) == 40
    assert len(p.body) == 300


def test_success_false_honored():
    result = '```json\n{"title": "fail", "body": "couldn\'t do it", "success": false}\n```'
    p = extract_notification_payload(result)
    assert p.success is False


def test_fallback_on_empty_string():
    p = extract_notification_payload("")
    assert p.title == "Pebble: done (no summary)"
    assert p.body == "(empty output)"
    assert p.success is True
