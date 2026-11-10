"""Typed API contracts used by the application.

- Request models reject unknown fields (extra="forbid").
- Response models document and validate what the API returns.
- AIDraft validates model output before any of it reaches a user; a schema alone does not establish
  grounding, so citation checks against the authorized evidence remain in kag.py.
"""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class QueryRequest(Strict):
    question: str = Field(min_length=3, max_length=4000)
    student_id: str | None = Field(default=None, max_length=100)
    stream: bool = False


class RecommendationRequest(Strict):
    goal: str = Field(default="Prepare for Algebra I", min_length=3, max_length=4000)
    student_id: str | None = Field(default=None, max_length=100)


class FeedbackRequest(Strict):
    request_id: str = Field(max_length=100)
    rating: int = Field(ge=1, le=5)
    comment: str = Field(default="", max_length=2000)


class LoginRequest(Strict):
    email: str = Field(min_length=3, max_length=200)
    password: str = Field(min_length=1, max_length=200)


class IngestRequest(Strict):
    title: str = Field(min_length=3, max_length=180)
    text: str = Field(min_length=30, max_length=50000)
    skill_id: str | None = Field(default=None, max_length=100)
    idempotency_key: str = Field(min_length=8, max_length=100)


class WaiverRequest(Strict):
    skill_id: str = Field(max_length=100)
    reason: str = Field(min_length=3, max_length=500)
    effective_from: str = Field(max_length=40)
    effective_until: str | None = Field(default=None, max_length=40)


class User(BaseModel):
    id: str
    tenant_id: str
    name: str
    email: str
    role: Literal["student", "teacher", "advisor", "admin"]


class Citation(BaseModel):
    id: str
    title: str
    source_version: str
    locator: str
    source_uri: str
    excerpt: str
    illustrative: bool


class RequirementState(BaseModel):
    id: str
    met: bool


class RequirementGroup(BaseModel):
    group_id: str
    logic: Literal["ALL", "ANY"]
    met: bool
    requires: list[RequirementState]


class PathStep(BaseModel):
    id: str
    name: str
    status: Literal["satisfied", "not_satisfied", "unknown"]
    reason: str
    ready: bool
    waived: bool
    blocked_by: list[str]
    requirement_groups: list[RequirementGroup]


class QueryResponse(BaseModel):
    answer: str
    ai_explanation: str | None
    evidence: list[Citation]
    steps: list[PathStep]
    generator: str
    warning: str | None
    manifest: int
    completeness: Literal["partial", "supported", "insufficient"]
    advisory_only: Literal[True]
    notice: str
    request_id: str


class RosterItem(BaseModel):
    id: str
    name: str
    grade: int


class AIDraft(BaseModel):
    """Structured output the model must return. Any other shape falls back to evidence-only answers."""
    model_config = ConfigDict(extra="forbid")
    explanation: str = Field(min_length=1, max_length=6000)
    evidence_ids: list[str] = Field(min_length=1)
