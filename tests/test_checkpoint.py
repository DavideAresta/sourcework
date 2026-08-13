"""Saving what a run produced, so an interruption costs minutes and not hours."""

from __future__ import annotations

import json
import os
import time
from types import SimpleNamespace

import pytest

from sourcework import checkpoint
from sourcework.agents.orchestrator import pipeline
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


def test_a_stage_holding_more_than_one_model_is_still_written(workspace):
    """Save failures are swallowed - insurance must not cause the accident - so
    a payload this could not serialise would leave the stage silently unwritten
    and nothing to say why."""
    cp = checkpoint.Checkpoint(run_id="r1", resume=True)
    cp.save("ingest", "fp", {"extractions": [_extraction()], "routed": {"A": "ingestion.x"}})

    stored = cp.load("ingest", "fp", lambda d: d)
    assert stored is not None
    assert stored["extractions"][0]["evidence"][0]["id"] == "ev-1"


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


async def test_a_resumed_run_still_reports_which_agent_handled_which_input():
    """The stats block names the route taken for every input. A resumed run
    that reported an empty map would describe work that did happen as work that
    did not."""
    pool = FakePool(fail_at="analyse_requirements")
    with pytest.raises(Exception, match="timed out"):
        await pipeline.run(_request(), pool)

    second = FakePool(fail_at="analyse_requirements")
    with pytest.raises(Exception, match="timed out"):
        await pipeline.run(_request(resume=True), second)

    stored = json.loads((checkpoint.directory() / "r1.json").read_text())
    assert stored["stages"]["ingest"]["data"]["routed"] == {
        "Note": "ingestion.extract_document"
    }


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


# --- retention and the command line ----------------------------------------


def test_saved_runs_are_offered_newest_first(workspace):
    """A bare `--resume` means "the one I was just watching"."""
    for run_id in ("older", "newer"):
        checkpoint.Checkpoint(run_id=run_id).save("ingest", "fp", [_extraction()])
    old = checkpoint.directory() / "older.json"
    os.utime(old, (0, 0))

    assert checkpoint.saved_runs() == ["newer", "older"]


def test_checkpoints_nobody_came_back_for_are_eventually_dropped(workspace):
    """A finished run deletes its own; these are the abandoned ones, and each
    holds the full text of every source it ingested."""
    checkpoint.Checkpoint(run_id="stale").save("ingest", "fp", [_extraction()])
    checkpoint.Checkpoint(run_id="fresh").save("ingest", "fp", [_extraction()])
    forgotten = checkpoint.directory() / "stale.json"
    os.utime(forgotten, (0, 0))

    assert checkpoint.prune() == 1
    assert checkpoint.saved_runs() == ["fresh"]


def test_pruning_keeps_a_run_the_user_could_still_resume(workspace):
    checkpoint.Checkpoint(run_id="yesterday").save("ingest", "fp", [_extraction()])
    day_old = checkpoint.directory() / "yesterday.json"
    os.utime(day_old, (time.time() - 86400, time.time() - 86400))

    assert checkpoint.prune() == 0


def test_resuming_with_nothing_saved_says_so_instead_of_starting_a_run(workspace, capsys):
    """Exit 2, not a silent full run: somebody who typed --resume is telling you
    they do not want to pay for the whole thing again."""
    from sourcework.cli import main

    assert main(["generate", "Returns", "-n", "a note", "--resume"]) == 2
    assert "Nothing to resume" in capsys.readouterr().err


def test_resuming_a_run_that_never_saved_anything_names_it(workspace, capsys):
    from sourcework.cli import main

    assert main(["generate", "Returns", "-n", "a note", "--resume", "nosuchrun"]) == 2
    assert "nosuchrun saved no stages" in capsys.readouterr().err


# --- inside the analyst ----------------------------------------------------


class FakeLLM:
    """The analyst's model, minus the model. Records what it was asked."""

    def __init__(self, *, fail_on: str | None = None) -> None:
        self.prompts: list[str] = []
        self.fail_on = fail_on

    async def structured(self, system, user, model, role=None):  # noqa: ANN001
        from sourcework.agents.requirements.agent import (
            DraftRequirement,
            MergeDecision,
            RequirementDraft,
        )

        if model is MergeDecision:
            return MergeDecision()
        self.prompts.append(user)
        if self.fail_on and self.fail_on in user:
            raise RuntimeError("the backend timed out")
        claim = user.rsplit("\n", 1)[-1]
        return RequirementDraft(
            requirements=[DraftRequirement(title=claim[:20], statement=claim, evidence_ids=[])]
        )


