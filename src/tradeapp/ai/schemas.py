"""What the model is allowed to say.

The whole run-time AI design rests on this file being narrow. A language model that can return
free-form advice is a model that can, eventually, return something that moves money in a way
nobody predicted. So it returns four bounded values and a sentence of reasoning, validated
strictly: anything outside the bounds is a parse failure, and a parse failure means the previous
view stands (D6). It never means "retry until it says something acceptable".
"""

from __future__ import annotations

import json
import re

from pydantic import BaseModel, Field, ValidationError


class AnalystView(BaseModel):
    """The only shape allowed to influence a trade."""

    model_config = {"extra": "forbid"}

    regime: str = Field(max_length=40, description="short label, e.g. risk-off, USD strength")
    bias: float = Field(ge=-1.0, le=1.0, description="-1 favours short, +1 favours long")
    size_mult: float = Field(ge=0.0, le=1.5, description="multiplier on position size")
    block: bool = Field(description="true stops new entries entirely")
    valid_minutes: int = Field(ge=5, le=1440, description="how long this view should be trusted")
    note: str = Field(default="", max_length=400, description="one sentence of reasoning for the journal")


class ScoutEvent(BaseModel):
    model_config = {"extra": "forbid"}

    title: str = Field(max_length=200)
    currency: str = Field(min_length=3, max_length=3)
    impact: str = Field(pattern="^(HIGH|MEDIUM|LOW)$")
    summary: str = Field(default="", max_length=300)


class ScoutReport(BaseModel):
    model_config = {"extra": "forbid"}

    events: list[ScoutEvent] = Field(default_factory=list, max_length=25)


class ReviewFinding(BaseModel):
    model_config = {"extra": "forbid"}

    decision_id: int | None = None
    classification: str = Field(pattern="^(variance|execution|regime|bug)$")
    note: str = Field(max_length=400)


class ReviewReport(BaseModel):
    """The reviewer explains; it never proposes an order or a parameter change (D11)."""

    model_config = {"extra": "forbid"}

    summary: str = Field(max_length=1000)
    findings: list[ReviewFinding] = Field(default_factory=list, max_length=30)
    bias_was_right: bool | None = None


def extract_json(text: str) -> str:
    """Pull the JSON object out of a reply that may be wrapped in prose or a code fence.

    Models add ```json fences and friendly preambles no matter how firmly the prompt says not to.
    Failing on that would throw away good answers, so this is tolerant about the wrapper and
    strict about the contents.
    """
    stripped = text.strip()
    fenced = re.search(r"```(?:json)?\s*(.+?)```", stripped, re.S)
    if fenced:
        stripped = fenced.group(1).strip()
    start, end = stripped.find("{"), stripped.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError("no JSON object found in the reply")
    return stripped[start : end + 1]


def parse(model: type[BaseModel], text: str) -> BaseModel:
    """Parse a model reply, raising ValueError with something a human can read."""
    try:
        payload = json.loads(extract_json(text))
    except (ValueError, json.JSONDecodeError) as e:
        raise ValueError(f"reply was not valid JSON: {e}") from e
    try:
        return model.model_validate(payload)
    except ValidationError as e:
        problems = "; ".join(f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in e.errors()[:5])
        raise ValueError(f"reply did not match {model.__name__}: {problems}") from e
