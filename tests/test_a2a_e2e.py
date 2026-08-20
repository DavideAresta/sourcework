"""End-to-end over real A2A JSON-RPC.

Boots the agents on loopback ports and drives them through the protocol - agent
card resolution, message/stream, task lifecycle, artifact aggregation - rather
than calling the handlers directly. If the SDK's wire format changes, this is
what catches it.
"""

from __future__ import annotations

import asyncio
import importlib
import socket
import threading
import time

import httpx
import pytest
import uvicorn

from sourcework.a2a_common import AgentPool, RemoteAgentError, build_app
from sourcework.cli import AGENTS
from sourcework.models import InputRef, Modality, PRDRequest, PRDResult

SAMPLES = None


def _free(port: int) -> bool:
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", port)) != 0


@pytest.fixture(scope="module")
def mesh():
    """All eight agents, in-process, on their canonical ports."""
    servers = []
    ports = []
    for name in AGENTS:
        module = importlib.import_module(AGENTS[name])
        if not _free(module.PORT):
            pytest.skip(f"port {module.PORT} already in use")
        app = build_app(module.card(), module.executor())
        server = uvicorn.Server(
            uvicorn.Config(app, host="127.0.0.1", port=module.PORT, log_level="error")
        )
        threading.Thread(target=server.run, daemon=True).start()
        servers.append(server)
        ports.append(module.PORT)

    deadline = time.time() + 30
    while time.time() < deadline:
        try:
            with httpx.Client(timeout=1.0) as http:
                if all(http.get(f"http://127.0.0.1:{p}/healthz").status_code == 200 for p in ports):
                    break
        except Exception:  # noqa: BLE001, S110
            pass
        time.sleep(0.2)
    else:
        pytest.fail("agents did not start")

    yield ports

    for server in servers:
        server.should_exit = True
    time.sleep(0.3)


async def test_agent_cards_are_served(mesh):
    async with httpx.AsyncClient(timeout=5.0) as http:
        resp = await http.get("http://127.0.0.1:8000/.well-known/agent-card.json")
    card = resp.json()
    assert resp.status_code == 200
    assert card["name"] == "PRD Orchestrator"
    assert card["supportedInterfaces"][0]["protocolBinding"] == "JSONRPC"
    assert {s["id"] for s in card["skills"]} == {"generate_prd", "mesh_status"}


async def test_discovery_finds_every_agent(mesh):
    async with AgentPool() as pool:
        found = await pool.discover()
    assert set(found) == set(AGENTS)
    assert "analyse_requirements" in found["requirements"]
    assert "publish_prd" in found["confluence"]


async def test_unknown_skill_is_rejected_before_the_call(mesh):
    async with AgentPool() as pool:
        with pytest.raises(RemoteAgentError, match="not advertised"):
            await pool.call("writer", "make_me_a_sandwich", {})


async def test_handler_error_surfaces_as_failed_task(mesh):
    async with AgentPool() as pool:
        with pytest.raises(RemoteAgentError, match="empty"):
            await pool.call(
                "writer",
                "write_prd",
                {"title": "x", "requirement_set": {"requirements": []}},
            )


async def test_transcript_agent_over_the_wire(mesh, tmp_path):
    vtt = tmp_path / "m.vtt"
    vtt.write_text(
        "WEBVTT\n\n00:00:04.000 --> 00:00:19.000\n"
        "<v Priya>We agreed the tolerance is one percent, configurable per entity.\n",
        encoding="utf-8",
    )
    async with AgentPool() as pool:
        data = await pool.call(
            "transcript", "extract_transcript", {"ref": {"uri": vtt.as_uri()}}
        )
    assert data["source"]["modality"] == "transcript"
    assert data["source"]["metadata"]["speakers"] == ["Priya"]
    assert data["evidence"], "stub extraction should still produce evidence"


async def test_full_pipeline_produces_a_traceable_prd(mesh, tmp_path):
    md = tmp_path / "notes.md"
    md.write_text(
        "# Scope\n\nThe nightly matching run must complete within two hours "
        "for fifteen thousand invoices.\n\n"
        "## Audit\n\nAudit records are immutable and retained for seven years.\n",
        encoding="utf-8",
    )
    vtt = tmp_path / "kickoff.vtt"
    vtt.write_text(
        "WEBVTT\n\n00:00:04.000 --> 00:00:19.000\n"
        "<v Marco>Reason codes must come from a fixed list so we can report on them.\n",
        encoding="utf-8",
    )

    request = PRDRequest(
        title="Invoice reconciliation",
        inputs=[
            InputRef(uri=md.as_uri(), title="Notes"),
            InputRef(uri=vtt.as_uri(), title="Kickoff", modality=Modality.TRANSCRIPT),
        ],
        review_rounds=1,
    )

    async with AgentPool() as pool:
        data = await asyncio.wait_for(
            pool.call("orchestrator", "generate_prd", request), timeout=180
        )
    result = PRDResult.model_validate(data)

    assert result.prd.title == "Invoice reconciliation"
    assert result.prd.requirements.requirements, "pipeline produced no requirements"
    assert len(result.prd.sources) == 2
    assert result.stats["sources"] == 2
    assert result.stats["evidence"] > 0
    assert not result.stats["failures"], result.stats["failures"]
    assert result.review is not None

    # Traceability: every citation resolves to real evidence from a real source.
    evidence_ids = {e.id for e in result.prd.evidence}
    source_ids = {s.id for s in result.prd.sources}
    for req in result.prd.requirements.requirements:
        for ref in req.source_refs:
            assert ref.evidence_id in evidence_ids
            assert ref.source_id in source_ids

    assert "## Traceability" in result.markdown
    assert result.confluence_storage and "<h2>Requirements</h2>" in result.confluence_storage