def _slices(*texts: str) -> list[list[Evidence]]:
    """One evidence item per slice, with fixed ids.

    Ids are minted randomly in real use and appear in the rendered prompt, so
    they must be pinned here or two calls would look like different work - which
    is exactly what happens when a resumed run re-ingests instead of reusing,
    and exactly why it should.
    """
    return [
        [Evidence(id=f"ev-{text}", source_id="src-1", modality=Modality.DOCUMENT, text=text)]
        for text in texts
    ]


async def _analyse(llm, saved, config_fp="cfg", batches=None):  # noqa: ANN001
    """Drive the batching path directly."""
    from sourcework.agents.requirements.agent import RequirementsExecutor

    async def progress(_message: str) -> None:
        return None

    batches = batches if batches is not None else _slices("alpha", "beta")
    return await RequirementsExecutor._analyse_in_batches(  # noqa: SLF001 - no mesh needed
        SimpleNamespace(llm=llm),
        batches,
        {"src-1": "A"},
        "SYSTEM",
        lambda body: f"Product: X\n{body}",
        progress,
        saved,
        config_fp,
    )


async def test_a_slice_that_finished_is_not_analysed_again():
    """The phase this exists for: minutes per slice, and the stage-level
    checkpoint cannot help because this lives inside a single stage."""
    first = FakeLLM(fail_on="beta")
    await _analyse(first, checkpoint.Checkpoint(run_id="r1", scope="analyst"))
    assert len(first.prompts) == 2

    second = FakeLLM()
    await _analyse(second, checkpoint.Checkpoint(run_id="r1", scope="analyst", resume=True))

    assert len(second.prompts) == 1
    assert "beta" in second.prompts[0]


async def test_slices_are_keyed_by_what_they_answered_not_by_their_position():
    """Change the batch size and the boundaries move. Keying by index would
    hand slice 2 the answer to a question that is now half of slice 1."""
    await _analyse(FakeLLM(), checkpoint.Checkpoint(run_id="r1", scope="analyst"))

    resumed = FakeLLM()
    await _analyse(
        resumed,
        checkpoint.Checkpoint(run_id="r1", scope="analyst", resume=True),
        batches=_slices("alpha", "gamma"),
    )

    assert len(resumed.prompts) == 1
    assert "gamma" in resumed.prompts[0]


async def test_slices_from_a_different_model_are_analysed_again():
    await _analyse(FakeLLM(), checkpoint.Checkpoint(run_id="r1", scope="analyst"))

    resumed = FakeLLM()
    await _analyse(
        resumed,
        checkpoint.Checkpoint(run_id="r1", scope="analyst", resume=True),
        config_fp="a-different-backend",
    )

    assert len(resumed.prompts) == 2


async def test_the_analyst_writes_its_own_file(workspace):
    """Two processes save state for one run. Sharing one file would mean both
    doing read-modify-write on it, safe only by an ordering invariant that
    lives in the other process."""
    checkpoint.Checkpoint(run_id="r1").save("ingest", "fp", [_extraction()])
    await _analyse(FakeLLM(), checkpoint.Checkpoint(run_id="r1", scope="analyst"))

    assert {p.name for p in checkpoint.directory().glob("*.json")} == {
        "r1.json",
        "r1.analyst.json",
    }


async def test_a_finished_run_takes_the_analysts_slices_with_it(workspace):
    """Per-scope clearing would leave them behind for the whole retention
    period, long after the run they belonged to stopped existing."""
    checkpoint.Checkpoint(run_id="r1").save("ingest", "fp", [_extraction()])
    await _analyse(FakeLLM(), checkpoint.Checkpoint(run_id="r1", scope="analyst"))

    checkpoint.discard("r1")

    assert checkpoint.saved_stages("r1") == []
    assert not list(checkpoint.directory().glob("*.json"))


async def test_a_half_analysed_run_says_how_far_it_got(workspace):
    """`ingest` alone would understate it: three of five slices done is the
    difference between resuming being worth it and not."""
    checkpoint.Checkpoint(run_id="r1").save("ingest", "fp", [_extraction()])
    await _analyse(FakeLLM(fail_on="beta"), checkpoint.Checkpoint(run_id="r1", scope="analyst"))

    stages = checkpoint.saved_stages("r1")

    assert stages[0] == "ingest"
    assert len(stages) == 2
    assert stages[1].startswith("analyst/slice:")


# --- resuming into a run that is still going -------------------------------


class FakeAgentPool:
    """A pool that connects to nothing. `registry` is what mesh_status counts."""

    registry = {"orchestrator": "", "requirements": ""}

    def __init__(self, *a, **k) -> None:  # noqa: ANN002, ANN003
        self.usage = SimpleNamespace(as_dict=dict)

    async def __aenter__(self):  # noqa: ANN204
        return self

    async def __aexit__(self, *exc) -> None:  # noqa: ANN002
        return None

    async def discover(self) -> dict[str, list[str]]:
        return {"requirements": ["analyse_requirements"]}


