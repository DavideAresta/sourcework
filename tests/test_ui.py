"""The UI layer and per-run model overrides.

The API tests drive the real FastAPI app through Starlette's test client; only
the run manager is stubbed, because starting a mesh is not what is under test
here. Everything else - multipart parsing, the settings allow-list, SSE framing,
the store - is exercised for real.
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sourcework import __version__
from sourcework.config import LLMOverrides, LLMSettings, effective_llm, llm_overrides, settings
from sourcework.llm import LLM
from sourcework.models import PRDRequest
from sourcework.ui import env_file
from sourcework.ui.app import build_app
from sourcework.ui.runner import RunManager
from sourcework.ui.store import Run, RunStore, now_iso

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
    # A mesh booted with SOURCEWORK_LLM__STUB=1 must not silently fake a run that
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
    from sourcework.a2a_common import AgentPool

    pool = AgentPool(registry={}, llm=LLMOverrides(backend="claude-code"))
    attached = pool._with_llm({"title": "x"})
    assert attached["llm"]["backend"] == "claude-code"

    # An explicit per-hop choice wins over the pool's default.
    kept = pool._with_llm({"title": "x", "llm": {"backend": "opencode-cli"}})
    assert kept["llm"]["backend"] == "opencode-cli"

    assert "llm" not in AgentPool(registry={})._with_llm({"title": "x"})


def test_the_executor_reads_overrides_without_consuming_them():
    from sourcework.a2a_common.executor import _read_overrides

    payload = {"title": "x", "llm": {"backend": "claude-code"}}
    overrides = _read_overrides(payload)
    assert overrides.backend == "claude-code"
    # Popping it left PRDRequest.llm empty, so the orchestrator built its pool
    # with no override and the other seven agents never heard about it.
    assert "llm" in payload


def test_a_malformed_override_is_ignored_rather_than_fatal():
    from sourcework.a2a_common.executor import _read_overrides

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
        "SOURCEWORK_LLM__BACKEND=litellm\n"
        "ANTHROPIC_API_KEY=sk-real-secret\n"
        "\n"
        "SOURCEWORK_CONFLUENCE__EMAIL=me@example.com\n",
        encoding="utf-8",
    )
    return path


def test_secrets_go_to_the_browser_masked(env_path: Path):
    fields = {f["key"]: f for f in env_file.describe(env_path)}
    assert fields["ANTHROPIC_API_KEY"]["value"] == env_file.MASK
    assert fields["ANTHROPIC_API_KEY"]["set"] is True
    assert fields["SOURCEWORK_CONFLUENCE__EMAIL"]["value"] == "me@example.com"


def test_saving_an_untouched_secret_keeps_it(env_path: Path):
    # The form posts every field, including the masked ones it never touched.
    assert env_file.write(env_path, {"ANTHROPIC_API_KEY": env_file.MASK}) == []
    assert env_file.read(env_path)["ANTHROPIC_API_KEY"] == "sk-real-secret"


def test_a_real_secret_change_is_written(env_path: Path):
    assert env_file.write(env_path, {"ANTHROPIC_API_KEY": "sk-new"}) == ["ANTHROPIC_API_KEY"]
    assert env_file.read(env_path)["ANTHROPIC_API_KEY"] == "sk-new"


def test_a_local_endpoint_is_not_offered_hosted_models(tmp_path: Path):
    """The profiles are hosted model ids. Pre-filling one into an empty cell on
    a local install means Save writes a model the operator has no key for, and
    the run fails on a value they never chose."""
    path = tmp_path / ".env"
    path.write_text(
        "SOURCEWORK_LLM__BACKEND=litellm\n"
        "SOURCEWORK_LLM__API_BASE=http://127.0.0.1:8081/v1\n",
        encoding="utf-8",
    )

    fields = {f["key"]: f for f in env_file.describe(path)}
    assert fields["SOURCEWORK_LLM__REASONING_MODEL"]["suggested"] == ""
    assert fields["SOURCEWORK_LLM__CRITIC_MODEL"]["suggested"] == ""
    # The CLI backends authenticate themselves; their suggestions still hold.
    assert fields["SOURCEWORK_LLM__CLAUDE_CODE_MODELS__REASONING"]["suggested"]

    # ...and a profile button cannot write one either.
    profiles = env_file.profiles_for(path)
    assert not any(k.startswith("SOURCEWORK_LLM__REASONING_MODEL")
                   for p in profiles.values() for k in p["models"])
    assert any("CLAUDE_CODE" in k for p in profiles.values() for k in p["models"])


def test_a_hosted_install_still_gets_its_suggestions(env_path: Path):
    """The fix must not cost the common case its presets."""
    fields = {f["key"]: f for f in env_file.describe(env_path)}
    assert fields["SOURCEWORK_LLM__REASONING_MODEL"]["suggested"].startswith("anthropic/")
    assert env_file.profiles_for(env_path) == env_file.PROFILES


def test_an_unset_switch_shows_the_default_it_actually_has(env_path: Path):
    """A checkbox has no "unset" position, and the form posts every control it
    drew. Rendering an absent default-on setting unticked means opening the
    settings page and pressing Save silently turns it off.
    """
    from sourcework.config import LLMSettings

    fields = {f["key"]: f for f in env_file.describe(env_path)}

    constrained = fields["SOURCEWORK_LLM__CONSTRAINED_JSON"]
    assert constrained["set"] is False, "the fixture must not set it, or this proves nothing"
    assert LLMSettings().constrained_json is True, "guarding the premise, not the behaviour"
    assert constrained["value"].lower() in ("true", "1"), "must render ticked"

    # ...and a default-off switch still reads as off rather than as ticked.
    assert fields["SOURCEWORK_LLM__STUB"]["value"].lower() in ("false", "0", "")


def test_the_ui_does_not_offer_itself_to_the_network_by_default():
    """It has no authentication, `/api/settings` rewrites .env including provider
    keys, and `/api/runs` returns the full text of every ingested document.
    Binding wider has to be a decision someone makes, not one they inherit."""
    import inspect

    from sourcework.ui import DEFAULT_HOST
    from sourcework.ui.app import serve

    assert DEFAULT_HOST == "127.0.0.1"
    assert inspect.signature(serve).parameters["host"].default == DEFAULT_HOST


def test_unknown_keys_cannot_be_injected(env_path: Path):
    # Without the allow-list this endpoint writes arbitrary environment
    # variables into the file the whole system boots from.
    assert env_file.write(env_path, {"EVIL": "pwned", "PATH": "/tmp"}) == []
    assert "EVIL" not in env_path.read_text()


def test_editing_preserves_comments_and_untouched_lines(env_path: Path):
    env_file.write(env_path, {"SOURCEWORK_LLM__BACKEND": "claude-code"})
    text = env_path.read_text()
    assert "# a comment worth keeping" in text
    assert "SOURCEWORK_LLM__BACKEND=claude-code" in text
    assert "SOURCEWORK_CONFLUENCE__EMAIL=me@example.com" in text


def test_a_new_key_is_appended(env_path: Path):
    env_file.write(env_path, {"SOURCEWORK_LLM__CLAUDE_CODE_MODELS__REASONING": "sonnet"})
    assert "SOURCEWORK_LLM__CLAUDE_CODE_MODELS__REASONING=sonnet" in env_path.read_text()


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


async def test_approval_survives_a_reopen(tmp_path: Path):
    store = RunStore(tmp_path / "runs.db")
    await store.save(Run(
        id="a1", title="T", status="ok", created_at=now_iso(), request={},
        approval={"state": "approved", "by": "D", "at": now_iso(), "history": []},
    ))
    store.close()

    reopened = RunStore(tmp_path / "runs.db")
    try:
        assert (await reopened.get("a1")).approval["by"] == "D"
    finally:
        reopened.close()


async def test_an_old_database_gains_the_approval_column(tmp_path: Path):
    """Databases written before approvals existed migrate in place."""
    import sqlite3

    db = tmp_path / "runs.db"
    legacy = sqlite3.connect(db)
    legacy.execute(
        "CREATE TABLE runs (id TEXT PRIMARY KEY, created_at TEXT NOT NULL, "
        "finished_at TEXT, title TEXT NOT NULL, status TEXT NOT NULL, request TEXT NOT NULL, "
        "result TEXT, error TEXT, events TEXT NOT NULL DEFAULT '[]', usage TEXT)"
    )
    legacy.execute("INSERT INTO runs (id, created_at, title, status, request) "
                   "VALUES ('old', '2026-01-01T00:00:00+00:00', 'Legacy', 'ok', '{}')")
    legacy.commit()
    legacy.close()

    store = RunStore(db)
    try:
        run = await store.get("old")
        assert run is not None and run.approval is None
        # And the migrated store accepts a new approval write.
        run.approval = {"state": "approved", "by": "D", "at": now_iso(), "history": []}
        await store.save(run)
        assert (await store.get("old")).approval["state"] == "approved"
    finally:
        store.close()


async def test_purge_removes_only_finished_runs_past_the_cutoff(tmp_path: Path):
    store = RunStore(tmp_path / "runs.db")
    try:
        old = "2020-01-01T00:00:00+00:00"
        await store.save(Run(id="old-ok", title="T", status="ok", created_at=old, request={}))
        await store.save(Run(id="old-run", title="T", status="running", created_at=old, request={}))
        await store.save(Run(id="new-ok", title="T", status="ok", created_at=now_iso(), request={}))

        # The ids, not a count: the caller has checkpoints to erase too.
        assert await store.purge_older_than(30) == ["old-ok"]
        # A running run is never purged, however old: that would lie about
        # work in progress.
        assert await store.get("old-run") is not None
        assert await store.get("new-ok") is not None
        assert await store.get("old-ok") is None
    finally:
        store.close()


def test_start_up_retention_erases_the_checkpoints_too(tmp_path: Path, monkeypatch):
    """A run is its row *and* its checkpoints - both hold the full source text.

    Deleting the row and leaving the checkpoint would make the retention
    setting a half-truth: the history stops showing the run while the text it
    was built from stays on disk.
    """
    from sourcework import checkpoint, config

    monkeypatch.setattr(
        "sourcework.ui.app.settings",
        lambda: config.Settings(runs=config.RunsSettings(retention_days=30)),
    )
    monkeypatch.setattr(checkpoint.paths, "workspace", lambda *a, **k: tmp_path)
    monkeypatch.setattr("sourcework.ui.app.RunManager", FakeManager)

    store = RunStore(tmp_path / "sourcework-ui.db")
    asyncio.run(store.save(Run(
        id="ancient", title="T", status="ok", request={},
        created_at="2020-01-01T00:00:00+00:00", finished_at="2020-01-01T00:00:00+00:00",
    )))
    store.close()
    checkpoint.directory().mkdir(parents=True, exist_ok=True)
    stale = checkpoint.directory() / "ancient.analyst.json"
    stale.write_text("{}")

    with TestClient(build_app(tmp_path), headers={"X-SourceWork-UI": "1"}) as purged_client:
        assert purged_client.get("/api/runs/ancient").status_code == 404
    assert not stale.exists()


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
    monkeypatch.setattr("sourcework.ui.app.RunManager", FakeManager)
    # The header every write carries, sent here for the same reason the browser
    # sends it. That it is *required* is asserted in test_security.py; these
    # tests are about what the endpoints do once a legitimate client reaches
    # them, and repeating it on every call would only obscure that.
    with TestClient(build_app(tmp_path), headers={"X-SourceWork-UI": "1"}) as test_client:
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
        files=[("files", ("../../../etc/sourcework-pwned", b"x", "text/plain"))],
    )
    assert response.status_code == 200
    run_id = response.json()["id"]
    written = list((tmp_path / "uploads" / run_id).iterdir())
    assert [p.name for p in written] == ["sourcework-pwned"]
    assert not Path("/etc/sourcework-pwned").exists()


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


def test_the_audit_bundle_downloads_as_a_zip(client: TestClient):
    response = client.post(
        "/api/runs", data={"request": json.dumps({"title": "Audited", "notes": ["n"]})}
    )
    run_id = response.json()["id"]

    audit = client.get(f"/api/runs/{run_id}/audit")
    assert audit.status_code == 200
    assert audit.headers["content-type"] == "application/zip"
    assert "audited" in audit.headers["content-disposition"]
    assert audit.content[:2] == b"PK"  # every zip starts with the PK magic
    assert client.get("/api/runs/nonexistent/audit").status_code == 404


def test_approval_is_recorded_with_its_history(client: TestClient):
    response = client.post(
        "/api/runs", data={"request": json.dumps({"title": "Signed off", "notes": ["n"]})}
    )
    run_id = response.json()["id"]

    rejected = client.post(
        f"/api/runs/{run_id}/approval", json={"state": "rejected", "by": "D", "note": "too thin"}
    )
    assert rejected.status_code == 200
    approved = client.post(f"/api/runs/{run_id}/approval", json={"state": "approved", "by": "D"})

    approval = approved.json()
    assert approval["state"] == "approved"
    # Append-only: a rejected-then-approved run shows both, which is the point
    # of an approval trail.
    assert [h["state"] for h in approval["history"]] == ["rejected", "approved"]

    run = client.get(f"/api/runs/{run_id}").json()
    assert run["approval"]["state"] == "approved"
    # The decision reaches the rendered document: the status the Confluence
    # lozenge renders follows the approval.
    assert run["result"]["prd"]["status"] == "approved"


def test_signing_off_does_not_strip_the_review_from_the_document(
    client: TestClient, tmp_path: Path
):
    """Approving re-renders the artifacts; the review has to survive that.

    The pipeline re-renders after the last review round precisely so the
    shipped Markdown carries its own verdict. Re-rendering here without the
    stored review would delete that section at the moment somebody signs the
    document - the quietest possible way to lose it.
    """
    run_id = _finished_run(client)
    store = RunStore(tmp_path / "sourcework-ui.db")
    try:
        run = asyncio.run(store.get(run_id))
        run.result = {
            "prd": {"title": "Returns portal"},
            "markdown": "# Returns portal\n\n## Automated review\n",
            "confluence_storage": "<p/>",
            "review": {
                "summary": "Two findings.",
                "verdict": "needs_revision",
                "standards": "ISO/IEC/IEEE 29148 characteristics; EARS patterns off",
                "findings": [{"severity": "minor", "category": "quality",
                              "location": "REQ-001", "detail": "Escape clause: TBD."}],
            },
            "stats": {},
        }
        asyncio.run(store.save(run))
    finally:
        store.close()

    client.post(f"/api/runs/{run_id}/approval", json={"state": "approved", "by": "D"})

    result = client.get(f"/api/runs/{run_id}").json()["result"]
    assert result["prd"]["status"] == "approved"
    assert "## Automated review" in result["markdown"]
    assert "needs_revision" in result["markdown"]
    # Including the basis the quality rules were checked against - a verdict
    # without its yardstick is just an adjective.
    assert "29148" in result["markdown"]
    assert "Escape clause" in result["confluence_storage"]


def test_an_invalid_approval_state_is_rejected(client: TestClient):
    response = client.post(
        "/api/runs", data={"request": json.dumps({"title": "t", "notes": ["n"]})}
    )
    run_id = response.json()["id"]
    assert client.post(f"/api/runs/{run_id}/approval", json={"state": "lgtm"}).status_code == 400
    assert client.post("/api/runs/nonexistent/approval", json={"state": "approved"}).status_code == 404


def test_deleting_a_run_returns_an_erasure_record(client: TestClient):
    response = client.post(
        "/api/runs",
        data={"request": json.dumps({"title": "Erase me", "notes": ["n"]})},
        files=[("files", ("spec.md", b"# Spec", "text/markdown"))],
    )
    run_id = response.json()["id"]

    record = client.delete(f"/api/runs/{run_id}").json()
    assert record["deleted"] is True
    # Uploads are shared-workspace files: listed as left in place, not silently
    # swept away under a "deleted" claim.
    assert any("uploads" in entry for entry in record["left_in_place"])
    assert client.get(f"/api/runs/{run_id}").status_code == 404


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
    assert "SOURCEWORK_LLM__BACKEND" in keys
    assert all(f["value"] != "sk-" for f in payload["fields"])

    result = client.put("/api/settings", json={"NOT_ALLOWED": "x"}).json()
    assert result["changed"] == []


def test_a_restart_requiring_save_restarts_the_mesh(client: TestClient, tmp_path: Path, monkeypatch):
    """Saving a restart-flagged setting must actually restart the mesh.

    The agents read their configuration once, at start-up, so the save is only
    worth anything once every peer has been asked to re-exec itself. If that
    call disappears, the page reports success for a change nothing running
    will ever see.
    """
    import sourcework.ui.app as ui_app
    from sourcework.config import Settings

    env = tmp_path / "env"
    env.write_text("SOURCEWORK_LLM__BACKEND=litellm\n", encoding="utf-8")
    monkeypatch.setattr(ui_app, "settings", lambda: Settings(env_file=str(env)))

    restarted: list[str] = []

    async def fake_restart_mesh() -> list[str]:
        restarted.append("called")
        return ["orchestrator", "writer"]

    monkeypatch.setattr(ui_app, "_restart_mesh", fake_restart_mesh)

    result = client.put("/api/settings", json={"SOURCEWORK_LLM__BACKEND": "llama-cpp"}).json()
    assert restarted == ["called"]
    assert result["restart_required"] is True
    assert "restarting" in result["message"]


async def _no_peers_reached() -> list[str]:
    return []


def test_a_save_with_no_mesh_reachable_keeps_the_manual_message(
    client: TestClient, tmp_path: Path, monkeypatch
):
    """An install with no running mesh must not claim one was restarted."""
    import sourcework.ui.app as ui_app
    from sourcework.config import Settings

    env = tmp_path / "env"
    env.write_text("SOURCEWORK_LLM__BACKEND=litellm\n", encoding="utf-8")
    monkeypatch.setattr(ui_app, "settings", lambda: Settings(env_file=str(env)))
    monkeypatch.setattr(ui_app, "_restart_mesh", _no_peers_reached)

    result = client.put("/api/settings", json={"SOURCEWORK_LLM__BACKEND": "llama-cpp"}).json()
    assert result["restart_required"] is True
    assert "restart" not in result["message"]


async def test_restart_mesh_asks_every_peer_and_skips_the_down_ones(monkeypatch):
    """One dead agent must not stop the others being told to restart."""
    import httpx

    import sourcework.ui.app as ui_app
    from sourcework.config import Settings

    called: list[str] = []

    class FakeHTTP:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc: object) -> None:
            return None

        async def post(self, url: str):
            called.append(url)
            if ":8007" in url:
                raise httpx.ConnectError("no such peer")

    fake = FakeHTTP()
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: fake)
    monkeypatch.setattr(
        ui_app,
        "settings",
        lambda: Settings(peers={
            "orchestrator": "http://127.0.0.1:8000",
            "critic": "http://127.0.0.1:8007",
        }),
    )

    reached = await ui_app._restart_mesh()
    assert "http://127.0.0.1:8000/api/restart" in called
    assert "http://127.0.0.1:8007/api/restart" in called
    assert len(reached) == len(called) - 1
    assert "critic" not in reached
    assert "orchestrator" in reached


def test_the_page_and_static_assets_are_served(client: TestClient):
    assert "PRD" in client.get("/").text
    assert client.get("/settings").status_code == 200
    assert client.get("/static/js/app.js").status_code == 200
    health = client.get("/healthz").json()
    assert health["status"] == "ok"
    # The version label in the header must show what the code actually is: a
    # label that drifted from __version__ would be a lie nobody notices.
    assert health["version"] == __version__


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

    from sourcework.ui.store import RunStore

    store = RunStore(tmp_path / "sourcework-ui.db")
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
    from sourcework import stream

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
    from sourcework.a2a_common import AgentPool

    assert "stream" not in AgentPool(registry={})._with_llm({"title": "x"})
    assert AgentPool(registry={}, narrate=True)._with_llm({"title": "x"})["stream"] is True


def test_the_executor_narrates_only_on_request():
    from sourcework.a2a_common.executor import _wants_narration

    assert _wants_narration({"stream": True})
    assert not _wants_narration({"title": "x"})
    assert not _wants_narration({"stream": False})


async def test_the_narrator_coalesces_and_flushes_what_is_left():
    from sourcework.backends.base import StreamChunk
    from sourcework.stream import Narrator

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
    from sourcework.backends.base import StreamChunk
    from sourcework.stream import Narrator

    published: list[tuple[str, str]] = []

    async def publish(kind: str, text: str) -> None:
        published.append((kind, text))

    async with Narrator(publish, interval_s=60, budget=10) as narrator:
        for _ in range(100):
            narrator.sink(StreamChunk(kind="text", text="xxxxx"))

    assert "".join(text for _, text in published) == "x" * 10


async def test_a_failing_sink_never_reaches_the_pipe_reader():
    from sourcework.backends.base import StreamChunk
    from sourcework.stream import Narrator

    async def publish(kind: str, text: str) -> None:
        raise RuntimeError("subscriber exploded")

    # The sink runs inside the loop draining the CLI's stdout. An exception
    # there stops the drain and deadlocks the very call it was narrating.
    async with Narrator(publish, interval_s=60) as narrator:
        narrator.sink(StreamChunk(kind="text", text="hello"))


async def test_narration_reaches_subscribers_without_being_stored(tmp_path: Path):
    from sourcework.ui.runner import RunManager

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
    from sourcework.backends.base import StreamChunk
    from sourcework.stream import Narrator

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

    from sourcework import stream
    from sourcework.a2a_common.executor import Progress

    class Updater:
        async def update_status(self, *args, **kwargs) -> None:  # noqa: ANN002, ANN003
            pass

    progress = Progress(Updater())
    with caplog.at_level(logging.INFO, logger="sourcework.a2a_common.executor"):
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
    for backend in ("litellm", "llama-cpp", "claude-code", "opencode-cli", "copilot-cli",
                    "codex-cli", "agy-cli"):
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
    assert "SOURCEWORK_LLM__ANALYSIS_BATCH_ITEMS" in keys
    assert "SOURCEWORK_LLM__ANALYSIS_BATCH_CHARS" in keys


def test_local_model_directories_are_reachable_from_the_ui():
    """The scanner cannot discover a model folder the settings page cannot save."""
    keys = {f.key for f in env_file.FIELDS}
    assert "SOURCEWORK_MODEL_DIRS" in keys


def test_every_profile_covers_every_model_cell():
    """A profile that leaves a backend blank is a broken failover.

    Each profile is applied to *all* backends, not just the active one, because
    the failover target is exactly the one nobody remembers to configure - and
    on opencode an unset model is not a default, it is an outright failure.
    """
    # llama.cpp serves whatever GGUFs the operator installed. A hosted preset
    # cannot know their ids, so profiles must deliberately leave those cells
    # alone rather than write a value guaranteed to fail.
    cells = {
        f.key for f in env_file.FIELDS
        if f.group == "Models" and f.backend != "llama-cpp"
    }
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
    # Not decoration: opus-5/sonnet-5 are the current generation in the live
    # `opencode models` catalogue (opus-4-6 was the model that reasoned well
    # when the profiles were first written), and gpt-5.4 is the Copilot model
    # that returns readable reasoning rather than an encrypted blob.
    balanced = env_file.PROFILES["balanced"]["models"]
    assert balanced["SOURCEWORK_LLM__OPENCODE_MODELS__REASONING"] == "opencode/claude-opus-5"
    assert balanced["SOURCEWORK_LLM__COPILOT_MODELS__REASONING"] == "gpt-5.4"


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
    assert fields["SOURCEWORK_LLM__CLI_TIMEOUT_S"]["placeholder"] == "600.0"
    assert fields["SOURCEWORK_LLM__ANALYSIS_BATCH_ITEMS"]["placeholder"] == "70"
    assert fields["SOURCEWORK_LLM__CLI_TIMEOUT_S"]["value"] == ""


def test_a_suggestion_never_masquerades_as_a_saved_value(env_path: Path):
    # The API must report what is actually in the file; pre-filling is the
    # browser's business, and `set` is what tells the two apart.
    fields = {f["key"]: f for f in env_file.describe(env_path)}
    cell = fields["SOURCEWORK_LLM__OPENCODE_MODELS__DEFAULT"]
    assert cell["value"] == ""
    assert cell["set"] is False
    assert cell["suggested"]


def test_the_vendored_picker_is_present_and_attributed():
    """The front end has no build step, so a dependency is a file in the tree.

    That is a deliberate trade, and it comes with an obligation: the file says
    where it came from, which version, and under what licence, because nothing
    else in this repo records it.
    """
    from sourcework.ui.app import STATIC

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
    from sourcework.ui.app import STATIC

    uses = ("el('datalist'", 'el("datalist"', "querySelector('datalist", "setAttribute('list'")
    offenders = [
        path.name
        for path in (STATIC / "js").glob("*.js")
        if any(use in path.read_text(encoding="utf-8") for use in uses)
    ]
    assert not offenders, f"datalist is back in {offenders}"


def test_clearing_a_number_leaves_a_config_that_still_loads(tmp_path: Path):
    """The bug this closes: the settings page posts every control it drew, so a
    blank "Max output tokens" arrived as "". Written as `KEY=` that reads back
    as the empty string - fine for a text field, not an int - and pydantic
    raised at construction. The save returned 200 and the *running* process
    carried on with its cached settings, so the symptom was every process
    started afterwards dying at startup, which looks nothing like a bad save.
    """
    from sourcework.config import Settings

    path = tmp_path / ".env"
    path.write_text("SOURCEWORK_LLM__MAX_TOKENS=8192\nSOURCEWORK_LLM__TEMPERATURE=0.2\n",
                    encoding="utf-8")

    env_file.write(path, {"SOURCEWORK_LLM__MAX_TOKENS": "", "SOURCEWORK_LLM__TEMPERATURE": ""})

    # Commented out, so `read` no longer sees it as set...
    assert "SOURCEWORK_LLM__MAX_TOKENS" not in env_file.read(path)
    assert "# SOURCEWORK_LLM__MAX_TOKENS=" in path.read_text(), "left visible, not deleted"
    # ...and the real assertion: a fresh process can still load the file.
    settings = Settings(_env_file=str(path))
    assert settings.llm.max_tokens == 8192, "cleared, so back to the code default"


def test_clearing_a_text_field_really_clears_it(tmp_path: Path):
    """Only numbers get the fallback treatment. Emptying a text box is a real
    value, and resurrecting a default there would undo what was asked for."""
    path = tmp_path / ".env"
    path.write_text("SOURCEWORK_CONFLUENCE__EMAIL=me@example.com\n", encoding="utf-8")

    env_file.write(path, {"SOURCEWORK_CONFLUENCE__EMAIL": ""})

    assert env_file.read(path).get("SOURCEWORK_CONFLUENCE__EMAIL", "") == ""


def test_a_number_never_set_and_left_blank_adds_nothing(tmp_path: Path):
    path = tmp_path / ".env"
    path.write_text("SOURCEWORK_LLM__BACKEND=litellm\n", encoding="utf-8")

    env_file.write(path, {"SOURCEWORK_LLM__TIMEOUT_S": ""})

    assert "TIMEOUT_S" not in path.read_text()


# ---------------------------------------------------------------------------
# The front end's copy of the server's vocabulary
#
# CI never runs the browser: there is no JS test and no JS lint, by design -
# the front end is plain modules with no build step. So the one thing worth
# asserting from here is that the strings it *branches on* still exist on this
# side. They are the failure that looks like nothing: a severity the critic
# renamed does not throw, it just renders every finding the same colour.
# ---------------------------------------------------------------------------

STATIC_JS = Path(__file__).resolve().parent.parent / "src" / "sourcework" / "ui" / "static" / "js"


def _object_keys(source: str, name: str) -> set[str]:
    """The keys of a `const NAME = { a: …, b: … }` literal in a JS module.

    Quoted values are blanked before the keys are read, so a colon inside a
    label cannot be mistaken for one.
    """
    body = re.search(rf"const {name} = \{{(.*?)\}};", source, re.DOTALL)
    assert body, f"{name} is no longer an object literal - update this test with it"
    without_values = re.sub(r"'[^']*'", "''", body.group(1))
    return set(re.findall(r"([A-Za-z_]\w*)\s*:", without_values))


def test_the_review_tab_knows_every_severity_the_critic_emits():
    """A severity with no entry here renders in whatever colour the fallback
    picks, so a blocker quietly reads as a nit. That is how `blocking` - a value
    Severity never had - survived in this file long enough to make every finding
    in every review the same shade of yellow."""
    from sourcework.models import Severity

    rendered = _object_keys((STATIC_JS / "result.js").read_text(), "SEVERITY_CLASS")
    assert rendered == {s.value for s in Severity}


def test_the_history_list_knows_every_status_a_run_can_hold():
    """A status with no pill still renders - unstyled, and indistinguishable
    from the ones that are fine."""
    from sourcework.ui.store import STATUSES

    styled = _object_keys((STATIC_JS / "app.js").read_text(), "STATUS_PILL")
    assert styled == set(STATUSES)


def test_the_dashboard_knows_every_readiness_state():
    from sourcework.readiness import Readiness, assess

    states = _object_keys((STATIC_JS / "dashboard.js").read_text(), "STATE")
    # `ready`, `needs_work`, `unreviewed` come from the assessment itself; the
    # other two are run statuses the dashboard falls back to (ui/app.py).
    assert {"ready", "needs_work", "unreviewed"} <= states
    assert assess(None, None).state in states
    assert Readiness(state="ready").state in states


async def test_a_run_that_warned_says_so_in_the_list(tmp_path: Path):
    """`ok` with a dropped source is still `ok`. The list view cannot carry the
    text of what was skipped, but carrying the count is what makes somebody open
    the run and read it."""
    store = RunStore(tmp_path / "runs.db")
    try:
        await store.save(Run(
            id="warned", title="T", status="ok", created_at=now_iso(), request={},
            result={"stats": {"warnings": ["Kickoff meeting: no text extracted"],
                              "failures": []}},
        ))
        summary = (await store.get("warned")).summary()
        assert summary["warnings"] == 1
        assert summary["failures"] == 0
        # The strings themselves stay out of the list payload.
        assert "Kickoff" not in json.dumps(summary)
    finally:
        store.close()


def test_the_roles_a_run_can_override_are_the_roles_settings_can_configure(client: TestClient):
    """One list, derived from the settings fields. Two hand-written copies is
    how the API came to offer `fast`, which no agent has ever asked for, while
    omitting `critic`, which every review runs on."""
    roles = client.get("/api/backends").json()["roles"]
    assert roles == env_file.model_roles()
    assert "critic" in roles
    assert "fast" not in roles


def test_a_finished_run_carries_the_same_verdict_the_dashboard_shows(client: TestClient):
    """The run's own page could not say whether the PRD was ready; the reader
    had to go to the dashboard to find out about the document in front of them."""
    run_id = _finished_run(client)
    run = client.get(f"/api/runs/{run_id}").json()
    assert run["readiness"]["state"] in {"ready", "needs_work", "unreviewed"}
    assert run["readiness"]["headline"]


def test_a_run_with_no_result_has_no_verdict_to_give(client: TestClient, tmp_path: Path):
    """Nothing to assess is not the same as "not ready", and saying the latter
    about a run that is still going would be inventing a judgement."""
    store = RunStore(tmp_path / "sourcework-ui.db")
    try:
        asyncio.run(store.save(Run(
            id="midflight", title="T", status="running", created_at=now_iso(), request={},
        )))
    finally:
        store.close()
    assert client.get("/api/runs/midflight").json()["readiness"] is None