async def test_bad_input_is_skipped_not_fatal(mesh, tmp_path):
    good = tmp_path / "ok.md"
    good.write_text("# T\n\nThe system must retain audit records for seven years.\n", encoding="utf-8")

    request = PRDRequest(
        title="Partial run",
        inputs=[
            InputRef(uri="file:///does/not/exist.pdf", title="Missing"),
            InputRef(uri=good.as_uri(), title="Good"),
        ],
        review_rounds=0,
    )
    async with AgentPool() as pool:
        result = PRDResult.model_validate(
            await asyncio.wait_for(pool.call("orchestrator", "generate_prd", request), timeout=180)
        )

    assert result.stats["sources"] == 1
    assert any("Missing" in f for f in result.stats["failures"])


async def test_mesh_status_skill(mesh):
    async with AgentPool() as pool:
        data = await pool.call("orchestrator", "mesh_status", {})
    assert data["unreachable"] == []
    assert len(data["agents"]) == len(AGENTS)


# ---------------------------------------------------------------------------
# Narration over the wire
#
# A dedicated one-skill agent rather than the mesh fixture: this is about the
# executor's plumbing, and it has to run whether or not the real ports are free.
# ---------------------------------------------------------------------------


async def test_a_working_agent_says_so_even_with_nothing_to_say(monkeypatch):
    """An agent must put bytes on the wire while a handler is thinking.

    Narration is the other thing that does, and it is not enough: it is opt-in,
    requested only for runs somebody is watching, and never emitted at all by
    the litellm backend or the hosted providers built on it. So a model call was
    minutes of total silence, which is exactly what httpx's read timeout and
    every reverse proxy's `proxy_read_timeout` are built to kill.
    """
    from sourcework.a2a_common import executor as ex

    monkeypatch.setattr(ex, "KEEPALIVE_INTERVAL_S", 0.02)
    beats: list[object] = []

    class Updater:
        async def update_status(self, state, message=None):  # noqa: ANN001, ANN202
            beats.append(message)

    async with ex._keepalive(Updater()):
        await asyncio.sleep(0.12)

    assert len(beats) >= 3
    # Messageless on purpose: `AgentPool.call` forwards only status updates that
    # carry a message, so this can never surface as a line in anyone's progress
    # log. It is there to be *bytes*, not to be read.
    assert all(message is None for message in beats)


async def test_a_keepalive_that_fails_does_not_take_the_run_with_it(monkeypatch):
    """A missed beat is worth strictly less than a run, so it is swallowed.

    The same rule narration follows: this is called from a background task
    alongside work that has already cost minutes and tokens, and letting it
    raise would throw that away to report a lost frame.
    """
    from sourcework.a2a_common import executor as ex

    monkeypatch.setattr(ex, "KEEPALIVE_INTERVAL_S", 0.01)

    class Broken:
        async def update_status(self, state, message=None):  # noqa: ANN001, ANN202
            raise RuntimeError("the event queue is gone")

    done = False
    async with ex._keepalive(Broken()):
        await asyncio.sleep(0.05)
        done = True
    assert done is True


def test_the_transport_outlasts_the_longest_call_it_has_to_carry():
    """The mesh read timeout is a ceiling on *silence*, and has to sit above the
    longest a single call may legitimately be silent for.

    They used to be the same number: a flat ``httpx.AsyncClient(timeout=600.0)``
    against ``llm.cli_timeout_s = 600``. The transport therefore gave up at the
    exact moment a CLI backend was still entitled to be thinking, never mind the
    empty-response retry, the schema retries and the failover chain wrapped
    around it. Connecting is not thinking, and must fail fast instead.
    """
    from sourcework.a2a_common import AgentPool
    from sourcework.config import settings

    timeouts = AgentPool(registry={})._timeouts()
    assert timeouts.read > settings().llm.cli_timeout_s
    assert timeouts.read > settings().llm.timeout_s
    assert timeouts.connect < timeouts.read


async def test_every_peer_is_probed_at_once():
    """Discovery must not cost the sum of the peers.

    This is what the browser's mesh indicator calls every thirty seconds. Walked
    sequentially, one host that hangs rather than refusing sets the latency of
    the whole answer - and did it eight times over, at a connect timeout sized
    for a model call.
    """
    from sourcework.a2a_common import AgentPool

    pool = AgentPool(registry={f"a{i}": f"http://127.0.0.1:{9000 + i}" for i in range(8)})
    in_flight = 0
    peak = 0

    async def slow_card(agent: str):
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        try:
            await asyncio.sleep(0.05)
            raise ConnectionError("nobody home")
        finally:
            in_flight -= 1

    pool.card = slow_card
    started = time.monotonic()
    assert await pool.discover() == {}  # unreachable is still an answer
    assert peak == 8
    # Eight sequential 50ms probes would be 400ms; concurrently it is one.
    assert time.monotonic() - started < 0.25



