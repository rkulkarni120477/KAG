"""Illustrative Pydantic 2 API/domain contracts; not authentication middleware."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class AuthorizedScope(StrictModel):
    """Created only after trusted authentication and a fresh policy decision."""

    tenant_id: UUID
    principal_id: UUID
    purpose: str = Field(min_length=1, max_length=120)
    allowed_student_ids: frozenset[UUID] = frozenset()
    acl_group_ids: frozenset[str] = frozenset()
    policy_version: str
    expires_at: datetime


class Manifest(StrictModel):
    tenant_id: UUID
    manifest_id: UUID
    source_snapshot: str
    graph_generation: str
    vector_collection: str
    vector_generation: str
    embedding_revision: str
    chunker_revision: str
    rule_version: str


class QueryRequest(StrictModel):
    # Tenant and authorization fields intentionally absent from client input.
    question: str = Field(min_length=1, max_length=8000)
    student_id: UUID | None = None
    state: str | None = Field(default=None, pattern=r"^[A-Z]{2}$")
    grade: str | None = Field(default=None, max_length=30)
    course_id: UUID | None = None
    academic_year: str | None = Field(default=None, max_length=20)
    stream: bool = False


class Evidence(StrictModel):
    evidence_id: UUID
    entity_id: UUID
    source_version: str
    source_title: str
    locator: str  # Page/section; resolve links through authorized evidence API.
    content_hash: str
    excerpt: str
    effective_at: datetime | None = None


class Claim(StrictModel):
    text: str
    category: Literal["authoritative_fact", "rule_result", "ai_inference"]
    evidence_ids: list[UUID]
    rule_id: str | None = None


class RuleResult(StrictModel):
    rule_id: str
    rule_version: str
    outcome: Literal["satisfied", "not_satisfied", "unknown"]
    evidence_ids: list[UUID]
    missing_evidence: list[str]


class QueryResponse(StrictModel):
    request_id: UUID
    manifest_id: UUID
    claims: list[Claim]
    evidence: list[Evidence]
    rule_results: list[RuleResult]
    completeness: Literal["high", "moderate", "insufficient"]
    limitations: list[str]
    student_facts_as_of: datetime | None = None
    model_id: str | None = None  # Null for deterministic fallback.
    advisory_only: Literal[True] = True


# Validate evidence IDs and claim support against the authorized evidence bundle
# in domain code. A schema alone cannot establish grounding or access rights.
