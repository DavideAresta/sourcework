"""Requirements analyst agent (port 8005).

The pivot of the whole system. Everything upstream produces evidence; this
agent turns evidence into a normalised, de-duplicated, prioritised, conflict-
checked requirement set, and it is the only place allowed to *infer* anything.

Two safeguards are enforced in code rather than left to the prompt:

* every ``source_ref`` is checked against the real evidence ids, and invented
  ones are stripped. A model that hallucinates a citation loses the citation.
* a requirement left with no valid refs is forced to ``derived=True`` so the
  PRD renders it as an inference rather than a sourced fact.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

from pydantic import BaseModel, Field

from prdforge.a2a_common import Progress, SkillError, SkillExecutor, build_card, public_url, skill
from prdforge.agents.schemas import AnalyseRequest
from prdforge.config import effective_llm
from prdforge.llm import LLM, register_stub
from prdforge.models import (
    Conflict,
    Evidence,
    OpenQuestion,
    Priority,
    ReqKind,
    Requirement,
    RequirementSet,
    SourceRef,
)

logger = logging.getLogger(__name__)

PORT = 8005

MAX_CONCURRENT_SLICES = 3
"""Slices analysed at once. These are subprocesses on a personal subscription,
so the ceiling is about not tripping a rate limit, not about CPU."""

SYSTEM = """You are a senior requirements analyst. You receive evidence items
gathered from documents, meeting transcripts, images and wiki pages, and you
produce one normalised requirement set.

Method:
1. Cluster evidence that describes the same underlying need, even when the
   wording differs across sources. One requirement per need, not per mention.
2. Write each requirement as a single testable statement. "The system must X
   when Y" beats "improve X". If a statement cannot be tested, it is not a
   requirement - make it an assumption, a goal, or an open question.
3. Cite evidence. Every requirement carries `evidence_ids` listing the ids it
   was derived from. Use the exact ids given to you. Never invent an id.
4. Set `derived: true` on anything you inferred rather than found. Be honest
   here; a reader needs to know what to double-check.
5. Prioritise with MoSCoW, and justify the priority from the evidence - explicit
   "must" language, a decision recorded in a meeting, a compliance obligation.
   Where the evidence gives no signal, default to `should` and lower confidence.
6. Write acceptance criteria as observable outcomes, not implementation steps.
7. Detect conflicts: two sources demanding incompatible behaviour, a meeting
   decision that contradicts an older document, a number that changed. Do not
   silently pick a winner - record the conflict and suggest how to resolve it.
   Prefer more recent evidence but say that you did.
8. Anything material that the evidence leaves undetermined becomes an open
   question. Mark it blocking if the team cannot build without the answer.
9. Build a glossary for domain terms that appear without definition.

Do not pad. A short, well-sourced set beats a long speculative one.
"""

REFINEMENT = """
=== THIS IS A REFINEMENT ===

You are not starting from nothing. A previous version of this requirement set
is given below, and the evidence now includes both the original material and
whatever has arrived since. Produce the NEXT VERSION of that set, not a fresh
one.

10. Carry every still-valid requirement forward, and put its existing id in
    `existing_id`. Keep the id even when you revise the wording, the priority
    or the acceptance criteria - the id identifies the *need*, not the
    sentence. A reader has the old document open and tickets that quote these
    ids; silently renumbering them is worse than any wording improvement.
11. Omit a requirement only if the new evidence genuinely retires it, and say
    so in your summary. Do not drop something merely because it did not come
    up again.
12. Leave `existing_id` empty only for a genuinely new need. Two requirements
    must never claim the same id.
13. Open questions: where the new evidence answers one, do not carry it
    forward - fold the answer into the affected requirements and cite the new
    evidence. Where it is still open, keep it. An answered question that
    reappears in the output tells the reader nothing was read.
14. Conflicts already recorded: if the new evidence resolves one, apply the
    resolution and drop the conflict. If it deepens one, keep it and update
    the description.
"""


SLICE = """
=== THIS IS ONE SLICE OF THE EVIDENCE ===

The evidence set is too large to analyse in one pass, so you are seeing a
subset of it. Work only from what is in front of you, and adjust three things:

A. Do not raise an open question merely because context seems to be missing.
   Another slice probably has it. Raise one only where the material you CAN see
   is itself indeterminate.
B. Do not record a conflict against material you cannot see. Conflicts that
   span slices are found in a later pass that sees every requirement at once.
C. Cite only ids present in this slice.

Duplicates across slices are expected and are merged later - do not try to
guess what another slice already covered.
"""

MERGE_SYSTEM = """You are consolidating several partial passes over one
evidence set into a single requirement set.

You are given every requirement each pass produced, keyed D-001, D-002, ... and
the open questions they raised, keyed Q-001, Q-002, ... You do NOT get the
evidence back; you are working on the requirements themselves.

Do four things:

1. **Merge duplicates.** The passes saw overlapping material, so the same need
   often appears more than once in different words. Group the keys that
   describe one need. A group of one is not a group - only list real
   duplicates. Where the wordings differ, supply the title and statement the
   merged requirement should carry; leave them out to keep the first one's.
   Requirements that merely relate to each other are NOT duplicates. Merging
   two distinct needs loses one of them permanently.

2. **Find conflicts.** Two requirements demanding incompatible behaviour, the
   same quantity with two values, a decision contradicted elsewhere. This is
   the pass that can see them: each slice saw only its own material. Name the
   keys involved and say how to resolve it - do not silently pick a winner.

3. **Settle the open questions.** Drop any that another pass's requirements
   answer. Keep the rest, and mark blocking the ones the team cannot build
   without. Rewrite them so they read as one list rather than several.

4. **Merge the glossary**, preferring the clearer definition where two passes
   defined the same term.

