"""Pydantic models for structured LLM responses and JSON parsing utilities."""

import json
import re

from pydantic import BaseModel, ValidationError


class PreliminaryPlanResponse(BaseModel):
    plan: str


class ThoroughPlanResponse(BaseModel):
    plan: str
    branch_name: str


class UpdatePlanResponse(BaseModel):
    summary: str
    updated_plan: str


class ImplementResponse(BaseModel):
    summary: str


class AddressReviewResponse(BaseModel):
    summary: str


def _strip_code_fences(text: str) -> str:
    """Strip markdown code fences (```json ... ``` or ``` ... ```) from text."""
    text = text.strip()
    # Remove ```json ... ``` or ``` ... ``` fences
    m = re.match(r"^```(?:json)?\s*\n([\s\S]*?)\n```$", text)
    if m:
        return m.group(1).strip()
    return text


def parse_response(text: str, model_class: type[BaseModel]) -> BaseModel:
    """Parse and validate a JSON response from the LLM.

    Strips markdown code fences if present, parses as JSON, and validates
    against the given Pydantic model.

    Raises:
        ValueError: If the text cannot be parsed as JSON or fails validation.
    """
    cleaned = _strip_code_fences(text)
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as e:
        raise ValueError(f"LLM response is not valid JSON: {e}\nRaw output: {text!r}") from e
    try:
        return model_class.model_validate(data)
    except ValidationError as e:
        raise ValueError(f"LLM response failed validation for {model_class.__name__}: {e}\nData: {data}") from e
