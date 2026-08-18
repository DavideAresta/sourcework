"""Driving a run, and letting the browser watch it happen.

The UI is an ordinary A2A client: it calls ``generate_prd`` on the orchestrator
exactly as the CLI does. The only thing it adds is patience - a run takes
minutes, so it is started in the background, its progress is fanned out to
however many browser tabs are watching, and everything is persisted as it goes
so a reload (or a restart) does not lose the thread.

Progress arrives because :meth:`AgentPool.call` forwards the remote agent's
status messages. Events are appended to the run record *and* pushed to live
subscribers; a tab that connects late replays the stored ones first, so there
is no gap between "what happened" and "what is happening".
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator, Callable
from typing import Any, Protocol, runtime_checkable

from sourcework import stream
from sourcework.a2a_common import AgentPool, RemoteAgentError
from sourcework.config import settings
from sourcework.models import PRDRequest
from sourcework.ui.store import Run, Store, new_run_id, now_iso

logger = logging.getLogger(__name__)

#: How many events a late subscriber can be behind before we stop queueing for
#: it. A wedged browser tab must not grow the server's memory without bound.
SUBSCRIBER_BACKLOG = 256


@runtime_checkable
class RunExecutor(Protocol):
    """What the UI needs of a place that *executes* runs.

    The in-process :class:`RunManager` is the local implementation: runs live in
    this process, subscribers are an in-memory dict. A hosted deployment keeps
    the same routes but none of the machinery — the API enqueues, workers
    execute, and events come back over Redis — so the six calls below are the
    whole interface between the HTTP layer and however a run is driven. Taken
    from what the routes actually call, as the :class:`Store` protocol is.
    """

    async def start(self, request: PRDRequest, *, run_id: str | None = None) -> Run: ...

    async def resume(self, run_id: str) -> Run | None: ...

    async def cancel(self, run_id: str) -> bool: ...

    def is_active(self, run_id: str) -> bool: ...

    def subscribe(self, run_id: str) -> AsyncIterator[dict[str, Any]]: ...

    async def shutdown(self) -> None: ...


class RunManager:
    """Starts runs, keeps the live ones, fans their events out."""

    def __init__(
        self,
        store: Store,
        *,
        max_concurrent: int | None = None,
        run_id_factory: Callable[[], str] = new_run_id,
    ) -> None:
        self.store = store
        self._run_id_factory = run_id_factory
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._subscribers: dict[str, set[asyncio.Queue[dict[str, Any] | None]]] = {}
        # Runs wait their turn rather than all starting at once. A run is not
        # CPU work the OS can fairly interleave - it is a queue of calls to one
        # model server, and on a local one that means a single GPU holding a
        # single model. Two concurrent runs wanting different models make it
        # unload and reload between every call, so both finish later than if
        # they had queued. Each in-flight run also holds the full text of every
        # source it ingested.
        limit = max_concurrent if max_concurrent is not None else settings().max_concurrent_runs
        self._slots = asyncio.Semaphore(max(1, limit))

    # -- lifecycle ---------------------------------------------------------

    async def start(self, request: PRDRequest, *, run_id: str | None = None) -> Run:
        run = Run(
            id=run_id or self._run_id_factory(),
            title=request.title,
            status="queued",
            created_at=now_iso(),
            request=request.model_dump(mode="json"),
        )
        await self.store.save(run)
        # Stamped here rather than by every caller, so that anything which
        # starts a run - a new one, a refinement, a future caller - writes its
        # stages down without having to remember to.
        request = request.model_copy(update={"run_id": run.id})
        self._tasks[run.id] = asyncio.create_task(self._execute(run.id, request))
        return run

    async def resume(self, run_id: str) -> Run | None:
        """Run ``run_id`` again, reusing the stages it had already finished.

        The same run, continued - not a new one. A refinement is a new version
        with its own id because it is a different document; this is the *same*
        document, interrupted, and giving it a second id would put two rows in
        the history for one piece of work.

        Its events are kept rather than cleared, so the record shows the failure
        and the recovery instead of quietly replacing one with the other.
        """
        run = await self.store.get(run_id)
        if run is None or self.is_active(run_id):
            return None

        request = PRDRequest.model_validate(run.request).model_copy(
            update={"run_id": run_id, "resume": True}
        )
        run.status = "queued"
        run.error = None
        run.finished_at = None
        await self.store.save(run)
        self._tasks[run_id] = asyncio.create_task(self._execute(run_id, request))
        return run

    async def cancel(self, run_id: str) -> bool:
        task = self._tasks.get(run_id)
        if task is None or task.done():
            return False
        task.cancel()
        return True

    async def shutdown(self) -> None:
        for task in list(self._tasks.values()):
            task.cancel()
        for task in list(self._tasks.values()):
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        for run_id in list(self._subscribers):
            await self._publish(run_id, None)

    def is_active(self, run_id: str) -> bool:
        task = self._tasks.get(run_id)
        return task is not None and not task.done()

    # -- execution ---------------------------------------------------------

    async def _execute(self, run_id: str, request: PRDRequest) -> None:
        # Acquired before the row is read, so a queued run stays `queued` in the
        # store and the UI can say so honestly instead of showing it as running
        # while it waits.
        async with self._slots:
            await self._run_now(run_id, request)

    async def _run_now(self, run_id: str, request: PRDRequest) -> None:
        run = await self.store.get(run_id)
        if run is None:  # deleted between start and schedule
            return

        run.status = "running"
        await self._emit(run, "status", "Run started")

        try:
            # narrate=True asks the mesh to stream the model's working back.
            # A run started from the UI is one somebody is looking at, which is
            # the whole condition the flag exists to express.
            async with AgentPool(llm=request.llm, narrate=True) as pool:

                async def progress(message: str) -> None:
                    narration = stream.decode(message)
                    if narration is not None:
                        await self._narrate(run.id, narration)
                        return
                    await self._emit(run, "progress", message)

                result = await pool.call(
                    "orchestrator", "generate_prd", request, on_progress=progress
                )
                run.usage = pool.usage.as_dict()

            run.result = result
            # The orchestrator's own total is the authoritative one - it saw
            # every agent. The pool here only saw the orchestrator.
            stats_usage = (result.get("stats") or {}).get("usage")
            if stats_usage:
                run.usage = stats_usage
            run.status = "ok"
            await self._emit(run, "done", "Finished")

        except asyncio.CancelledError:
            run.status = "cancelled"
            run.error = "Cancelled."
            await self._emit(run, "error", "Cancelled")
            raise
        except RemoteAgentError as exc:
            run.status = "failed"
            run.error = exc.detail
            await self._emit(run, "error", exc.detail)
        except Exception as exc:  # noqa: BLE001 - a failed run is data, not a crash
            logger.exception("run %s failed", run_id)
            run.status = "failed"
            run.error = f"{type(exc).__name__}: {exc}"
            await self._emit(run, "error", run.error)
        finally:
            run.finished_at = now_iso()
            await self.store.save(run)
            await self._publish(run_id, None)  # close open streams
            self._tasks.pop(run_id, None)

    async def _emit(self, run: Run, kind: str, message: str) -> None:
        # `seq` is what makes replay-then-stream safe: a subscriber that joins
        # mid-run reads the stored events, notes the highest seq it saw, and
        # discards anything the live queue hands it at or below that. Without
        # it, the events produced between "read the store" and "start reading
        # the queue" are either shown twice or not at all.
        event = {
            "seq": len(run.events),
            "t": now_iso(),
            "kind": kind,
            "message": message,
            "status": run.status,
        }
        run.events.append(event)
        await self.store.save(run)
        await self._publish(run.id, event)

    async def _narrate(self, run_id: str, narration: dict[str, str]) -> None:
        """Push the model's working to whoever is watching. Nothing is stored.

        Deliberately not an :meth:`_emit`: narration is megabytes per run, it
        is not evidence, and every stored event rewrites the whole run record
        to SQLite - which at token rate would mean thousands of writes of a
        steadily growing JSON blob. ``seq`` is None to mark it as ephemeral,
        which is also what keeps it out of the replay bookkeeping in
        :meth:`subscribe`.
        """
        await self._publish(
            run_id,
            {
                "seq": None,
                "t": now_iso(),
                "kind": "stream",
                "agent": narration.get("agent") or "",
                "stream_kind": narration.get("kind") or "text",
                "message": narration.get("text") or "",
            },
        )

    # -- fan-out -----------------------------------------------------------

    async def _publish(self, run_id: str, event: dict[str, Any] | None) -> None:
        for queue in list(self._subscribers.get(run_id, ())):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                # A tab that cannot keep up gets dropped rather than allowed to
                # apply backpressure to the run itself.
                logger.warning("dropping a slow subscriber on run %s", run_id)

    async def subscribe(self, run_id: str) -> AsyncIterator[dict[str, Any]]:
        """Replay this run's events, then stream new ones until it finishes."""
        run = await self.store.get(run_id)
        if run is None:
            return

        # Register BEFORE reading the stored events, so nothing emitted during
        # the read is lost. Duplicates are then filtered by seq.
        queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue(SUBSCRIBER_BACKLOG)
        self._subscribers.setdefault(run_id, set()).add(queue)
        try:
            run = await self.store.get(run_id) or run
            highest = -1
            for event in run.events:
                highest = max(highest, int(event.get("seq", -1)))
                yield event
            if run.done and not self.is_active(run_id):
                return

            while True:
                event = await queue.get()
                if event is None:
                    return
                seq = event.get("seq")
                if seq is None:
                    # Ephemeral (narration): never stored, so never replayed,
                    # and it must not disturb the seq the replay filter tracks.
                    yield event
                    continue
                if int(seq) <= highest:
                    continue  # already replayed
                highest = int(seq)
                yield event
        finally:
            subs = self._subscribers.get(run_id)
            if subs is not None:
                subs.discard(queue)
                if not subs:
                    self._subscribers.pop(run_id, None)
