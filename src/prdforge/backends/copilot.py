"""GitHub Copilot CLI as a generation backend (``copilot-cli``).

Shells out to ``copilot -p ... --output-format json`` and folds the JSONL event
stream into one answer. Authentication is the CLI's own ``copilot login``, so
this runs on a GitHub Copilot subscription with no key plumbed through PRD
Forge.

There is no Python Copilot SDK to bind against - the SDK is a Node/Java affair -
so the CLI is the whole integration surface here. That costs one thing worth
knowing about: **the prompt can only travel as a command-line argument.**
``-p`` takes its text inline and the CLI does not read stdin, so a prompt past
the kernel's argv ceiling cannot be sent at all. Rather than let ``execve``
fail with an opaque ``E2BIG``, that is detected and raised as a normal backend
error - which is exactly the condition the failover chain exists to route
around.

Two smaller findings from running it, both encoded below:

* ``--silent`` suppresses the ``session.usage_checkpoint`` event, which is the
  only accounting the CLI emits. It is not passed - the answer is picked out of
  the JSONL stream by event type anyway, so silencing the rest buys nothing and
  costs the numbers.
* That checkpoint reports the *session's* credits, and one non-interactive
  invocation is one session, so it is this call's cost. (It reads like a
  running total; three consecutive calls reported 1.521, 1.616 and 1.583
  credits, which a cumulative counter cannot do.)
"""

from __future__ import annotations

import contextlib
import json
import logging

from prdforge.backends import process
from prdforge.backends.base import (
    COST_USD_FROM_CREDITS,
    BackendError,
    BackendRequest,
    BackendResult,
    EmptyBackendResponseError,
    LLMBackend,
    LLMUsage,
    StreamChunk,
    classify,
)

logger = logging.getLogger(__name__)

CREDIT_USD = 0.01
"""GitHub's published rate: 1 AI credit = $0.01 USD, the same for every model
because the model multiplier is already baked into how many credits a call
consumes. A published unit conversion, not an estimate - which is what makes it
safe to do here. Reporting the raw credit count in a field called "cost in USD"
is how a $2 run gets shown as a $200 one."""


class CopilotBackend(LLMBackend):
    id = "copilot-cli"
    supports_vision = True  # --attachment takes image files in non-interactive mode

    def __init__(self, *, home: str | None = None) -> None:
        self.home = home
        """``COPILOT_HOME``. The CLI keeps credentials, MCP config, plugins and
        skills in one directory, so pointing this at a copy holding only the
        credentials gives a generation call a clean session: no user MCP servers
        to dial (one of them failed and cost seconds of wall clock on every
        call), no unrelated skill catalogue in the prompt. Unset by default,
        because getting it wrong means "not logged in"."""

    def available(self) -> bool:
        return process.which("copilot") is not None

    def list_models(self) -> list[str]:
        """Curated - the CLI has no model-listing command.

        ``auto`` lets Copilot choose, and is the right default: the concrete ids
        move with GitHub's catalogue, and one missing here is still accepted by
        ``--model``, it just is not offered as a suggestion.
        """
        return ["auto"]

    async def generate(self, request: BackendRequest) -> BackendResult:
        prompt = f"SYSTEM:\n{request.system}\n\nUSER:\n{request.user}"
        if process.exceeds_argv_limit(prompt):
            raise BackendError(
                f"copilot-cli cannot send a {len(prompt.encode('utf-8'))}-byte prompt: it takes "
                f"the prompt inline via -p and the OS caps one argument at "
                f"~{process.MAX_ARGV_PROMPT_BYTES} bytes. Use claude-code or opencode-cli for "
                "this call (both accept the prompt on stdin), or reduce the input size.",
                backend=self.id,
            )

        with process.staged_media(request.images) if request.images else contextlib.nullcontext([]) as media:
            argv = [
                "copilot",
                "-p",
                prompt,
                "--output-format",
                "json",
                # Required for non-interactive mode. Paired with the neutral cwd
                # below: a generation call must not be able to reach a real
                # checkout, and -C is what decides which one it sees.
                "--allow-all-tools",
                "--no-color",
                # A PRD generation call has no business loading a stray AGENTS.md,
                # talking to the GitHub MCP server, or blocking on a question
                # nobody is there to answer.
                "--no-custom-instructions",
                "--disable-builtin-mcps",
                "--no-ask-user",
                "-C",
                str(process.neutral_cwd()),
            ]
            if not media:
                # Nothing to edit, nothing to run: an empty tool list keeps the
                # model from working the question agentically. Not applied when
                # attachments are present - the same grant that lets a model
                # open an image is the one an empty list would take away, and a
                # model that cannot see the image still answers, confidently,
                # about nothing.
                argv.append("--available-tools=")
            if request.on_chunk is not None:
                # Turns `assistant.reasoning_delta` on. Only OpenAI models honour
                # it - an Anthropic model behind Copilot returns the reasoning
                # encrypted whatever you ask - but it costs nothing to request
                # and the difference is 175 readable deltas versus none.
                argv.append("--enable-reasoning-summaries")
            if request.model and request.model.strip():
                argv += ["--model", request.model.strip()]
            if request.effort and request.effort.strip():
                argv += ["--effort", request.effort.strip()]
            for path in media or []:
                argv += ["--attachment", str(path)]

            result = await process.run(
                argv,
                cwd=process.neutral_cwd(),
                env={"COPILOT_HOME": self.home} if self.home else None,
                timeout_s=request.timeout_s,
                on_line=_line_streamer(request.on_chunk),
            )

        if result.timed_out:
            raise BackendError(
                f"copilot-cli timed out after {request.timeout_s:.0f}s", backend=self.id
            )

        text, usage = parse_events(result.stdout)

        if result.exit_code != 0:
            raise classify(
                f"copilot-cli failed (exit {result.exit_code}): {process.error_detail(result)}",
                backend=self.id,
                usage=usage,
            )
        if not text.strip():
            raise EmptyBackendResponseError(
                "No assistant content in copilot-cli output", backend=self.id, usage=usage
            )
        return BackendResult(text=text, usage=usage, model=request.model)


