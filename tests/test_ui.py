"""The UI layer and per-run model overrides.

The API tests drive the real FastAPI app through Starlette's test client; only
the run manager is stubbed, because starting a mesh is not what is under test
here. Everything else - multipart parsing, the settings allow-list, SSE framing,
the store - is exercised for real.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from prdforge.config import LLMOverrides, LLMSettings, effective_llm, llm_overrides, settings
from prdforge.llm import LLM
from prdforge.models import PRDRequest
from prdforge.ui import env_file
from prdforge.ui.app import build_app
from prdforge.ui.runner import RunManager
from prdforge.ui.store import Run, RunStore, now_iso

# ---------------------------------------------------------------------------
# Per-run overrides
# ---------------------------------------------------------------------------


def test_an_llm_built_at_startup_still_sees_a_later_override():
    # This is the whole mechanism: agents construct their LLM once, when the
    # executor is created, long before any request arrives.
    agent_llm = LLM(role="reasoning")
    before = agent_llm.cfg.active_backend

    with llm_overrides(LLMOverrides(backend="claude-code", models={"reasoning": "opus"})):
        assert agent_llm.cfg.active_backend == "claude-code"
        assert agent_llm.cfg.model_for("reasoning") == "opus"

    assert agent_llm.cfg.active_backend == before


def test_an_explicit_cfg_is_not_overridden():
    pinned = LLMSettings(backend="litellm", default_model="anthropic/claude-sonnet-4-5")
    llm = LLM(cfg=pinned)
    with llm_overrides(LLMOverrides(backend="claude-code")):
        assert llm.cfg.active_backend == "litellm"


def test_choosing_a_backend_turns_stub_mode_off():
    # A mesh booted with PRDFORGE_LLM__STUB=1 must not silently fake a run that
    # explicitly asked for a real backend.
    stubbed = LLMSettings(stub=True)
    assert LLMOverrides(backend="claude-code").applied_to(stubbed).stub is False
    assert LLMOverrides(backend="stub").applied_to(stubbed).stub is True


def test_an_override_that_says_nothing_changes_nothing():
    base = LLMSettings(backend="opencode-cli", effort="high")
    assert LLMOverrides().applied_to(base) is base


def test_models_land_on_the_backend_the_run_actually_uses():
    base = LLMSettings(backend="litellm")
    applied = LLMOverrides(backend="claude-code", models={"default": "haiku"}).applied_to(base)
    assert applied.model_for("default") == "haiku"
    # ...and nowhere else: a model id is meaningless to another backend.
    assert applied.model_for("default", "opencode-cli") is None


def test_overrides_normalise_environment_spelling():
    applied = LLMOverrides(backend="claude_code", failover_order=["opencode_cli"]).applied_to(
        LLMSettings()
    )
    assert applied.active_backend == "claude-code"
    assert applied.failover_order == ["opencode-cli"]


def test_the_request_carries_overrides_so_the_pool_can_forward_them():
    request = PRDRequest.model_validate(
        {"title": "x", "llm": {"backend": "claude-code", "models": {"default": "haiku"}}}
    )
    assert request.llm is not None
    assert request.llm.backend == "claude-code"


async def test_the_pool_attaches_overrides_to_every_outbound_payload():
    from prdforge.a2a_common import AgentPool

    pool = AgentPool(registry={}, llm=LLMOverrides(backend="claude-code"))
    attached = pool._with_llm({"title": "x"})
    assert attached["llm"]["backend"] == "claude-code"

    # An explicit per-hop choice wins over the pool's default.
    kept = pool._with_llm({"title": "x", "llm": {"backend": "opencode-cli"}})
    assert kept["llm"]["backend"] == "opencode-cli"

    assert "llm" not in AgentPool(registry={})._with_llm({"title": "x"})


def test_the_executor_reads_overrides_without_consuming_them():
    from prdforge.a2a_common.executor import _read_overrides

    payload = {"title": "x", "llm": {"backend": "claude-code"}}
    overrides = _read_overrides(payload)
    assert overrides.backend == "claude-code"
    # Popping it left PRDRequest.llm empty, so the orchestrator built its pool
    # with no override and the other seven agents never heard about it.
    assert "llm" in payload


def test_a_malformed_override_is_ignored_rather_than_fatal():
    from prdforge.a2a_common.executor import _read_overrides

    assert _read_overrides({"llm": {"failover_order": "not a list"}}) is None
    assert _read_overrides({}) is None


def test_effective_llm_falls_back_to_the_environment():
    assert effective_llm().active_backend == settings().llm.active_backend


# ---------------------------------------------------------------------------
# Settings file
# ---------------------------------------------------------------------------


@pytest.fixture
def env_path(tmp_path: Path) -> Path:
    path = tmp_path / ".env"
    path.write_text(
        "# a comment worth keeping\n"
        "PRDFORGE_LLM__BACKEND=litellm\n"
        "ANTHROPIC_API_KEY=sk-real-secret\n"
        "\n"
        "PRDFORGE_CONFLUENCE__EMAIL=me@example.com\n",
        encoding="utf-8",
    )
    return path


def test_secrets_go_to_the_browser_masked(env_path: Path):
    fields = {f["key"]: f for f in env_file.describe(env_path)}
    assert fields["ANTHROPIC_API_KEY"]["value"] == env_file.MASK
    assert fields["ANTHROPIC_API_KEY"]["set"] is True
    assert fields["PRDFORGE_CONFLUENCE__EMAIL"]["value"] == "me@example.com"


def test_saving_an_untouched_secret_keeps_it(env_path: Path):
    # The form posts every field, including the masked ones it never touched.
    assert env_file.write(env_path, {"ANTHROPIC_API_KEY": env_file.MASK}) == []
    assert env_file.read(env_path)["ANTHROPIC_API_KEY"] == "sk-real-secret"


def test_a_real_secret_change_is_written(env_path: Path):
    assert env_file.write(env_path, {"ANTHROPIC_API_KEY": "sk-new"}) == ["ANTHROPIC_API_KEY"]
    assert env_file.read(env_path)["ANTHROPIC_API_KEY"] == "sk-new"


def test_an_unset_switch_shows_the_default_it_actually_has(env_path: Path):
    """A checkbox has no "unset" position, and the form posts every control it
    drew. Rendering an absent default-on setting unticked means opening the
    settings page and pressing Save silently turns it off.
    """
    from prdforge.config import LLMSettings

    fields = {f["key"]: f for f in env_file.describe(env_path)}

    constrained = fields["PRDFORGE_LLM__CONSTRAINED_JSON"]
    assert constrained["set"] is False, "the fixture must not set it, or this proves nothing"
    assert LLMSettings().constrained_json is True, "guarding the premise, not the behaviour"
    assert constrained["value"].lower() in ("true", "1"), "must render ticked"

    # ...and a default-off switch still reads as off rather than as ticked.
    assert fields["PRDFORGE_LLM__STUB"]["value"].lower() in ("false", "0", "")


def test_the_ui_does_not_offer_itself_to_the_network_by_default():
    """It has no authentication, `/api/settings` rewrites .env including provider
    keys, and `/api/runs` returns the full text of every ingested document.
    Binding wider has to be a decision someone makes, not one they inherit."""
    import inspect

    from prdforge.ui import DEFAULT_HOST
    from prdforge.ui.app import serve

    assert DEFAULT_HOST == "127.0.0.1"
    assert inspect.signature(serve).parameters["host"].default == DEFAULT_HOST


def test_unknown_keys_cannot_be_injected(env_path: Path):
    # Without the allow-list this endpoint writes arbitrary environment
    # variables into the file the whole system boots from.
    assert env_file.write(env_path, {"EVIL": "pwned", "PATH": "/tmp"}) == []
    assert "EVIL" not in env_path.read_text()


def test_editing_preserves_comments_and_untouched_lines(env_path: Path):
    env_file.write(env_path, {"PRDFORGE_LLM__BACKEND": "claude-code"})
    text = env_path.read_text()
    assert "# a comment worth keeping" in text
    assert "PRDFORGE_LLM__BACKEND=claude-code" in text
    assert "PRDFORGE_CONFLUENCE__EMAIL=me@example.com" in text


def test_a_new_key_is_appended(env_path: Path):
    env_file.write(env_path, {"PRDFORGE_LLM__CLAUDE_CODE_MODELS__REASONING": "sonnet"})
    assert "PRDFORGE_LLM__CLAUDE_CODE_MODELS__REASONING=sonnet" in env_path.read_text()


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


async def test_runs_survive_a_reopen(tmp_path: Path):
    store = RunStore(tmp_path / "runs.db")
    await store.save(Run(id="a1", title="T", status="ok", created_at=now_iso(), request={"title": "T"}))
    store.close()

    reopened = RunStore(tmp_path / "runs.db")
    try:
        assert (await reopened.get("a1")).title == "T"
    finally:
        reopened.close()


async def test_an_interrupted_run_does_not_stay_running_forever(tmp_path: Path):
    store = RunStore(tmp_path / "runs.db")
    try:
        await store.save(
            Run(id="a1", title="T", status="running", created_at=now_iso(), request={})
        )
        assert await store.reap_orphans() == 1
        run = await store.get("a1")
        assert run.status == "failed" and "restarted" in run.error
    finally:
        store.close()


async def test_the_summary_leaves_out_the_payloads(tmp_path: Path):
    store = RunStore(tmp_path / "runs.db")
    try:
        run = Run(
            id="a1", title="T", status="ok", created_at=now_iso(),
            request={"title": "T", "llm": {"backend": "claude-code"}},
            result={"stats": {"requirements": 12}, "review": {"verdict": "approved"},
                    "markdown": "x" * 10_000},
        )
        await store.save(run)
        summary = (await store.get("a1")).summary()
        assert summary["requirements"] == 12
        assert summary["verdict"] == "approved"
        assert summary["backend"] == "claude-code"
        assert "result" not in summary  # the list view must stay cheap
    finally:
        store.close()


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------


class FakeManager:
    """A run manager that finishes instantly, so the API can be tested alone."""

    def __init__(self, store: RunStore) -> None:
        self.store = store
        self.started: list[PRDRequest] = []

    async def start(self, request: PRDRequest, *, run_id: str | None = None) -> Run:
        self.started.append(request)
        run = Run(
            id=run_id or "fixed", title=request.title, status="ok",
            created_at=now_iso(), finished_at=now_iso(),
            request=request.model_dump(mode="json"),
            result={"markdown": "# Done", "prd": {"title": request.title},
                    "confluence_storage": "<p/>", "stats": {}},
            events=[{"seq": 0, "t": now_iso(), "kind": "done", "message": "Finished"}],
        )
        await self.store.save(run)
        return run

    def is_active(self, run_id: str) -> bool:
        return False

    async def cancel(self, run_id: str) -> bool:
        return False

    async def shutdown(self) -> None:
        return None

    async def subscribe(self, run_id: str):
        run = await self.store.get(run_id)
        for event in (run.events if run else []):
            yield event


@pytest.fixture
def client(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("prdforge.ui.app.RunManager", FakeManager)
    with TestClient(build_app(tmp_path)) as test_client:
        yield test_client


def test_a_run_needs_something_to_work_from(client: TestClient):
    response = client.post("/api/runs", data={"request": json.dumps({"title": "Empty"})})
    assert response.status_code == 400
    assert "work from" in response.json()["detail"]


def test_uploads_become_file_uris_the_agents_can_read(client: TestClient, tmp_path: Path):
    response = client.post(
        "/api/runs",
        data={"request": json.dumps({"title": "With files", "notes": ["a note"]})},
        files=[("files", ("spec.md", b"# Spec\nThe run must finish.", "text/markdown"))],
    )
    assert response.status_code == 200, response.text
    run = client.get(f"/api/runs/{response.json()['id']}").json()

    uris = [i["uri"] for i in run["request"]["inputs"]]
    assert any(u.startswith("file://") and u.endswith("spec.md") for u in uris)
    assert "inline:note" in uris
    # Written under the run's own directory, so uploads are attributable.
    assert (tmp_path / "uploads" / run["id"] / "spec.md").read_bytes().startswith(b"# Spec")


def test_a_traversing_filename_cannot_escape_the_upload_directory(client: TestClient, tmp_path: Path):
    response = client.post(
        "/api/runs",
        data={"request": json.dumps({"title": "Nasty"})},
        files=[("files", ("../../../etc/prdforge-pwned", b"x", "text/plain"))],
    )
    assert response.status_code == 200
    run_id = response.json()["id"]
    written = list((tmp_path / "uploads" / run_id).iterdir())
    assert [p.name for p in written] == ["prdforge-pwned"]
    assert not Path("/etc/prdforge-pwned").exists()


def test_the_run_form_carries_model_overrides_through(client: TestClient):
    spec = {
        "title": "Overridden",
        "notes": ["something"],
        "llm": {"backend": "claude-code", "models": {"reasoning": "opus"}, "effort": "high"},
    }
    response = client.post("/api/runs", data={"request": json.dumps(spec)})
    run = client.get(f"/api/runs/{response.json()['id']}").json()
    assert run["request"]["llm"]["backend"] == "claude-code"
    assert run["request"]["llm"]["models"]["reasoning"] == "opus"
    assert run["backend"] == "claude-code"


def test_artifacts_download_with_sensible_filenames(client: TestClient):
    response = client.post(
        "/api/runs", data={"request": json.dumps({"title": "Invoice matching", "notes": ["n"]})}
    )
    run_id = response.json()["id"]

    markdown = client.get(f"/api/runs/{run_id}/artifact/md")
    assert markdown.text == "# Done"
    assert "invoice-matching.md" in markdown.headers["content-disposition"]
    assert client.get(f"/api/runs/{run_id}/artifact/json").json() == {"title": "Invoice matching"}
    assert client.get(f"/api/runs/{run_id}/artifact/xhtml").text == "<p/>"
    assert client.get(f"/api/runs/{run_id}/artifact/nonsense").status_code == 404


def test_events_are_served_as_sse(client: TestClient):
    response = client.post(
        "/api/runs", data={"request": json.dumps({"title": "Streamed", "notes": ["n"]})}
    )
    stream = client.get(f"/api/runs/{response.json()['id']}/events")
    assert stream.headers["content-type"].startswith("text/event-stream")
    assert "data: " in stream.text
    assert '"kind": "done"' in stream.text


def test_a_missing_run_is_a_404_everywhere(client: TestClient):
    assert client.get("/api/runs/nope").status_code == 404
    assert client.get("/api/runs/nope/events").status_code == 404
    assert client.get("/api/runs/nope/artifact/md").status_code == 404


def test_the_settings_endpoint_masks_and_allow_lists(client: TestClient):
    payload = client.get("/api/settings").json()
    keys = {f["key"] for f in payload["fields"]}
    assert "PRDFORGE_LLM__BACKEND" in keys
    assert all(f["value"] != "sk-" for f in payload["fields"])

    result = client.put("/api/settings", json={"NOT_ALLOWED": "x"}).json()
    assert result["changed"] == []


def test_the_page_and_static_assets_are_served(client: TestClient):
    assert "PRD" in client.get("/").text
    assert client.get("/settings").status_code == 200
    assert client.get("/static/js/app.js").status_code == 200
    assert client.get("/healthz").json()["status"] == "ok"


# ---------------------------------------------------------------------------
# Run manager fan-out
# ---------------------------------------------------------------------------


async def test_a_late_subscriber_gets_the_backlog_then_the_live_events(tmp_path: Path):
    store = RunStore(tmp_path / "runs.db")
    manager = RunManager(store)
    try:
        run = Run(id="r1", title="T", status="running", created_at=now_iso(), request={})
        await store.save(run)
        await manager._emit(run, "progress", "first")
        await manager._emit(run, "progress", "second")

        seen = []

        async def watch() -> None:
            async for event in manager.subscribe("r1"):
                seen.append(event["message"])
                if event["message"] == "third":
                    break

        task = asyncio.create_task(watch())
        await asyncio.sleep(0.05)
        await manager._emit(run, "progress", "third")
        await asyncio.wait_for(task, timeout=2)

        # Replayed history and live events, each exactly once.
        assert seen == ["first", "second", "third"]
    finally:
        await manager.shutdown()
        store.close()


# ---------------------------------------------------------------------------
# Refinement
# ---------------------------------------------------------------------------


def _finished_run(client: TestClient) -> str:
    """A run with a PRD carrying evidence, requirements and an open question."""
    response = client.post(
        "/api/runs", data={"request": json.dumps({"title": "Returns portal", "notes": ["n"]})}
    )
    run_id = response.json()["id"]
    return run_id


@pytest.fixture
def parent(client: TestClient, tmp_path: Path) -> str:
    run_id = _finished_run(client)
    # FakeManager writes a thin result; give it the shape a refinement reads.
    import asyncio

    from prdforge.ui.store import RunStore

    store = RunStore(tmp_path / "prdforge-ui.db")
    try:
        run = asyncio.run(store.get(run_id))
        run.result = {
            "markdown": "# Done",
            "confluence_storage": "<p/>",
            "stats": {},
            "prd": {
                "title": "Returns portal",
                "sources": [{"id": "src-1", "uri": "file:///a.pdf", "title": "BRD",
                             "modality": "document"}],
                "evidence": [{"id": "ev-1", "source_id": "src-1", "modality": "document",
                              "text": "Refund within 5 business days.", "locator": "p.1"}],
                "requirements": {
                    "requirements": [{"id": "REQ-001", "title": "Refund SLA",
                                      "statement": "The system must refund within 5 days."}],
                    "open_questions": [{"question": "Are marketplace returns in scope?",
                                        "blocking": True}],
                },
            },
        }
        asyncio.run(store.save(run))
    finally:
        store.close()
    return run_id


def test_refining_carries_the_baseline_forward(client: TestClient, parent: str):
    response = client.post(
        f"/api/runs/{parent}/refine",
        data={"request": json.dumps({
            "answers": [{"question": "Are marketplace returns in scope?", "answer": "Yes, phase 1."}],
        })},
    )
    assert response.status_code == 200, response.text
    child = client.get(f"/api/runs/{response.json()['id']}").json()

    baseline = child["request"]["baseline"]
    assert baseline["run_id"] == parent
    # Evidence is carried, NOT re-ingested: re-reading would mint new ids and
    # break every citation in the PRD the reader already has.
    assert [e["id"] for e in baseline["evidence"]] == ["ev-1"]
    assert [r["id"] for r in baseline["requirements"]["requirements"]] == ["REQ-001"]
    assert child["parent_id"] == parent


def test_an_answer_becomes_a_self_contained_source(client: TestClient, parent: str):
    response = client.post(
        f"/api/runs/{parent}/refine",
        data={"request": json.dumps({
            "answers": [{"question": "Are marketplace returns in scope?", "answer": "Yes, phase 1."}],
        })},
    )
    child = client.get(f"/api/runs/{response.json()['id']}").json()
    text = child["request"]["inputs"][0]["text"]
    # The extractor sees this text alone - "Yes, phase 1." without its question
    # is not evidence of anything.
    assert "Are marketplace returns in scope?" in text
    assert "Yes, phase 1." in text


def test_a_refinement_can_add_notes_and_files(client: TestClient, parent: str):
    response = client.post(
        f"/api/runs/{parent}/refine",
        data={"request": json.dumps({"notes": ["The fee is waived for loyalty members."]})},
        files=[("files", ("addendum.md", b"# Addendum", "text/markdown"))],
    )
    child = client.get(f"/api/runs/{response.json()['id']}").json()
    uris = [i["uri"] for i in child["request"]["inputs"]]
    assert "inline:note" in uris
    assert any(u.endswith("addendum.md") for u in uris)


def test_a_refinement_with_nothing_new_is_rejected(client: TestClient, parent: str):
    response = client.post(f"/api/runs/{parent}/refine", data={"request": json.dumps({})})
    assert response.status_code == 400
    assert "Nothing to add" in response.json()["detail"]


def test_blank_answers_leave_the_question_open(client: TestClient, parent: str):
    response = client.post(
        f"/api/runs/{parent}/refine",
        data={"request": json.dumps({
            "answers": [{"question": "Are marketplace returns in scope?", "answer": "   "}],
            "notes": ["something else"],
        })},
    )
    child = client.get(f"/api/runs/{response.json()['id']}").json()
    assert all(i["uri"] != "inline:answer" for i in child["request"]["inputs"])


def test_refining_an_unfinished_run_is_a_404(client: TestClient):
    assert client.post("/api/runs/nope/refine", data={"request": "{}"}).status_code == 404


def test_a_refinement_inherits_the_parents_model_choice(client: TestClient, parent: str):
    # A version built by a different model for no stated reason is a difference
    # nobody asked for.
    response = client.post(
        f"/api/runs/{parent}/refine",
        data={"request": json.dumps({"notes": ["x"], "llm": {"backend": "opencode-cli"}})},
    )
    child = client.get(f"/api/runs/{response.json()['id']}").json()
    assert child["request"]["llm"]["backend"] == "opencode-cli"


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------


def test_the_dashboard_reports_one_row_per_prd_with_a_verdict(client: TestClient, parent: str):
    body = client.get("/api/dashboard").json()
    assert body["prds"], "the finished run should appear"
    row = next(r for r in body["prds"] if r["run"]["id"] == parent)
    # The parent fixture has a blocking open question, so it is not ready.
    assert row["readiness"]["state"] == "needs_work"
    assert row["readiness"]["counts"]["question"] == 1
    assert "marketplace" in row["readiness"]["blockers"][0]["detail"]


def test_a_refinement_collapses_into_its_parents_row(client: TestClient, parent: str):
    created = client.post(
        f"/api/runs/{parent}/refine", data={"request": json.dumps({"notes": ["something"]})}
    ).json()

    body = client.get("/api/dashboard").json()
    ids = [r["run"]["id"] for r in body["prds"]]
    # Two runs, one PRD: the chain is collapsed rather than listed twice.
    assert created["id"] not in ids or parent not in ids
    row = next(r for r in body["prds"] if created["id"] in (r["run"]["id"], (r["in_flight"] or {}).get("id"))
               or r["root_id"] == parent)
    assert row["versions"] == 2


def test_the_dashboard_totals_add_up(client: TestClient, parent: str):
    body = client.get("/api/dashboard").json()
    assert sum(body["totals"].values()) == len(body["prds"])


# ---------------------------------------------------------------------------
# Narration
#
# The model's working, from the backend that produces it to the browser. The
# properties that matter are that it is opt-in, that it survives the relay
# intact, and that it is never written to the run record.
# ---------------------------------------------------------------------------


def test_narration_is_told_apart_from_ordinary_progress():
    from prdforge import stream

    wire = stream.encode("reasoning", "weighing the options", agent="requirements")
    assert stream.decode(wire) == {
        "kind": "reasoning",
        "text": "weighing the options",
        "agent": "requirements",
    }
    # A human progress line must never be mistaken for narration, whatever it
    # happens to start with.
    assert stream.decode("Normalising requirements") is None
    assert stream.decode('{"kind": "text"}') is None


async def test_the_pool_only_asks_for_narration_when_someone_is_watching():
    from prdforge.a2a_common import AgentPool

    assert "stream" not in AgentPool(registry={})._with_llm({"title": "x"})
    assert AgentPool(registry={}, narrate=True)._with_llm({"title": "x"})["stream"] is True


def test_the_executor_narrates_only_on_request():
    from prdforge.a2a_common.executor import _wants_narration

    assert _wants_narration({"stream": True})
    assert not _wants_narration({"title": "x"})
    assert not _wants_narration({"stream": False})


async def test_the_narrator_coalesces_and_flushes_what_is_left():
    from prdforge.backends.base import StreamChunk
    from prdforge.stream import Narrator

    published: list[tuple[str, str]] = []

    async def publish(kind: str, text: str) -> None:
        published.append((kind, text))

    # A long interval: nothing should be published until the block exits, which
    # is what proves the per-token chunks are being batched rather than sent.
    async with Narrator(publish, interval_s=60) as narrator:
        for word in ["think", "ing ", "hard"]:
            narrator.sink(StreamChunk(kind="reasoning", text=word))
        narrator.sink(StreamChunk(kind="text", text="the answer"))
        assert published == []

    assert published == [("reasoning", "thinking hard"), ("text", "the answer")]


async def test_a_runaway_model_cannot_grow_the_browser_without_bound():
    from prdforge.backends.base import StreamChunk
    from prdforge.stream import Narrator

    published: list[tuple[str, str]] = []

    async def publish(kind: str, text: str) -> None:
        published.append((kind, text))

    async with Narrator(publish, interval_s=60, budget=10) as narrator:
        for _ in range(100):
            narrator.sink(StreamChunk(kind="text", text="xxxxx"))

    assert "".join(text for _, text in published) == "x" * 10


async def test_a_failing_sink_never_reaches_the_pipe_reader():
    from prdforge.backends.base import StreamChunk
    from prdforge.stream import Narrator

    async def publish(kind: str, text: str) -> None:
        raise RuntimeError("subscriber exploded")

    # The sink runs inside the loop draining the CLI's stdout. An exception
    # there stops the drain and deadlocks the very call it was narrating.
    async with Narrator(publish, interval_s=60) as narrator:
        narrator.sink(StreamChunk(kind="text", text="hello"))


async def test_narration_reaches_subscribers_without_being_stored(tmp_path: Path):
    from prdforge.ui.runner import RunManager

    store = RunStore(tmp_path / "runs.db")
    try:
        manager = RunManager(store)
        run = Run(id="a1", title="T", status="running", created_at=now_iso(), request={})
        await store.save(run)

        seen: list[dict] = []

        async def watch() -> None:
            async for event in manager.subscribe("a1"):
                seen.append(event)

        watcher = asyncio.create_task(watch())
        while not manager._subscribers.get("a1"):
            await asyncio.sleep(0)  # let the subscription register

        await manager._narrate("a1", {"kind": "reasoning", "text": "hm", "agent": "writer"})
        await manager._publish("a1", None)  # close the stream
        await asyncio.wait_for(watcher, timeout=5)

        narration = [e for e in seen if e["kind"] == "stream"]
        assert narration and narration[0]["message"] == "hm"
        assert narration[0]["agent"] == "writer"
        # Persisting a token stream would rewrite the whole run record to SQLite
        # thousands of times per run, for something nothing downstream cites.
        assert (await store.get("a1")).events == []
    finally:
        store.close()


async def test_a_step_supersedes_rather_than_accumulates():
    from prdforge.backends.base import StreamChunk
    from prdforge.stream import Narrator

    published: list[tuple[str, str]] = []

    async def publish(kind: str, text: str) -> None:
        published.append((kind, text))

    async with Narrator(publish, interval_s=60) as narrator:
        narrator.sink(StreamChunk(kind="step", text="thinking… ~50 tokens"))
        narrator.sink(StreamChunk(kind="step", text="thinking… ~150 tokens"))

    # Concatenating would read "thinking… ~50 tokensthinking… ~150 tokens".
    assert published == [("step", "thinking… ~150 tokens")]


def test_narration_is_kept_out_of_the_log(caplog):
    import logging

    from prdforge import stream
    from prdforge.a2a_common.executor import Progress

    class Updater:
        async def update_status(self, *args, **kwargs) -> None:  # noqa: ANN002, ANN003
            pass

    progress = Progress(Updater())
    with caplog.at_level(logging.INFO, logger="prdforge.a2a_common.executor"):
        asyncio.run(progress("Normalising requirements"))
        asyncio.run(progress(stream.encode("text", "the whole PRD, again", agent="writer")))

    # The orchestrator relays every specialist's chunks through here. Logging
    # them writes each model's full output into the mesh log at INFO.
    assert "Normalising requirements" in caplog.text
    assert "the whole PRD, again" not in caplog.text


def test_the_model_fields_carry_both_axes():
    """A model choice is two-dimensional: which backend, which role.

    The settings page draws that as a grid, and it decides to do so by checking
    that every field in the group carries both. A field added without them
    silently drops the whole group back to a flat list, so pin it here.
    """
    models = [f for f in env_file.FIELDS if f.group == "Models"]
    assert models
    assert all(f.backend and f.role for f in models)

    covered = {(f.backend, f.role) for f in models}
    for backend in ("litellm", "claude-code", "opencode-cli", "copilot-cli"):
        for role in ("default", "reasoning", "vision"):
            assert (backend, role) in covered, f"no control for {backend}/{role}"


def test_every_field_belongs_to_a_named_group():
    assert all(f.group for f in env_file.FIELDS)
    # One flat group of sixteen controls was the thing being fixed.
    from collections import Counter

    biggest = Counter(f.group for f in env_file.FIELDS).most_common(1)[0]
    assert biggest[0] == "Models", "the grid group may be large; a flat list may not"


def test_the_batching_knobs_are_reachable_from_the_ui():
    # They change whether a large run succeeds, so they cannot be env-only.
    keys = {f.key for f in env_file.FIELDS}
    assert "PRDFORGE_LLM__ANALYSIS_BATCH_ITEMS" in keys
    assert "PRDFORGE_LLM__ANALYSIS_BATCH_CHARS" in keys


def test_every_profile_covers_every_model_cell():
    """A profile that leaves a backend blank is a broken failover.

    Each profile is applied to *all* backends, not just the active one, because
    the failover target is exactly the one nobody remembers to configure - and
    on opencode an unset model is not a default, it is an outright failure.
    """
    cells = {f.key for f in env_file.FIELDS if f.group == "Models"}
    for name, profile in env_file.PROFILES.items():
        assert set(profile["models"]) == cells, f"{name} does not cover every cell"
        assert all(profile["models"].values()), f"{name} has an empty value"
        assert profile["label"] and profile["detail"]


def test_the_default_profile_is_what_gets_pre_filled():
    fields = {f["key"]: f for f in env_file.describe(Path("/nonexistent.env"))}
    balanced = env_file.PROFILES[env_file.DEFAULT_PROFILE]["models"]
    for key, value in balanced.items():
        assert fields[key]["suggested"] == value


def test_the_profiles_encode_what_was_measured():
    # Not decoration: opus-4-6 is the opencode model that reasons well, and
    # gpt-5.4 is the Copilot model that returns readable reasoning rather than
    # an encrypted blob. Both were established by running them.
    balanced = env_file.PROFILES["balanced"]["models"]
    assert balanced["PRDFORGE_LLM__OPENCODE_MODELS__REASONING"] == "opencode/claude-opus-4-6"
    assert balanced["PRDFORGE_LLM__COPILOT_MODELS__REASONING"] == "gpt-5.4"


def test_suggestions_are_confined_to_the_model_cells():
    # Elsewhere "unset" is the right answer: the code has a default, and writing
    # a redundant copy of it into .env just pins a value nobody chose.
    fields = env_file.describe(Path("/nonexistent.env"))
    stray = [f["key"] for f in fields if f["suggested"] and f["group"] != "Models"]
    assert not stray


def test_a_limit_shows_the_default_it_would_otherwise_use(tmp_path: Path):
    fields = {f["key"]: f for f in env_file.describe(tmp_path / "missing.env")}
    # Placeholder, not value: shown so an empty box reads as "600s, because that
    # is the default" rather than as an unknown - without writing it anywhere.
    assert fields["PRDFORGE_LLM__CLI_TIMEOUT_S"]["placeholder"] == "600.0"
    assert fields["PRDFORGE_LLM__ANALYSIS_BATCH_ITEMS"]["placeholder"] == "70"
    assert fields["PRDFORGE_LLM__CLI_TIMEOUT_S"]["value"] == ""


def test_a_suggestion_never_masquerades_as_a_saved_value(env_path: Path):
    # The API must report what is actually in the file; pre-filling is the
    # browser's business, and `set` is what tells the two apart.
    fields = {f["key"]: f for f in env_file.describe(env_path)}
    cell = fields["PRDFORGE_LLM__OPENCODE_MODELS__DEFAULT"]
    assert cell["value"] == ""
    assert cell["set"] is False
    assert cell["suggested"]


def test_the_vendored_picker_is_present_and_attributed():
    """The front end has no build step, so a dependency is a file in the tree.

    That is a deliberate trade, and it comes with an obligation: the file says
    where it came from, which version, and under what licence, because nothing
    else in this repo records it.
    """
    from prdforge.ui.app import STATIC

    vendored = STATIC / "js" / "vendor" / "autocomplete.js"
    assert vendored.is_file()
    header = vendored.read_text(encoding="utf-8")[:900]
    assert "autocompleter 10.0.0" in header
    assert "MIT" in header
    assert "github.com/denis-taran/autocomplete" in header


def test_no_datalist_is_used_in_the_front_end():
    """`<datalist>` renders as an unstyleable system menu.

    Matching on *use* rather than the word: the note in combo.js explaining why
    it was replaced is the reason this rule exists, and a test that forbids
    saying so would delete its own justification.
    """
    from prdforge.ui.app import STATIC

    uses = ("el('datalist'", 'el("datalist"', "querySelector('datalist", "setAttribute('list'")
    offenders = [
        path.name
        for path in (STATIC / "js").glob("*.js")
        if any(use in path.read_text(encoding="utf-8") for use in uses)
    ]
    assert not offenders, f"datalist is back in {offenders}"
