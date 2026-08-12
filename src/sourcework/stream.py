"""Live model output: the model's working, from the backend to the browser.

A run takes minutes, and until it finishes there is nothing to look at but a
spinner. The coding CLIs all narrate themselves as they go - reasoning, then
the answer forming - and this module is the channel that carries that narration
out to whoever is watching.

Three problems make it more than "pass a callback down":

* **Nothing in the middle wants to know about it.** An agent calls
  ``llm.structured(...)``; it has no argument for a stream sink and should not
  grow one. So the sink is a :class:`~contextvars.ContextVar`, installed by
  whoever is watching and read by :mod:`sourcework.llm` when it builds the
  request. Nothing between the two changes.

* **The rate is wrong by three orders of magnitude.** Chunks arrive per token,
  synchronously, from the subprocess reader. Each hop outward is an async
  status update crossing a JSON-RPC connection. :class:`Narrator` sits between
  them: the sink appends to a buffer, a timer task publishes what accumulated.

* **It must never be able to break a run.** The sink is called from the code
  draining the CLI's stdout pipe. An exception there stops the drain and
  deadlocks the very call it was narrating, so every sink here swallows its own
  failures - a lost line of narration is not worth a failed run.

Narration is deliberately *ephemeral*: it is published, and never stored. It is
worth several megabytes per run, it is not evidence, and nothing downstream
cites it.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import Awaitable, Callable, Iterator
from contextvars import ContextVar

from sourcework.backends.base import ON_CHUNK, StreamChunk

logger = logging.getLogger(__name__)

__all__ = ["Narrator", "current_sink", "encode", "decode", "stream_to", "MARKER"]

_SINK: ContextVar[ON_CHUNK | None] = ContextVar("sourcework_stream_sink", default=None)


def current_sink() -> ON_CHUNK | None:
    """The sink installed for this task, if anyone is watching."""
    return _SINK.get()


@contextlib.contextmanager
def stream_to(sink: ON_CHUNK | None) -> Iterator[None]:
    """Install ``sink`` for the duration of the block."""
    token = _SINK.set(sink)
    try:
        yield
    finally:
        _SINK.reset(token)


# ---------------------------------------------------------------------------
# Wire format
#
# Narration rides the same A2A status-update channel as progress messages,
# because that channel already reaches every caller in the mesh - the
# orchestrator relays it to the UI, the UI relays it to the browser. A marker
# prefix is what keeps the two apart: a reader that does not know about
# narration sees an unrecognised progress line, not a corrupted one.
# ---------------------------------------------------------------------------

MARKER = "\x1fsourcework-stream\x1f"
"""Deliberately not printable. A progress message is human text and could
plausibly start with any prefix a person might type; this one cannot."""


def encode(kind: str, text: str, *, agent: str | None = None) -> str:
    return MARKER + json.dumps({"kind": kind, "text": text, "agent": agent})


def decode(message: str) -> dict[str, str] | None:
    """The narration carried by ``message``, or None if it is ordinary progress."""
    if not message.startswith(MARKER):
        return None
    try:
        node = json.loads(message[len(MARKER):])
    except ValueError:
        return None
    return node if isinstance(node, dict) else None


# ---------------------------------------------------------------------------
# Rate control
# ---------------------------------------------------------------------------

PUBLISH_INTERVAL_S = 0.6
"""Fast enough to read as live, slow enough that a 20k-token answer is ~40
messages rather than 20,000."""

DEFAULT_BUDGET = 400_000
"""Characters of narration per watched call. A safety ceiling, not a target: a
runaway model that never stops talking must not be able to grow a browser tab
without bound. Reaching it stops the narration, not the run."""


class Narrator:
    """Buffers :class:`StreamChunk`\\ s and publishes them on a timer.

    Used as an async context manager around the work being narrated::

        async with Narrator(publish) as narrator, stream_to(narrator.sink):
            ...

    Consecutive chunks of the same kind are concatenated, so a burst of
    single-token deltas becomes one message. Exiting flushes what is left.
    """

    def __init__(
        self,
        publish: Callable[[str, str], Awaitable[None]],
        *,
        interval_s: float = PUBLISH_INTERVAL_S,
        budget: int = DEFAULT_BUDGET,
    ) -> None:
        self._publish = publish
        self._interval = interval_s
        self._remaining = budget
        self._pending: list[list[str]] = []  # [kind, text] pairs, mutated in place
        self._task: asyncio.Task | None = None

    # -- the sink ----------------------------------------------------------

    def sink(self, chunk: StreamChunk) -> None:
        """Synchronous, cheap, and unable to raise. See the module docstring."""
        try:
            if self._remaining <= 0:
                return
            text = chunk.text[: self._remaining]
            self._remaining -= len(text)
            if not text:
                # A text-less chunk still says "the model is thinking", which is
                # the difference between a live pause and a hung one.
                if not chunk.kind:
                    return
                text = ""
            if self._pending and self._pending[-1][0] == chunk.kind:
                # `step` is a status, not prose: the newest one replaces the
                # last rather than being glued onto it. Concatenating would
                # produce "thinking… 50 tokensthinking… 150 tokens".
                if chunk.kind == "step":
                    self._pending[-1][1] = text
                else:
                    self._pending[-1][1] += text
            else:
                self._pending.append([chunk.kind, text])
        except Exception:  # noqa: BLE001 - never break the pipe reader
            logger.debug("narration sink failed", exc_info=True)

    # -- lifecycle ---------------------------------------------------------

    async def __aenter__(self) -> Narrator:
        self._task = asyncio.create_task(self._loop())
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        await self._flush()

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(self._interval)
            await self._flush()

    async def _flush(self) -> None:
        if not self._pending:
            return
        batch, self._pending = self._pending, []
        for kind, text in batch:
            try:
                await self._publish(kind, text)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - narration is never worth a run
                logger.debug("could not publish narration", exc_info=True)
                return
