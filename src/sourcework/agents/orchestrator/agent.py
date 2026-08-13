"""Orchestrator agent (port 8000).

The only user-facing agent, and itself an A2A server - so a calendar bot, a
Slack app, or another company's agent can drive it with the same protocol the
internal agents use. It is simultaneously an A2A *client* of the other six.

It holds no domain logic beyond routing and sequencing; see
:mod:`sourcework.agents.orchestrator.pipeline`.
"""

from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel, Field

from sourcework import stream
from sourcework.a2a_common import (
    AgentPool,
    Progress,
    SkillError,
    SkillExecutor,
    build_card,
    public_url,
    skill,
)
from sourcework.agents.orchestrator import pipeline
from sourcework.models import PRDRequest, PRDResult

logger = logging.getLogger(__name__)

PORT = 8000


class MeshStatus(BaseModel):
    agents: dict[str, list[str]] = Field(default_factory=dict)
    unreachable: list[str] = Field(default_factory=list)
    in_flight: list[str] = Field(default_factory=list)
    """Run ids executing here right now.

    Reported so a client can find out *before* it asks for something that would
    collide. The refusal in :meth:`generate_prd` is the race-free backstop; this
    is what lets the answer arrive without a wasted round trip through a
    request the caller could have been told not to make."""
    summary: str = ""


class OrchestratorExecutor(SkillExecutor):
    def __init__(self) -> None:
        self.skills = {
            "generate_prd": self.generate_prd,
            "mesh_status": self.mesh_status,
        }
        self.default_skill = "generate_prd"
        self._in_flight: set[str] = set()
        """Run ids currently executing here.

        Resuming a run that is still going would put two pipelines on the same
        checkpoint file and the same work, and the case is not hypothetical: a
        client that disconnects - Ctrl-C in the CLI, a closed browser, a
        restarted UI - does not stop the run, which carries on here to
        completion. The client then has every reason to think it is dead.

        Held in memory on purpose. A lock file would have to answer "is the
        process that wrote this still alive", and would strand a run behind a
        stale lock after a crash - exactly when resuming is most wanted. A set
        that dies with the process cannot go stale.
        """
        super().__init__()

    async def generate_prd(self, payload: dict[str, Any], progress: Progress) -> PRDResult:
        try:
            request = PRDRequest.model_validate(payload)
        except Exception as exc:  # noqa: BLE001
            raise SkillError(f"Malformed request: {exc}") from exc
        if not request.inputs and not request.confluence_queries:
            raise SkillError(
                "Give me something to work from: `inputs` (files/URIs/inline text) "
                "and/or `confluence_queries` (CQL)."
            )

        # The pool carries the run's model settings to every agent it calls, so
        # the whole mesh runs on the caller's chosen backend without any of the
        # seven specialists knowing this feature exists.
        # Narration propagates the same way, but is not carried on the request:
        # the executor installs a sink when the caller asked to watch, so its
        # presence *is* the answer to "is anyone looking at this run".
        if request.run_id and request.run_id in self._in_flight:
            raise SkillError(
                f"Run {request.run_id} is already in flight here. Disconnecting a client "
                f"does not stop a run - it carries on and will finish on its own. Wait for "
                f"it, or cancel it, before resuming."
            )
        if request.run_id:
            self._in_flight.add(request.run_id)
        try:
            async with AgentPool(
                llm=request.llm, narrate=stream.current_sink() is not None
            ) as pool:
                try:
                    return await pipeline.run(request, pool, notify=progress)
                except RuntimeError as exc:
                    raise SkillError(str(exc)) from exc
        finally:
            self._in_flight.discard(request.run_id or "")

    async def mesh_status(self, payload: dict[str, Any]) -> MeshStatus:
        async with AgentPool() as pool:
            found = await pool.discover()
            unreachable = [a for a in pool.registry if a not in found]
        return MeshStatus(
            agents=found,
            unreachable=unreachable,
            in_flight=sorted(self._in_flight),
            summary=f"{len(found)}/{len(found) + len(unreachable)} agents reachable.",
        )


def card():  # noqa: ANN201
    return build_card(
        name="PRD Orchestrator",
        description=(
            "Turns documents, meeting transcriptions, images and Confluence pages into "
            "a traceable Product Requirements Document. Fans work out to specialist "
            "A2A agents, then analyses, drafts, reviews and optionally publishes."
        ),
        url=public_url(PORT),
        skills=[
            skill(
                "generate_prd",
                "Generate a PRD",
                "Ingest mixed source material, normalise it into requirements, draft a "
                "PRD, review it, and optionally publish it to Confluence. Returns the "
                "PRD as structured JSON plus Markdown and Confluence storage format.",
                tags=["prd", "orchestration", "requirements"],
                examples=[
                    '{"skill":"generate_prd","payload":{"title":"Checkout retries",'
                    '"inputs":[{"uri":"file:///kickoff.vtt"},{"uri":"file:///rfp.pdf"}],'
                    '"confluence_queries":["space=PRD AND text ~ \\"checkout\\""],'
                    '"publish":true}}'
                ],
            ),
            skill(
                "mesh_status",
                "Mesh status",
                "Report which specialist agents are reachable and what skills they "
                "advertise. Useful as a readiness probe before a long run.",
                tags=["ops", "discovery"],
            ),
        ],
    )


def executor() -> SkillExecutor:
    return OrchestratorExecutor()
