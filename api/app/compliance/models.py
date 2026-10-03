"""Pydantic request/response models for the API."""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class RuleIn(BaseModel):
    rule_id: str
    rule_name: str
    category: str = "general"
    rule_type: str = "internal_policy"
    regulation: str = ""
    regulation_ref: str = ""
    remediation: str = ""
    required_fields: list[str] = Field(default_factory=list)
    condition: dict
    applies_when: Optional[dict] = None


class DocumentIn(BaseModel):
    """Ingest real regulatory text (pasted or uploaded as .txt/.md)."""
    title: str
    category: str = "General"
    source_type: str = "RBI"
    effective_date: Optional[str] = None
    text: str  # full document text; chunked server-side


class LoanApplication(BaseModel):
    application_id: str
    loan_type: str = "personal_loan"
    customer: dict = Field(default_factory=dict)
    documents: dict = Field(default_factory=dict)
    loan_terms: dict = Field(default_factory=dict)
    risk: dict = Field(default_factory=dict)


class RagQuery(BaseModel):
    query: str
    top_k: int = 5
    # true = the Regulatory Q&A agent writes a cited answer from the passages
    # (kept only if grounded); false = the top passage verbatim (pre-agent)
    synthesize: bool = False


class ExtractRequest(BaseModel):
    prefer_llm: bool = True


class ApproveCandidate(BaseModel):
    """Reviewer ('checker') decision. Any provided field overrides the
    extractor's proposal; `condition` must have its placeholder field mapped."""
    reviewer: str = "compliance_officer"
    rule_id: Optional[str] = None
    rule_name: Optional[str] = None
    rule_type: Optional[str] = None
    category: Optional[str] = None
    regulation: Optional[str] = None
    regulation_ref: Optional[str] = None
    remediation: Optional[str] = None
    required_fields: Optional[list[str]] = None
    condition: Optional[dict] = None
    applies_when: Optional[dict] = None


class RejectCandidate(BaseModel):
    reviewer: str = "compliance_officer"
    note: str = ""
