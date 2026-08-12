"""What the system refuses to do.

Every test here has a demonstrated exploit behind it: each one was run against
the live application before the fix existed, and passed.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from prdforge.config import SecuritySettings
from prdforge.ingest.fetch import FetchError, FetchRefused, _refuse_private_target
from prdforge.models import (
    Evidence,
    Modality,
    Priority,
    ReqKind,
    Requirement,
    RequirementSet,
    SourceRef,
)
from prdforge.ui.app import build_app

WRITE = {"X-PRDForge-UI": "1"}


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    return TestClient(build_app(workspace=tmp_path))


# ---------------------------------------------------------------------------
# Arbitrary file read
# ---------------------------------------------------------------------------


def test_a_local_path_cannot_be_ingested_over_http(client: TestClient):
    """Demonstrated before the fix: `uris: ["file:///etc/passwd", ".../.env"]`
    returned both files as quotable evidence, provider API key included."""
    for hostile in ("file:///etc/passwd", "/etc/passwd", "FILE:///etc/passwd", "./.env"):
        response = client.post(
            "/api/runs",
            data={"request": f'{{"title": "x", "uris": ["{hostile}"]}}'},
            headers=WRITE,
        )
        assert response.status_code == 400, hostile
        assert "Refusing the local path" in response.json()["detail"]


def test_remote_schemes_are_still_accepted(client: TestClient):
    """The fix must not cost the feature: a URL is the point of the field."""
    response = client.post(
        "/api/runs",
        data={"request": '{"title": "x", "uris": ["https://example.com/spec.pdf"]}'},
        headers=WRITE,
    )
    # Accepted by the endpoint; whether the fetch succeeds is not this test's
    # business - only that it was not refused out of hand.
    assert response.status_code != 400


# ---------------------------------------------------------------------------
# SSRF
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "169.254.169.254", "10.0.0.1",
                                  "192.168.1.1", "172.16.0.1", "[::1]"])
def test_private_and_link_local_targets_are_refused(host: str):
    """Demonstrated before the fix: an internal service on 127.0.0.1 returned
    its response body as evidence. 169.254.169.254 is cloud credentials."""
    with pytest.raises(FetchRefused):
        _refuse_private_target(host.strip("[]"), f"http://{host}/")


def test_a_public_host_is_allowed():
    _refuse_private_target("example.com", "https://example.com/spec.pdf")


def test_a_refusal_is_a_fetch_error_so_one_source_fails_not_the_run():
    assert issubclass(FetchRefused, FetchError)


def test_the_escape_hatch_exists_for_internal_document_stores(monkeypatch):
    from prdforge import config

    monkeypatch.setattr(config, "settings",
                        lambda: config.Settings(security=SecuritySettings(allow_private_fetch=True)))
    _refuse_private_target("10.0.0.1", "http://10.0.0.1/spec.pdf")


# ---------------------------------------------------------------------------
# Cross-site writes
# ---------------------------------------------------------------------------


def test_a_write_without_the_ui_header_is_refused(client: TestClient):
    """A cross-origin form POST cannot set a header, and this app answers no
    preflight - so requiring one is what stops a page the operator visits from
    starting runs on their machine."""
    response = client.post("/api/runs", data={"request": '{"title": "x", "notes": ["hi"]}'})
    assert response.status_code == 403
    assert "X-PRDForge-UI" in response.json()["detail"]

    assert client.put("/api/settings", json={}).status_code == 403
    assert client.delete("/api/runs/whatever").status_code == 403


def test_reads_are_left_alone(client: TestClient):
    """The same-origin policy already stops another site reading these, and
    requiring a header on GET would break every plain link."""
    assert client.get("/api/runs").status_code == 200
    assert client.get("/healthz").status_code == 200


# ---------------------------------------------------------------------------
# The mesh secret
# ---------------------------------------------------------------------------


def test_enforcing_with_the_published_default_key_refuses_to_start():
    """Enforcement with the secret everybody has is worse than none: it reads
    as authentication in a config review and stops nobody."""
    with pytest.raises(RuntimeError, match="published in this repository"):
        SecuritySettings(enforce=True).guard()

    SecuritySettings(enforce=True, api_key="something-generated").guard()
    SecuritySettings().guard()  # not enforcing: still a laptop, still fine


# ---------------------------------------------------------------------------
# Resource bounds
# ---------------------------------------------------------------------------


async def test_runs_queue_instead_of_all_starting_at_once(tmp_path: Path):
    """Unbounded, N runs against a local server make it unload and reload a
    model between every call, so all of them finish later than if they had
    queued - and each holds the full text of everything it ingested."""
    import asyncio

    from prdforge.models import PRDRequest
    from prdforge.ui.runner import RunManager
    from prdforge.ui.store import RunStore

    manager = RunManager(RunStore(tmp_path / "runs.db"), max_concurrent=2)
    running, peak = 0, 0
    release = asyncio.Event()

    async def fake_run(run_id, request):  # noqa: ANN001, ANN202
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        await release.wait()
        running -= 1

    manager._run_now = fake_run
    runs = [await manager.start(PRDRequest(title=f"run {i}")) for i in range(5)]
    await asyncio.sleep(0.05)

    assert peak <= 2, f"{peak} runs executed at once against a limit of 2"
    release.set()
    await asyncio.gather(*(manager._tasks[r.id] for r in runs))
    assert peak <= 2

    manager.store.close()


# ---------------------------------------------------------------------------
# Traceability
# ---------------------------------------------------------------------------


def _prior() -> tuple[RequirementSet, Evidence]:
    evidence = Evidence(id="ev-1", source_id="src-1", locator="p.3", kind="statement",
                        modality=Modality.DOCUMENT, confidence=0.9,
                        text="Refunds must complete within 14 days.")
    prior = RequirementSet(requirements=[Requirement(
        id="REQ-001", title="Refund window", statement="The system must refund within 14 days.",
        kind=ReqKind.FUNCTIONAL, priority=Priority.MUST, derived=False,
        source_refs=[SourceRef(evidence_id="ev-1", source_id="src-1", locator="p.3",
                               quote=evidence.text)])])
    return prior, evidence


def _refine(statement: str) -> Requirement:
    from prdforge.agents.requirements.agent import DraftRequirement, RequirementDraft, _materialise

    prior, evidence = _prior()
    draft = RequirementDraft(requirements=[DraftRequirement(
        existing_id="REQ-001", title="Refund window", statement=statement,
        kind="functional", priority="must", evidence_ids=[], confidence=0.9)])
    return _materialise(draft, {"ev-1": evidence}, prior=prior).requirements[0]


def test_a_rewritten_requirement_cannot_inherit_the_old_evidence():
    """The hole this closes: a refinement rewrote "within 14 days" into "within
    24 hours", cited nothing, inherited the old citation and rendered as
    *sourced* - printing a quote that contradicted the requirement into the
    traceability matrix as though it were provenance."""
    rewritten = _refine("The system must refund within 24 HOURS and waive all restocking fees.")

    assert rewritten.source_refs == []
    assert rewritten.derived is True, "an uncited claim is derived - that is what the word is for"


def test_an_untouched_requirement_still_keeps_its_citations():
    """The behaviour the inheritance existed for. Losing this would demote every
    carried-forward requirement to `derived` on each refinement, telling the
    reader that facts sourced last week were inferred."""
    untouched = _refine("The system must refund within 14 days.")

    assert [r.evidence_id for r in untouched.source_refs] == ["ev-1"]
    assert untouched.derived is False


def test_reformatting_is_not_a_rewrite():
    same = _refine("The system  must   refund within 14 DAYS.")
    assert [r.evidence_id for r in same.source_refs] == ["ev-1"]


@pytest.mark.parametrize("filename", ["../../etc/passwd", "..", ".", "", "a/../../b"])
def test_an_upload_cannot_name_its_way_out_of_the_upload_directory(
    tmp_path: Path, filename: str
):
    """`.name` handles the classic traversal; a filename of exactly ".." slips
    through it and resolves to the parent, which is a 500 rather than an escape
    but should be neither."""
    import asyncio
    import io

    from starlette.datastructures import Headers
    from starlette.datastructures import UploadFile as StarletteUpload

    from prdforge.ui.app import _store_upload

    target = tmp_path / "uploads" / "run-1"
    upload = StarletteUpload(file=io.BytesIO(b"data"), filename=filename,
                             headers=Headers({"content-type": "text/plain"}))
    ref = asyncio.run(_store_upload(upload, target))

    written = Path(ref.uri.removeprefix("file://"))
    assert written.parent == target.resolve(), f"{filename!r} escaped to {written}"
    assert written.is_file()
