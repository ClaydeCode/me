"""Tests for clayde.responses — structured JSON parsing and Pydantic validation."""

import pytest

from clayde.responses import (
    WorkResponse,
    _extract_json,
    parse_response,
)


class TestExtractJson:
    def test_strips_json_fences(self):
        text = "```json\n{\"summary\": \"hello\"}\n```"
        assert _extract_json(text) == '{"summary": "hello"}'

    def test_strips_plain_fences(self):
        text = "```\n{\"summary\": \"hello\"}\n```"
        assert _extract_json(text) == '{"summary": "hello"}'

    def test_no_fences_unchanged(self):
        text = '{"summary": "hello"}'
        assert _extract_json(text) == text

    def test_strips_surrounding_whitespace(self):
        text = '  {"summary": "hello"}  '
        assert _extract_json(text) == '{"summary": "hello"}'

    def test_partial_fence_unchanged(self):
        # Only opening fence — should not strip
        text = "```json\n{\"summary\": \"hello\"}"
        result = _extract_json(text)
        assert "summary" in result

    def test_extracts_json_with_preamble(self):
        text = 'Here is my plan.\n\n```json\n{"summary": "hello"}\n```'
        assert _extract_json(text) == '{"summary": "hello"}'

    def test_extracts_json_object_without_fences(self):
        text = 'Now I have enough context.\n\n{"summary": "hello"}'
        assert _extract_json(text) == '{"summary": "hello"}'

    def test_handles_nested_braces_in_json(self):
        text = 'Some text\n{"summary": "use {braces} here"}'
        result = _extract_json(text)
        assert '"summary"' in result
        assert "{braces}" in result

    def test_handles_escaped_quotes(self):
        text = r'Preamble {"summary": "say \"hello\""}'
        result = _extract_json(text)
        assert '"summary"' in result


class TestParseResponse:
    def test_parses_work_response(self):
        text = '{"summary": "Implemented the feature"}'
        result = parse_response(text, WorkResponse)
        assert isinstance(result, WorkResponse)
        assert result.summary == "Implemented the feature"

    def test_strips_code_fences_before_parsing(self):
        text = '```json\n{"summary": "Done"}\n```'
        result = parse_response(text, WorkResponse)
        assert result.summary == "Done"

    def test_invalid_json_raises_value_error(self):
        with pytest.raises(ValueError, match="not valid JSON"):
            parse_response("this is not json", WorkResponse)

    def test_missing_required_field_raises_value_error(self):
        text = '{"other_field": "value"}'
        with pytest.raises(ValueError, match="failed validation"):
            parse_response(text, WorkResponse)

    def test_empty_string_raises_value_error(self):
        with pytest.raises(ValueError):
            parse_response("", WorkResponse)

    def test_extra_fields_are_ignored(self):
        text = '{"summary": "Done", "extra_field": "ignored"}'
        result = parse_response(text, WorkResponse)
        assert result.summary == "Done"

    def test_multiline_summary_preserved(self):
        import json
        content = "## Summary\n\nStep 1\nStep 2"
        text = json.dumps({"summary": content})
        result = parse_response(text, WorkResponse)
        assert result.summary == content
