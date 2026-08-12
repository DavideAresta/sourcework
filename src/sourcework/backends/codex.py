"""OpenAI's Codex CLI (``codex``), driven as a generation backend.

``codex exec`` runs one turn non-interactively and prints JSONL events. Like
the other CLI backends it carries its own authentication - ``codex login``
writes credentials under ``CODEX_HOME`` - so a developer signed into Codex runs
the whole pipeline with no API key plumbed through SourceWork.

**One caveat worth stating loudly**, because it is the opposite of what the CLI
family promises. ``process.run`` merges the parent environment
(:func:`sourcework.backends.process.run`), and Codex prefers ``CODEX_API_KEY``
and then ``OPENAI_API_KEY`` over its stored login. A developer who exported
``OPENAI_API_KEY`` for the litellm backend will silently have Codex bill the
**API** rather than their subscription. Unsetting it here would be worse - for
some installs it is the only credential Codex has - so it is documented instead.

Three flags are load-bearing rather than tidy:

* ``--skip-git-repo-check`` is mandatory. Every CLI backend runs in
  :func:`~sourcework.backends.process.neutral_cwd`, a temp directory, and
  without this flag Codex refuses with "Not inside a trusted directory". It is
  a 100% failure rate that never shows up when a developer tries the command by
  hand inside a checkout.
* The prompt positional must come **before** every ``-i``. ``-i/--image`` is
  variadic (``<FILE>...`` in ``--help``), so a positional after it is read as
  another image path - the same trap as OpenCode's ``-f``.
* ``--ignore-user-config --ignore-rules --ephemeral`` keep the developer's own
  config, execpolicy rules and session history out of a generation call. Auth
  still resolves through ``CODEX_HOME``, so this does not break sign-in.
"""

from __future__ import annotations

import contextlib
import json
import logging
from typing import Any