def parse_events(stdout: str | None) -> tuple[str, LLMUsage | None]:
    """Fold Copilot's JSONL events into (answer, usage).

    A complete ``assistant.message`` wins over the accumulated deltas when both
    are present; the deltas are the fallback for a stream that never got its
    closing event.
    """
    deltas: list[str] = []
    message: str | None = None
    usage: LLMUsage | None = None
    session_credits: float | None = None

    for line in (stdout or "").splitlines():
        stripped = line.strip()
        if not stripped.startswith("{"):
            continue
        try:
            event = json.loads(stripped)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue

        type_ = event.get("type", "")
        data = event.get("data") if isinstance(event.get("data"), dict) else {}

        if type_ == "assistant.message_delta":
            deltas.append(str(data.get("deltaContent") or ""))
        elif type_ == "assistant.message":
            content = str(data.get("content") or "")
            if content.strip():
                message = content
        elif type_ == "assistant.usage":
            # Per-turn accounting, as the Copilot SDK emits it. The CLI does not
            # today; handled so it is picked up for free if it starts.
            usage = _usage_from(data)
        elif type_ == "session.usage_checkpoint":
            # Last checkpoint wins: it is the session's running total, and one
            # non-interactive invocation is one session.
            nano = data.get("totalNanoAiu")
            if isinstance(nano, (int, float)):
                session_credits = nano / 1_000_000_000

    if session_credits is not None and (usage is None or usage.credits is None):
        usage = usage or LLMUsage()
        usage.credits = session_credits
        usage.cost = session_credits * CREDIT_USD
        usage.cost_unit = COST_USD_FROM_CREDITS

    return (message if message else "".join(deltas)), usage


def _usage_from(data: dict) -> LLMUsage | None:
    """Read a per-turn usage event, converting credits at the published rate.

    ``cost`` on this event is the *model multiplier*, not money - it is 1.0 for
    every call of a given model regardless of size. The billing figure is
    ``copilotUsage.totalNanoAiu``; AIU is the human-facing unit.
    """
    if not data:
        return None
    copilot_usage = data.get("copilotUsage") or {}
    nano = copilot_usage.get("totalNanoAiu") if isinstance(copilot_usage, dict) else None
    credits = (nano / 1_000_000_000) if isinstance(nano, (int, float)) else None

    def as_int(value: object) -> int | None:
        return int(value) if isinstance(value, (int, float)) else None

    return LLMUsage(
        input_tokens=as_int(data.get("inputTokens")),
        output_tokens=as_int(data.get("outputTokens")),
        cache_read_tokens=as_int(data.get("cacheReadTokens")),
        cache_write_tokens=as_int(data.get("cacheWriteTokens")),
        reasoning_tokens=as_int(data.get("reasoningTokens")),
        cost=credits * CREDIT_USD if credits is not None else None,
        cost_unit=COST_USD_FROM_CREDITS if credits is not None else None,
        duration_ms=as_int(data.get("duration")),
        credits=credits,
        finish_reason=data.get("finishReason"),
    )


def _line_streamer(on_chunk):  # noqa: ANN001, ANN202
    """Copilot's JSONL deltas as normalised chunks.

    Both the answer and - with ``--enable-reasoning-summaries`` on an OpenAI
    model - the reasoning arrive token by token. The closing ``assistant.
    reasoning`` event is ignored: it repeats the whole summary that already
    streamed, and on models that do not produce one it carries an opaque blob
    with an empty ``content``.
    """
    if on_chunk is None:
        return None

    KINDS = {"assistant.message_delta": "text", "assistant.reasoning_delta": "reasoning"}

    def handle(line: str) -> None:
        line = line.strip()
        if not line.startswith("{"):
            return
        try:
            event = json.loads(line)
        except ValueError:
            return
        kind = KINDS.get(str(event.get("type")))
        if kind is None:
            return
        data = event.get("data") if isinstance(event.get("data"), dict) else {}
        text = str(data.get("deltaContent") or "")
        if text:
            on_chunk(StreamChunk(kind=kind, text=text))

    return handle
