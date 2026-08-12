"""Renderers must produce well-formed output and never lose traceability."""

from __future__ import annotations

import re
from xml.etree import ElementTree

from prdforge.confluence.storage import render_prd
from prdforge.models import PRDDocument, ReviewFinding, ReviewReport, Severity
from prdforge.render import to_markdown


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


def test_empty_prd_still_renders(prd: PRDDocument):
    empty = PRDDocument(title="Nothing yet")
    assert empty.title in to_markdown(empty)
    wrapped = (
        '<root xmlns:ac="http://atlassian.com/content" '
        f'xmlns:ri="http://atlassian.com/resource">{render_prd(empty)}</root>'
    )
    ElementTree.fromstring(wrapped)