from sourcework.backends import process
from sourcework.backends.base import (
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

# Tool features to switch off for a single-shot generation call. Every name is
# checked against `codex features list` - an invented one would ride along in
# every invocation. Measured on this machine: disabling these took a trivial
# call from 12,852 to 10,778 input tokens, because the tool definitions are the
# bulk of a Codex system prompt.
_DISABLED_FEATURES = (
    "shell_tool",
    "unified_exec",
    "browser_use",
    "computer_use",
    "apps",
    "multi_agent",
)

_VIEW_IMAGE = "view_image"
"""Kept enabled when images are attached, and disabled otherwise.

Exactly the trade claude-code makes by granting ``Read`` for images, and
copilot by withholding ``--available-tools=``: the tool that reads the picture
is the one tool a vision call cannot do without."""

# Codex accepts three rungs and defaults to the top one. `medium` maps to
# `high`, not `low`: the default being `xhigh` means "medium" asks for less than
# the default rather than for the floor, and `high` is literally the middle.
#
# A value outside this table emits no flag at all. Effort travels as `-c
# model_reasoning_effort=…`, a config override, where an unrecognised value is a
# hard startup error that kills the whole call - unlike a flag value, which
# fails validation with something legible.
_EFFORT = {
    "low": "low",
    "medium": "high",
    "high": "high",
    "xhigh": "xhigh",
    "max": "xhigh",
}


class CodexBackend(LLMBackend):
    id = "codex-cli"
    supports_vision = True

    def __init__(self, *, home: str | None = None) -> None:
        self.home = home
        """``CODEX_HOME``. Codex keeps credentials, config and session history in
        one directory; pointing this at a copy holding only the credentials
        gives generation calls a clean session. Unset uses the developer's own,
        which always works."""

    def available(self) -> bool:
        return process.which("codex") is not None

    def list_models(self) -> list[str]:
        """Deliberately empty: free text.

        Codex model ids move with each release and there is no cheap offline
        way to enumerate them, so an out-of-date curated list would offer
        suggestions that fail. Empty means "free text only" to the picker.
        """
        return []

    async def generate(self, request: BackendRequest) -> BackendResult:
        # No --system-prompt flag exists on `codex exec`, so the two halves are
        # folded into one message, as opencode and copilot do.
        prompt = f"SYSTEM:\n{request.system}\n\nUSER:\n{request.user}"
        oversized = process.exceeds_argv_limit(prompt)

        media_ctx = (
            process.staged_media(request.images) if request.images
            else contextlib.nullcontext([])
        )
        with media_ctx as media:
            argv = self._argv(request, prompt, media, oversized=oversized)
            result = await process.run(
                argv,
                cwd=process.neutral_cwd(),
                env={"CODEX_HOME": self.home} if self.home else None,
                stdin_text=prompt if oversized else None,
                timeout_s=request.timeout_s,
                on_line=_line_streamer(request.on_chunk),
            )

        if result.timed_out:
            raise BackendError(
                f"codex-cli timed out after {request.timeout_s:.0f}s", backend=self.id
            )

        # Parsed before the exit code is judged, so usage billed before a
        # failure is still attached to the error - copilot's ordering.
        text, usage, error = parse_events(result.stdout)

        if result.exit_code != 0 or error:
            detail = error or process.error_detail(result)
            raise classify(
                f"codex-cli failed (exit {result.exit_code}): {detail}",
                backend=self.id,
                usage=usage,
            )
        if not text.strip():
            raise EmptyBackendResponseError(
                "codex-cli returned no agent message", backend=self.id, usage=usage
            )
        return BackendResult(text=text, usage=usage, model=request.model)

    def _argv(
        self,
        request: BackendRequest,
        prompt: str,
        media: list,  # noqa: ANN001 - list[Path]
        *,
        oversized: bool,
    ) -> list[str]:
        argv = [
            "codex", "exec",
            # Mandatory: neutral_cwd() is a temp directory, not a repository.
            "--skip-git-repo-check",
            "-C", str(process.neutral_cwd()),
            "--sandbox", "read-only",
            "--json",
            "--color", "never",
            "--ephemeral",
            "--ignore-user-config",
            "--ignore-rules",
        ]

        for feature in _DISABLED_FEATURES:
            argv += ["--disable", feature]
        if not media:
            argv += ["--disable", _VIEW_IMAGE]

        if request.model:
            argv += ["-m", request.model]

        effort = _EFFORT.get((request.effort or "").strip().lower())
        if effort:
            argv += ["-c", f"model_reasoning_effort={effort}"]
        elif request.effort:
            logger.debug("codex-cli: dropping unmapped effort %r", request.effort)

        # The positional, and it must precede every -i. A bare "-" tells Codex
        # to read the instructions from stdin; passing the prompt as well would
        # append it as a separate <stdin> block rather than replace it.
        if oversized:
            logger.info(
                "codex-cli: prompt is %d bytes, sending it on stdin", len(prompt.encode())
            )
            argv.append("-")
        else:
            argv.append(prompt)

        for path in media:
            argv += ["-i", str(path)]
        return argv


def parse_events(stdout: str) -> tuple[str, LLMUsage | None, str | None]:
    """``(text, usage, error)`` from Codex's JSONL event stream.

    Shape confirmed against codex-cli 0.147.0::

        {"type":"thread.started","thread_id":"…"}
        {"type":"turn.started"}
        {"type":"item.completed","item":{"id":"item_0","type":"agent_message","text":"OK"}}
        {"type":"turn.completed","usage":{"input_tokens":12852,…}}
    """
    parts: list[str] = []
    error: str | None = None
    totals: dict[str, int] = {}
    saw_usage = False

    for line in stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue

        kind = event.get("type")
        if kind == "item.completed":
            item = event.get("item") or {}
            if item.get("type") == "agent_message" and item.get("text"):
                parts.append(str(item["text"]))
            elif item.get("type") == "error":
                error = error or str(item.get("message") or item.get("text") or "error")
        elif kind in ("turn.completed", "turn.failed"):
            usage = event.get("usage")
            if isinstance(usage, dict):
                saw_usage = True
                for key, value in usage.items():
                    if isinstance(value, (int, float)):
                        totals[key] = totals.get(key, 0) + int(value)
            if kind == "turn.failed":
                error = error or _failure_detail(event)
        elif kind == "error":
            error = error or str(event.get("message") or event)

    return _pick_answer(parts), (_usage_from(totals) if saw_usage else None), error


def _failure_detail(event: dict[str, Any]) -> str:
    node = event.get("error")
    if isinstance(node, dict):
        return str(node.get("message") or node)
    return str(node or "turn failed")


def _pick_answer(parts: list[str]) -> str:
    """One answer out of possibly several agent messages.

    Same resolution as ``opencode.parse_events``: a model that narrates and then
    answers emits both, and the answer is the standalone JSON value at the end.
    Anything else is joined with newlines rather than concatenated, so two
    prose fragments do not run together mid-sentence.
    """
    if not parts:
        return ""
    if len(parts) > 1 and _looks_like_complete_json(parts[-1]):
        return parts[-1]
    return "\n".join(parts)


def _looks_like_complete_json(text: str) -> bool:
    """Is ``text`` one complete JSON value?

    Deliberately duplicated from ``opencode.py`` rather than shared: ``process``
    is about subprocess mechanics, and a common helper would let a change to
    OpenCode's answer selection silently change this one.
    """
    stripped = text.strip()
    if not stripped or stripped[0] not in "{[":
        return False
    try:
        json.loads(stripped)
    except ValueError:
        return False
    return True


def _usage_from(totals: dict[str, int]) -> LLMUsage:
    """Codex reports tokens and no money.

    ``cost`` and ``cost_unit`` stay ``None`` on purpose: a subscription run has
    no per-call price, and deriving dollars from token counts would invent the
    number the cost units exist to prevent.

    ``input_tokens`` is reported as-is. Whether it already includes the cached
    portion is undocumented, and reporting what the provider said is the rule
    everywhere else here.
    """
    return LLMUsage(
        input_tokens=totals.get("input_tokens"),
        output_tokens=totals.get("output_tokens"),
        cache_read_tokens=totals.get("cached_input_tokens"),
        cache_write_tokens=totals.get("cache_write_input_tokens"),
        reasoning_tokens=totals.get("reasoning_output_tokens"),
    )


def _line_streamer(on_chunk):  # noqa: ANN001, ANN202
    """Narrate as Codex works, or ``None`` when nobody is watching.

    Unlike every other backend here this needs **no extra flag**: ``--json`` is
    already unconditional because it is how the answer is parsed at all, so the
    streaming and non-streaming invocations are byte-identical.
    """
    if on_chunk is None:
        return None

    streamed: set[str] = set()

    def handle(line: str) -> None:
        line = line.strip()
        if not line.startswith("{"):
            return
        try:
            event = json.loads(line)
        except ValueError:
            return

        item = event.get("item") or {}
        item_id, item_type = item.get("id"), item.get("type")
        text = item.get("text") or item.get("delta") or ""
        if not text:
            return

        if event.get("type") == "item.updated":
            if item_id:
                streamed.add(str(item_id))
            on_chunk(StreamChunk("reasoning" if item_type == "reasoning" else "text", text))
        elif event.get("type") == "item.completed":
            # Skip the completed text when deltas already carried it, or a build
            # that emits both shows the whole answer twice.
            if item_id and str(item_id) in streamed:
                return
            if item_type == "agent_message":
                on_chunk(StreamChunk("text", text))
            elif item_type == "reasoning":
                on_chunk(StreamChunk("reasoning", text))

    return handle
