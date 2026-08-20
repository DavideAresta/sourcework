"""A skill-dispatching :class:`AgentExecutor`.

The A2A SDK gives you one ``execute()`` entry point per agent. Our agents each
expose several skills, so this base class does the boring part:

* creates the Task if the framework has not already
* parses the inbound envelope into ``(skill, payload)``
* routes to a registered async handler
* emits progress status updates, then the result as a JSON artifact
* turns any exception into a ``failed`` task with a readable message

Handlers are plain async functions returning a Pydantic model. They never see
protobuf.
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import logging
import traceback
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

from a2a.helpers import new_task, new_text_message
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.tasks import TaskUpdater
from a2a.types import Role, TaskState
from pydantic import BaseModel

from sourcework import usage
from sourcework.a2a_common.parts import model_part, read_envelope, text_summary_part, usage_part
from sourcework.config import LLMOverrides, llm_overrides
from sourcework.stream import Narrator, decode, encode, stream_to

logger = logging.getLogger(__name__)

Handler = Callable[..., Awaitable[BaseModel]]


class SkillError(RuntimeError):
    """Raised by handlers for an expected, user-facing failure."""


KEEPALIVE_INTERVAL_S = 15.0
"""How often a working agent says "still here" while a handler runs.

The update carries no message, so nothing downstream shows it: the client
forwards only status updates that have one, and the browser never learns this
exists. What it carries is *bytes*, and bytes on the wire are the only thing
that distinguishes a model thinking for nine minutes from a peer that died.

Without it the mesh was silent for the length of every model call, because
narration - the other thing that puts bytes on this channel - is opt-in, is only
requested for runs somebody is watching, and is never emitted at all by the
litellm backend and the hosted providers built on it. Every idle-connection
clock between the agent and the reader then measured that silence and drew the
obvious wrong conclusion: httpx's read timeout, and every reverse proxy's
`proxy_read_timeout`.

