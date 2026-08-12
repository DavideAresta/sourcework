"""Shared evidence-extraction logic.

Four agents (documents, transcripts, images, Confluence) all do the same core
job: take content, return :class:`Evidence`. Only the prompt framing and the
locator semantics differ, so the loop lives here once.

The contract with the model is narrow on purpose - it returns claims, not
requirements. Turning claims into requirements is the requirements analyst's
job, and keeping the two apart is what stops the pipeline inventing scope
during ingestion.
"""

from __future__ import annotations

import logging

from pydantic import BaseModel, Field

from prdforge.llm import LLM, ImageInput
from prdforge.models import Evidence, Modality, SourceDocument

logger = logging.getLogger(__name__)

Block = tuple[str, str]


class EvidenceItem(BaseModel):
    text: str = Field(description="The claim, restated as one self-contained sentence.")
    locator: str | None = Field(
        default=None,
        description="Copy the [[locator]] of the block this came from, verbatim.",
    )
    speaker: str | None = Field(default=None, description="Who said it, if known.")
    kind: str = Field(
        default="statement",
        description="One of: statement, decision, action_item, constraint, question, metric.",
    )
    confidence: float = Field(default=0.8, ge=0.0, le=1.0)


class EvidenceDraft(BaseModel):
    summary: str = Field(description="Two or three sentences on what this source is about.")
    items: list[EvidenceItem] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


BASE_SYSTEM = """You extract requirement-bearing evidence from source material for a PRD.

Rules, in priority order:
1. Extract only what the source actually says. Never infer, never extrapolate,
   never fill gaps with domain knowledge. A later agent does the inferring.
2. One claim per item, restated as a single self-contained sentence that makes
   sense without its surrounding context.
3. Copy the [[locator]] marker of the block a claim came from into `locator`,
   exactly as given. This is what lets a reader verify the claim.
4. Keep anything that constrains, scopes, or measures the product: features,
   behaviours, rules, limits, SLAs, integrations, compliance obligations,
   decisions taken, explicit non-goals, numbers and dates.
5. Drop pleasantries, scheduling chatter, navigation furniture and boilerplate.
6. Classify each item with `kind`. Prefer `decision` for anything settled and
   `constraint` for anything that limits the solution space.
7. If the source is ambiguous, keep the claim and lower `confidence`. Put the
   ambiguity in `warnings` rather than resolving it yourself.
"""

MODALITY_HINTS = {
    Modality.DOCUMENT: (
        "This is a document. Locators look like `p.12`, `slide 4` or `heading: Scope`. "
        "Requirement language in specs is often normative - treat 'must', 'shall', "
        "'should' and 'may' as significant and preserve the exact modal verb."
    ),
    Modality.TRANSCRIPT: (
        "This is a meeting transcript. Locators are timestamps. Attribute every claim "
        "to its speaker. Distinguish sharply between someone floating an idea and the "
        "group deciding something - only the latter is `decision`. Capture action "
        "items with their owner. Disagreement that was never resolved is a `question`."
    ),
    Modality.IMAGE: (
        "This is an image: a screenshot, mockup, whiteboard photo or diagram. Describe "
        "only what is visibly present. Locators name a region, e.g. `top-left panel`, "
        "`step 3 of flow`. Transcribe visible labels and text verbatim. Do not guess "
        "at behaviour that is not shown."
    ),
    Modality.CONFLUENCE: (
        "This is an existing Confluence page. Locators are section headings. Existing "
        "documentation is often stale - flag anything that reads as outdated or as a "
        "decision that may since have changed."
    ),
    Modality.SPREADSHEET: (
        "This is tabular data. Locators name the sheet and row range. Capture the "
        "schema and any rows that encode rules, limits or thresholds."
    ),
    Modality.FREETEXT: "This is free-form notes pasted by a user.",
}


async def extract_evidence(
    llm: LLM,
    source: SourceDocument,
    blocks: list[Block],
    *,
    focus: str | None = None,
    images: list[ImageInput] | None = None,
    role: str = "default",
    max_chars_per_batch: int = 12000,
) -> tuple[list[Evidence], str, list[str]]:
    """Run the extraction over ``blocks``, batching to stay inside context.

    Returns ``(evidence, summary, warnings)``.
    """
    system = BASE_SYSTEM + "\n" + MODALITY_HINTS.get(source.modality, "")
    if focus:
        system += (
            f"\n\nThe reader cares specifically about: {focus}\n"
            "Still extract everything requirement-bearing, but rank the relevant "
            "material first and lower confidence on the peripheral."
        )

    batches = _batch(blocks, max_chars_per_batch)
    if not batches and images:
        batches = [[("image", "(see attached image)")]]

    evidence: list[Evidence] = []
    summaries: list[str] = []
    warnings: list[str] = []

    for index, batch in enumerate(batches, start=1):
        rendered = "\n\n".join(f"[[{loc}]]\n{text}" for loc, text in batch)
        user = (
            f"Source: {source.title} ({source.modality.value})\n"
            f"Part {index} of {len(batches)}.\n\n<<<CONTENT>>>\n{rendered}"
        )
        try:
            draft = await llm.structured(
                system,
                user,
                EvidenceDraft,
                images=images if index == 1 else None,
                role=role,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("extraction failed on batch %d of %s", index, source.title)
            warnings.append(f"batch {index} failed: {type(exc).__name__}: {exc}")
            continue

        summaries.append(draft.summary)
        warnings.extend(draft.warnings)
        known_locators = {loc for loc, _ in batch}
        for item in draft.items:
            if not item.text.strip():
                continue
            locator = item.locator if item.locator in known_locators else (batch[0][0] if batch else None)
            evidence.append(
                Evidence(
                    source_id=source.id,
                    modality=source.modality,
                    text=item.text.strip(),
                    locator=locator,
                    speaker=item.speaker,
                    kind=item.kind,
                    confidence=item.confidence,
                )
            )

    return evidence, " ".join(summaries).strip(), warnings


def _batch(blocks: list[Block], max_chars: int) -> list[list[Block]]:
    batches: list[list[Block]] = []
    current: list[Block] = []
    size = 0
    for block in blocks:
        if current and size + len(block[1]) > max_chars:
            batches.append(current)
            current, size = [], 0
        current.append(block)
        size += len(block[1])
    if current:
        batches.append(current)
    return batches
