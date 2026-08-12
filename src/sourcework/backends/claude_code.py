"""Claude Code CLI as a generation backend (``claude-code``).

Shells out to ``claude -p --output-format json`` and reads the ``result``
field. Authentication is the CLI's own stored login, so a developer already
signed into Claude Code runs the whole pipeline on that subscription with no
``ANTHROPIC_API_KEY`` anywhere.

Two things are deliberate and worth not undoing:

* **No tools.** This is plain generation - there is nothing to edit and nothing
  to run - but the CLI otherwise brings its full toolset and works the question
  agentically, spending several turns and gluing its narration onto the answer.
  ``--tools ""`` disables the built-in set and collapses it to a single turn
  whose output parses as JSON directly. A denylist is not equivalent: the model
  routes around it through whichever tool was left.

* **Except when there are images**, where the *only* way in is the read-only
  ``Read`` tool. ``--tools ""`` would override that grant, and a model that
  cannot open the file answers with the tool call it wanted to make - which
  then validates as a perfectly well-formed, entirely content-free result.

* **No MCP servers.** Without ``--strict-mcp-config`` the CLI loads whatever the
  developer has configured globally. Observed live: a transcription call came
  back with the correct answer followed by a paragraph about authorising Gmail,
  Calendar and Drive connectors. Those servers are irrelevant here, they cost
  prompt tokens and start-up time, and their narration ends up inside the
  answer an agent is about to parse as JSON.
"""

from __future__ import annotations

import contextlib
import json
import logging

from sourcework.backends import process
from sourcework.backends.base import (
    COST_USD_API_EQUIVALENT,
    BackendError,
    BackendQuotaError,
    BackendRequest,
    BackendResult,
    EmptyBackendResponseError,
    LLMBackend,
    LLMUsage,
    OutputTruncatedError,
    StreamChunk,
    classify,
    looks_like_quota,
)

logger = logging.getLogger(__name__)

_DEFAULT_OUTPUT_CAP = 32_000
"""Claude Code's own default output ceiling. Raised per call when the request
asks for more, so a long PRD narrative is not silently cut at 32k."""


