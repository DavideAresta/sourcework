"""Deterministic requirements-quality rules.

The critic is half a model and half this file. The model handles the judgement
calls; everything here runs in Python, because a quality rule that is itself a
model call cannot be trusted to fire every time - and these are the rules that
must fire every time.

The rule basis is public standards, not taste:

* **ISO/IEC/IEEE 29148** - the characteristics a well-formed requirement has:
  singular, unambiguous, verifiable, consistent. The *traceable* and
  *necessary* characteristics are covered elsewhere (citation validation and
  the critic model respectively); the grammar-level ones are checked here.
* **INCOSE Guide to Writing Requirements** - the operational rules: no vague
  terms, no escape clauses, no open-ended lists, no superfluous infinitives,
  defined terms only.
* **EARS** (Easy Approach to Requirements Syntax) - optional. Off by default
  because it constrains *phrasing*, which is a team choice, not a defect. With
  ``SOURCEWORK_QUALITY__EARS=1`` the analyst is asked to write EARS-shaped
  statements and non-conforming ones are flagged here.

Every finding is a ``minor`` at worst. These rules exist to be fixed in the
revision loop, not to block a document over a semicolon.
"""

from __future__ import annotations

import re

from sourcework.models import Requirement, ReviewFinding, Severity

STANDARDS_BASIS = "ISO/IEC/IEEE 29148 characteristics; INCOSE Guide to Writing Requirements"
"""The rule-pack provenance, recorded in the review report and the audit bundle
so a reader can see what "quality" was ever checked against."""


def standards_line(*, ears: bool) -> str:
    """The basis note rendered into the PRD's review section."""
    return f"{STANDARDS_BASIS}; EARS patterns {'on' if ears else 'off'}"


# -- word-level rules --------------------------------------------------------
# (rule id, pattern, severity, what is wrong, how to fix it)

_WORD_RULES: tuple[tuple[str, re.Pattern[str], Severity, str, str], ...] = (
    (
        "escape-clause",
        re.compile(
            r"\b(as appropriate|if appropriate|if feasible|where practical|"
            r"if possible|to be determined|\bTBD\b)\b",
            re.IGNORECASE,
        ),
        Severity.MINOR,
        "Escape clause: {hit}",
        "State the condition or the owner of the decision instead.",
    ),
    (
        "open-ended",
        re.compile(
            r"\b(such as|including but not limited to|among others|and more)\b",
            re.IGNORECASE,
        ),
        Severity.MINOR,
        "Open-ended wording: {hit}",
        "Close the list, or bound it.",
    ),
    (
        "absolute",
        re.compile(
            r"\b(never|always|100\s?%|zero downtime|no downtime)\b",
            re.IGNORECASE,
        ),
        Severity.MINOR,
        "Absolute wording: {hit}",
        "Absolutes are almost never achievable - bound it.",
    ),
    (
        "superfluous-infinitive",
        re.compile(r"\b(be designed to|be able to|be capable of)\b", re.IGNORECASE),
        Severity.NIT,
        "Superfluous infinitive: {hit}",
        "Write the obligation directly: 'the system must X'.",
    ),
    (
        "negative",
        re.compile(r"\b(shall not|must not)\b", re.IGNORECASE),
        Severity.NIT,
        "Negative requirement: {hit}",
        "State what the system does rather than what it does not, where possible.",
    ),
)

_MODALS = re.compile(r"\b(shall|must|should|may)\b", re.IGNORECASE)
"""Obligation verbs. More than one in a statement is two requirements wearing
one id - the split is what makes each half independently testable."""

# The modal a priority expects. A mismatch reads as a requirement that was
# prioritised by one person and written by another.
_PRIORITY_MODALS = {
    "must": {"must", "shall"},
    "should": {"should"},
    "could": {"could", "may"},
}

_MEASURABLE = re.compile(
    r"\d|(%|percent|\bms\b|\bseconds?\b|\bminutes?\b|\bhours?\b|\bdays?\b|"
    r"\busers?\b|\brequests?\b|\bwithin\b|\bper\b|\bat least\b|\bat most\b)",
    re.IGNORECASE,
)
"""Anything a pass/fail test could key on. A requirement with no measurable
term anywhere - statement *and* criteria - cannot be verified, only agreed
with."""

_ACRONYM = re.compile(r"\b[A-Z][A-Z0-9]{1,}\b")
_ACRONYM_KNOWN = {"API", "UI", "URL", "ID", "PRD", "REQ", "HTTP", "HTTPS", "JSON", "SQL"}
"""Widely understood without a glossary entry. Everything else in caps that the
glossary does not define is a term the reader has to guess at."""

