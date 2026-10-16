"""Lightweight pydantic models for request validation / OpenAPI docs.
Analyze/query responses are plain dicts (the canonical candidate-profile schema
lives in the pipeline)."""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, ConfigDict, model_validator


class HealthResponse(BaseModel):
    status: str
    service: str
    version: str
    llm_mode: str
    ocr: dict[str, Any]
    models: dict[str, Any]
    privacy: dict[str, Any]
    sessions: dict[str, Any]
    time: str


class ErrorEnvelope(BaseModel):
    error: dict[str, Any]


class QueryRequest(BaseModel):
    question: str
    options: dict[str, Any] | None = None


class ScoreRequest(BaseModel):
    jd_text: str
    lang: str | None = "en"  # report language: en | ar
    top_n: int | None = None  # table rows shown (None = policy default)


class AuditRequest(BaseModel):
    """Optional input for the four-fifths adverse-impact computation. The service
    never infers demographics: group labels come from the operator."""

    # group labels such as {"age_band": 3} are accepted and compared as text
    model_config = ConfigDict(coerce_numbers_to_str=True)

    labels: dict[str, dict[str, str]] | None = None  # candidate_id -> {axis: group}
    selected: dict[str, bool] | None = None  # candidate_id -> selected?

    @model_validator(mode="after")
    def _both_or_neither(self) -> AuditRequest:
        if (self.labels is None) != (self.selected is None):
            raise ValueError("'labels' and 'selected' must be supplied together")
        return self


def parse_options(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        v = json.loads(raw)
        return v if isinstance(v, dict) else {}
    except (ValueError, TypeError):
        return {}