Fifteen seconds is well inside the tightest of those defaults (60s) and costs one
small frame per agent per interval.
"""


@contextlib.asynccontextmanager
async def _keepalive(updater: TaskUpdater) -> AsyncIterator[None]:
    """Tick ``TASK_STATE_WORKING`` at the caller for the length of the block.

    Cancelled on the way out, before the handler's own terminal update: the
    updater refuses to publish anything once a terminal state is reached, and a
    beat racing `complete()` would raise into the beat task rather than into
    anything that matters. Failures are logged and end the beating, never the
    run - the same rule narration follows, for the same reason.
    """

    async def beat() -> None:
        while True:
            await asyncio.sleep(KEEPALIVE_INTERVAL_S)
            try:
                await updater.update_status(TaskState.TASK_STATE_WORKING)
            except Exception:  # noqa: BLE001 - a missed beat is never worth a run
                logger.debug("keepalive beat failed", exc_info=True)
                return

    task = asyncio.create_task(beat())
    try:
        yield
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


class Progress:
    """Passed to handlers so they can stream status back to the caller."""

    def __init__(self, updater: TaskUpdater) -> None:
        self._updater = updater

    async def __call__(self, message: str) -> None:
        # Narration goes on the wire but not into the log. The orchestrator
        # relays each specialist's chunks through here, and logging them writes
        # every model's full output - an entire PRD, per draft - into the mesh
        # log at INFO. `decode` is cheap: a prefix test that fails immediately
        # for the human-readable lines this is actually for.
        if decode(message) is None:
            logger.info("progress: %s", message)
        await self._updater.update_status(
            TaskState.TASK_STATE_WORKING,
            new_text_message(message, role=Role.ROLE_AGENT),
        )


class SkillExecutor(AgentExecutor):
    """Subclass and fill in ``skills``."""

    #: skill id -> async handler. Handlers accept ``(payload: dict)`` and may
    #: additionally accept a ``progress`` keyword.
    skills: dict[str, Handler]

    #: used when the caller does not name a skill
    default_skill: str | None = None

    def __init__(self) -> None:
        if not getattr(self, "skills", None):
            raise ValueError(f"{type(self).__name__} declares no skills")
        if self.default_skill is None:
            self.default_skill = next(iter(self.skills))
        self._running: dict[str, asyncio.Task[Any]] = {}
        """task id -> the asyncio task executing it, so :meth:`cancel` can stop
        the work rather than only relabel it."""

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        # The server runs this in its own task; holding a reference is what
        # turns a cancel request into a stopped model call instead of a status
        # change on a run that keeps going.
        running = asyncio.current_task()
        if running is not None:
            self._running[context.task_id] = running
        try:
            await self._execute(context, event_queue)
        finally:
            self._running.pop(context.task_id, None)

    async def _execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        if context.current_task is None:
            await event_queue.enqueue_event(
                new_task(
                    context.task_id,
                    context.context_id,
                    TaskState.TASK_STATE_SUBMITTED,
                    history=[context.message] if context.message else [],
                )
            )

        updater = TaskUpdater(event_queue, context.task_id, context.context_id)
        await updater.start_work()

        skill_id, payload = read_envelope(context.message)
        skill_id = skill_id or self.default_skill
        handler = self.skills.get(skill_id or "")

        if handler is None:
            await updater.reject(
                new_text_message(
                    f"Unknown skill {skill_id!r}. Available: {sorted(self.skills)}",
                    role=Role.ROLE_AGENT,
                )
            )
            return

        # `llm` is a cross-cutting envelope key carrying the caller's per-run
        # model choice. Read here so every LLM built while the handler runs
        # picks it up, without any of the seven specialist agents having to
        # know the feature exists.
        overrides = _read_overrides(payload)

        # Opt-in, because narration is not free: it makes claude-code use a
        # different output format and opencode pass --thinking, and it puts a
        # status update on the wire every ~0.6s for the length of the call.
        # Nobody watches the mesh's internal calls.
        narrator = (
            Narrator(_narration_publisher(updater, skill_id or ""))
            if _wants_narration(payload)
            else None
        )

        try:
            with llm_overrides(overrides), usage.track(skill_id or "") as ledger:
                # Outside the narrator, so it covers the calls narration cannot:
                # an unwatched mesh call, and any backend that does not stream.
                async with _keepalive(updater):
                    if narrator is None:
                        result = await self._invoke(handler, payload, Progress(updater))
                    else:
                        async with narrator:
                            with stream_to(narrator.sink):
                                result = await self._invoke(handler, payload, Progress(updater))
        except SkillError as exc:
            logger.warning("skill %s rejected input: %s", skill_id, exc)
            await updater.failed(new_text_message(str(exc), role=Role.ROLE_AGENT))
            return
        except Exception as exc:  # noqa: BLE001 - surface anything as a task failure
            logger.exception("skill %s crashed", skill_id)
            detail = f"{type(exc).__name__}: {exc}\n{traceback.format_exc(limit=4)}"
            await updater.failed(new_text_message(detail, role=Role.ROLE_AGENT))
            return

        # What this skill actually spent, sent back with the result. The mesh is
        # eight processes, so a caller totalling its own ledger would report
        # zero - every model call happens over here.
        parts = [model_part(result), text_summary_part(_summarise(result))]
        if ledger.calls:
            parts.append(usage_part(ledger.as_dict()))

        await updater.add_artifact(
            parts,
            name=f"{skill_id}.result",
            metadata={"schema": type(result).__name__, "skill": skill_id or ""},
            last_chunk=True,
        )
        await updater.complete()

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        """Stop the work, then say so.

        Marking the task cancelled without stopping anything was the whole bug:
        a run reported "cancelled" the instant the button was pressed and went
        on spending tokens for another ten minutes, because nothing here ever
        touched the coroutine doing the work.

        The status goes out first. Cancelling the task unwinds the handler,
        which tears down the event queue it would otherwise have used to report
        the outcome.
        """
        updater = TaskUpdater(event_queue, context.task_id, context.context_id)
        await updater.cancel(new_text_message("Cancelled by caller.", role=Role.ROLE_AGENT))

        running = self._running.get(context.task_id)
        if running is None:
            logger.info("cancel for task %s: nothing running here", context.task_id)
            return
        logger.info("cancelling task %s", context.task_id)
        running.cancel()

    # -- internals ---------------------------------------------------------

    @staticmethod
    async def _invoke(handler: Handler, payload: dict[str, Any], progress: Progress) -> BaseModel:
        params = inspect.signature(handler).parameters
        if "progress" in params:
            return await handler(payload, progress=progress)
        return await handler(payload)


STREAM_KEY = "stream"
"""Envelope key asking this agent to narrate itself. Like ``llm`` it is
cross-cutting: no skill schema declares it, and Pydantic drops it before any
handler sees it."""


def _wants_narration(payload: dict[str, Any]) -> bool:
    return bool(isinstance(payload, dict) and payload.get(STREAM_KEY))


def _narration_publisher(updater: TaskUpdater, skill_id: str):  # noqa: ANN202
    """Put one batch of narration on the wire as an ordinary status update.

    Narration rides the progress channel because that channel already reaches
    every caller; the marker in :func:`sourcework.stream.encode` is what keeps a
    reader from mistaking it for a human-readable line.
    """

    async def publish(kind: str, text: str) -> None:
        await updater.update_status(
            TaskState.TASK_STATE_WORKING,
            new_text_message(encode(kind, text, agent=skill_id), role=Role.ROLE_AGENT),
        )

    return publish


def _read_overrides(payload: dict[str, Any]) -> LLMOverrides | None:
    """Parse the ``llm`` envelope key. Junk is ignored, never fatal.

    Read, not popped. The orchestrator's own request schema declares ``llm``
    and re-reads it to build the pool that carries the setting to the other
    seven agents; removing it here left that field empty, and a run that asked
    for claude-code quietly came back built by whatever the mesh booted with.
    Handlers that do not declare it ignore it - Pydantic drops unknown keys.

    A caller that sends a malformed override should get their PRD built with
    the mesh's own settings, not a rejected task.
    """
    raw = payload.get("llm") if isinstance(payload, dict) else None
    if not raw:
        return None
    try:
        return LLMOverrides.model_validate(raw)
    except Exception as exc:  # noqa: BLE001
        logger.warning("ignoring malformed llm overrides %r: %s", raw, exc)
        return None


def _summarise(result: BaseModel) -> str:
    """Short human-readable line accompanying the machine-readable artifact."""
    for attr in ("summary", "verdict", "title"):
        value = getattr(result, attr, None)
        if isinstance(value, str) and value:
            return value
    return f"{type(result).__name__} produced."
