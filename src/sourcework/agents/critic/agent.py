"""PRD critic agent (port 8007).

An adversarial reader. It gets the finished draft plus the evidence it was
supposed to be built from, and looks for the failure modes that matter in a
generated document - above all, claims nobody can trace back to a source.

Deterministic checks run first (in code, not in the prompt) so the critic never
has to be trusted about arithmetic: uncited requirements, dangling REQ ids,
missing acceptance criteria, empty sections. The model then handles the
judgement calls: ambiguity, untestability, scope creep, contradictions.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from pydantic import BaseModel, Field

from sourcework import quality
from sourcework.a2a_common import Progress, SkillExecutor, build_card, public_url, skill
from sourcework.agents.schemas import ReviewRequest, ReviewResponse
from sourcework.config import settings
from sourcework.llm import LLM, register_stub
from sourcework.models import PRDDocument, ReviewFinding, ReviewReport, Severity
from sourcework.render import to_markdown

logger = logging.getLogger(__name__)

PORT = 8007

MAX_PROMPT_FINDINGS = 200
MAX_PROMPT_MARKDOWN_CHARS = 60_000
MAX_PROMPT_EVIDENCE = 250
"""Caps on what the reviewer is shown in one pass.

The markdown cap is not a truncation point: a PRD larger than it is split on
section boundaries and reviewed in several passes, so every part is read
adversarially - a review that saw only the first N characters could not honestly
claim to have covered the document. The evidence and findings caps are still
reported when they bite.

The findings cap is the one that grows without bound on a large PRD: the wording
rules can fire several times per requirement, and the list is in the prompt only
so the model does not spend its answer repeating it. Every finding stays in the
report either way; only this copy is trimmed, and never quietly."""

VAGUE = re.compile(
    r"\b(fast|slow|easy|simple|intuitive|robust|scalable|user-friendly|seamless|"
    r"efficient|modern|appropriate|reasonable|as needed|etc\.?|and so on|"
    r"where possible|if necessary|best[- ]in[- ]class)\b",
    re.IGNORECASE,
)

SYSTEM = """You are reviewing a PRD that was generated from source material by a
pipeline of models. Assume good intent and bad grounding: the most likely defect
is a confident claim that no source supports.

Look for, in priority order:

1. `unsupported` - a statement in the narrative that no requirement or evidence
   item backs. Quote the statement. This is the most important category; a
   plausible invented fact is worse than an obvious gap.
2. `contradiction` - two parts of the document that cannot both be true, or a
   requirement contradicting evidence.
3. `untestable` - a requirement no one could write a pass/fail test for.
4. `ambiguous` - wording that two competent engineers would implement
   differently. Say which two readings.
5. `missing` - a section or consideration the document needs and lacks, given
   what the requirements imply (auth, data retention, migration, failure
   handling, rollout, accessibility, i18n - only where genuinely implied).
6. `scope` - narrative that goes beyond what the requirements cover.

Severity: `blocker` if a team would build the wrong thing; `major` if it would
cause significant rework; `minor` for real but contained issues; `nit` for
polish. Be sparing with blocker.

Every finding needs a `location` - the section heading or REQ id - and a
concrete `suggested_fix`. Do not report style preferences. Do not repeat the
deterministic findings you are shown; add to them.

Verdict: `approved` only when there are no blockers and no majors.

