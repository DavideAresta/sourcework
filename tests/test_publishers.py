"""Naming a destination, so a second one does not mean editing the pipeline."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from sourcework import publishers
from sourcework.agents.orchestrator import pipeline
from sourcework.models import InputRef, PRDRequest


def test_confluence_is_a_target_like_any_other():
    found = publishers.targets()

    assert found["confluence"].agent == "confluence"
    assert found["confluence"].skill == "publish_prd"
    assert found["confluence"].name == "Confluence"


def test_an_unset_target_still_means_confluence():
    """Every request written before there was a choice meant Confluence, and
    `publish=True` has to keep meaning it."""
    assert publishers.get(None) is publishers.CONFLUENCE
    assert publishers.get("confluence") is publishers.CONFLUENCE
    assert publishers.get("nowhere") is None


class FakeEntryPoint:
    """What `importlib.metadata` hands back: a name and something to load."""

    def __init__(self, name: str, value, *, raises: bool = False) -> None:  # noqa: ANN001
        self.name = name
        self._value = value
        self._raises = raises

    def load(self):  # noqa: ANN201
        if self._raises:
            raise ImportError("the plugin is not installed properly")
        return self._value


def _installed(monkeypatch, *entries: FakeEntryPoint) -> None:
    monkeypatch.setattr(publishers, "entry_points", lambda group: list(entries))


def test_a_plugin_cannot_take_over_a_built_in_destination(monkeypatch):
    """Shadowing `confluence` would let an installed package silently redirect
    published documents somewhere else. There is no reading of that which is a
    feature."""
    impostor = publishers.PublishTarget(id="confluence", agent="somewhere-else")
    _installed(monkeypatch, FakeEntryPoint("confluence", impostor))

    assert publishers.targets()["confluence"].agent == "confluence"


def test_a_plugin_that_registers_a_new_destination_is_found(monkeypatch):
    jira = publishers.PublishTarget(id="jira", agent="jira", label="Jira")
    _installed(monkeypatch, FakeEntryPoint("jira", jira))

    assert publishers.get("jira") is jira


def test_one_plugin_may_register_several_destinations(monkeypatch):
    """A package fronting two systems should not need two entry points."""
    pair = [
        publishers.PublishTarget(id="jira", agent="atlassian", skill="publish_jira"),
        publishers.PublishTarget(id="bitbucket", agent="atlassian", skill="publish_bb"),
    ]
    _installed(monkeypatch, FakeEntryPoint("atlassian", pair))

    assert sorted(publishers.targets()) == ["bitbucket", "confluence", "jira"]


@pytest.mark.parametrize("broken", [None, "a string", 42])
def test_a_plugin_returning_the_wrong_thing_is_skipped(monkeypatch, broken):
    """A run that got as far as having a document is not one to throw away over
    a destination."""
    _installed(monkeypatch, FakeEntryPoint("junk", broken))

    assert publishers.targets() == {"confluence": publishers.CONFLUENCE}


def test_one_plugin_failing_to_import_does_not_hide_the_others(monkeypatch):
    working = publishers.PublishTarget(id="jira", agent="jira")
    _installed(
        monkeypatch,
        FakeEntryPoint("broken", None, raises=True),
        FakeEntryPoint("jira", working),
    )

    assert publishers.get("jira") is working


def test_a_target_must_name_an_agent():
    with pytest.raises(ValueError, match="id and an agent"):
        publishers.PublishTarget(id="jira", agent="")


# --- through the pipeline --------------------------------------------------


class PublishPool:
    """Records the publish call, and answers everything else plausibly."""

    def __init__(self, *, agents: dict[str, list[str]]) -> None:
        self.agents = agents
        self.published: tuple[str, str, object] | None = None
        self.usage = SimpleNamespace(as_dict=dict)

    async def discover(self):  # noqa: ANN201
        return self.agents

    async def call(self, agent, skill, payload, on_progress=None):  # noqa: ANN001
        if skill == "extract_document":
            from sourcework.models import Evidence, ExtractionResult, Modality, SourceDocument

            source = SourceDocument(
                id="src-1", uri="inline:note", title="Note", modality=Modality.FREETEXT
            )
            return ExtractionResult(
                source=source,
                evidence=[Evidence(
                    id="ev-1", source_id="src-1", modality=Modality.FREETEXT,
                    text="Returns must be free.",
                )],
            ).model_dump(mode="json")
        if skill == "analyse_requirements":
            from sourcework.models import RequirementSet

            return RequirementSet().model_dump(mode="json")
        if skill == "write_prd":
            from sourcework.models import PRDDocument

            return {
                "prd": PRDDocument(title="T").model_dump(mode="json"),
                "markdown": "# T",
                "confluence_storage": "<p>T</p>",
            }
        if skill in ("publish_prd", "send_to_board"):
            self.published = (agent, skill, payload)
            return {"page_id": "1", "url": "https://example.invalid/p/1",
                    "version": 1, "created": True, "summary": "Published."}
        raise AssertionError(f"unexpected {skill}")


def _request(**kw) -> PRDRequest:
    return PRDRequest(
        title="Returns",
        inputs=[InputRef(uri="inline:note", text="Returns must be free.", title="Note")],
        publish=True,
        review_rounds=0,
        **kw,
    )


MESH = {
    "ingestion": ["extract_document"],
    "requirements": ["analyse_requirements"],
    "writer": ["write_prd"],
    "confluence": ["publish_prd"],
}


async def test_publishing_goes_to_the_agent_the_target_names():
    pool = PublishPool(agents=MESH)
    result = await pipeline.run(_request(), pool)

    assert pool.published is not None
    agent, skill, payload = pool.published
    assert (agent, skill) == ("confluence", "publish_prd")
    assert payload.space_key is None
    assert result.published_url == "https://example.invalid/p/1"


async def test_both_renderings_travel_so_a_new_target_need_not_speak_confluence():
    """`storage_xhtml` means nothing outside Confluence. Sending the markdown
    too costs a string and saves every future publisher from asking the writer
    to run again."""
    pool = PublishPool(agents=MESH)
    await pipeline.run(_request(), pool)

    _, _, payload = pool.published
    assert payload.storage_xhtml == "<p>T</p>"
    assert payload.markdown == "# T"


async def test_a_plugin_target_is_published_to_without_touching_the_pipeline(monkeypatch):
    board = publishers.PublishTarget(id="board", agent="board", skill="send_to_board")
    _installed(monkeypatch, FakeEntryPoint("board", board))

    pool = PublishPool(agents={**MESH, "board": ["send_to_board"]})
    await pipeline.run(_request(publish_to="board", publish_options={"project": "RET"}), pool)

    agent, skill, payload = pool.published
    assert (agent, skill) == ("board", "send_to_board")
    assert payload.options == {"project": "RET"}


async def test_an_unknown_target_is_a_warning_and_says_what_it_knows():
    pool = PublishPool(agents=MESH)
    result = await pipeline.run(_request(publish_to="nowhere"), pool)

    assert pool.published is None
    assert any("unknown target" in w and "confluence" in w for w in result.stats["warnings"])


async def test_an_agent_that_does_not_advertise_the_skill_is_caught_before_the_call():
    """The same rule `pool.call(strict=True)` applies: a missing skill is a
    configuration error and should read like one, not like a remote failure."""
    pool = PublishPool(agents={**MESH, "confluence": ["fetch_page"]})
    result = await pipeline.run(_request(), pool)

    assert pool.published is None
    assert any("does not advertise publish_prd" in w for w in result.stats["warnings"])
