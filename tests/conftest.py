import os

os.environ.setdefault("SOURCEWORK_LLM__STUB", "1")
os.environ.setdefault("SOURCEWORK_LOG_LEVEL", "WARNING")

import pytest  # noqa: E402

from sourcework.models import (  # noqa: E402
    Evidence,
    Modality,
    PRDDocument,
    Priority,
    ReqKind,
    Requirement,
    RequirementSet,
    SourceDocument,
    SourceRef,
)


@pytest.fixture(autouse=True)
def _hermetic_env(monkeypatch):
    """No developer .env may leak into the suite. Importing litellm calls
    ``load_dotenv()``, which seeds os.environ with the checkout's real .env,
    and pydantic-settings prefers env vars over dotenv files - so a Settings
    built from a tmp .env silently reads the developer's values. Scrub every
    SOURCEWORK_* key before each test, then re-apply the suite's own two."""
    for key in [k for k in os.environ if k.startswith("SOURCEWORK_")]:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("SOURCEWORK_LLM__STUB", "1")
    monkeypatch.setenv("SOURCEWORK_LOG_LEVEL", "WARNING")


@pytest.fixture
def source() -> SourceDocument:
    return SourceDocument(
        id="src-1", uri="file:///spec.pdf", title="Spec", modality=Modality.DOCUMENT
    )


@pytest.fixture
def evidence(source: SourceDocument) -> list[Evidence]:
    return [
        Evidence(
            id="ev-1",
            source_id=source.id,
            modality=Modality.DOCUMENT,
            text="The nightly run must finish within two hours.",
            locator="p.4",
            kind="constraint",
        ),
        Evidence(
            id="ev-2",
            source_id=source.id,
            modality=Modality.DOCUMENT,
            text="Audit records are retained for seven years.",
            locator="p.5",
            kind="constraint",
        ),
    ]


@pytest.fixture
def prd(source: SourceDocument, evidence: list[Evidence]) -> PRDDocument:
    reqs = RequirementSet(
        requirements=[
            Requirement(
                id="REQ-001",
                title="Nightly run duration",
                statement="The nightly matching run must complete within two hours for 15,000 invoices.",
                kind=ReqKind.NON_FUNCTIONAL,
                priority=Priority.MUST,
                acceptance_criteria=["A 15,000-invoice run completes in under 120 minutes."],
                source_refs=[
                    SourceRef(evidence_id="ev-1", source_id="src-1", locator="p.4", quote=evidence[0].text)
                ],
            ),
            Requirement(
                id="REQ-002",
                title="Audit retention",
                statement="The system must retain immutable audit records for seven years.",
                kind=ReqKind.CONSTRAINT,
                priority=Priority.MUST,
                acceptance_criteria=["Records older than one year remain byte-identical."],
                source_refs=[
                    SourceRef(evidence_id="ev-2", source_id="src-1", locator="p.5", quote=evidence[1].text)
                ],
            ),
        ]
    )
    return PRDDocument(
        title="Invoice reconciliation",
        summary="Automate invoice-to-PO matching.",
        problem_statement="Finance reconciles by hand for three days each month.",
        goals=["Cut month-end reconciliation effort"],
        non_goals=["Replacing ERP approvals"],
        requirements=reqs,
        sources=[source],
        evidence=evidence,
    )
