"""Milestone 1 objects. No extra status or source class is permitted."""
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator, model_validator

from veryjudge.anchors import parse_anchor


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Status(str, Enum):
    SUPPORTED = "SUPPORTED"
    CONTRADICTED = "CONTRADICTED"
    NOT_ESTABLISHED = "NOT_ESTABLISHED"
    CONFLICTING = "CONFLICTING"
    NOT_CHECKABLE = "NOT_CHECKABLE"
    PENDING_REVIEW = "PENDING_REVIEW"


class SourceClass(str, Enum):
    primary_record = "primary_record"
    official_statement = "official_statement"
    direct_witness = "direct_witness"
    original_reporting = "original_reporting"
    wire = "wire"
    republication = "republication"
    fact_check = "fact_check"
    reference = "reference"
    user_generated = "user_generated"


class PropositionType(str, Enum):
    factual = "factual"
    opinion = "opinion"
    prediction = "prediction"
    satire = "satire"
    vague = "vague"
    mixed = "mixed"


class ClaimType(str, Enum):
    statistic = "statistic"
    law = "law"
    event = "event"
    quote = "quote"
    other = "other"


class HighRiskCategory(str, Enum):
    health = "health"
    elections = "elections"
    disaster = "disaster"
    private_individual = "private_individual"
    crime_accusation = "crime_accusation"
    listed_company_or_market = "listed_company_or_market"
    physical_safety = "physical_safety"


class Relationship(str, Enum):
    supports = "supports"
    contradicts = "contradicts"
    contextualizes = "contextualizes"


class Strength(str, Enum):
    direct = "direct"
    indirect = "indirect"
    weak = "weak"


class Submission(Model):
    id: str
    text: str
    text_sha256: str
    extraction_status: Literal["ok", "failed"]
    extraction_error: Optional[str] = None
    created_at: datetime


class ExtractedProposition(Model):
    text: str
    proposition_type: PropositionType
    type_reason: str
    claim_type: ClaimType
    time_anchor: Optional[str] = None
    entities: list[str] = Field(default_factory=list)
    high_risk_category: Optional[HighRiskCategory] = None
    suggested_material: bool = False

    @field_validator("time_anchor", mode="before")
    @classmethod
    def valid_anchor_or_null(cls, value):
        return value if parse_anchor(value) else None


class Proposition(ExtractedProposition):
    id: str
    submission_id: str
    ordinal: int
    original_text: str
    text_normalized: str
    operator_edited: bool = False
    selected: bool = False
    extraction_flag: bool = False
    created_at: Optional[datetime] = None

    @computed_field
    @property
    def high_risk(self) -> bool:
        return self.high_risk_category is not None


class RunnerConfig(Model):
    max_searches: int = Field(default=8, ge=1, le=8)
    max_captures: int = Field(default=10, ge=1, le=10)
    max_fetch_attempts: int = Field(default=15, ge=1, le=15)
    wall_clock_s: float = Field(default=180, gt=0, le=180)


class ResearchRun(Model):
    id: str
    proposition_id: str
    arm: Literal["A"] = "A"
    runner_version: str = "0.1.0"
    model: str
    config: RunnerConfig = Field(default_factory=RunnerConfig)
    status: Literal["running", "done", "failed", "timeout"]
    stop_reason: Optional[Literal["sufficient_primary", "contradiction_found", "budget_exhausted", "timeout", "no_candidates"]] = None
    failure_reason: Optional[str] = None
    rejected_evidence_count: int = 0
    token_in: int = 0
    token_out: int = 0
    cost_usd: Decimal = Decimal("0")
    started_at: datetime
    finished_at: Optional[datetime] = None


class SearchScope(Model):
    site_restrict: Optional[list[str]] = None
    max_results: int = Field(default=10, ge=1, le=10)


class SearchResult(Model):
    url: str
    title: str
    snippet: str
    published_hint: Optional[str] = None
    provider: str
    rank: int


class RetrievalStep(Model):
    id: str
    run_id: str
    step_number: int
    provider: str
    record_key: Optional[str] = None
    query: str
    language: Literal["en", "fil"]
    site_restrict: Optional[list[str]] = None
    results: list[SearchResult]
    result_count: int
    planner_reason: str
    created_at: Optional[datetime] = None


