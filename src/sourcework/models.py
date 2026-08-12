"""The shared vocabulary of the system.

Every A2A payload that crosses an agent boundary is one of these models,
serialised as a JSON DataPart. Agents never exchange free-form prose as their
primary channel - prose is always carried *inside* a typed field.

The central design commitment is **traceability**: an ``Evidence`` record is
the only thing that may enter the pipeline, and every ``Requirement`` must
point back at the evidence that justifies it. That is what lets the critic
agent detect unsupported claims and what produces the traceability matrix at
the bottom of the PRD.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime
from enum import Enum

from pydantic import BaseModel, Field, field_validator

from sourcework.config import LLMOverrides


def _now() -> datetime:
    return datetime.now(UTC)


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


class Modality(str, Enum):
    DOCUMENT = "document"
    TRANSCRIPT = "transcript"
    IMAGE = "image"
    CONFLUENCE = "confluence"
    FREETEXT = "freetext"
    SPREADSHEET = "spreadsheet"


class SourceDocument(BaseModel):
    """A thing that was fed in. Never mutated after ingestion."""

    id: str = Field(default_factory=lambda: new_id("src"))
    uri: str
    title: str
    modality: Modality
    media_type: str | None = None
    byte_size: int | None = None
    checksum: str | None = None
    retrieved_at: datetime = Field(default_factory=_now)
    metadata: dict[str, object] = Field(default_factory=dict)

    @staticmethod
    def checksum_of(data: bytes) -> str:
        return hashlib.sha256(data).hexdigest()[:32]


class Evidence(BaseModel):
    """An atomic, quotable claim lifted out of a source.

    ``locator`` is deliberately a free string because it means something
    different per modality: ``p.12`` for a PDF, ``00:14:32`` for a transcript,
    ``slide 4`` for a deck, ``region: top-left`` for an image, ``#heading`` for
    a Confluence page. It is shown verbatim to the reader in the PRD.
    """

    id: str = Field(default_factory=lambda: new_id("ev"))
    source_id: str
    modality: Modality
    text: str
    locator: str | None = None
    speaker: str | None = None
    kind: str = "statement"
    """statement | decision | action_item | constraint | question | metric"""
    confidence: float = 1.0

    @field_validator("confidence")
    @classmethod
    def _clamp(cls, v: float) -> float:
        return max(0.0, min(1.0, v))


class ExtractionResult(BaseModel):
    """What every ingestion-family agent returns."""

    source: SourceDocument
    evidence: list[Evidence] = Field(default_factory=list)
    summary: str = ""
    warnings: list[str] = Field(default_factory=list)


class Priority(str, Enum):
    MUST = "must"
    SHOULD = "should"
    COULD = "could"
    WONT = "wont"


class ReqKind(str, Enum):
    FUNCTIONAL = "functional"
    NON_FUNCTIONAL = "non_functional"
    CONSTRAINT = "constraint"
    ASSUMPTION = "assumption"
    OUT_OF_SCOPE = "out_of_scope"


class SourceRef(BaseModel):
    evidence_id: str
    source_id: str
    locator: str | None = None
    quote: str | None = None


class Requirement(BaseModel):
    id: str
    """Stable, human-facing: REQ-001. Assigned by the requirements agent."""
    title: str
    statement: str
    kind: ReqKind = ReqKind.FUNCTIONAL
    priority: Priority = Priority.SHOULD
    rationale: str | None = None
    acceptance_criteria: list[str] = Field(default_factory=list)
    source_refs: list[SourceRef] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    confidence: float = 0.8
    derived: bool = False
    """True when the requirement was inferred rather than stated. Surfaced in
    the PRD so a reader knows what the model invented versus what was said."""


class Conflict(BaseModel):
    requirement_ids: list[str]
    description: str
    resolution_hint: str | None = None


class OpenQuestion(BaseModel):
    id: str = Field(default_factory=lambda: new_id("q"))
    question: str
    why_it_matters: str | None = None
    blocking: bool = False
    source_refs: list[SourceRef] = Field(default_factory=list)


class RequirementSet(BaseModel):
    requirements: list[Requirement] = Field(default_factory=list)
    conflicts: list[Conflict] = Field(default_factory=list)
    open_questions: list[OpenQuestion] = Field(default_factory=list)
    glossary: dict[str, str] = Field(default_factory=dict)

    def by_id(self, req_id: str) -> Requirement | None:
        return next((r for r in self.requirements if r.id == req_id), None)


class UserStory(BaseModel):
    id: str
    persona: str
    want: str
    so_that: str
    requirement_ids: list[str] = Field(default_factory=list)

    def as_sentence(self) -> str:
        return f"As a {self.persona}, I want {self.want}, so that {self.so_that}."


class Metric(BaseModel):
    name: str
    definition: str
    baseline: str | None = None
    target: str | None = None


class Risk(BaseModel):
    description: str
    impact: str = "medium"
    likelihood: str = "medium"
    mitigation: str | None = None


class Milestone(BaseModel):
    name: str
    description: str | None = None
    requirement_ids: list[str] = Field(default_factory=list)
    target: str | None = None


class PRDSection(BaseModel):
    """Free-form narrative section, rendered between the fixed ones."""

    heading: str
    body_markdown: str
    level: int = 2


class PRDDocument(BaseModel):
    id: str = Field(default_factory=lambda: new_id("prd"))
    title: str
    status: str = "draft"
    version: str = "0.1.0"
    generated_at: datetime = Field(default_factory=_now)
    authors: list[str] = Field(default_factory=list)

    summary: str = ""
    problem_statement: str = ""
    background: str = ""
    goals: list[str] = Field(default_factory=list)
    non_goals: list[str] = Field(default_factory=list)
    personas: list[str] = Field(default_factory=list)
    user_stories: list[UserStory] = Field(default_factory=list)

    requirements: RequirementSet = Field(default_factory=RequirementSet)
    metrics: list[Metric] = Field(default_factory=list)
    risks: list[Risk] = Field(default_factory=list)
    milestones: list[Milestone] = Field(default_factory=list)
    extra_sections: list[PRDSection] = Field(default_factory=list)

    sources: list[SourceDocument] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)

    def evidence_by_id(self) -> dict[str, Evidence]:
        return {e.id: e for e in self.evidence}

    def source_by_id(self) -> dict[str, SourceDocument]:
        return {s.id: s for s in self.sources}


class Severity(str, Enum):
    BLOCKER = "blocker"
    MAJOR = "major"
    MINOR = "minor"
    NIT = "nit"


class ReviewFinding(BaseModel):
    id: str = Field(default_factory=lambda: new_id("find"))
    severity: Severity
    category: str
    """unsupported | ambiguous | untestable | missing | contradiction | scope"""
    location: str
    """Section heading or REQ id the finding attaches to."""
    detail: str
    suggested_fix: str | None = None


class ReviewReport(BaseModel):
    findings: list[ReviewFinding] = Field(default_factory=list)
    coverage: dict[str, float] = Field(default_factory=dict)
    verdict: str = "needs_revision"
    """approved | needs_revision | reject"""

    @property
    def blocking(self) -> list[ReviewFinding]:
        return [f for f in self.findings if f.severity in (Severity.BLOCKER, Severity.MAJOR)]


class InputRef(BaseModel):
    """One item the user handed to the orchestrator."""

    uri: str
    """file:///..., https://..., confluence://SPACE/12345, or inline:text"""
    title: str | None = None
    modality: Modality | None = None
    media_type: str | None = None
    content_b64: str | None = None
    """Inline bytes, for callers who cannot expose a fetchable URI."""
    text: str | None = None
    notes: str | None = None