Do not invent requirements here, and do not restate every requirement - only
the merged wording for groups you are actually merging.
"""


class DraftRequirement(BaseModel):
    title: str
    statement: str
    kind: str = "functional"
    priority: str = "should"
    rationale: str | None = None
    acceptance_criteria: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    confidence: float = 0.8
    derived: bool = False
    existing_id: str | None = Field(
        default=None,
        description=(
            "On a refinement, the REQ-xxx id this requirement continues. Set it "
            "whenever you are carrying forward or revising an existing "
            "requirement rather than raising a new one. Omit it for genuinely "
            "new requirements."
        ),
    )


class DraftConflict(BaseModel):
    requirement_titles: list[str] = Field(default_factory=list)
    description: str
    resolution_hint: str | None = None


class DraftQuestion(BaseModel):
    question: str
    why_it_matters: str | None = None
    blocking: bool = False
    evidence_ids: list[str] = Field(default_factory=list)


class RequirementDraft(BaseModel):
    summary: str = ""
    requirements: list[DraftRequirement] = Field(default_factory=list)
    conflicts: list[DraftConflict] = Field(default_factory=list)
    open_questions: list[DraftQuestion] = Field(default_factory=list)
    glossary: dict[str, str] = Field(default_factory=dict)


class MergeGroup(BaseModel):
    keys: list[str] = Field(
        default_factory=list, description="The D-xxx keys describing one and the same need."
    )
    title: str | None = Field(default=None, description="Wording for the merged requirement.")
    statement: str | None = None


class KeyedConflict(BaseModel):
    keys: list[str] = Field(default_factory=list)
    description: str
    resolution_hint: str | None = None


class MergeDecision(BaseModel):
    """Decisions, not content.

    The merge pass deliberately does not re-emit every requirement: its output
    would then be as large as its input, which is the exact failure mode
    batching exists to avoid. It says what to merge and what conflicts; the
    merging itself is done in code, where it cannot lose a citation.
    """

    summary: str = ""
    duplicates: list[MergeGroup] = Field(default_factory=list)
    conflicts: list[KeyedConflict] = Field(default_factory=list)
    open_questions: list[DraftQuestion] = Field(default_factory=list)
    glossary: dict[str, str] = Field(default_factory=dict)


class AnalysisResult(RequirementSet):
    summary: str = ""


class RequirementsExecutor(SkillExecutor):
    def __init__(self) -> None:
        self.llm = LLM(role="reasoning")
        self.skills = {"analyse_requirements": self.analyse_requirements}
        self.default_skill = "analyse_requirements"
        super().__init__()

    async def analyse_requirements(
        self, payload: dict[str, Any], progress: Progress
    ) -> AnalysisResult:
        req = AnalyseRequest.model_validate(payload)
        if not req.evidence:
            raise SkillError("No evidence supplied; nothing to analyse.")

        by_id = {e.id: e for e in req.evidence}
        source_titles = {s.id: s.title for s in req.sources}
        await progress(
            f"Analysing {len(req.evidence)} evidence item(s) from {len(req.sources)} source(s)"
        )

        rendered = _render_evidence(req.evidence, source_titles)
        instructions = f"\n\nAdditional instruction from the requester: {req.instructions}" if req.instructions else ""

        system = SYSTEM
        previous = ""
        if req.prior and req.prior.requirements:
            system = SYSTEM + REFINEMENT
            previous = f"\n\n<<<PREVIOUS VERSION>>>\n{_render_prior(req.prior)}"
            await progress(
                f"Refining {len(req.prior.requirements)} existing requirement(s) "
                f"and {len(req.prior.open_questions)} open question(s)"
            )

        def prompt_for(body: str) -> str:
            return (
                f"Product: {req.title}\nAudience: {req.audience}{instructions}"
                f"{previous}\n\n<<<CONTENT>>>\n{body}"
            )

        cfg = effective_llm()
        batches = _batch(
            req.evidence, source_titles, cfg.analysis_batch_chars, cfg.analysis_batch_items
        )
        if len(batches) == 1:
            draft = await self.llm.structured(
                system, prompt_for(rendered), RequirementDraft, role="reasoning"
            )
        else:
            draft = await self._analyse_in_batches(
                batches, source_titles, system, prompt_for, progress
            )
        await progress(f"Model proposed {len(draft.requirements)} requirement(s); validating citations")

        result = _materialise(draft, by_id, req.prior)
        dropped = sum(1 for r in result.requirements if r.derived and not r.source_refs)
        if dropped:
            await progress(f"{dropped} requirement(s) had no valid citation and were marked derived")
        if req.prior:
            kept = {r.id for r in result.requirements} & {r.id for r in req.prior.requirements}
            await progress(
                f"{len(kept)} requirement(s) carried forward, "
                f"{len(result.requirements) - len(kept)} new, "
                f"{len(req.prior.requirements) - len(kept)} retired"
            )
        return result


    # -- batched analysis --------------------------------------------------

    async def _analyse_in_batches(
        self,
        batches: list[list[Evidence]],
        titles: dict[str, str],
        system: str,
        prompt_for,  # noqa: ANN001
        progress: Progress,
    ) -> RequirementDraft:
        """Map over slices of the evidence, then reduce to one draft.

        The map calls run concurrently but bounded: these are subprocesses on a
        personal subscription, and eight at once is how you find a rate limit.
        A slice that fails is a warning, not a failed run - the same rule
        ingestion follows, and for the same reason: losing one of six slices
        should not cost the user the other five.
        """
        await progress(
            f"Evidence is too large for one pass - analysing it in {len(batches)} slices"
        )
        semaphore = asyncio.Semaphore(MAX_CONCURRENT_SLICES)
        drafts: list[RequirementDraft | None] = [None] * len(batches)

        async def analyse(index: int, batch: list[Evidence]) -> None:
            async with semaphore:
                await progress(f"Slice {index + 1}/{len(batches)}: {len(batch)} evidence item(s)")
                try:
                    drafts[index] = await self.llm.structured(
                        system + SLICE,
                        prompt_for(_render_evidence(batch, titles)),
                        RequirementDraft,
                        role="reasoning",
                    )
                except Exception as exc:  # noqa: BLE001 - one slice must not kill the set
                    logger.exception("evidence slice %d failed", index + 1)
                    await progress(f"Slice {index + 1} failed ({type(exc).__name__}); continuing")

        await asyncio.gather(*(analyse(i, b) for i, b in enumerate(batches)))

        produced = [d for d in drafts if d is not None]
        if not produced:
            raise SkillError(
                "Every slice of the evidence failed to analyse. The model could not be "
                "reached, or the batch size is still too large for it."
            )

        total = sum(len(d.requirements) for d in produced)
        await progress(f"{total} requirement(s) across {len(produced)} slice(s); merging")
        keyed = _keyed(produced)
        decision = await self.llm.structured(
            MERGE_SYSTEM, _render_for_merge(keyed, produced), MergeDecision, role="reasoning"
        )
        merged = _apply_merge(keyed, produced, decision)
        await progress(
            f"Merged {total} into {len(merged.requirements)} requirement(s), "
            f"{len(merged.conflicts)} conflict(s)"
        )
        return merged


def _batch(
    evidence: list[Evidence], titles: dict[str, str], limit: int, items: int = 0
) -> list[list[Evidence]]:
    """Slice the evidence so each slice fits under ``limit`` chars and ``items``.

    Splits on source boundaries wherever it can. Evidence from one document
    read together produces one coherent requirement; the same evidence split
    across two calls produces two half-requirements that the merge pass then
    has to guess were the same thing. A single source larger than a slice is
    split anyway - there is nothing else to do - and that is the case the merge
    pass exists for.

    **Two limits, because two different things blow up.** Characters bound the
    prompt: forty transcript cues and forty PDF paragraphs are an order of
    magnitude apart, and it is the characters that reach the model. Item count
    bounds the *answer*, which is the one that actually failed - 176 items
    rendered to only 45k characters, comfortably under any prompt limit, while
    the requirement set covering them ran to 33k output tokens and stopped at
    the ceiling. A short prompt is no guarantee of a short reply.
    """
    if limit <= 0 and items <= 0:
        return [list(evidence)]
    limit = limit or 10**9
    items = items or 10**9

    groups: list[list[Evidence]] = []
    for item in evidence:
        if groups and groups[-1][0].source_id == item.source_id:
            groups[-1].append(item)
        else:
            groups.append([item])

    def too_big(candidate: list[Evidence]) -> bool:
        return len(candidate) > items or len(_render_evidence(candidate, titles)) > limit

    batches: list[list[Evidence]] = []
    current: list[Evidence] = []
    for group in groups:
        if too_big(group):
            # One source bigger than a whole slice: flush, then split it.
            if current:
                batches.append(current)
                current = []
            part: list[Evidence] = []
            for item in group:
                if part and too_big([*part, item]):
                    batches.append(part)
                    part = []
                part.append(item)
            if part:
                current = part
            continue
        if current and too_big([*current, *group]):
            batches.append(current)
            current = []
        current.extend(group)
    if current:
        batches.append(current)
    return batches or [list(evidence)]


def _keyed(drafts: list[RequirementDraft]) -> dict[str, DraftRequirement]:
    """Every drafted requirement under a stable D-xxx key, in slice order."""
    return {
        f"D-{index:03d}": item
        for index, item in enumerate(
            (r for draft in drafts for r in draft.requirements), start=1
        )
    }


def _render_for_merge(
    keyed: dict[str, DraftRequirement], drafts: list[RequirementDraft]
) -> str:
    """The merge prompt: requirements and questions, no evidence.

    This is what keeps the merge bounded. The evidence was the large thing; the
    requirements derived from it are an order of magnitude smaller, so this
    prompt does not grow with the size of the input material.
    """
    lines = ["REQUIREMENTS FROM ALL PASSES:"]
    for key, item in keyed.items():
        lines.append(f"{key} [{item.priority}/{item.kind}] {item.title}")
        lines.append(f"  {item.statement}")
        if item.acceptance_criteria:
            lines.append("  criteria: " + " | ".join(item.acceptance_criteria[:4]))

    questions = [q for draft in drafts for q in draft.open_questions]
    if questions:
        lines.append("\nOPEN QUESTIONS RAISED:")
        lines += [
            f"Q-{i:03d} {q.question}" + (f" (why: {q.why_it_matters})" if q.why_it_matters else "")
            for i, q in enumerate(questions, start=1)
        ]

    glossary = {term: d for draft in drafts for term, d in draft.glossary.items()}
    if glossary:
        lines.append("\nGLOSSARY TERMS DEFINED:")
        lines += [f"- {term}: {definition}" for term, definition in glossary.items()]
    return "\n".join(lines)


def _apply_merge(
    keyed: dict[str, DraftRequirement],
    drafts: list[RequirementDraft],
    decision: MergeDecision,
) -> RequirementDraft:
    """Carry out the merge the model decided on.

    In code, not in the prompt, because the thing being merged is citations:
    a merged requirement must inherit the evidence ids of *every* draft folded
    into it, and asking a model to transcribe them back is asking it to drop
    some.
    """
    merged_into: dict[str, str] = {}  # key -> canonical key
    groups: dict[str, MergeGroup] = {}
    for group in decision.duplicates:
        members = [k for k in group.keys if k in keyed and k not in merged_into]
        if len(members) < 2:
            continue  # a group of one is not a merge
        canonical = members[0]
        groups[canonical] = group
        for key in members:
            merged_into[key] = canonical

    requirements: list[DraftRequirement] = []
    key_to_title: dict[str, str] = {}
    for key, item in keyed.items():
        canonical = merged_into.get(key, key)
        if canonical != key:
            continue  # folded into an earlier one

        folded = [k for k, target in merged_into.items() if target == key and k != key]
        combined = item.model_copy(deep=True)
        for other_key in folded:
            other = keyed[other_key]
            combined.evidence_ids += [
                e for e in other.evidence_ids if e not in combined.evidence_ids
            ]
            combined.acceptance_criteria += [
                c for c in other.acceptance_criteria if c not in combined.acceptance_criteria
            ]
            combined.tags += [t for t in other.tags if t not in combined.tags]
            combined.priority = _stronger(combined.priority, other.priority)
            combined.confidence = max(combined.confidence, other.confidence)
            # Sourced beats inferred: if any pass found real evidence for this
            # need, the merged requirement is not an inference.
            combined.derived = combined.derived and other.derived
            combined.existing_id = combined.existing_id or other.existing_id

        group = groups.get(key)
        if group is not None:
            combined.title = (group.title or combined.title).strip() or combined.title
            combined.statement = (group.statement or combined.statement).strip() or combined.statement

        key_to_title[key] = combined.title
        for other_key in folded:
            key_to_title[other_key] = combined.title
        requirements.append(combined)

    conflicts = [
        DraftConflict(
            requirement_titles=[
                key_to_title[k] for k in c.keys if k in key_to_title
            ],
            description=c.description,
            resolution_hint=c.resolution_hint,
        )
        for c in decision.conflicts
    ]

    glossary = {term: d for draft in drafts for term, d in draft.glossary.items()}
    glossary.update(decision.glossary)

    return RequirementDraft(
        summary=decision.summary
        or f"{len(requirements)} requirement(s) merged from {len(keyed)} across "
        f"{len(drafts)} slice(s) of the evidence.",
        requirements=requirements,
        conflicts=conflicts,
        # The merge pass sees every slice's questions and decides which survive;
        # a per-slice question answered by another slice must not come through.
        open_questions=decision.open_questions,
        glossary=glossary,
    )


_PRIORITY_ORDER = ["wont", "could", "should", "must"]


def _stronger(left: str, right: str) -> str:
    """The higher MoSCoW priority of two. A merged need keeps the stronger claim."""

    def rank(value: str) -> int:
        try:
            return _PRIORITY_ORDER.index(str(value).strip().lower())
        except ValueError:
            return 1

    return left if rank(left) >= rank(right) else right


def _render_evidence(evidence: list[Evidence], titles: dict[str, str]) -> str:
    lines: list[str] = []
    for e in evidence:
        meta = [f"id={e.id}", f"source={titles.get(e.source_id, e.source_id)}", f"type={e.kind}"]
        if e.locator:
            meta.append(f"at={e.locator}")
        if e.speaker:
            meta.append(f"said_by={e.speaker}")
        if e.confidence < 0.7:
            meta.append(f"confidence={e.confidence:.2f}")
        lines.append(f"[{'; '.join(meta)}]\n{e.text}")
    return "\n\n".join(lines)


def _same_claim(a: str, b: str) -> bool:
    """Do two statements assert the same thing, allowing for reformatting?

    Whitespace and case only. Deliberately not fuzzy: the question being asked
    is "may this requirement keep citing evidence gathered for the previous
    wording", and anything looser starts guessing that a rewrite was harmless.
    A false negative costs a `derived` tag on a requirement somebody can re-cite;
    a false positive puts a quote under a claim it does not support.
    """
    return " ".join(a.split()).casefold() == " ".join(b.split()).casefold()


def _materialise(
    draft: RequirementDraft,
    by_id: dict[str, Evidence],
    prior: RequirementSet | None = None,
) -> AnalysisResult:
    """Assign stable ids and strip citations the model invented.

    On a first run ids are positional - REQ-001 downwards. On a refinement that
    is exactly wrong: inserting one requirement in the middle would renumber
    everything after it, and every citation in the PRD a reader already has,
    every Jira ticket that quotes a REQ id, silently starts pointing at a
    different requirement. So an id claimed via ``existing_id`` is honoured,
    and new requirements are allocated from above the highest id ever issued -
    never reusing the number of something that was dropped.
    """
    requirements: list[Requirement] = []
    title_to_id: dict[str, str] = {}

    known = {r.id for r in (prior.requirements if prior else [])}
    prior_refs = {r.id: r.source_refs for r in (prior.requirements if prior else [])}
    prior_statements = {r.id: r.statement for r in (prior.requirements if prior else [])}
    taken: set[str] = set()
    next_free = max((_id_number(r) for r in known), default=0) + 1

    def allocate(item: DraftRequirement) -> str:
        nonlocal next_free
        claimed = (item.existing_id or "").strip().upper()
        if claimed in known and claimed not in taken:
            taken.add(claimed)
            return claimed
        if claimed and claimed not in known:
            logger.warning("ignoring unknown existing_id %r on %r", claimed, item.title)
        while f"REQ-{next_free:03d}" in known or f"REQ-{next_free:03d}" in taken:
            next_free += 1
        new_id = f"REQ-{next_free:03d}"
        taken.add(new_id)
        return new_id

    for index, item in enumerate(draft.requirements, start=1):
        req_id = allocate(item) if prior else f"REQ-{index:03d}"
        title_to_id[item.title.strip().lower()] = req_id

        refs: list[SourceRef] = []
        for ev_id in item.evidence_ids:
            evidence = by_id.get(ev_id)
            if evidence is None:
                logger.warning("dropping invented citation %r on %s", ev_id, req_id)
                continue
            refs.append(
                SourceRef(
                    evidence_id=evidence.id,
                    source_id=evidence.source_id,
                    locator=evidence.locator,
                    quote=evidence.text[:400],
                )
            )

        # A requirement the refinement did not touch tends to come back with no
        # citations at all - the model re-cites what the new material justifies
        # and lets the rest go. Left alone that demotes every untouched
        # requirement to `derived`, and the PRD starts telling the reader that
        # facts it sourced last week were inferred. The prior version's
        # citations are still valid evidence, so inherit them.
        #
        # Only when the *statement* is unchanged. Inheriting by id alone cannot
        # tell "untouched" from "rewritten and uncited", and the second case
        # attaches evidence for the old claim to the new one: a requirement
        # rewritten from "refund within 14 days" to "within 24 hours" would keep
        # citing a quote that says 14, render as sourced rather than derived,
        # and print the contradiction in the traceability matrix as though it
        # were provenance. A claim nobody cited is `derived` - that is what the
        # word is for.
        if not refs and prior_refs.get(req_id):
            if _same_claim(item.statement, prior_statements.get(req_id, "")):
                refs = list(prior_refs[req_id])
                logger.debug("carried %d citation(s) forward onto %s", len(refs), req_id)
            else:
                logger.info(
                    "%s was rewritten without citations - it becomes `derived` rather than "
                    "inheriting evidence for the previous wording", req_id
                )

        requirements.append(
            Requirement(
                id=req_id,
                title=item.title.strip() or req_id,
                statement=item.statement.strip(),
                kind=_enum(ReqKind, item.kind, ReqKind.FUNCTIONAL),
                priority=_enum(Priority, item.priority, Priority.SHOULD),
                rationale=item.rationale,
                acceptance_criteria=[c for c in item.acceptance_criteria if c.strip()],
                source_refs=refs,
                tags=item.tags,
                confidence=min(item.confidence, 0.5) if not refs else item.confidence,
                derived=item.derived or not refs,
            )
        )

    conflicts = [
        Conflict(
            requirement_ids=[
                title_to_id[t.strip().lower()]
                for t in c.requirement_titles
                if t.strip().lower() in title_to_id
            ],
            description=c.description,
            resolution_hint=c.resolution_hint,
        )
        for c in draft.conflicts
    ]

    questions = [
        OpenQuestion(
            question=q.question,
            why_it_matters=q.why_it_matters,
            blocking=q.blocking,
            source_refs=[
                SourceRef(
                    evidence_id=e,
                    source_id=by_id[e].source_id,
                    locator=by_id[e].locator,
                    quote=by_id[e].text[:400],
                )
                for e in q.evidence_ids
                if e in by_id
            ],
        )
        for q in draft.open_questions
    ]

    return AnalysisResult(
        requirements=requirements,
        conflicts=conflicts,
        open_questions=questions,
        glossary=draft.glossary,
        summary=draft.summary
        or f"{len(requirements)} requirement(s), {len(conflicts)} conflict(s), "
        f"{len(questions)} open question(s).",
    )


def _enum(enum_cls, value: str, default):  # noqa: ANN001, ANN202
    try:
        return enum_cls(str(value).strip().lower().replace(" ", "_").replace("-", "_"))
    except ValueError:
        return default


def card():  # noqa: ANN201
    return build_card(
        name="Requirements Analyst",
        description=(
            "Normalises evidence from every source into one de-duplicated, MoSCoW-"
            "prioritised requirement set with conflicts, open questions and a glossary. "
            "Citations are validated against real evidence ids."
        ),
        url=public_url(PORT),
        skills=[
            skill(
                "analyse_requirements",
                "Analyse evidence into requirements",
                "Cluster, de-duplicate, prioritise and conflict-check evidence, producing "
                "testable requirements each traceable to its source.",
                tags=["requirements", "analysis", "prd"],
            )
        ],
    )


def executor() -> SkillExecutor:
    return RequirementsExecutor()


# -- stub mode --------------------------------------------------------------
# Lets the whole pipeline run end-to-end with no API key: each evidence id in
# the prompt becomes one requirement citing itself.


def _stub_requirements(user: str) -> dict[str, Any]:
    ids = re.findall(r"\[id=(ev-[0-9a-f]+)", user)
    return {
        "summary": f"[stub] {len(ids)} requirement(s) derived one-per-evidence.",
        "requirements": [
            {
                "title": f"Stub requirement {i + 1}",
                "statement": f"The system must satisfy the claim recorded in {ev}.",
                "kind": "functional",
                "priority": "should",
                "acceptance_criteria": [f"Behaviour traceable to {ev} is observable."],
                "evidence_ids": [ev],
                "confidence": 0.5,
            }
            for i, ev in enumerate(ids[:25])
        ],
        "open_questions": [{"question": "Stub mode was used; no real analysis happened."}],
        "glossary": {},
    }


register_stub("RequirementDraft", _stub_requirements)


def _id_number(req_id: str) -> int:
    """The numeric part of REQ-014, or 0 for anything that is not one."""
    match = re.search(r"(\d+)", req_id or "")
    return int(match.group(1)) if match else 0


def _render_prior(prior: RequirementSet) -> str:
    """The previous version, compactly, for the refinement prompt.

    Ids first and prominent, because the one thing the model must get right is
    reusing them. Acceptance criteria are included: without them the model
    rewrites criteria it has never seen and calls it a revision.
    """
    lines: list[str] = []
    for r in prior.requirements:
        head = f"{r.id} [{r.priority.value}/{r.kind.value}] {r.title}"
        lines.append(f"{head}\n  {r.statement}")
        for criterion in r.acceptance_criteria:
            lines.append(f"  - {criterion}")
    if prior.open_questions:
        lines.append("\nOpen questions still recorded:")
        lines += [
            f"  ? {q.question}" + (" (blocking)" if q.blocking else "")
            for q in prior.open_questions
        ]
    if prior.conflicts:
        lines.append("\nConflicts still recorded:")
        lines += [f"  ! {', '.join(c.requirement_ids)}: {c.description}" for c in prior.conflicts]
    return "\n".join(lines)
