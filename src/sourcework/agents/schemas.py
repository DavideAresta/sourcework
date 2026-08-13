"""The wire contract between agents.

Each skill has a request model here. Response models live in
:mod:`sourcework.models` because they are the shared domain objects. Keeping the
requests separate makes it obvious what is an input boundary.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from sourcework.models import (
    Evidence,
    ExtractionResult,
    InputRef,
    PRDDocument,
    RequirementSet,
    ReviewReport,
    SourceDocument,
)

# -- ingestion / vision / transcript ----------------------------------------


class ExtractRequest(BaseModel):
    ref: InputRef
    focus: str | None = None
    """Optional steer, e.g. 'we only care about the payments flow'."""


# -- confluence -------------------------------------------------------------


class ConfluenceSearchRequest(BaseModel):
    cql: str
    limit: int = 10


class ConfluenceHit(BaseModel):
    page_id: str
    title: str
    url: str
    space_key: str | None = None
    excerpt: str | None = None
    last_modified: str | None = None


class ConfluenceSearchResult(BaseModel):
    hits: list[ConfluenceHit] = Field(default_factory=list)
    summary: str = ""


class ConfluenceFetchRequest(BaseModel):
    uri: str
    """``confluence://SPACE/12345``, a browser URL, or a bare page id."""
    include_attachments: bool = False
    focus: str | None = None


class PublishRequest(BaseModel):
    title: str
    storage_xhtml: str
    space_key: str | None = None
    parent_id: str | None = None


class PublishResult(BaseModel):
    page_id: str
    url: str
    version: int
    created: bool
    summary: str = ""


# -- requirements analyst ---------------------------------------------------


class AnalyseRequest(BaseModel):
    title: str
    sources: list[SourceDocument] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    instructions: str | None = None
    audience: str = "engineering and product"
    prior: RequirementSet | None = None
    """The previous version's requirement set, on a refinement run. Present, it
    turns the analyst's job from "produce a requirement set" into "produce the
    next version of this one" - carrying ids forward, closing questions the new
    evidence answers, and revising rather than duplicating."""

    run_id: str | None = None
    """Which run this analysis belongs to, so a slice of the evidence that has
    already been analysed can be written down and read back. The analyst is a
    separate process from the orchestrator and would otherwise have no way to
    know that two calls are the same piece of work."""

    resume: bool = False
    """Reuse slices saved by an earlier attempt at ``run_id``. Same rule as
    everywhere else: saving is unconditional, reusing is asked for."""

    @classmethod
    def from_extractions(
        cls, title: str, extractions: list[ExtractionResult], **kw: object
    ) -> AnalyseRequest:
        return cls(
            title=title,
            sources=[e.source for e in extractions],
            evidence=[item for e in extractions for item in e.evidence],
            **kw,  # type: ignore[arg-type]
        )


# -- PRD writer -------------------------------------------------------------


class WriteRequest(BaseModel):
    title: str
    requirement_set: RequirementSet
    sources: list[SourceDocument] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    audience: str = "engineering and product"
    template: str = "standard"
    instructions: str | None = None
    revision_notes: list[str] = Field(default_factory=list)
    """Findings from a previous review round, folded into the next draft."""


class WriteResult(BaseModel):
    prd: PRDDocument
    markdown: str
    confluence_storage: str
    summary: str = ""


# -- critic -----------------------------------------------------------------


class ReviewRequest(BaseModel):
    prd: PRDDocument
    markdown: str | None = None
    rubric: str | None = None


class ReviewResponse(BaseModel):
    report: ReviewReport
    summary: str = ""
    verdict: str = "needs_revision"