Put in `notes` the one sentence a reader should have before the findings: what
this document is like to receive. Not a count - they can see the count.
"""


class CriticDraft(BaseModel):
    findings: list[ReviewFinding] = Field(default_factory=list)
    verdict: str = "needs_revision"
    notes: str = ""


class CriticExecutor(SkillExecutor):
    def __init__(self) -> None:
        self.llm = LLM(role="critic")
        self.skills = {"review_prd": self.review_prd}
        self.default_skill = "review_prd"
        super().__init__()

    async def review_prd(self, payload: dict[str, Any], progress: Progress) -> ReviewResponse:
        req = ReviewRequest.model_validate(payload)
        prd = req.prd

        await progress("Running deterministic checks")
        findings = structural_findings(prd)
        coverage = coverage_stats(prd)
        await progress(
            f"{len(findings)} structural finding(s); "
            f"{coverage['cited_requirements']:.0%} of requirements are cited"
        )

        markdown = req.markdown or to_markdown(prd)
        system = SYSTEM + (f"\n\nAdditional rubric from the requester:\n{req.rubric}" if req.rubric else "")
        shown = findings[:MAX_PROMPT_FINDINGS]
        if len(findings) > len(shown):
            await progress(
                f"{len(findings) - len(shown)} deterministic finding(s) left out of the "
                f"review prompt (showing {len(shown)}); all of them stay in the report"
            )
        evidence_shown = prd.evidence[:MAX_PROMPT_EVIDENCE]
        if len(prd.evidence) > len(evidence_shown):
            await progress(
                f"{len(prd.evidence) - len(evidence_shown)} evidence item(s) left out of the "
                f"review prompt (showing {len(evidence_shown)})"
            )

        # The document is reviewed in passes, not cut at a character count. The
        # splits fall on `## ` boundaries, so a pass sees whole sections and
        # every part of the PRD is read by someone.
        chunks = _split_markdown(markdown, MAX_PROMPT_MARKDOWN_CHARS)
        if len(chunks) > 1:
            await progress(
                f"PRD is {len(markdown)} characters; reviewing it in {len(chunks)} sections"
            )

        findings_block = (
            "\n".join(f"- [{f.severity.value}] {f.location}: {f.detail}" for f in shown) or "none"
        )
        evidence_block = "\n".join(f"- {e.id} [{e.kind}] {e.text}" for e in evidence_shown)

        drafts: list[CriticDraft] = []
        for index, chunk in enumerate(chunks, start=1):
            scope = (
                ""
                if len(chunks) == 1
                else f"\n\n(This is section {index} of {len(chunks)} of the PRD - "
                "review what is shown and do not assume what the rest says.)"
            )
            user = (
                f"PRD under review:\n\n{chunk}{scope}\n\n"
                "---\nDETERMINISTIC FINDINGS ALREADY RECORDED (do not repeat):\n"
                f"{findings_block}\n\n---\nEVIDENCE AVAILABLE TO THE WRITER:\n{evidence_block}"
            )
            label = (
                "Adversarial review"
                if len(chunks) == 1
                else f"Adversarial review ({index}/{len(chunks)})"
            )
            await progress(label)
            drafts.append(await self.llm.structured(system, user, CriticDraft, role="critic"))

        for draft in drafts:
            findings.extend(draft.findings)
        findings = _dedupe(findings)

        report = ReviewReport(
            findings=findings,
            coverage=coverage,
            verdict=_verdict(findings, _fold_verdict(drafts)),
            standards=quality.standards_line(ears=settings().quality.ears),
            # The reviewer's prose, kept rather than dropped on the floor: the
            # findings are the itemised part, this is the sentence that frames
            # them - and in stub mode it is the marker saying no model ran.
            summary=" ".join(d.notes.strip() for d in drafts if d.notes.strip()),
        )
        return ReviewResponse(
            report=report,
            verdict=report.verdict,
            summary=f"{report.verdict}: {len(report.blocking)} blocking of "
            f"{len(report.findings)} finding(s).",
        )


def structural_findings(prd: PRDDocument) -> list[ReviewFinding]:
    """Checks that do not need a model and must never be wrong."""
    out: list[ReviewFinding] = []
    reqs = prd.requirements.requirements
    valid_ids = {r.id for r in reqs}
    evidence_ids = {e.id for e in prd.evidence}

    for r in reqs:
        if not r.source_refs:
            out.append(
                ReviewFinding(
                    severity=Severity.MAJOR if r.priority.value == "must" else Severity.MINOR,
                    category="unsupported",
                    location=r.id,
                    detail=f"'{r.title}' cites no evidence and is marked "
                    f"{'derived' if r.derived else 'sourced'}.",
                    suggested_fix="Point it at the evidence that justifies it, or drop it.",
                )
            )
        for ref in r.source_refs:
            if ref.evidence_id not in evidence_ids:
                out.append(
                    ReviewFinding(
                        severity=Severity.MAJOR,
                        category="unsupported",
                        location=r.id,
                        detail=f"Cites evidence id {ref.evidence_id!r}, which does not exist.",
                        suggested_fix="Remove the dangling citation.",
                    )
                )
        if not r.acceptance_criteria and r.priority.value in ("must", "should"):
            out.append(
                ReviewFinding(
                    severity=Severity.MINOR,
                    category="untestable",
                    location=r.id,
                    detail=f"'{r.title}' is {r.priority.value} but has no acceptance criteria.",
                    suggested_fix="Add at least one observable pass/fail condition.",
                )
            )
        vague = set(VAGUE.findall(r.statement))
        if vague:
            out.append(
                ReviewFinding(
                    severity=Severity.MINOR,
                    category="ambiguous",
                    location=r.id,
                    detail=f"Unquantified wording: {', '.join(sorted(vague))}.",
                    suggested_fix="Replace with a measurable threshold.",
                )
            )

    referenced = {i for s in prd.user_stories for i in s.requirement_ids}
    referenced |= {i for m in prd.milestones for i in m.requirement_ids}
    for dangling in sorted(referenced - valid_ids):
        out.append(
            ReviewFinding(
                severity=Severity.MAJOR,
                category="contradiction",
                location="User stories / milestones",
                detail=f"References {dangling}, which is not in the requirement set.",
                suggested_fix="Fix the reference or add the requirement.",
            )
        )

    for field, label, severity in (
        ("summary", "Summary", Severity.MAJOR),
        ("problem_statement", "Problem statement", Severity.MAJOR),
        ("goals", "Goals", Severity.MAJOR),
        ("non_goals", "Non-goals", Severity.MINOR),
    ):
        if not getattr(prd, field):
            out.append(
                ReviewFinding(
                    severity=severity,
                    category="missing",
                    location=label,
                    detail=f"{label} is empty.",
                    suggested_fix=f"Write the {label.lower()} section.",
                )
            )

    if prd.requirements.conflicts:
        out.append(
            ReviewFinding(
                severity=Severity.BLOCKER,
                category="contradiction",
                location="Conflicts",
                detail=f"{len(prd.requirements.conflicts)} unresolved conflict(s) between sources.",
                suggested_fix="Resolve with the requester before the PRD is approved.",
            )
        )
    blocking_q = [q for q in prd.requirements.open_questions if q.blocking]
    if blocking_q:
        out.append(
            ReviewFinding(
                severity=Severity.BLOCKER,
                category="missing",
                location="Open questions",
                detail=f"{len(blocking_q)} blocking question(s) unanswered.",
                suggested_fix="Answer them, or downgrade them if the team can proceed.",
            )
        )
    if not prd.metrics:
        out.append(
            ReviewFinding(
                severity=Severity.MINOR,
                category="missing",
                location="Success metrics",
                detail="No success metrics defined.",
                suggested_fix="Add at least one measurable outcome.",
            )
        )

    # The wording rules (ISO 29148 / INCOSE, optionally EARS) live in their own
    # module so they can be tested without booting an agent.
    out.extend(
        quality.rule_findings(
            prd.requirements.requirements,
            prd.requirements.glossary,
            ears=settings().quality.ears,
        )
    )
    return out


def coverage_stats(prd: PRDDocument) -> dict[str, float]:
    reqs = prd.requirements.requirements
    total = len(reqs) or 1
    used_evidence = {ref.evidence_id for r in reqs for ref in r.source_refs}
    # The quality score is computed over the wording findings only, so a
    # degrading *writing* pipeline is visible separately from a degrading
    # *citation* pipeline - they fail for different reasons and are fixed in
    # different places.
    wording = quality.rule_findings(
        reqs, prd.requirements.glossary, ears=settings().quality.ears
    )
    return {
        "requirements": float(len(reqs)),
        "cited_requirements": sum(1 for r in reqs if r.source_refs) / total,
        "with_acceptance_criteria": sum(1 for r in reqs if r.acceptance_criteria) / total,
        "derived_share": sum(1 for r in reqs if r.derived) / total,
        "evidence_used": len(used_evidence) / (len(prd.evidence) or 1),
        "quality_clean": quality.quality_score(reqs, wording),
    }


def _verdict(findings: list[ReviewFinding], model_verdict: str) -> str:
    if any(f.severity == Severity.BLOCKER for f in findings):
        return "needs_revision"
    if any(f.severity == Severity.MAJOR for f in findings):
        return "needs_revision"
    return model_verdict if model_verdict in ("approved", "needs_revision", "reject") else "approved"


def _fold_verdict(drafts: list[CriticDraft]) -> str:
    """One verdict from per-section ones: the worst wins.

    A section the reviewer called `reject` makes the document that; any
    `needs_revision` makes it that if nothing is worse. A section that looked
    clean cannot cancel one that did not.
    """
    verdict = "approved"
    for draft in drafts:
        if draft.verdict == "reject":
            return "reject"
        if draft.verdict == "needs_revision":
            verdict = "needs_revision"
    return verdict


def _dedupe(findings: list[ReviewFinding]) -> list[ReviewFinding]:
    """Drop exact repeats, which sections can produce over shared boilerplate."""
    seen: set[tuple[str, str, str]] = set()
    out: list[ReviewFinding] = []
    for finding in findings:
        key = (finding.category, finding.location, finding.detail)
        if key in seen:
            continue
        seen.add(key)
        out.append(finding)
    return out


def _split_markdown(markdown: str, limit: int) -> list[str]:
    """Split a document into review-sized passes on section boundaries.

    `## ` starts a section; consecutive sections are packed until the next would
    cross ``limit``. A single section larger than ``limit`` is split on its own
    lines, and only then on character count, so no part is ever dropped - the
    reviewer reads all of it across several passes rather than the first
    ``limit`` characters in one.
    """
    if len(markdown) <= limit:
        return [markdown]

    sections: list[str] = []
    current: list[str] = []
    for line in markdown.splitlines(keepends=True):
        if line.startswith("## ") and current:
            sections.append("".join(current))
            current = [line]
        else:
            current.append(line)
    if current:
        sections.append("".join(current))

    chunks: list[str] = []
    for section in sections:
        if len(section) > limit:
            chunks.extend(_hard_split(section, limit))
        elif chunks and len(chunks[-1]) + len(section) <= limit:
            chunks[-1] += section
        else:
            chunks.append(section)
    # Only truly empty chunks go; a chunk of pure whitespace is still part of
    # the document and dropping it would break the partition.
    return [chunk for chunk in chunks if chunk]


def _hard_split(text: str, limit: int) -> list[str]:
    """The last resort for one section bigger than a whole pass: by lines, then
    characters. Only ever reached by a single section that is itself huge."""
    parts: list[str] = []
    current = ""
    for line in text.splitlines(keepends=True):
        if len(line) > limit:
            if current:
                parts.append(current)
                current = ""
            parts.extend(line[i : i + limit] for i in range(0, len(line), limit))
            continue
        if current and len(current) + len(line) > limit:
            parts.append(current)
            current = ""
        current += line
    if current:
        parts.append(current)
    return parts


def card():  # noqa: ANN201
    return build_card(
        name="PRD Critic",
        description=(
            "Adversarially reviews a generated PRD for unsupported claims, "
            "contradictions, untestable or ambiguous requirements and missing sections. "
            "Runs deterministic traceability checks before invoking a model."
        ),
        url=public_url(PORT),
        skills=[
            skill(
                "review_prd",
                "Review a PRD",
                "Return findings with severity, location and a suggested fix, plus "
                "coverage statistics and an overall verdict.",
                tags=["review", "quality", "prd"],
            )
        ],
    )


def executor() -> SkillExecutor:
    return CriticExecutor()


# -- stub mode --------------------------------------------------------------


def _stub_review(user: str) -> dict[str, Any]:
    return {
        "findings": [],
        "verdict": "needs_revision",
        "notes": "[stub] no model review was performed.",
    }


register_stub("CriticDraft", _stub_review)