@pytest.fixture
def orchestrator(monkeypatch):
    """An executor whose pipeline blocks until the test lets it finish."""
    import asyncio

    from sourcework.agents.orchestrator import agent as orch

    state = SimpleNamespace(started=asyncio.Event(), release=asyncio.Event(), runs=0)

    async def blocking_run(request, pool, notify=None):  # noqa: ANN001
        state.runs += 1
        state.started.set()
        await state.release.wait()
        return "a result"

    monkeypatch.setattr(orch.pipeline, "run", blocking_run)
    monkeypatch.setattr(orch, "AgentPool", FakeAgentPool)
    state.executor = orch.OrchestratorExecutor()
    return state


def _payload(run_id: str = "r1") -> dict:
    return {
        "title": "Returns",
        "inputs": [{"uri": "inline:note", "text": "Returns must be free."}],
        "run_id": run_id,
    }


async def test_the_orchestrator_refuses_to_run_one_id_twice(orchestrator):
    """Disconnecting a client does not stop a run. Resuming one that is still
    going would put two pipelines on the same checkpoint and the same work."""
    import asyncio

    from sourcework.a2a_common import SkillError

    async def progress(_message: str) -> None:
        return None

    first = asyncio.create_task(orchestrator.executor.generate_prd(_payload(), progress))
    await orchestrator.started.wait()

    with pytest.raises(SkillError, match="already in flight"):
        await orchestrator.executor.generate_prd(_payload(), progress)

    orchestrator.release.set()
    await first
    assert orchestrator.runs == 1


async def test_a_different_run_is_not_blocked_by_one_in_flight(orchestrator):
    import asyncio

    async def progress(_message: str) -> None:
        return None

    first = asyncio.create_task(orchestrator.executor.generate_prd(_payload("r1"), progress))
    await orchestrator.started.wait()
    second = asyncio.create_task(orchestrator.executor.generate_prd(_payload("r2"), progress))
    await asyncio.sleep(0)

    orchestrator.release.set()
    await asyncio.gather(first, second)
    assert orchestrator.runs == 2


async def test_the_mesh_reports_which_runs_are_in_flight(orchestrator):
    """So a client can find out before asking for something that would collide,
    rather than by making the request and being turned down."""
    import asyncio

    async def progress(_message: str) -> None:
        return None

    assert (await orchestrator.executor.mesh_status({})).in_flight == []

    running = asyncio.create_task(orchestrator.executor.generate_prd(_payload(), progress))
    await orchestrator.started.wait()
    assert (await orchestrator.executor.mesh_status({})).in_flight == ["r1"]

    orchestrator.release.set()
    await running
    # Released on the way out, so a finished run does not block its own resume.
    assert (await orchestrator.executor.mesh_status({})).in_flight == []


async def test_a_run_that_fails_still_releases_its_id(orchestrator, monkeypatch):
    """The failure path is exactly when somebody resumes, so a lock leaked there
    would block the case the whole feature exists for."""
    from sourcework.a2a_common import SkillError
    from sourcework.agents.orchestrator import agent as orch

    async def exploding_run(request, pool, notify=None):  # noqa: ANN001
        raise RuntimeError("every backend failed")

    monkeypatch.setattr(orch.pipeline, "run", exploding_run)

    async def progress(_message: str) -> None:
        return None

    with pytest.raises(SkillError, match="every backend failed"):
        await orchestrator.executor.generate_prd(_payload(), progress)

    assert (await orchestrator.executor.mesh_status({})).in_flight == []


def test_the_cli_says_so_rather_than_starting_a_second_run(workspace, monkeypatch, capsys):
    from sourcework import cli

    checkpoint.Checkpoint(run_id="r1").save("ingest", "fp", [_extraction()])

    async def in_flight(_run_id: str) -> bool:
        return True

    monkeypatch.setattr(cli, "_in_flight", in_flight)

    assert cli.main(["generate", "Returns", "-n", "a note", "--resume", "r1"]) == 2
    assert "still going" in capsys.readouterr().err


async def test_a_mesh_that_cannot_be_asked_does_not_block_the_resume(monkeypatch):
    """"I could not check" is not "it is running". Treating it as such would
    refuse the resume whenever the mesh is unreachable - and the real call a
    moment later reports that far better than this could."""
    import sourcework.a2a_common as a2a
    from sourcework.cli import _in_flight

    class Unreachable(FakeAgentPool):
        async def __aenter__(self):  # noqa: ANN204
            raise OSError("connection refused")

    monkeypatch.setattr(a2a, "AgentPool", Unreachable)

    assert await _in_flight("r1") is False
