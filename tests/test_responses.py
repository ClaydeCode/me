"""Tests for clayde.responses — structured JSON parsing and Pydantic validation."""

import pytest

from clayde.responses import (
    AddressReviewResponse,
    ImplementResponse,
    PreliminaryPlanResponse,
    ThoroughPlanResponse,
    UpdatePlanResponse,
    _extract_json,
    parse_response,
)


class TestExtractJson:
    def test_strips_json_fences(self):
        text = "```json\n{\"plan\": \"hello\"}\n```"
        assert _extract_json(text) == '{"plan": "hello"}'

    def test_strips_plain_fences(self):
        text = "```\n{\"plan\": \"hello\"}\n```"
        assert _extract_json(text) == '{"plan": "hello"}'

    def test_no_fences_unchanged(self):
        text = '{"plan": "hello"}'
        assert _extract_json(text) == text

    def test_strips_surrounding_whitespace(self):
        text = '  {"plan": "hello"}  '
        assert _extract_json(text) == '{"plan": "hello"}'

    def test_partial_fence_unchanged(self):
        # Only opening fence — should not strip
        text = "```json\n{\"plan\": \"hello\"}"
        result = _extract_json(text)
        # no closing fence so brace-matching finds the JSON object
        assert "plan" in result

    def test_extracts_json_with_preamble(self):
        text = 'Here is my plan.\n\n```json\n{"plan": "hello"}\n```'
        assert _extract_json(text) == '{"plan": "hello"}'

    def test_extracts_json_object_without_fences(self):
        text = 'Now I have enough context.\n\n{"plan": "hello"}'
        assert _extract_json(text) == '{"plan": "hello"}'

    def test_handles_nested_braces_in_json(self):
        text = 'Some text\n{"plan": "use {braces} here"}'
        result = _extract_json(text)
        assert '"plan"' in result
        assert "{braces}" in result

    def test_handles_escaped_quotes(self):
        text = r'Preamble {"plan": "say \"hello\""}'
        result = _extract_json(text)
        assert '"plan"' in result


class TestParseResponse:
    def test_parses_preliminary_plan(self):
        text = '{"plan": "My plan here", "size": "small", "branch_name": "clayde/issue-1-fix"}'
        result = parse_response(text, PreliminaryPlanResponse)
        assert isinstance(result, PreliminaryPlanResponse)
        assert result.plan == "My plan here"
        assert result.size == "small"
        assert result.branch_name == "clayde/issue-1-fix"

    def test_parses_preliminary_plan_large(self):
        text = '{"plan": "Big plan", "size": "large", "branch_name": "clayde/issue-2-big-feature"}'
        result = parse_response(text, PreliminaryPlanResponse)
        assert result.size == "large"

    def test_parses_thorough_plan(self):
        text = '{"plan": "Thorough plan"}'
        result = parse_response(text, ThoroughPlanResponse)
        assert isinstance(result, ThoroughPlanResponse)
        assert result.plan == "Thorough plan"

    def test_parses_update_plan(self):
        text = '{"summary": "Changed X", "updated_plan": "Updated plan"}'
        result = parse_response(text, UpdatePlanResponse)
        assert isinstance(result, UpdatePlanResponse)
        assert result.summary == "Changed X"
        assert result.updated_plan == "Updated plan"

    def test_parses_implement_response(self):
        text = '{"summary": "Implemented the feature"}'
        result = parse_response(text, ImplementResponse)
        assert isinstance(result, ImplementResponse)
        assert result.summary == "Implemented the feature"

    def test_parses_address_review_response(self):
        text = '{"summary": "Fixed the typo"}'
        result = parse_response(text, AddressReviewResponse)
        assert isinstance(result, AddressReviewResponse)
        assert result.summary == "Fixed the typo"

    def test_strips_code_fences_before_parsing(self):
        text = '```json\n{"plan": "My plan", "size": "small", "branch_name": "clayde/issue-1-fix"}\n```'
        result = parse_response(text, PreliminaryPlanResponse)
        assert result.plan == "My plan"

    def test_invalid_json_raises_value_error(self):
        with pytest.raises(ValueError, match="not valid JSON"):
            parse_response("this is not json", PreliminaryPlanResponse)

    def test_missing_required_field_raises_value_error(self):
        # PreliminaryPlanResponse requires plan, size, and branch_name
        text = '{"plan": "only plan, no size or branch_name"}'
        with pytest.raises(ValueError, match="failed validation"):
            parse_response(text, PreliminaryPlanResponse)

    def test_wrong_type_raises_value_error(self):
        text = '{"plan": 123}'  # plan should be a string
        with pytest.raises(ValueError, match="failed validation"):
            parse_response(text, PreliminaryPlanResponse)

    def test_empty_string_raises_value_error(self):
        with pytest.raises(ValueError):
            parse_response("", PreliminaryPlanResponse)

    def test_extra_fields_are_ignored(self):
        text = '{"plan": "My plan", "size": "small", "branch_name": "clayde/issue-1-fix", "extra_field": "ignored"}'
        result = parse_response(text, PreliminaryPlanResponse)
        assert result.plan == "My plan"

    def test_multiline_plan_preserved(self):
        import json
        plan_content = "## Plan\n\nStep 1\nStep 2"
        text = json.dumps({"plan": plan_content, "size": "large", "branch_name": "clayde/issue-1-fix"})
        result = parse_response(text, PreliminaryPlanResponse)
        assert result.plan == plan_content
