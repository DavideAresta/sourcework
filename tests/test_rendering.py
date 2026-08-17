"""Renderers must produce well-formed output and never lose traceability."""

from __future__ import annotations

import re
from xml.etree import ElementTree

import pytest

from sourcework.confluence.storage import render_prd
from sourcework.models import PRDDocument, ReviewFinding, ReviewReport, Severity
from sourcework.render import to_markdown


def test_storage_output_is_well_formed_xml(prd: PRDDocument):
    xhtml = render_prd(prd)
    # Storage format uses undeclared ac:/ri: prefixes, so declare them to parse.
    wrapped = (
        '<root xmlns:ac="http://atlassian.com/content" '
        f'xmlns:ri="http://atlassian.com/resource">{xhtml}</root>'
    )
    ElementTree.fromstring(wrapped)  # raises on malformed markup


def test_storage_escapes_hostile_content(prd: PRDDocument):
    prd.title = 'Ampersand & "quotes" <tag>'
    prd.summary = "5 < 6 & 7 > 6"
    prd.requirements.requirements[0].statement = 'if a < b && c > d then "go"'
    xhtml = render_prd(prd)
    wrapped = (
        '<root xmlns:ac="http://atlassian.com/content" '
        f'xmlns:ri="http://atlassian.com/resource">{xhtml}</root>'
    )
    ElementTree.fromstring(wrapped)
    assert "&amp;" in xhtml


def test_storage_contains_traceability_and_sources(prd: PRDDocument):
    xhtml = render_prd(prd)
    assert "Traceability" in xhtml
    assert "REQ-001" in xhtml
    assert "p.4" in xhtml
    assert prd.sources[0].title in xhtml


def test_markdown_has_every_requirement_and_citation(prd: PRDDocument):
    md = to_markdown(prd)
    for r in prd.requirements.requirements:
        assert r.id in md
        assert r.title in md
    assert "## Traceability" in md
    assert "p.4" in md


def test_markdown_table_cells_escape_pipes(prd: PRDDocument):
    prd.requirements.requirements[0].statement = "a | b | c"
    md = to_markdown(prd)
    row = next(ln for ln in md.splitlines() if ln.startswith("| REQ-001"))
    # 6 unescaped delimiters for 5 columns; the content pipes survive as "\|"
    assert len(re.findall(r"(?<!\\)\|", row)) == 6
    assert r"a \| b \| c" in md


def test_review_block_renders(prd: PRDDocument):
    report = ReviewReport(
        findings=[
            ReviewFinding(
                severity=Severity.BLOCKER,
                category="unsupported",
                location="REQ-001",
                detail="No evidence.",
                suggested_fix="Cite something.",
            )
        ],
        verdict="needs_revision",
    )
    assert "needs_revision" in to_markdown(prd, report)
    assert "unsupported" in render_prd(prd, report)


def test_estimates_render_marked_as_inference(prd: PRDDocument):
    req = prd.requirements.requirements[0]
    req.effort = "M"
    req.effort_rationale = "one integration"
    md = to_markdown(prd)
    assert "≈M" in md
    assert "inferred by the model" in md  # the honesty note rides along
    xhtml = render_prd(prd)
    assert "≈M" in xhtml


def test_no_estimates_means_no_estimate_column(prd: PRDDocument):
    """An all-empty column would read as data that failed to load."""
    md = to_markdown(prd)
    assert "≈" not in md
    row = next(ln for ln in md.splitlines() if ln.startswith("| REQ-001"))
    assert len(re.findall(r"(?<!\\)\|", row)) == 6  # 5 columns, unchanged
    assert "≈" not in render_prd(prd)


@pytest.mark.parametrize("effort", [None, "M"])
def test_every_delimiter_row_matches_its_header(prd: PRDDocument, effort: str | None):
    """A delimiter row one cell short renders the whole table as literal pipes.

    GFM only recognises a table when the two counts agree, so this is not a
    cosmetic property: it is the difference between a requirements table and a
    paragraph full of dashes, in every renderer except the UI's own (which
    skips the delimiter and would never have shown the break).
    """
    from sourcework.models import Milestone

    prd.requirements.requirements[0].effort = effort
    prd.milestones = [Milestone(name="M1", requirement_ids=["REQ-001"])]
    lines = to_markdown(prd).splitlines()
    delimiters = [i for i, ln in enumerate(lines) if re.fullmatch(r"\|(---\|)+", ln)]
    assert delimiters  # the document does have tables to check
    for i in delimiters:
        header, delimiter = lines[i - 1], lines[i]
        assert header.count("|") == delimiter.count("|"), f"{header}\n{delimiter}"


def test_milestone_rollup_is_counted_in_code(prd: PRDDocument):
    from sourcework.models import Milestone

    reqs = prd.requirements.requirements
    reqs[0].effort, reqs[1].effort = "S", "XL"
    prd.milestones = [Milestone(name="M1", requirement_ids=["REQ-001", "REQ-002"])]
    md = to_markdown(prd)
    assert "1S 1XL" in md
    assert "1S 1XL" in render_prd(prd)


def test_the_status_lozenge_takes_the_colour_of_the_decision(prd: PRDDocument):
    """A signed-off page and a rejected one have to read differently at a glance."""
    def lozenge_colour(xhtml: str, label: str) -> str:
        # The colour parameter that follows this label's title parameter.
        after = xhtml.split(f'"title">{label}</ac:parameter>', 1)[1]
        return re.search(r'"colour">(\w+)<', after).group(1)

    prd.status = "approved"
    assert lozenge_colour(render_prd(prd), "APPROVED") == "Green"
    prd.status = "rejected"
    assert lozenge_colour(render_prd(prd), "REJECTED") == "Red"
    prd.status = "draft"
    assert lozenge_colour(render_prd(prd), "DRAFT") == "Yellow"


def test_empty_prd_still_renders(prd: PRDDocument):
    empty = PRDDocument(title="Nothing yet")
    assert empty.title in to_markdown(empty)
    wrapped = (
        '<root xmlns:ac="http://atlassian.com/content" '
        f'xmlns:ri="http://atlassian.com/resource">{render_prd(empty)}</root>'
    )
    ElementTree.fromstring(wrapped)
