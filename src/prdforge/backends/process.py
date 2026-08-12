"""Running a coding CLI as a subprocess, safely.

Everything here exists because a CLI backend is not just "an HTTP call with a
different transport". Four things bite, and they bite all three CLIs the same
way, so they are solved once:

* **argv has a size limit.** Linux caps a *single* argv entry at
  ``MAX_ARG_STRLEN`` (128 KB). Past it ``execve`` fails with ``E2BIG`` and the
  program never starts. A PRD prompt carrying a full transcript plus a
  requirements set gets there easily. Over a threshold the prompt travels on
  stdin instead - all three CLIs accept that.

* **pipes fill up.** A model response larger than the OS pipe buffer will
  deadlock a process that is waited on before its output is drained. Both
  streams are read concurrently, from the start.

* **cwd is not neutral.** These are *coding agents*. Pointed at a repository
  they read its instruction files, honour its permission allowlists, and will
  happily write into it. Generation calls run in a scratch directory that
  contains nothing.

* **a timeout must still yield evidence.** Output is accumulated as it arrives,
  so a killed process still reports what it had produced - which is usually the
  only clue about why it hung.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import shutil
import tempfile
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from prdforge.backends.base import ImageInput

logger = logging.getLogger(__name__)

MAX_ARGV_PROMPT_BYTES = 96_000
"""Below the kernel's 128 KB per-argument ceiling with room for the rest of the
command line. A threshold rather than always-stdin: every prompt that fits keeps
the exact invocation that is known to work."""


def exceeds_argv_limit(text: str | None) -> bool:
    """Whether ``text`` must travel on stdin rather than as an argument.

    Bytes, not characters - the kernel counts bytes, so 100k CJK characters are
    ~300 KB to ``execve`` while a length check would wave them through.
    """
    return bool(text) and len(text.encode("utf-8")) > MAX_ARGV_PROMPT_BYTES


def neutral_cwd() -> Path:
    """A scratch directory with nothing in it, used as every CLI's cwd.

    A coding CLI inherits meaning from its working directory: ``AGENTS.md`` and
    ``CLAUDE.md`` get loaded, ``.claude/settings.local.json`` grants tools, and
    relative paths in a generated answer resolve somewhere real. Running a PRD
    generation call in the project checkout means the model is silently reading
    - and potentially writing - this repository.
    """
    path = Path(tempfile.gettempdir()) / "prdforge-llm-scratch"
    path.mkdir(parents=True, exist_ok=True)
    return path


def which(program: str) -> str | None:
    return shutil.which(program)


@dataclass(slots=True)
class ProcessResult:
    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool = False

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out


async def run(
    argv: Sequence[str],
    *,
    cwd: str | Path | None = None,
    env: Mapping[str, str] | None = None,
    stdin_text: str | None = None,
    timeout_s: float = 300.0,
    on_line: Callable[[str], None] | None = None,
) -> ProcessResult:
    """Run ``argv`` to completion, or kill it at ``timeout_s``.

    ``env`` is merged over the parent environment rather than replacing it: the
    CLIs need ``HOME``, ``PATH`` and their own credential paths to work at all.

    ``on_line`` receives each complete stdout line *as it arrives*. Without it
    the whole point of a CLI that streams NDJSON is lost - the events exist,
    they are just invisible until the process exits nine minutes later. The
    full output is still buffered and returned, so parsing is unaffected.
    """
    merged = {**os.environ, **(env or {})}
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.PIPE if stdin_text is not None else asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(cwd) if cwd else None,
            env=merged,
        )
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"{argv[0]!r} is not on PATH") from exc

    out_buf = bytearray()
    err_buf = bytearray()

    async def drain(stream: asyncio.StreamReader | None, into: bytearray) -> None:
        if stream is None:
            return
        while chunk := await stream.read(65_536):
            into.extend(chunk)

    async def drain_lines(stream: asyncio.StreamReader | None, into: bytearray) -> None:
        """Same as :func:`drain`, but hands each finished line to ``on_line``.

        Reads blocks and splits on newlines rather than calling ``readline()``.
        That is not a style choice: ``StreamReader.readline`` raises above its
        64 KB limit and, on the way out, **clears its buffer** - so an event
        larger than that is not merely un-delivered, it is *destroyed*, and the
        captured stdout comes back short. These CLIs emit one JSON object per
        line and a large answer is one line, so that is the normal case, not an
        edge case. Measured: a 200 KB event survived as 69 KB.

        Reading by block costs nothing in latency: ``read(n)`` returns as soon
        as any bytes arrive, not when ``n`` of them do.
        """
        if stream is None:
            return

        def emit(raw: bytes) -> None:
            try:
                on_line(raw.decode("utf-8", errors="replace"))
            except Exception:  # noqa: BLE001 - a bad sink must not kill the run
                logger.debug("stream sink raised; dropping the line", exc_info=True)

        pending = bytearray()
        while chunk := await stream.read(65_536):
            into.extend(chunk)
            pending.extend(chunk)
            if b"\n" not in chunk:
                continue
            *complete, tail = pending.split(b"\n")
            pending = bytearray(tail)
            for raw in complete:
                emit(raw)
        if pending:
            emit(bytes(pending))  # a final line with no trailing newline

    # Both pipes are read from the moment the process starts. Waiting on exit
    # first is the classic deadlock: the child blocks writing a large answer
    # into a full pipe while we block waiting for it to exit.
    readers = [
        asyncio.create_task(
            (drain_lines if on_line else drain)(proc.stdout, out_buf)
        ),
        asyncio.create_task(drain(proc.stderr, err_buf)),
    ]

    async def feed_stdin() -> None:
        if stdin_text is None or proc.stdin is None:
            return
        try:
            proc.stdin.write(stdin_text.encode("utf-8"))
            await proc.stdin.drain()
        except (BrokenPipeError, ConnectionResetError):
            # The CLI decided it had enough input. Not our problem to report.
            pass
        finally:
            with contextlib.suppress(BrokenPipeError, ConnectionResetError):
                proc.stdin.close()

    timed_out = False
    try:
        await asyncio.wait_for(
            asyncio.gather(feed_stdin(), proc.wait()), timeout=timeout_s
        )
    except TimeoutError:
        timed_out = True
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
        with contextlib.suppress(Exception):
            await proc.wait()
    finally:
        # The readers finish on their own once the pipes close, which killing
        # the process guarantees. Whatever they gathered is still worth having.
        with contextlib.suppress(Exception):
            await asyncio.wait_for(asyncio.gather(*readers, return_exceptions=True), timeout=5)
        for task in readers:
            task.cancel()

    return ProcessResult(
        exit_code=proc.returncode if proc.returncode is not None else -1,
        stdout=out_buf.decode("utf-8", errors="replace"),
        stderr=err_buf.decode("utf-8", errors="replace"),
        timed_out=timed_out,
    )


def error_detail(result: ProcessResult, *, max_chars: int = 2000) -> str:
    """The most useful explanation available for a failed invocation.

    JSON-output CLIs report errors as events on *stdout*, so trusting stderr
    alone produces "opencode-cli failed: " with the reason sitting right there
    in the output that was thrown away. Prefer stderr, then error-shaped stdout
    events, then the stdout tail.
    """
    if result.stderr and result.stderr.strip():
        return _truncate(result.stderr.strip(), max_chars)

    events: list[str] = []
    for line in (result.stdout or "").splitlines():
        stripped = line.strip()
        if not stripped.startswith("{"):
            continue
        try:
            node = json.loads(stripped)
        except ValueError:
            continue
        if not isinstance(node, dict):
            continue
        type_ = str(node.get("type", ""))
        if "error" in type_.lower() or "error" in node:
            events.append(stripped)
    if events:
        return _truncate("\n".join(events), max_chars)

    if not (result.stdout or "").strip():
        return "(no stderr, no stdout)"
    return "(stderr empty; stdout tail) " + _truncate(result.stdout.strip()[-600:], max_chars)


def _truncate(text: str, max_chars: int) -> str:
    return text if len(text) <= max_chars else text[:max_chars] + "…[truncated]"


@contextlib.contextmanager
def staged_media(images: Sequence[ImageInput]) -> Iterator[list[Path]]:
    """Write in-memory images out as real files, and clean them up after.

    The API transport takes base64 in the request body; a CLI takes a path on
    the command line. Files land under :func:`neutral_cwd` because that is the
    only directory these CLIs are allowed to look at.
    """
    directory = Path(tempfile.mkdtemp(prefix="media-", dir=neutral_cwd()))
    paths: list[Path] = []
    try:
        for index, image in enumerate(images):
            target = directory / f"{index:02d}-image{image.suffix()}"
            target.write_bytes(image.raw_bytes())
            paths.append(target)
        yield paths
    finally:
        shutil.rmtree(directory, ignore_errors=True)
