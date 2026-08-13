"""Client side of the mesh.

The orchestrator uses :class:`AgentPool` to talk to its peers. The pool:

* resolves each peer's agent card once and caches it
* validates that a skill actually exists on the card before calling it, so a
  typo fails loudly at the caller instead of producing an empty PRD section
* consumes the A2A stream and returns the final JSON artifact as a dict
* injects the shared-secret header when intra-mesh auth is enforced
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

import httpx
from a2a.client import ClientCallContext, ClientConfig, create_client
from a2a.client.client import Client
from a2a.helpers import get_data_parts, get_message_text, new_message
from a2a.types import AgentCard, CancelTaskRequest, Role, SendMessageRequest, TaskState
from pydantic import BaseModel

from sourcework.a2a_common.parts import USAGE_KEY, envelope
from sourcework.config import LLMOverrides, settings
from sourcework.usage import UsageLedger

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


class RemoteAgentError(RuntimeError):
    def __init__(self, agent: str, skill: str, detail: str) -> None:
        super().__init__(f"{agent}.{skill} failed: {detail}")
        self.agent = agent
        self.skill = skill
        self.detail = detail


class AgentPool:
    """Lazily-connected pool of A2A peers keyed by logical name."""

    def __init__(
        self,
        registry: dict[str, str] | None = None,
        *,
        llm: LLMOverrides | None = None,
        narrate: bool = False,
    ) -> None:
        self.registry = registry or settings().peers.as_map()
        self.usage = UsageLedger()
        """Everything every agent reported spending on this pool's calls. The
        pool is the only object that spans a whole run, which makes it the
        natural place for the total."""
        self.llm = llm
        """Per-run model settings, injected into every call this pool makes.

        One object covers the whole run: the alternative is threading it
        through every ``pool.call`` site in the pipeline, where the next call
        added would quietly not get it."""
        self.narrate = narrate
        """Ask every peer to stream the model's working back as it happens.

        Off by default: it changes how the CLIs are invoked and puts a status
        update on the wire every ~0.6s, which is worth it for a run someone is
        watching and pure overhead for one nobody is."""
        self._clients: dict[str, Client] = {}
        self._cards: dict[str, AgentCard] = {}
        self._httpx: httpx.AsyncClient | None = None
        self._lock = asyncio.Lock()
        self._cancellations: list[asyncio.Task[Any]] = []
        """Cancel requests still on the wire. Held so :meth:`close` can let them
        land before the transport underneath them is torn down."""

    async def __aenter__(self) -> AgentPool:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    async def close(self) -> None:
        # Before the transport goes: a cancel request racing a closing httpx
        # client is a cancel that never arrives, which is the whole failure this
        # pool now exists to prevent.
        if self._cancellations:
            # CancelledError included: this runs while the caller is unwinding
            # from its own cancellation, and cleanup must not raise something
            # new over the top of it.
            with contextlib.suppress(Exception, asyncio.CancelledError):
                await asyncio.wait(self._cancellations, timeout=10)
            self._cancellations.clear()
        for client in self._clients.values():
            await client.close()
        self._clients.clear()
        if self._httpx is not None:
            await self._httpx.aclose()
            self._httpx = None

    # -- discovery ---------------------------------------------------------

    def _http(self) -> httpx.AsyncClient:
        if self._httpx is None:
            sec = settings().security
            headers = {sec.header: sec.api_key} if sec.enforce else {}
            self._httpx = httpx.AsyncClient(timeout=600.0, headers=headers)
        return self._httpx

    async def client(self, agent: str) -> Client:
        async with self._lock:
            if agent in self._clients:
                return self._clients[agent]
            url = self.registry.get(agent)
            if not url:
                raise KeyError(f"No URL registered for agent {agent!r}")
            cfg = ClientConfig(streaming=True, httpx_client=self._http())
            client = await create_client(url, cfg)
            self._clients[agent] = client
            return client

    async def card(self, agent: str) -> AgentCard:
        if agent not in self._cards:
            from a2a.client import A2ACardResolver

            url = self.registry[agent]
            resolver = A2ACardResolver(self._http(), url)
            self._cards[agent] = await resolver.get_agent_card()
        return self._cards[agent]

    async def discover(self) -> dict[str, list[str]]:
        """Return ``{agent: [skill ids]}`` for everything reachable."""
        found: dict[str, list[str]] = {}
        for name in self.registry:
            try:
                card = await self.card(name)
                found[name] = [s.id for s in card.skills]
            except Exception as exc:  # noqa: BLE001
                logger.warning("agent %s unreachable: %s", name, exc)
        return found

    # -- cancellation ------------------------------------------------------

    async def _cancel_remote(self, agent: str, task_id: str) -> None:
        """Ask ``agent`` to stop the task it is running for us.

        Sent from inside an ``except CancelledError``, which is a hostile place
        to await from: the enclosing task is unwinding and anything tied to it
        can be torn down mid-flight. So the request goes out as its own task,
        shielded, and is only *waited* on briefly - if the wait is cut short the
        request is still in the air, and :meth:`close` gives it until the
        transport shuts.

        Every failure is swallowed. The caller is already being cancelled; the
        one thing that must not happen is this raising something new over the
        top of the cancellation.
        """
        client = self._clients.get(agent)
        if client is None:  # pragma: no cover - we only get here after a call
            return
        sending = asyncio.ensure_future(client.cancel_task(CancelTaskRequest(id=task_id)))
        self._cancellations.append(sending)
        logger.info("cancelling %s task %s", agent, task_id)
        with contextlib.suppress(Exception, asyncio.CancelledError):
            await asyncio.wait_for(asyncio.shield(sending), timeout=5)

    # -- invocation --------------------------------------------------------

    async def call(
        self,
        agent: str,
        skill: str,
        payload: BaseModel | dict[str, Any],
        *,
        strict: bool = True,
        on_progress: Callable[[str], Awaitable[None]] | None = None,
    ) -> dict[str, Any]:
        """Invoke ``skill`` on ``agent`` and return the result artifact.

        ``on_progress`` receives each status message the remote agent emits -
        including narration, which arrives on the same channel wearing the
        marker from :mod:`sourcework.stream`.
        Without it those updates are logged at debug and dropped, which is fine
        for the pipeline's own internal calls and useless for anything holding
        a user's attention while a fourteen-minute run goes by.
        """
        if strict:
            card = await self.card(agent)
            available = {s.id for s in card.skills}
            if skill not in available:
                raise RemoteAgentError(
                    agent, skill, f"skill not advertised on card; has {sorted(available)}"
                )

        client = await self.client(agent)
        message = new_message([envelope(skill, self._with_llm(payload))], role=Role.ROLE_USER)
        request = SendMessageRequest(message=message)

        artifacts: list[Any] = []
        failure: str | None = None
        ctx = ClientCallContext()
        remote_task: str | None = None

        try:
            async for event in client.send_message(request, context=ctx):
                remote_task = _task_id(event) or remote_task
                which = event.WhichOneof("payload")
                if which == "artifact_update":
                    artifacts.extend(get_data_parts(list(event.artifact_update.artifact.parts)))
                elif which == "status_update":
                    state = event.status_update.status.state
                    if state in _FAILED_STATES:
                        failure = get_message_text(event.status_update.status.message) or str(state)
                    elif event.status_update.status.HasField("message"):
                        text = get_message_text(event.status_update.status.message)
                        logger.debug("[%s] %s", agent, text)
                        if on_progress is not None and text:
                            await on_progress(text)
                elif which == "task":
                    for artifact in event.task.artifacts:
                        artifacts.extend(get_data_parts(list(artifact.parts)))
                    if event.task.status.state in _FAILED_STATES:
                        failure = (
                            get_message_text(event.task.status.message)
                            if event.task.status.HasField("message")
                            else str(event.task.status.state)
                        )
                elif which == "message":
                    logger.debug("[%s] message: %s", agent, get_message_text(event.message))
        except asyncio.CancelledError:
            # Cancelling this await stops us *listening*. On its own it does not
            # stop the agent, which carries on to completion and bills for every
            # token of it - measured at six minutes past a Ctrl-C.
            #
            # Doing it here makes one cancel travel the whole mesh: the pipeline
            # is nested calls all the way down, and each agent is itself a
            # caller whose await is now being cancelled.
            if remote_task is not None:
                await self._cancel_remote(agent, remote_task)
            raise

        if failure:
            raise RemoteAgentError(agent, skill, failure)

        result: dict[str, Any] | None = None
        for item in artifacts:
            if not isinstance(item, dict):
                continue
            if USAGE_KEY in item:
                self.usage.merge(item[USAGE_KEY] or {})
                continue
            if result is None:
                result = item
        if result is not None:
            return result
        raise RemoteAgentError(agent, skill, "completed without a JSON artifact")

    async def call_as(
        self,
        agent: str,
        skill: str,
        payload: BaseModel | dict[str, Any],
        schema: type[T],
    ) -> T:
        data = await self.call(agent, skill, payload)
        return schema.model_validate(data)

    def _with_llm(self, payload: BaseModel | dict[str, Any]) -> dict[str, Any]:
        """Attach this pool's per-run model settings to an outbound payload.

        ``llm`` is an envelope key that no skill schema declares - the receiving
        executor lifts it out before the handler sees it. An explicit ``llm``
        already in the payload wins, so a caller can still steer one hop
        differently.
        """
        data = payload.model_dump(mode="json") if isinstance(payload, BaseModel) else dict(payload)
        if self.llm is not None:
            data.setdefault("llm", self.llm.model_dump(mode="json", exclude_none=True))
        if self.narrate:
            data.setdefault("stream", True)
        return data


_FAILED_STATES = {
    TaskState.TASK_STATE_FAILED,
    TaskState.TASK_STATE_REJECTED,
    TaskState.TASK_STATE_CANCELED,
}


def pretty(payload: BaseModel | dict[str, Any]) -> str:
    if isinstance(payload, BaseModel):
        return payload.model_dump_json(indent=2)
    return json.dumps(payload, indent=2, default=str)


def _task_id(event: Any) -> str | None:  # noqa: ANN401 - the a2a event union
    """The remote task id an event belongs to, if it carries one.

    Captured on the way past rather than requested up front: the id is minted by
    the agent, so the first event that mentions it is the earliest this side can
    know what to cancel.
    """
    which = event.WhichOneof("payload")
    if which == "task":
        return event.task.id or None
    if which in ("status_update", "artifact_update"):
        return getattr(event, which).task_id or None
    return None