class Paragraph(Model):
    index: int = Field(ge=0)
    start: int = Field(ge=0)
    end: int = Field(ge=0)

    @model_validator(mode="after")
    def ordered(self):
        if self.end < self.start:
            raise ValueError("paragraph end precedes start")
        return self


class FetchResult(Model):
    url: str
    final_url: Optional[str] = None
    http_status: Optional[int] = None
    fetch_status: Literal["ok", "failed"]
    failure_reason: Optional[str] = None
    content_type: Optional[str] = None
    html_path: Optional[str] = None
    text: Optional[str] = None
    text_sha256: Optional[str] = None
    paragraphs: list[Paragraph] = Field(default_factory=list)
    title: Optional[str] = None
    published_at: Optional[datetime] = None
    fetched_at: datetime

    @model_validator(mode="after")
    def capture_consistency(self):
        if (self.fetch_status == "ok") != (self.text is not None):
            raise ValueError("ok captures require text; failed captures have no text")
        if self.text is not None:
            if " ".join(self.text.split()) != self.text:
                raise ValueError("stored text must already be whitespace-normalized")
            previous = 0
            indices = set()
            for paragraph in self.paragraphs:
                if paragraph.index in indices or paragraph.start < previous or paragraph.end > len(self.text):
                    raise ValueError("invalid paragraph offsets")
                indices.add(paragraph.index)
                previous = paragraph.end
        return self


class Ancestry(Model):
    role: Literal["originator", "carrier"] = "originator"
    root_document_id: Optional[str] = None


class Document(FetchResult):
    id: str
    run_id: str
    retrieval_step_id: Optional[str] = None
    discovered_from_document_id: Optional[str] = None
    operator_added: bool = False
    ancestry: Ancestry = Field(default_factory=Ancestry)

    @model_validator(mode="after")
    def provenance_and_ancestry(self):
        if not (self.retrieval_step_id or self.discovered_from_document_id or self.operator_added):
            raise ValueError("capture provenance required")
        if self.ancestry.root_document_id == self.id:
            raise ValueError("document cannot be its own root")
        return self


class EvidenceCandidate(Model):
    paragraph_index: int = Field(ge=0)
    excerpt: str
    source_class: SourceClass
    attests_own_act: Optional[bool] = Field(default=None, strict=True)
    attribution: Optional[Literal["named_record", "named_person", "unnamed", "none"]] = None
    relationship: Relationship
    strength: Strength
    contradiction_route: Optional[Literal["A", "B", "C"]] = None
    impossibility_note: Optional[str] = None
    closure_record_key: Optional[str] = None
    closure_period: Optional[str] = None
    limitations: Optional[str] = None
    rationale: str


class Locator(Model):
    type: Literal["text_span"] = "text_span"
    start: int = Field(ge=0)
    end: int = Field(ge=0)
    paragraph_index: int = Field(ge=0)

    @model_validator(mode="after")
    def nonempty(self):
        if self.end <= self.start:
            raise ValueError("locator_end must exceed locator_start")
        return self


class DiscoveredBy(Model):
    run_id: str
    step_id: Optional[str] = None
    provider: Optional[str] = None
    query: Optional[str] = None


class Evidence(Model):
    id: str
    proposition_id: str
    run_id: str
    document_id: str
    retrieval_step_id: Optional[str] = None
    operator_added: bool = False
    locator: Locator
    excerpt: str = Field(min_length=25, max_length=300)
    source_class: SourceClass
    attests_own_act: Optional[bool] = Field(default=None, strict=True)
    attribution: Optional[Literal["named_record", "named_person", "unnamed", "none"]] = None
    relationship: Relationship
    strength: Strength
    contradiction_route: Optional[Literal["A", "B", "C"]] = None
    impossibility_note: Optional[str] = None
    closure_record_key: Optional[str] = None
    closure_period: Optional[str] = None
    limitations: Optional[str] = None
    model_rationale: str
    discovered_by: Optional[DiscoveredBy] = None
    removed_by_operator: bool = False
    extraction_flag: bool = False
    created_at: Optional[datetime] = None


class Sentence(Model):
    text: str
    kind: Literal["factual", "meta"]
    evidence_ids: list[str]

    @model_validator(mode="after")
    def kind_ids(self):
        if self.kind == "factual" and not self.evidence_ids:
            raise ValueError("factual sentence requires evidence")
        if self.kind == "meta" and self.evidence_ids:
            raise ValueError("meta sentence cannot cite evidence")
        return self