# EARS patterns: ubiquitous, event-driven, state-driven, unwanted, optional.
# Checked loosely - the point is "one of the known shapes", not a parse tree.
_EARS = re.compile(
    r"^(the system\b|when\b.+\bt|while\b.+\bt|if\b.+\bthen\b.+\bt|where\b.+\bt)",
    re.IGNORECASE,
)
_EARS_HEADS = re.compile(r"^(when|while|if|where)\b", re.IGNORECASE)


def rule_findings(
    requirements: list[Requirement],
    glossary: dict[str, str] | None = None,
    *,
    ears: bool = False,
) -> list[ReviewFinding]:
    """Run every deterministic quality rule over ``requirements``.

    All findings attach to a requirement id; the score in
    :func:`quality_score` counts how many requirements come back clean.
    """
    glossary_terms = {t.casefold() for t in (glossary or {})}
    out: list[ReviewFinding] = []
    for req in requirements:
        statement = req.statement

        for _rule_id, pattern, severity, detail, fix in _WORD_RULES:
            hits = sorted({m.group(0) for m in pattern.finditer(statement)})
            if hits:
                out.append(
                    ReviewFinding(
                        severity=severity,
                        category="quality",
                        location=req.id,
                        detail=detail.format(hit=", ".join(hits)) + ".",
                        suggested_fix=fix,
                    )
                )

        modals = [m.group(1).lower() for m in _MODALS.finditer(statement)]
        if len(modals) > 1:
            out.append(
                ReviewFinding(
                    severity=Severity.MINOR,
                    category="quality",
                    location=req.id,
                    detail="Compound requirement: more than one obligation verb "
                    f"({', '.join(modals)}).",
                    suggested_fix="Split it - one statement, one obligation, one test.",
                )
            )

        expected = _PRIORITY_MODALS.get(req.priority.value)
        if expected and modals and not expected & set(modals):
            out.append(
                ReviewFinding(
                    severity=Severity.MINOR,
                    category="quality",
                    location=req.id,
                    detail=f"Priority is '{req.priority.value}' but the statement says "
                    f"'{modals[0]}'.",
                    suggested_fix="Align the modal verb with the priority, or change the priority.",
                )
            )

        body = statement + " " + " ".join(req.acceptance_criteria)
        if not _MEASURABLE.search(body):
            out.append(
                ReviewFinding(
                    severity=Severity.MINOR,
                    category="quality",
                    location=req.id,
                    detail="Nothing measurable in the statement or its acceptance criteria.",
                    suggested_fix="Add a number, a unit or a time bound someone could test against.",
                )
            )

        undefined = sorted(
            {
                m.group(0)
                for m in _ACRONYM.finditer(statement)
                if m.group(0) not in _ACRONYM_KNOWN and m.group(0).casefold() not in glossary_terms
            }
        )
        if undefined:
            out.append(
                ReviewFinding(
                    severity=Severity.NIT,
                    category="quality",
                    location=req.id,
                    detail=f"Term(s) used but not in the glossary: {', '.join(undefined)}.",
                    suggested_fix="Add a glossary entry or write the term out.",
                )
            )

        if ears and not _ears_conforms(statement):
            out.append(
                ReviewFinding(
                    severity=Severity.MINOR,
                    category="quality",
                    location=req.id,
                    detail="Statement does not follow an EARS pattern (ubiquitous, "
                    "WHEN, WHILE, IF-THEN or WHERE).",
                    suggested_fix="Rewrite as e.g. 'When <trigger>, the system shall <response>'.",
                )
            )

    return out


def _ears_conforms(statement: str) -> bool:
    """Does the statement take one of the five EARS shapes?

    Deliberately shallow: a full grammar would reject good requirements for a
    missing comma. What is checked is the skeleton - a recognised opening (or
    the ubiquitous form) plus an obligation verb later in the sentence.
    """
    text = statement.strip()
    if not _EARS.match(text):
        return False
    if _EARS_HEADS.match(text) and "the system" not in text.casefold():
        return False
    return _MODALS.search(text) is not None


def quality_score(requirements: list[Requirement], findings: list[ReviewFinding]) -> float:
    """The share of requirements with no quality finding, 0..1.

    Only wording rules count - a requirement with an unsupported claim is a
    traceability problem and is already visible as such; mixing the two would
    make this number read as both and mean neither.
    """
    if not requirements:
        return 1.0
    flagged = {
        f.location
        for f in findings
        if f.category in ("quality", "ambiguous") and f.location.startswith("REQ-")
    }
    clean = sum(1 for r in requirements if r.id not in flagged)
    return clean / len(requirements)
