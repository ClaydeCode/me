"""Pydantic models for structured LLM responses and JSON parsing utilities."""

import json
import re
from typing import Literal

from pydantic import BaseModel, ValidationError


class PreliminaryPlanResponse(BaseModel):
    plan: str
    size: Literal["small", "large"]
    branch_name: str


class ThoroughPlanResponse(BaseModel):
    plan: str


class UpdatePlanResponse(BaseModel):
    summary: str
    updated_plan: str


class ImplementResponse(BaseModel):
    summary: str


class AddressReviewResponse(BaseModel):
    summary: str


def _extract_json(text: str) -> str:
    """Extract a JSON object from LLM output that may contain surrounding text.

    Tries in order:
    1. Code-fenced JSON block (```json ... ``` or ``` ... ```)
    2. First top-level { ... } object in the text
    3. Falls back to stripped original text
    """
    text = text.strip()
    # 1. Extract from markdown code fence anywhere in the text
    m = re.search(r"```(?:json)?\s*\n([\s\S]*?)\n```", text)
    if m:
        return m.group(1).strip()
    # 2. Find the first top-level JSON object by matching braces
    start = text.find("{")
    if start != -1:
        depth = 0
        in_string = False
        escape = False
        for i in range(start, len(text)):
            c = text[i]
            if escape:
                escape = False
                continue
            if c == "\\":
                escape = True
                continue
            if c == '"' and not escape:
                in_string = not in_string
                continue
            if in_string:
                continue
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    return text[start : i + 1]
    return text


def parse_response(text: str, model_class: type[BaseModel]) -> BaseModel:
    """Parse and validate a JSON response from the LLM.

    Strips markdown code fences if present, parses as JSON, and validates
    against the given Pydantic model.

    Raises:
        ValueError: If the text cannot be parsed as JSON or fails validation.
    """
    cleaned = _extract_json(text)
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as e:
        raise ValueError(f"LLM response is not valid JSON: {e}\nRaw output: {text!r}") from e
    try:
        return model_class.model_validate(data)
    except ValidationError as e:
        raise ValueError(f"LLM response failed validation for {model_class.__name__}: {e}\nData: {data}") from e