@pytest.fixture
def narrating_agent():
    """An agent whose only skill reports what the executor installed for it."""
    from pydantic import BaseModel

    from sourcework import stream
    from sourcework.a2a_common import SkillExecutor, build_card, skill
    from sourcework.backends.base import StreamChunk

    class Echo(BaseModel):
        summary: str = ""

    class Executor(SkillExecutor):
        def __init__(self) -> None:
            self.skills = {"echo": self.echo}
            super().__init__()

        async def echo(self, payload: dict) -> Echo:
            sink = stream.current_sink()
            if sink is not None:
                sink(StreamChunk(kind="reasoning", text="mulling "))
                sink(StreamChunk(kind="reasoning", text="it over"))
                sink(StreamChunk(kind="text", text="the answer"))
            return Echo(summary="watched" if sink is not None else "unwatched")

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    url = f"http://127.0.0.1:{port}"

    app = build_app(
        build_card(name="Probe", description="d", url=url, skills=[skill("echo", "Echo", "d")]),
        Executor(),
    )
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    threading.Thread(target=server.run, daemon=True).start()

    deadline = time.time() + 20
    while time.time() < deadline:
        try:
            with httpx.Client(timeout=1.0) as http:
                if http.get(f"{url}/healthz").status_code == 200:
                    break
        except Exception:  # noqa: BLE001, S110
            pass
        time.sleep(0.05)
    else:  # pragma: no cover
        pytest.fail("probe agent did not start")

    yield {"probe": url}
    server.should_exit = True
    time.sleep(0.2)


async def test_narration_travels_back_over_a2a(narrating_agent):
    from sourcework import stream

    heard: list[dict] = []
    plain: list[str] = []

    async def on_progress(message: str) -> None:
        narration = stream.decode(message)
        (heard.append(narration) if narration else plain.append(message))

    async with AgentPool(registry=narrating_agent, narrate=True) as pool:
        result = await pool.call("probe", "echo", {}, on_progress=on_progress)

    assert result["summary"] == "watched"
    # Coalesced by kind, so the two reasoning fragments arrive as one message.
    assert [(h["kind"], h["text"]) for h in heard] == [
        ("reasoning", "mulling it over"),
        ("text", "the answer"),
    ]
    assert all(h["agent"] == "echo" for h in heard)


async def test_nothing_is_narrated_to_a_caller_that_did_not_ask(narrating_agent):
    from sourcework import stream

    heard: list[str] = []

    async def on_progress(message: str) -> None:
        if stream.decode(message):
            heard.append(message)

    async with AgentPool(registry=narrating_agent) as pool:
        result = await pool.call("probe", "echo", {}, on_progress=on_progress)

    # No sink installed means the CLI backends never pay for streaming either.
    assert result["summary"] == "unwatched"
    assert heard == []


# ---------------------------------------------------------------------------
# The restart route: how a settings save makes a running agent re-read config
# ---------------------------------------------------------------------------


def test_a_restart_request_re_execs_the_process_with_its_original_argv(monkeypatch):
    """POST /api/restart must swap in a fresh process image, not merely answer.

    Agents read their configuration once at start-up, so the only honest way
    for a settings save to take effect is a re-exec of this very process with
    the argv it was launched with. If that regresses to a polite 200 and a
    shrug, every saved setting quietly stays dead until someone notices the
    mesh never restarted.
    """
    import os
    import sys
    import time

    from fastapi.testclient import TestClient
    from pydantic import BaseModel

    from sourcework.a2a_common import SkillExecutor, build_app, build_card, skill

    class Nothing(BaseModel):
        ok: bool = True

    class Quiet(SkillExecutor):
        def __init__(self) -> None:
            self.skills = {"noop": self.noop}
            super().__init__()

        async def noop(self, payload: dict) -> Nothing:
            return Nothing()

    app = build_app(
        build_card(name="Probe", description="d", url="http://x", skills=[skill("noop", "Noop", "d")]),
        Quiet(),
    )
    argv = sys.argv[:]
    execs: list[tuple[str, list[str]]] = []
    monkeypatch.setattr(os, "execv", lambda exe, args: execs.append((exe, args)))
    monkeypatch.setattr(time, "sleep", lambda seconds: None)

    with TestClient(app) as client:
        response = client.post("/api/restart")
        assert response.status_code == 200
        assert response.json() == {"status": "restarting"}

    # The relaunch thread fires as soon as sleep is a no-op; the response was
    # already sent, which is the whole point of the delay.
    deadline = time.monotonic() + 2
    while not execs and time.monotonic() < deadline:
        time.sleep(0.01)
    assert execs, "the relaunch thread never ran"
    executable, args = execs[0]
    assert executable == sys.executable
    assert args[1:] == argv, "re-exec must keep the original command line"
