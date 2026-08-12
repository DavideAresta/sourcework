"""Render a :class:`PRDDocument` into Confluence storage format (XHTML).

Storage format is XHTML with the ``ac:`` and ``ri:`` namespaces implicit.
Everything emitted here must be well-formed or Confluence rejects the whole
page, so all text goes through :func:`esc` and CDATA is guarded.

Also here: a storage-format -> plain-text reader, used when pulling existing
Confluence pages in as source material.
"""

from __future__ import annotations

import re
from xml.sax.saxutils import escape

from sourcework.models import PRDDocument, Priority, Requirement, ReviewReport, Severity

_PRIORITY_COLOUR = {
    Priority.MUST: "Red",
    Priority.SHOULD: "Yellow",
    Priority.COULD: "Blue",
    Priority.WONT: "Grey",
}

_SEVERITY_COLOUR = {
    Severity.BLOCKER: "Red",
    Severity.MAJOR: "Red",
    Severity.MINOR: "Yellow",
    Severity.NIT: "Grey",
}


def esc(text: str | None) -> str:
    return escape(text or "", {'"': "&quot;"})


def status_lozenge(label: str, colour: str = "Grey") -> str:
    return (
        '<ac:structured-macro ac:name="status" ac:schema-version="1">'
        f'<ac:parameter ac:name="title">{esc(label)}</ac:parameter>'
        f'<ac:parameter ac:name="colour">{colour}</ac:parameter>'
        "</ac:structured-macro>"
    )


def panel(title: str, body_html: str, macro: str = "info") -> str:
    return (
        f'<ac:structured-macro ac:name="{macro}" ac:schema-version="1">'
        f'<ac:parameter ac:name="title">{esc(title)}</ac:parameter>'
        f"<ac:rich-text-body>{body_html}</ac:rich-text-body>"
        "</ac:structured-macro>"
    )


def toc(max_level: int = 3) -> str:
    return (
        '<ac:structured-macro ac:name="toc" ac:schema-version="1">'
        f'<ac:parameter ac:name="maxLevel">{max_level}</ac:parameter>'
        '<ac:parameter ac:name="minLevel">1</ac:parameter>'
        '<ac:parameter ac:name="type">list</ac:parameter>'
        "</ac:structured-macro>"
    )


def expand(title: str, body_html: str) -> str:
    return (
        '<ac:structured-macro ac:name="expand" ac:schema-version="1">'
        f'<ac:parameter ac:name="title">{esc(title)}</ac:parameter>'
        f"<ac:rich-text-body>{body_html}</ac:rich-text-body>"
        "</ac:structured-macro>"
    )


def code_block(text: str, language: str = "json", title: str | None = None) -> str:
    safe = text.replace("]]>", "]]]]><![CDATA[>")
    title_param = (
        f'<ac:parameter ac:name="title">{esc(title)}</ac:parameter>' if title else ""
    )
    return (
        '<ac:structured-macro ac:name="code" ac:schema-version="1">'
        f'<ac:parameter ac:name="language">{esc(language)}</ac:parameter>'
        f"{title_param}"
        f"<ac:plain-text-body><![CDATA[{safe}]]></ac:plain-text-body>"
        "</ac:structured-macro>"
    )


def table(headers: list[str], rows: list[list[str]]) -> str:
    """Cells are pre-escaped HTML fragments; escape before passing them in."""
    head = "".join(f"<th>{h}</th>" for h in headers)
    body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
    return f"<table><tbody><tr>{head}</tr>{body}</tbody></table>"


def bullets(items: list[str]) -> str:
    if not items:
        return "<p><em>None recorded.</em></p>"
    return "<ul>" + "".join(f"<li><p>{esc(i)}</p></li>" for i in items) + "</ul>"


def paragraphs(markdown_ish: str) -> str:
    """Minimal markdown -> storage. Deliberately conservative.

    Handles paragraphs, bullet lists and inline bold/italic/code. Anything
    fancier is left as literal text rather than risking malformed XHTML.
    """
    if not markdown_ish.strip():
        return ""
    out: list[str] = []
    pending: list[str] = []

    def flush_list() -> None:
        if pending:
            out.append("<ul>" + "".join(f"<li><p>{i}</p></li>" for i in pending) + "</ul>")
            pending.clear()

    for block in re.split(r"\n\s*\n", markdown_ish.strip()):
        lines = block.strip().splitlines()
        if all(ln.strip().startswith(("- ", "* ")) for ln in lines if ln.strip()):
            pending.extend(_inline(ln.strip()[2:]) for ln in lines if ln.strip())
            flush_list()
        else:
            out.append(f"<p>{_inline(' '.join(ln.strip() for ln in lines))}</p>")
    flush_list()
    return "".join(out)