class PRDBaseline(BaseModel):
    """A previously generated PRD, handed back in to be refined.

    A refinement is a new run, not an edit: the old PRD stays exactly as it was
    and the new one records what it was built from. That is the same argument
    the rest of the system makes about evidence - a document you cannot trace
    is a document you cannot trust - and it is why this travels in the request
    rather than living as mutable state anywhere.

    Carrying the evidence forward also means the prior sources are *not*
    re-ingested. Re-reading them would cost the tokens again and, worse, mint
    fresh evidence ids, so every citation in the existing PRD would break.
    """

    run_id: str | None = None
    sources: list[SourceDocument] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    requirements: RequirementSet | None = None
    """What the previous run concluded. The analyst sees this so it can carry
    requirements forward under their existing ids instead of renumbering, and
    so it knows which open questions the new material answers."""


class PRDRequest(BaseModel):
    """The orchestrator's entry payload."""

    title: str
    inputs: list[InputRef] = Field(default_factory=list)
    confluence_queries: list[str] = Field(default_factory=list)
    """CQL strings whose results are pulled in as additional sources."""
    audience: str = "engineering and product"
    template: str = "standard"
    publish: bool = False
    confluence_space_key: str | None = None
    confluence_parent_id: str | None = None
    review_rounds: int = 1
    extra_instructions: str | None = None
    llm: LLMOverrides | None = None
    """Model settings for this run only, overriding each agent's environment.
    Lets a caller pick the backend and per-role models without restarting the
    mesh - see :class:`sourcework.config.LLMOverrides`."""

    baseline: PRDBaseline | None = None
    """Refine an existing PRD rather than starting from nothing: its evidence
    is carried in, ``inputs`` supplies whatever is new, and the analyst
    reconciles the two."""


class PRDResult(BaseModel):
    prd: PRDDocument
    markdown: str
    confluence_storage: str | None = None
    review: ReviewReport | None = None
    published_url: str | None = None
    stats: dict[str, object] = Field(default_factory=dict)