class Explanation(Model):
    sentences: list[Sentence]
    what_would_change: list[str]
    limitations: list[str]


class StatusResult(Model):
    status: Status
    route: Optional[Literal["A", "B", "C"]] = None
    provisional_status: Optional[Status] = None
    basis_evidence_ids: list[str] = Field(default_factory=list)
    review_reasons: list[str] = Field(default_factory=list)
    rule_trace: list[str] = Field(default_factory=list)


class AncestryEdit(Model):
    document_id: str
    ancestry_role: Literal["originator", "carrier"]
    root_document_id: Optional[str] = None


class AddedEvidence(Model):
    url: str
    paragraph_index: int = Field(ge=0)
    excerpt: str
    source_class: SourceClass
    relationship: Relationship
    strength: Strength
    attests_own_act: Optional[bool] = Field(default=None, strict=True)
    attribution: Optional[Literal["named_record", "named_person", "unnamed", "none"]] = None
    limitations: Optional[str] = None
    contradiction_route: Optional[Literal["A", "B", "C"]] = None
    impossibility_note: Optional[str] = None
    closure_record_key: Optional[str] = None
    closure_period: Optional[str] = None


FinalStatus = Literal["SUPPORTED", "CONTRADICTED", "NOT_ESTABLISHED", "CONFLICTING", "NOT_CHECKABLE"]
ErrorClass = Literal["missed_primary", "wrong_locator", "overclaim", "false_negative", "bad_proposition", "bad_type", "other"]


class ReviewRequest(Model):
    decision: Literal["approve", "downgrade", "reject"]
    final_status: Optional[FinalStatus] = None
    final_explanation: Optional[Explanation] = None
    removed_evidence_ids: list[str] = Field(default_factory=list)
    flagged_evidence_ids: list[str] = Field(default_factory=list)
    ancestry_edits: list[AncestryEdit] = Field(default_factory=list)
    added_evidence: list[AddedEvidence] = Field(default_factory=list)
    proposition_flag: bool = False
    error_class: Optional[ErrorClass] = None
    machine_error_reason: Optional[str] = None


class Review(Model):
    id: str
    judgment_id: str
    decision: Literal["approve", "downgrade", "reject"]
    status_before: Status
    relicensed_status: Optional[Status] = None
    status_after: Optional[Status] = None
    removed_evidence_ids: list[str] = Field(default_factory=list)
    flagged_evidence_ids: list[str] = Field(default_factory=list)
    added_evidence_ids: list[str] = Field(default_factory=list)
    ancestry_edits: list[AncestryEdit] = Field(default_factory=list)
    explanation_edited: bool
    proposition_flagged: bool = False
    error_class: Optional[ErrorClass] = None
    machine_error_reason: Optional[str] = None
    created_at: datetime


class Judgment(Model):
    id: str
    proposition_id: str
    run_id: Optional[str] = None
    superseded_by: Optional[str] = None
    machine_status: Status
    machine_route: Optional[Literal["A", "B", "C"]] = None
    provisional_status: Optional[Status] = None
    basis_evidence_ids: list[str] = Field(default_factory=list)
    rule_trace: list[str] = Field(default_factory=list)
    review_reasons: list[str] = Field(default_factory=list)
    machine_explanation: Explanation
    relicensed_status: Optional[Status] = None
    relicensed_route: Optional[Literal["A", "B", "C"]] = None
    review_state: Literal["draft", "approved", "rejected"] = "draft"
    final_status: Optional[FinalStatus] = None
    final_explanation: Optional[Explanation] = None
    as_of: datetime
    created_at: Optional[datetime] = None
    evidence: list[Evidence] = Field(default_factory=list)
    documents: list[Document] = Field(default_factory=list)
    trace: dict = Field(default_factory=dict)
    reviews: list[Review] = Field(default_factory=list)


class ResearchTrace(Model):
    run_id: str
    proposition_id: str
    arm: Literal["A"] = "A"
    model: str
    runner_version: str = "0.1.0"
    steps: list[RetrievalStep] = Field(default_factory=list)
    documents: list[Document] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    rejected_evidence_count: int = 0
    stop_reason: Literal["sufficient_primary", "contradiction_found", "budget_exhausted", "timeout", "no_candidates"]
    started_at: datetime
    finished_at: datetime
    token_in: int = 0
    token_out: int = 0
    cost_usd: Decimal = Decimal("0")