def _inline(text: str) -> str:
    text = esc(text)
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"(?<!\*)\*([^*]+?)\*(?!\*)", r"<em>\1</em>", text)
    return re.sub(r"`([^`]+?)`", r"<code>\1</code>", text)


# ---------------------------------------------------------------------------
# Full document
# ---------------------------------------------------------------------------


def render_prd(prd: PRDDocument, review: ReviewReport | None = None) -> str:
    ev = prd.evidence_by_id()
    src = prd.source_by_id()
    out: list[str] = []

    out.append(
        panel(
            "Generated document",
            "<p>"
            + status_lozenge(prd.status.upper(), "Yellow")
            + f" Version {esc(prd.version)} &#183; generated "
            + f"{esc(prd.generated_at.strftime('%Y-%m-%d %H:%M UTC'))} by SourceWork from "
            + f"{len(prd.sources)} source(s) and {len(prd.evidence)} evidence item(s)."
            + "</p><p>Requirements marked <em>derived</em> were inferred by a model, not "
            "stated in a source. Review those first.</p>",
        )
    )
    out.append(toc())

    out.append("<h2>Summary</h2>")
    out.append(paragraphs(prd.summary))

    out.append("<h2>Problem statement</h2>")
    out.append(paragraphs(prd.problem_statement))

    if prd.background:
        out.append("<h2>Background</h2>")
        out.append(paragraphs(prd.background))

    out.append("<h2>Goals</h2>")
    out.append(bullets(prd.goals))
    out.append("<h2>Non-goals</h2>")
    out.append(bullets(prd.non_goals))

    if prd.personas:
        out.append("<h2>Personas</h2>")
        out.append(bullets(prd.personas))

    if prd.user_stories:
        out.append("<h2>User stories</h2>")
        out.append(
            table(
                ["ID", "Story", "Requirements"],
                [
                    [esc(s.id), esc(s.as_sentence()), esc(", ".join(s.requirement_ids))]
                    for s in prd.user_stories
                ],
            )
        )

    out.append("<h2>Requirements</h2>")
    for kind in ("functional", "non_functional", "constraint", "assumption", "out_of_scope"):
        group = [r for r in prd.requirements.requirements if r.kind.value == kind]
        if not group:
            continue
        out.append(f"<h3>{esc(kind.replace('_', ' ').title())}</h3>")
        out.append(
            table(
                ["ID", "Priority", "Requirement", "Acceptance criteria", "Sources"],
                [_requirement_row(r, ev, src) for r in group],
            )
        )

    if prd.requirements.conflicts:
        out.append("<h2>Conflicts to resolve</h2>")
        for c in prd.requirements.conflicts:
            out.append(
                panel(
                    " / ".join(c.requirement_ids) or "Conflict",
                    f"<p>{esc(c.description)}</p>"
                    + (f"<p><em>{esc(c.resolution_hint)}</em></p>" if c.resolution_hint else ""),
                    macro="warning",
                )
            )

    if prd.requirements.open_questions:
        out.append("<h2>Open questions</h2>")
        out.append(
            table(
                ["Question", "Why it matters", "Blocking"],
                [
                    [esc(q.question), esc(q.why_it_matters or ""), "Yes" if q.blocking else "No"]
                    for q in prd.requirements.open_questions
                ],
            )
        )

    if prd.metrics:
        out.append("<h2>Success metrics</h2>")
        out.append(
            table(
                ["Metric", "Definition", "Baseline", "Target"],
                [
                    [esc(m.name), esc(m.definition), esc(m.baseline or "-"), esc(m.target or "-")]
                    for m in prd.metrics
                ],
            )
        )

    if prd.risks:
        out.append("<h2>Risks</h2>")
        out.append(
            table(
                ["Risk", "Impact", "Likelihood", "Mitigation"],
                [
                    [esc(r.description), esc(r.impact), esc(r.likelihood), esc(r.mitigation or "-")]
                    for r in prd.risks
                ],
            )
        )

    if prd.milestones:
        out.append("<h2>Milestones</h2>")
        out.append(
            table(
                ["Milestone", "Description", "Requirements", "Target"],
                [
                    [
                        esc(m.name),
                        esc(m.description or ""),
                        esc(", ".join(m.requirement_ids)),
                        esc(m.target or "-"),
                    ]
                    for m in prd.milestones
                ],
            )
        )

    for section in prd.extra_sections:
        level = min(max(section.level, 2), 5)
        out.append(f"<h{level}>{esc(section.heading)}</h{level}>")
        out.append(paragraphs(section.body_markdown))

    if prd.requirements.glossary:
        out.append("<h2>Glossary</h2>")
        out.append(
            table(
                ["Term", "Definition"],
                [[esc(k), esc(v)] for k, v in sorted(prd.requirements.glossary.items())],
            )
        )

    out.append("<h2>Sources</h2>")
    out.append(
        table(
            ["ID", "Title", "Type", "Location"],
            [
                [esc(s.id), esc(s.title), esc(s.modality.value), esc(s.uri)]
                for s in prd.sources
            ],
        )
    )

    out.append("<h2>Traceability</h2>")
    out.append(expand("Requirement to evidence matrix", _traceability(prd, ev, src)))

    if review is not None:
        out.append("<h2>Automated review</h2>")
        out.append(_review_block(review))

    return "".join(out)