class ClaudeCodeBackend(LLMBackend):
    id = "claude-code"
    supports_vision = True

    def available(self) -> bool:
        return process.which("claude") is not None

    def list_models(self) -> list[str]:
        """Curated, because the CLI has no model-listing command.

        Aliases first - they resolve to the latest build of that tier - then
        pinned ids for a run that must not move underneath you. A model missing
        here is still usable, it just is not offered as a suggestion.
        """
        return [
            "default",
            "opus",
            "sonnet",
            "haiku",
            "claude-opus-4-6",
            "claude-sonnet-4-5",
            "claude-haiku-4-5",
        ]

    async def generate(self, request: BackendRequest) -> BackendResult:
        has_media = bool(request.images)
        with process.staged_media(request.images) if has_media else contextlib.nullcontext([]) as media_paths:
            argv = ["claude", "-p"]

            # Both of these flags are VARIADIC: placed last they would swallow
            # the positional prompt ("Input must be provided..."). They go first,
            # terminated by the --output-format flag that follows.
            if has_media:
                argv += ["--allowed-tools", "Read"]
            else:
                argv += ["--tools", ""]
            # No --mcp-config alongside it, so this means "no MCP servers at all".
            argv.append("--strict-mcp-config")
            if request.on_chunk is not None:
                # stream-json emits events as they happen instead of one blob at
                # exit; --include-partial-messages turns that into token deltas.
                # The final `result` event carries the same fields the plain json
                # format returns, so parsing below is unchanged.
                argv += ["--output-format", "stream-json", "--verbose",
                         "--include-partial-messages"]
            else:
                argv += ["--output-format", "json"]

            if request.system and request.system.strip():
                # REPLACES Claude Code's own system prompt: this is generation,
                # not agent work, and the agentic preamble only dilutes it.
                argv += ["--system-prompt", request.system]
            if request.model and request.model.strip().lower() not in {"default", "auto"}:
                argv += ["--model", request.model.strip()]
            if request.effort and request.effort.strip():
                argv += ["--effort", request.effort.strip()]

            user = request.user
            if has_media:
                listing = "\n".join(f"- {p}" for p in media_paths or [])
                user = (
                    f"{user}\n\n=== ATTACHED IMAGE FILES ===\n"
                    "Before answering, open EACH of these images with the Read tool. They are "
                    "primary source material and your answer must reflect what they actually "
                    f"show:\n{listing}"
                )

            stdin_text: str | None = None
            if process.exceeds_argv_limit(user):
                stdin_text = user
                logger.info(
                    "claude-code prompt is %d bytes - passing it on stdin (argv limit is %d)",
                    len(user.encode("utf-8")),
                    process.MAX_ARGV_PROMPT_BYTES,
                )
            else:
                argv.append(user)

            env = {}
            requested = request.max_tokens or 0
            if requested > _DEFAULT_OUTPUT_CAP:
                env["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] = str(requested)

            result = await process.run(
                argv,
                cwd=process.neutral_cwd(),
                env=env,
                stdin_text=stdin_text,
                timeout_s=request.timeout_s,
                on_line=_line_streamer(request.on_chunk),
            )

        if result.timed_out:
            raise BackendError(
                f"claude-code timed out after {request.timeout_s:.0f}s",
                backend=self.id,
            )
        if result.exit_code != 0:
            detail = process.error_detail(result)
            raise classify(f"claude-code failed (exit {result.exit_code}): {detail}", backend=self.id)

        return self._parse(_result_payload(result.stdout))

    def _parse(self, stdout: str) -> BackendResult:
        try:
            node = json.loads(stdout)
        except ValueError as exc:
            raise BackendError(
                f"claude-code output was not the expected JSON: {exc}", backend=self.id
            ) from exc
        if not isinstance(node, dict):
            raise BackendError("claude-code returned a non-object JSON result", backend=self.id)

        usage = _usage_from(node)

        if node.get("is_error"):
            message = str(node.get("result") or "(no detail)")
            if looks_like_quota(message):
                raise BackendQuotaError(
                    f"claude-code usage limit: {message}", backend=self.id, usage=usage
                )
            raise BackendError(f"claude-code error result: {message}", backend=self.id, usage=usage)

        # Truncation is not a parse problem and must not reach the parser. The
        # response is genuinely incomplete, and the largest recoverable object
        # inside it is a *fragment of a different shape* - which then fails
        # schema validation with a message describing a symptom, not the cause.
        if str(node.get("stop_reason", "")) in {"max_tokens", "max_output_tokens"}:
            produced = (usage.output_tokens if usage else None) or "?"
            raise OutputTruncatedError(
                f"claude-code stopped at the model's output limit after {produced} output "
                "tokens - the response is incomplete, so its JSON cannot be trusted. Lower the "
                "reasoning effort (thinking shares the output budget), shrink the input, or "
                "raise SOURCEWORK_LLM__MAX_TOKENS.",
                backend=self.id,
                usage=usage,
            )

        text = node.get("result") or ""
        if not str(text).strip():
            raise EmptyBackendResponseError(
                "claude-code returned an empty result field", backend=self.id, usage=usage
            )
        return BackendResult(text=str(text), usage=usage, model=node.get("model"))


def _usage_from(node: dict) -> LLMUsage | None:
    """Read the accounting fields off the result object, if it carried any."""
    usage = node.get("usage") or {}
    if not isinstance(usage, dict):
        usage = {}
    cost = node.get("total_cost_usd")
    fields = {
        "input_tokens": usage.get("input_tokens"),
        "output_tokens": usage.get("output_tokens"),
        "cache_read_tokens": usage.get("cache_read_input_tokens"),
        "cache_write_tokens": usage.get("cache_creation_input_tokens"),
        "duration_ms": node.get("duration_ms"),
    }
    if not any(v is not None for v in fields.values()) and cost is None:
        return None
    return LLMUsage(
        **fields,
        cost=cost,
        # NOT plain dollars: the CLI reports what these tokens would have cost
        # on the API, which under a subscription is nobody's actual bill.
        cost_unit=COST_USD_API_EQUIVALENT if cost is not None else None,
        finish_reason=node.get("stop_reason"),
    )


def _result_payload(stdout: str) -> str:
    """The object to parse, whichever output format produced ``stdout``.

    Plain ``--output-format json`` is a single object. ``stream-json`` is NDJSON
    whose last ``type: "result"`` event carries the same fields, so the parser
    below needs no second implementation.
    """
    stripped = (stdout or "").strip()
    if stripped.startswith("{") and "\n" not in stripped.rstrip("\n"):
        return stripped
    for line in reversed(stripped.splitlines()):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict) and event.get("type") == "result":
            return line
    return stripped


def _line_streamer(on_chunk):  # noqa: ANN001, ANN202
    """Turn Claude Code's stream-json into normalised chunks.

    The answer streams token by token. The reasoning does not, and cannot:
    ``thinking_delta`` events do arrive, but every one carries ``thinking: ""``
    and the content is only present as an encrypted ``signature`` blob. Verified
    against the CLI at ``--effort high``, which produced eight thinking deltas
    totalling zero characters.

    What those events *do* carry is ``estimated_tokens``, so the one honest
    thing to report is how much thinking is happening. That goes out as a
    ``step`` chunk - a status that supersedes the last one - rather than as
    ``reasoning``, which would put words the model never said in a panel headed
    with its name.
    """
    if on_chunk is None:
        return None

    thought = 0

    def handle(line: str) -> None:
        nonlocal thought
        line = line.strip()
        if not line.startswith("{"):
            return
        try:
            event = json.loads(line)
        except ValueError:
            return
        if event.get("type") != "stream_event":
            return
        delta = (event.get("event") or {}).get("delta") or {}
        if delta.get("type") == "text_delta" and delta.get("text"):
            on_chunk(StreamChunk(kind="text", text=str(delta["text"])))
        elif delta.get("type") == "thinking_delta":
            if text := str(delta.get("thinking") or ""):
                on_chunk(StreamChunk(kind="reasoning", text=text))  # if it ever ships
                return
            estimated = delta.get("estimated_tokens")
            if isinstance(estimated, (int, float)):
                thought += int(estimated)
            on_chunk(
                StreamChunk(kind="step", text=f"thinking… ~{thought} tokens (content withheld)")
            )

    return handle
