"""The wording rules a requirements document is checked against.

Each rule exists because a real review caught the phrasing it flags. They run
in code, not in the critic's prompt, because a rule that depends on the model
remembering to apply it is a rule that sometimes does not apply.
"""

from __future__ import annotations

from sourcework.models import Priority, ReqKind, Requirement, ReviewFinding, Severity
from sourcework.quality import quality_score, rule_findings


def _req(
    statement: str, *, priority: Priority = Priority.MUST, ac: list[str] | None = None
) -> Requirement:
    return Requirement(
        id="REQ-001",
        title="t",
        statement=statement,
        kind=ReqKind.FUNCTIONAL,
        priority=priority,
        acceptance_criteria=ac or [],
    )


def _by_rule(findings: list[ReviewFinding], fragment: str) -> list[ReviewFinding]:
    return [f for f in findings if fragment in f.detail]


def test_a_clean_statement_passes_every_rule():
    findings = rule_findings(
        [
            _req(
                "The system must settle each batch within 2 hours.",
                ac=["A batch settles in under 120 minutes."],
            )
        ]
    )
    assert findings == []


def test_escape_clauses_are_flagged():
    findings = rule_findings([_req("The system must archive records where practical.")])
    assert _by_rule(findings, "Escape clause")


def test_open_ended_lists_are_flagged():
    findings = rule_findings([_req("The system must support exports such as CSV.")])
    assert _by_rule(findings, "Open-ended")


def test_absolutes_are_flagged():
    findings = rule_findings([_req("The system must never lose an audit record.")])
    assert _by_rule(findings, "Absolute")


def test_superfluous_infinitives_are_flagged():
    findings = rule_findings([_req("The system must be able to retry failed batches.")])
    assert _by_rule(findings, "Superfluous infinitive")


def test_negative_requirements_are_a_nit():
    findings = rule_findings([_req("The system shall not store credentials in logs.")])
    hits = _by_rule(findings, "Negative requirement")
    assert hits and hits[0].severity == Severity.NIT


def test_two_obligations_in_one_statement_are_flagged():
    findings = rule_findings([_req("The system must settle batches and must alert on failure.")])
    assert _by_rule(findings, "Compound requirement")


def test_a_single_obligation_is_singular():
    findings = rule_findings([_req("The system must settle each batch within 2 hours.")])
    assert not _by_rule(findings, "Compound")


def test_a_priority_that_disagrees_with_its_modal_verb_is_flagged():
    findings = rule_findings(
        [_req("The system should settle each batch within 2 hours.", priority=Priority.MUST)]
    )
    assert _by_rule(findings, "Priority is 'must'")


def test_an_unmeasurable_requirement_is_flagged():
    findings = rule_findings([_req("The system must reconcile invoices reliably.")])
    assert _by_rule(findings, "Nothing measurable")


def test_a_numbered_acceptance_criterion_satisfies_measurability():
    req = _req(
        "The system must reconcile invoices.",
        ac=["A 15,000-invoice run completes in under 120 minutes."],
    )
    findings = rule_findings([req])
    assert not _by_rule(findings, "Nothing measurable")


def test_glossary_terms_are_not_flagged_but_undefined_acronyms_are():
    req = _req("The system must post each batch to the ERP within 10 minutes.")
    assert _by_rule(rule_findings([req], glossary={}), "not in the glossary")
    assert not _by_rule(
        rule_findings([req], glossary={"ERP": "the accounting system"}), "not in the glossary"
    )


def test_well_known_acronyms_are_not_flagged():
    assert not _by_rule(
        rule_findings([_req("The system must expose a JSON API over HTTPS.")]), "glossary"
    )


def test_ears_is_off_unless_asked_for():
    req = _req("Batches settle every night at 2 AM.")  # no EARS shape
    assert not _by_rule(rule_findings([req]), "EARS")
    assert _by_rule(rule_findings([req], ears=True), "EARS")


def test_ears_accepts_each_of_the_five_shapes():
    shapes = [
        "The system must settle batches within 2 hours.",
        "When a batch fails, the system must alert the operator.",
        "While in maintenance mode, the system must reject writes.",
        "If a duplicate is detected, then the system must quarantine it.",
        "Where multi-currency is enabled, the system must store the currency code.",
    ]
    for shape in shapes:
        assert not _by_rule(rule_findings([_req(shape)], ears=True), "EARS"), shape


def test_the_score_counts_only_clean_requirements():
    reqs = [
        _req("The system must settle each batch within 2 hours.", ac=["under 120 minutes"]),
        _req("The system must be fast."),  # vague + unmeasurable
    ]
    reqs[0].id, reqs[1].id = "REQ-001", "REQ-002"
    findings = rule_findings(reqs)
    assert quality_score(reqs, findings) == 0.5


def test_the_score_ignores_traceability_findings():
    """A citation problem is already visible as one; the quality score is about wording."""
    reqs = [_req("The system must settle each batch within 2 hours.", ac=["under 120 minutes"])]
    citation = ReviewFinding(
        severity=Severity.MAJOR, category="unsupported", location="REQ-001", detail="cites nothing"
    )
    assert quality_score(reqs, [citation]) == 1.0


def test_an_empty_set_scores_full_marks():
    assert quality_score([], []) == 1.0