def _requirement_row(r: Requirement, ev: dict, src: dict) -> list[str]:
    refs = []
    for ref in r.source_refs:
        source = src.get(ref.source_id)
        label = source.title if source else ref.source_id
        loc = ref.locator or (ev[ref.evidence_id].locator if ref.evidence_id in ev else "")
        refs.append(esc(f"{label} ({loc})" if loc else label))
    marker = " " + status_lozenge("derived", "Blue") if r.derived else ""
    return [
        esc(r.id),
        status_lozenge(r.priority.value.upper(), _PRIORITY_COLOUR[r.priority]),
        f"<p><strong>{esc(r.title)}</strong>{marker}</p><p>{esc(r.statement)}</p>"
        + (f"<p><em>{esc(r.rationale)}</em></p>" if r.rationale else ""),
        "<ul>" + "".join(f"<li><p>{esc(a)}</p></li>" for a in r.acceptance_criteria) + "</ul>"
        if r.acceptance_criteria
        else "<p><em>none defined</em></p>",
        "<br/>".join(refs) or "<em>unsupported</em>",
    ]


def _traceability(prd: PRDDocument, ev: dict, src: dict) -> str:
    rows: list[list[str]] = []
    for r in prd.requirements.requirements:
        if not r.source_refs:
            rows.append([esc(r.id), "<em>no evidence</em>", "", ""])
        for ref in r.source_refs:
            evidence = ev.get(ref.evidence_id)
            source = src.get(ref.source_id)
            rows.append(
                [
                    esc(r.id),
                    esc(source.title if source else ref.source_id),
                    esc(ref.locator or (evidence.locator if evidence else "")),
                    esc(ref.quote or (evidence.text[:280] if evidence else "")),
                ]
            )
    return table(["Requirement", "Source", "Location", "Evidence"], rows)


def _review_block(review: ReviewReport) -> str:
    verdict_colour = {"approved": "Green", "needs_revision": "Yellow", "reject": "Red"}
    head = f"<p>Verdict: {status_lozenge(review.verdict, verdict_colour.get(review.verdict, 'Grey'))}</p>"
    if not review.findings:
        return head + "<p>No findings.</p>"
    rows = [
        [
            status_lozenge(f.severity.value, _SEVERITY_COLOUR[f.severity]),
            esc(f.category),
            esc(f.location),
            esc(f.detail) + (f"<p><em>{esc(f.suggested_fix)}</em></p>" if f.suggested_fix else ""),
        ]
        for f in review.findings
    ]
    return head + expand(
        f"{len(review.findings)} finding(s)",
        table(["Severity", "Category", "Where", "Detail"], rows),
    )


# ---------------------------------------------------------------------------
# Reading storage format back out
# ---------------------------------------------------------------------------

_TAG = re.compile(r"<[^>]+>")
_HEADING = re.compile(r"<h([1-6])[^>]*>(.*?)</h\1>", re.DOTALL | re.IGNORECASE)


def storage_to_blocks(xhtml: str) -> list[tuple[str, str]]:
    """Split a Confluence page into ``(heading, text)`` blocks."""
    if not xhtml:
        return []
    cleaned = re.sub(r"<ac:plain-text-body><!\[CDATA\[(.*?)\]\]></ac:plain-text-body>", r"\1", xhtml, flags=re.DOTALL)
    parts = _HEADING.split(cleaned)
    blocks: list[tuple[str, str]] = []
    preamble = _plain(parts[0]) if parts else ""
    if preamble:
        blocks.append(("preamble", preamble))
    for i in range(1, len(parts) - 2, 3):
        heading = _plain(parts[i + 1]).strip() or "section"
        body = _plain(parts[i + 2])
        if body:
            blocks.append((f"heading: {heading}", body))
    return blocks


def _plain(fragment: str) -> str:
    text = re.sub(r"<(li|p|tr|br\s*/?)[^>]*>", "\n", fragment, flags=re.IGNORECASE)
    text = re.sub(r"</t[dh]>", " | ", text, flags=re.IGNORECASE)
    text = _TAG.sub("", text)
    text = (
        text.replace("&nbsp;", " ")
        .replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", '"')
    )
    return re.sub(r"\n{3,}", "\n\n", re.sub(r"[ \t]+", " ", text)).strip()
