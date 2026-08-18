"""PRD -> Markdown. The portable output, and what the critic agent reads."""

from __future__ import annotations

from sourcework.models import PRDDocument, ReviewReport


def render(prd: PRDDocument, review: ReviewReport | None = None) -> str:
    ev = prd.evidence_by_id()
    src = prd.source_by_id()
    L: list[str] = []

    L.append(f"# {prd.title}")
    L.append("")
    L.append(
        f"> **Status** `{prd.status}` &nbsp;|&nbsp; **Version** {prd.version} &nbsp;|&nbsp; "
        f"**Generated** {prd.generated_at:%Y-%m-%d %H:%M UTC} by SourceWork"
    )
    L.append(">")
    L.append(
        f"> Synthesised from {len(prd.sources)} source(s) and {len(prd.evidence)} evidence item(s). "
        "Requirements tagged `derived` were inferred, not stated."
    )
    L.append("")

    _section(L, "Summary", prd.summary)
    _section(L, "Problem statement", prd.problem_statement)
    _section(L, "Background", prd.background)

    _bullets(L, "Goals", prd.goals)
    _bullets(L, "Non-goals", prd.non_goals)
    _bullets(L, "Personas", prd.personas)

    if prd.user_stories:
        L += ["## User stories", ""]
        for s in prd.user_stories:
            refs = f" _({', '.join(s.requirement_ids)})_" if s.requirement_ids else ""
            L.append(f"- **{s.id}** — {s.as_sentence()}{refs}")
        L.append("")

    L += ["## Requirements", ""]
    # The estimate column appears only when the run asked for estimates: an
    # all-empty column would read as data that failed to load. Estimates are
    # marked derived because that is what they are - model arithmetic, never
    # something a source said.
    any_effort = any(r.effort for r in prd.requirements.requirements)
    if any_effort:
        L += [
            "_Effort estimates (≈) are T-shirt sizes inferred by the model, not stated "
            "in any source. Treat them as planning hints._",
            "",
        ]
    for kind in ("functional", "non_functional", "constraint", "assumption", "out_of_scope"):
        group = [r for r in prd.requirements.requirements if r.kind.value == kind]
        if not group:
            continue
        L += [f"### {kind.replace('_', ' ').title()}", ""]
        headers = ["ID", "Pri"] + (["Est"] if any_effort else []) + [
            "Requirement", "Acceptance criteria", "Sources",
        ]
        L += _table_head(headers)
        for r in group:
            sources = "; ".join(
                f"{(src[ref.source_id].title if ref.source_id in src else ref.source_id)}"
                f"{' @ ' + ref.locator if ref.locator else ''}"
                for ref in r.source_refs
            ) or "**unsupported**"
            crit = "<br>".join(f"• {c}" for c in r.acceptance_criteria) or "_none_"
            tag = " `derived`" if r.derived else ""
            est_cell = f" {_esc(_effort_cell(r))} |" if any_effort else ""
            L.append(
                f"| {r.id} | {r.priority.value.upper()} |{est_cell} **{_esc(r.title)}**{tag}<br>"
                f"{_esc(r.statement)} | {_esc(crit)} | {_esc(sources)} |"
            )
        L.append("")

    if prd.requirements.conflicts:
        L += ["## Conflicts to resolve", ""]
        for c in prd.requirements.conflicts:
            L.append(f"- **{' / '.join(c.requirement_ids)}** — {c.description}")
            if c.resolution_hint:
                L.append(f"  - _Suggested resolution:_ {c.resolution_hint}")
        L.append("")

    if prd.requirements.open_questions:
        L += ["## Open questions", "", "| Question | Why it matters | Blocking |", "|---|---|---|"]
        for q in prd.requirements.open_questions:
            L.append(
                f"| {_esc(q.question)} | {_esc(q.why_it_matters or '')} | "
                f"{'yes' if q.blocking else 'no'} |"
            )
        L.append("")

    if prd.metrics:
        L += ["## Success metrics", "", "| Metric | Definition | Baseline | Target |", "|---|---|---|---|"]
        for m in prd.metrics:
            L.append(
                f"| {_esc(m.name)} | {_esc(m.definition)} | {_esc(m.baseline or '—')} | "
                f"{_esc(m.target or '—')} |"
            )
        L.append("")

    if prd.risks:
        L += ["## Risks", "", "| Risk | Impact | Likelihood | Mitigation |", "|---|---|---|---|"]
        for r in prd.risks:
            L.append(
                f"| {_esc(r.description)} | {r.impact} | {r.likelihood} | "
                f"{_esc(r.mitigation or '—')} |"
            )
        L.append("")

    if prd.milestones:
        L += ["## Milestones", ""] + _table_head(["Milestone", "Requirements", "Effort", "Target"])
        # The rollup is arithmetic done here, in code, because "how much of each
        # size does this milestone hold" is counting, and the model is never
        # trusted to count.
        effort_by_id = {r.id: r.effort for r in prd.requirements.requirements if r.effort}
        for m in prd.milestones:
            L.append(
                f"| **{_esc(m.name)}** — {_esc(m.description or '')} | "
                f"{', '.join(m.requirement_ids)} | {_effort_rollup(m.requirement_ids, effort_by_id)} | "
                f"{_esc(m.target or '—')} |"
            )
        L.append("")

    for s in prd.extra_sections:
        L += [f"{'#' * min(max(s.level, 2), 6)} {s.heading}", "", s.body_markdown, ""]

    if prd.requirements.glossary:
        L += ["## Glossary", ""]
        for term, definition in sorted(prd.requirements.glossary.items()):
            L.append(f"- **{term}** — {definition}")
        L.append("")

    L += ["## Sources", "", "| ID | Title | Type | Location |", "|---|---|---|---|"]
    for s in prd.sources:
        L.append(f"| `{s.id}` | {_esc(s.title)} | {s.modality.value} | `{_esc(s.uri)}` |")
    L.append("")

    L += [
        "## Traceability",
        "",
        "| Requirement | Source | Location | Evidence |",
        "|---|---|---|---|",
    ]
    for r in prd.requirements.requirements:
        if not r.source_refs:
            L.append(f"| {r.id} | _no evidence_ | | |")
        for ref in r.source_refs:
            e = ev.get(ref.evidence_id)
            title = src[ref.source_id].title if ref.source_id in src else ref.source_id
            quote = ref.quote or (e.text[:200] if e else "")
            L.append(
                f"| {r.id} | {_esc(title)} | {_esc(ref.locator or (e.locator if e else ''))} | "
                f"{_esc(quote)} |"
            )
    L.append("")

    if review is not None:
        L += ["## Automated review", "", f"**Verdict:** `{review.verdict}`", ""]
        if review.standards:
            L += [f"_Quality rules checked: {review.standards}._", ""]
        if review.findings:
            L += ["| Severity | Category | Where | Detail |", "|---|---|---|---|"]
            for f in review.findings:
                fix = f" _Fix: {f.suggested_fix}_" if f.suggested_fix else ""
                L.append(
                    f"| {f.severity.value} | {f.category} | {_esc(f.location)} | "
                    f"{_esc(f.detail)}{_esc(fix)} |"
                )
        else:
            L.append("No findings.")
        L.append("")

    return "\n".join(L).rstrip() + "\n"


