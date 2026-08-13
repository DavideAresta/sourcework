"""Saving what a run produced, so an interruption costs minutes and not hours."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from sourcework.agents.orchestrator import checkpoint, pipeline
from sourcework.models import (
    Evidence,
    ExtractionResult,
    InputRef,
    Modality,
    PRDRequest,
    RequirementSet,
    SourceDocument,
)


@pytest.fixture(autouse=True)
def workspace(tmp_path, monkeypatch):
    """Checkpoints under the test's own directory, never the developer's."""
    monkeypatch.setattr(checkpoint.paths, "workspace", lambda *a, **k: tmp_path)
    return tmp_path


def _extraction(text: str = "a claim") -> ExtractionResult:
    source = SourceDocument(id="src-1", uri="file:///a.md", title="A", modality=Modality.DOCUMENT)
    return ExtractionResult(
        source=source,
        evidence=[
            Evidence(id="ev-1", source_id="src-1", modality=Modality.DOCUMENT, text=text)
        ],
    )


# --- the store itself ------------------------------------------------------


def test_a_saved_stage_comes_back_as_the_object_that_was_stored():
    """Artifacts, not recipes. Evidence ids are minted randomly, so anything
    that re-derived a stage would hand back the same claims under new ids and
    break every citation in a PRD already written against them."""
    cp = checkpoint.Checkpoint(run_id="r1", resume=True)
    cp.save("ingest", "fp", [_extraction()])

    loaded = cp.load("ingest", "fp", lambda d: [ExtractionResult.model_validate(e) for e in d])

    assert loaded is not None
    assert loaded[0].evidence[0].id == "ev-1"
    assert cp.reused == ["ingest"]


def test_saving_happens_without_being_asked_but_reusing_does_not():
    """Cancelling is usually "I picked the wrong model". A resume that happened
    by itself would rebuild the PRD from output the user had just rejected -
    but writing costs nothing, so the option stays open."""
    writer = checkpoint.Checkpoint(run_id="r1")
    writer.save("ingest", "fp", [_extraction()])

    assert writer.path.is_file()
    assert writer.load("ingest", "fp", list) is None

    asked = checkpoint.Checkpoint(run_id="r1", resume=True)
    assert asked.load("ingest", "fp", list) is not None


def test_a_stage_produced_by_a_different_configuration_is_not_reused():
    """The alternative is a PRD that is half one model and half another, with
    nothing on its face to say so. Losing the work is the better failure."""
    cp = checkpoint.Checkpoint(run_id="r1", resume=True)
    cp.save("analyse", "fingerprint-of-run-one", RequirementSet())

    assert cp.load("analyse", "fingerprint-of-run-two", RequirementSet.model_validate) is None
    assert cp.reused == []


def test_nothing_is_saved_and_nothing_breaks_without_a_run_id():
    """A caller with nowhere to resume to gets a working no-op rather than a
    special case at every call site."""
    cp = checkpoint.Checkpoint()
    cp.save("ingest", "fp", [_extraction()])
    cp.clear()

    assert cp.path is None
    assert cp.load("ingest", "fp", list) is None


@pytest.mark.parametrize("content", ["}{ not json", '{"version": 99, "stages": {}}', ""])
def test_an_unusable_checkpoint_file_costs_time_not_the_run(workspace, content):
    """Every failure recomputes, because recomputing is always correct and only
    slow. A checkpoint that could raise would be insurance that causes the
    accident."""
    checkpoint.directory().mkdir(parents=True, exist_ok=True)
    (checkpoint.directory() / "r1.json").write_text(content)

    assert checkpoint.Checkpoint(run_id="r1", resume=True).load("ingest", "fp", list) is None
    assert checkpoint.saved_stages("r1") == []


def test_a_stage_whose_shape_no_longer_parses_is_recomputed(workspace):
    """A checkpoint written by an older build outlives the schema that made it."""
    cp = checkpoint.Checkpoint(run_id="r1", resume=True)
    cp.save("analyse", "fp", {"requirements": "this was a list once"})

    assert cp.load("analyse", "fp", RequirementSet.model_validate) is None


def test_the_file_is_replaced_whole_so_a_crash_cannot_truncate_it(workspace):
    cp = checkpoint.Checkpoint(run_id="r1")
    cp.save("ingest", "fp", [_extraction()])
    cp.save("analyse", "fp", RequirementSet())

    document = json.loads(cp.path.read_text())
    assert sorted(document["stages"]) == ["analyse", "ingest"]
    assert not list(checkpoint.directory().glob("*.tmp"))


# --- fingerprints ----------------------------------------------------------


def test_editing_a_source_file_invalidates_the_evidence_taken_from_it(tmp_path):
    """The path does not change when somebody rewrites the document, and reusing
    the old evidence would attribute quotes to a file that no longer contains
    them."""
    document = tmp_path / "spec.md"
    document.write_text("first draft")
    ref = InputRef(uri=str(document))
    before = checkpoint.input_identity(ref)

    document.write_text("a second, longer draft")

    assert checkpoint.input_identity(ref) != before


def test_a_fingerprint_survives_a_dict_being_reordered():
    """Pydantic can hand back the same payload with its keys in a different
    order; discarding good work over that would make resume useless."""
    assert checkpoint.digest({"a": 1, "b": 2}) == checkpoint.digest({"b": 2, "a": 1})


# --- the pipeline ----------------------------------------------------------


class FakePool:
    """Enough AgentPool to drive the pipeline, and a record of what was called."""

    def __init__(self, *, fail_at: str | None = None) -> None:
        self.calls: list[str] = []
        self.fail_at = fail_at
        self.usage = SimpleNamespace(as_dict=dict)

    async def discover(self) -> dict[str, list[str]]:
        return {
            "ingestion": ["extract_document"],
            "requirements": ["analyse_requirements"],
            "writer": ["write_prd"],
            "critic": ["review_prd"],
        }

    async def call(self, agent: str, skill: str, payload, on_progress=None):  # noqa: ANN001
        self.calls.append(skill)
        if skill == self.fail_at:
            raise RuntimeError("the backend timed out")
        if skill == "extract_document":
            return _extraction().model_dump(mode="json")
        if skill == "analyse_requirements":
            return RequirementSet().model_dump(mode="json")
        raise AssertionError(f"unexpected call to {skill}")


def _request(**overrides) -> PRDRequest:
    return PRDRequest(
        title="Returns",
        inputs=[InputRef(uri="inline:note", text="Returns must be free.", title="Note")],
        run_id="r1",
        **overrides,
    )


async def test_a_run_that_dies_in_analysis_keeps_the_evidence_it_extracted():
    """The failure that motivated all of this: the last call of the longest
    phase unwound the stack and took two minutes of extraction with it."""
    pool = FakePool(fail_at="analyse_requirements")
    with pytest.raises(Exception, match="timed out"):
        await pipeline.run(_request(), pool)

    assert checkpoint.saved_stages("r1") == ["ingest"]


async def test_resuming_does_not_read_the_sources_again():
    pool = FakePool(fail_at="analyse_requirements")
    with pytest.raises(Exception, match="timed out"):
        await pipeline.run(_request(), pool)

    second = FakePool(fail_at="analyse_requirements")
    with pytest.raises(Exception, match="timed out"):
        await pipeline.run(_request(resume=True), second)

    assert second.calls == ["analyse_requirements"]
    assert checkpoint.saved_stages("r1") == ["ingest"]


async def test_a_rerun_that_did_not_ask_to_resume_starts_over():
    pool = FakePool(fail_at="analyse_requirements")
    with pytest.raises(Exception, match="timed out"):
        await pipeline.run(_request(), pool)

    second = FakePool(fail_at="analyse_requirements")
    with pytest.raises(Exception, match="timed out"):
        await pipeline.run(_request(), second)

    assert second.calls == ["extract_document", "analyse_requirements"]


async def test_a_run_with_no_id_saves_nothing(workspace):
    """The CLI's one-shot generate has nowhere to resume to."""
    pool = FakePool(fail_at="analyse_requirements")
    with pytest.raises(Exception, match="timed out"):
        await pipeline.run(_request().model_copy(update={"run_id": None}), pool)

    assert not checkpoint.directory().exists() or not list(checkpoint.directory().iterdir())


async def test_changing_the_evidence_invalidates_the_requirements_built_on_it():
    """Fingerprints chain: new evidence means the stored requirement set no
    longer describes the inputs, resume or not."""
    pool = FakePool(fail_at="write_prd")
    with pytest.raises(Exception, match="timed out"):
        await pipeline.run(_request(), pool)
    assert checkpoint.saved_stages("r1") == ["ingest", "analyse"]

    changed = _request(resume=True).model_copy(
        update={"inputs": [InputRef(uri="inline:note", text="Returns cost 5 euros.")]}
    )
    second = FakePool(fail_at="write_prd")
    with pytest.raises(Exception, match="timed out"):
        await pipeline.run(changed, second)

    assert second.calls == ["extract_document", "analyse_requirements", "write_prd"]