def _section(lines: list[str], heading: str, body: str) -> None:
    if body and body.strip():
        lines += [f"## {heading}", "", body.strip(), ""]


def _bullets(lines: list[str], heading: str, items: list[str]) -> None:
    if items:
        lines += [f"## {heading}", "", *[f"- {i}" for i in items], ""]


def _esc(text: str) -> str:
    """Keep markdown tables from exploding on pipes and newlines."""
    return (text or "").replace("|", "\\|").replace("\n", " ")


def _table_head(headers: list[str]) -> list[str]:
    """Header row and its delimiter, counted from the same list.

    GFM only recognises a table when the delimiter row has exactly as many
    cells as the header; one short and the whole table renders as literal
    pipes. The fixed-width tables below write both rows by hand, which is
    readable and safe - but the two tables whose width depends on whether the
    run asked for estimates cannot, and hand-counting dashes is precisely how
    that breaks.
    """
    return [f"| {' | '.join(headers)} |", "|" + "---|" * len(headers)]


def _effort_cell(r) -> str:  # noqa: ANN001, ANN202 - Requirement, kept short for the table row
    """`≈M`, with the rationale where a hover can reach it; empty when unestimated."""
    if not r.effort:
        return ""
    return f"≈{r.effort}" + (f" — {r.effort_rationale}" if r.effort_rationale else "")


def _effort_rollup(req_ids: list[str], effort_by_id: dict[str, str]) -> str:
    """A milestone's sizes, counted in code: `2S 3M 1XL`. Em-dash when nothing
    was estimated."""
    if not effort_by_id:
        return "—"
    counts: dict[str, int] = {}
    for req_id in req_ids:
        size = effort_by_id.get(req_id)
        if size:
            counts[size] = counts.get(size, 0) + 1
    if not counts:
        return "—"
    return " ".join(f"{counts[s]}{s}" for s in ("S", "M", "L", "XL") if s in counts)
