"""OpenCode CLI as a generation backend (``opencode-cli``).

Shells out to ``opencode run --format json`` and folds the newline-delimited
event stream back into one answer. Authentication is OpenCode's own provider
configuration, which is what makes this the backend of choice for running the
pipeline against models PRD Forge has no credentials for.

The awkward parts, all of them load-bearing:

* **The message is the first argument.** ``-f/--file`` is a greedy array flag -
  any positional after it is eaten as another filename, and the CLI fails with
  ``File not found: SYSTEM:``.

* **There is no system-prompt flag**, so the system prompt is folded into the
  first message, which is also why oversized prompts hit the argv ceiling here
  sooner than anywhere else: system and user are concatenated into one argument.

* **OpenCode does not mark the final answer.** Narration and answer both arrive
  as ``type: "text"`` events. A tool-less agent (installed on first use, below)
  removes the narration at the source; the parser still separates the parts
  rather than concatenating them, because gluing produced strings like
  ``"...reproduce the run.Let me verify..."``.

* **Model entitlement is provider state, not configuration.** A quota window
  can demote an account to a smaller model list mid-run, and the model that
  worked ten minutes ago starts returning "not supported". That is retried once
  on the provider default rather than failing the call.
"""

from __future__ import annotations

import contextlib
import json
import logging
import subprocess
from pathlib import Path

from prdforge.backends import process
from prdforge.backends.base import (
    COST_USD,
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

ANSWER_AGENT = "prdforge-answer"

_ANSWER_AGENT_BODY = """---
description: Single-shot answerer for PRD Forge - no tools, no narration.
mode: primary
tools:
  write: false
  edit: false
  patch: false
  bash: false
  read: false
  grep: false
  glob: false
  list: false
  webfetch: false
  todowrite: false
  todoread: false
  task: false
  skill: false
---
You answer in exactly one message.

You have no tools. Never describe actions, plans, or intentions - no "Let me...",
no "I'll check...", no commentary before or after. Emit only the requested output
and nothing else. When a response format or schema is given, your entire message
must be that value alone.
"""


class OpenCodeBackend(LLMBackend):
    id = "opencode-cli"
    supports_vision = True

    def __init__(self, *, pure: bool = False) -> None:
        self.pure = pure
        """``--pure`` runs OpenCode without external plugins. Off by default
        because it also disables user-global plugins; on, it skips re-installing
        a ~60 MB plugin tree into the working directory on every single call."""

    def available(self) -> bool:
        return process.which("opencode") is not None

    def list_models(self) -> list[str]:
        """Live discovery: ``opencode models``, one ``provider/model`` per line."""
        if not self.available():
            return []
        try:
            completed = subprocess.run(  # noqa: S603
                ["opencode", "models"],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            logger.warning("opencode models failed: %s", exc)
            return []
        if completed.returncode != 0:
            return []
        return [line.strip() for line in completed.stdout.splitlines() if "/" in line.strip()]

    async def generate(self, request: BackendRequest) -> BackendResult:
        cwd = process.neutral_cwd()
        with process.staged_media(request.images) if request.images else contextlib.nullcontext([]) as media:
            argv, stdin_text = self._build_argv(request, cwd, media or [])
            result = await process.run(
                argv,
                cwd=cwd,
                env=_env(request.max_tokens),
                stdin_text=stdin_text,
                timeout_s=request.timeout_s,
                on_line=_line_streamer(request.on_chunk),
            )

            parsed = parse_events(result.stdout)

            # Retry once without the model pin. Demotion is a property of the
            # account at this instant, not of the request, and losing a whole
            # extraction to it is a poor trade for a second attempt.
            if (
                (result.exit_code != 0 or parsed.error)
                and _model_rejected(parsed.error, result.stderr)
                and request.model
            ):
                logger.warning(
                    "opencode rejected model %r (tier/quota state) - retrying on provider default",
                    request.model,
                )
                fallback = _strip_flags(argv, {"-m", "--model", "--variant"})
                result = await process.run(
                    fallback,
                    cwd=cwd,
                    env=_env(request.max_tokens),
                    # The retry must carry the message the same way the first
                    # attempt did: with the prompt on stdin the copied argv has
                    # no positional at all, and OpenCode answers "You must
                    # provide a message or a command".
                    stdin_text=stdin_text,
                    timeout_s=request.timeout_s,
                )
                parsed = parse_events(result.stdout)

        if result.timed_out:
            raise BackendError(
                f"opencode-cli timed out after {request.timeout_s:.0f}s",
                backend=self.id,
                usage=parsed.usage,
            )
        if result.exit_code != 0 or parsed.error:
            detail = parsed.error or process.error_detail(result)
            # Whatever the provider processed before erroring was still billed,
            # and the stream is right here - report it rather than discard it.
            raise classify(
                f"opencode-cli failed (exit {result.exit_code}): {detail}",
                backend=self.id,
                usage=parsed.usage,
            )
        if not parsed.text.strip():
            raise EmptyBackendResponseError(
                "No assistant content in opencode-cli output", backend=self.id, usage=parsed.usage
            )
        return BackendResult(text=parsed.text, usage=parsed.usage, model=request.model)

    def _build_argv(
        self, request: BackendRequest, cwd: Path, media: list[Path]
    ) -> tuple[list[str], str | None]:
        argv = ["opencode", "run"]
        if self.pure:
            argv.append("--pure")

        message = f"SYSTEM:\n{request.system}\n\nUSER:\n{request.user}"
        stdin_text: str | None = None
        if process.exceeds_argv_limit(message):
            # `opencode run` reads the message from stdin when given no
            # positional - verified against the binary, which otherwise answers
            # "You must provide a message or a command".
            stdin_text = message
            logger.info(
                "opencode message is %d bytes - passing it on stdin (argv limit is %d)",
                len(message.encode("utf-8")),
                process.MAX_ARGV_PROMPT_BYTES,
            )
        else:
            argv.append(message)

        # Name the session ourselves. Left unset, OpenCode generates a title by
        # making a SECOND model call - its own "small" model, on its own
        # provider - for every single invocation. Observed live as
        # `agent=title small=true modelID=gpt-5.4-nano`: a nano-model call per
        # PRD Forge call, spending an account nobody chose, on a title that is
        # never read because the plain-generation path never resumes a session.
        # It is also invisible to the usage ledger, which only sums the main
        # stream's step_finish events.
        argv += ["--title", "prdforge", "--format", "json"]
        if request.on_chunk is not None:
            # Without it the stream carries the answer but not the working: the
            # `reasoning` events simply never appear. Only asked for when someone
            # is watching, since it is the model's tokens either way.
            argv.append("--thinking")
        if request.model and request.model.strip():
            argv += ["-m", request.model.strip()]
        if request.effort and request.effort.strip():
            argv += ["--variant", request.effort.strip()]
        for path in media:
            argv += ["-f", str(path)]
        argv += ["--dir", str(cwd)]
        if _install_answer_agent(cwd):
            argv += ["--agent", ANSWER_AGENT]
        return argv, stdin_text


# ---------------------------------------------------------------------------
# Event stream
# ---------------------------------------------------------------------------


class ParsedRun:
    __slots__ = ("text", "error", "usage", "parts")

    def __init__(self, text: str, error: str | None, usage: LLMUsage | None, parts: list[str]):
        self.text = text
        self.error = error
        self.usage = usage
        self.parts = parts


def parse_events(stdout: str | None) -> ParsedRun:
    """Fold the NDJSON event stream into text, an error and a usage total."""
    parts: list[str] = []
    error: str | None = None
    totals = LLMUsage(cost=0.0, cost_unit=COST_USD)
    saw_usage = False

    for line in (stdout or "").splitlines():
        stripped = line.strip()
        if not stripped.startswith("{"):
            continue
        try:
            event = json.loads(stripped)
        except ValueError:
            continue  # non-JSON noise between events
        if not isinstance(event, dict):
            continue

        type_ = event.get("type", "")
        part = event.get("part") if isinstance(event.get("part"), dict) else event

        if type_ == "text":
            text = str(part.get("text") or "")
            if text.strip():
                parts.append(text)
        elif type_ == "error":
            err = event.get("error") or {}
            data = err.get("data") if isinstance(err, dict) else {}
            error = str(
                (data or {}).get("message")
                or (err.get("name") if isinstance(err, dict) else None)
                or "unknown opencode error"
            )
        elif type_ == "step_finish":
            saw_usage = True
            totals = _accumulate(totals, part)

    # The answer is the LAST block when it stands alone as a complete JSON
    # value; anything before it was narration. Any other shape keeps every
    # part, joined with a newline so the pieces stay separable instead of
    # running together mid-sentence.
    if len(parts) > 1 and _looks_like_complete_json(parts[-1]):
        text = parts[-1]
    else:
        text = "\n".join(parts)

    return ParsedRun(text=text, error=error, usage=totals if saw_usage else None, parts=parts)


def _accumulate(totals: LLMUsage, part: dict) -> LLMUsage:
    tokens = part.get("tokens") or {}
    cache = tokens.get("cache") or {} if isinstance(tokens, dict) else {}

    def bump(current: int | None, value: object) -> int | None:
        if not isinstance(value, (int, float)):
            return current
        return (current or 0) + int(value)

    totals.input_tokens = bump(totals.input_tokens, tokens.get("input"))
    totals.output_tokens = bump(totals.output_tokens, tokens.get("output"))
    totals.reasoning_tokens = bump(totals.reasoning_tokens, tokens.get("reasoning"))
    totals.cache_read_tokens = bump(totals.cache_read_tokens, cache.get("read"))
    totals.cache_write_tokens = bump(totals.cache_write_tokens, cache.get("write"))
    if isinstance(part.get("cost"), (int, float)):
        totals.cost = (totals.cost or 0.0) + float(part["cost"])
    return totals


def _looks_like_complete_json(text: str) -> bool:
    candidate = text.strip()
    if not candidate.startswith(("{", "[")):
        return False
    try:
        json.loads(candidate)
    except ValueError:
        return False
    return True


def _model_rejected(error: str | None, stderr: str | None) -> bool:
    blob = f"{error or ''} {stderr or ''}".lower()
    return "not supported" in blob or "unknown model" in blob or "model not found" in blob


def _strip_flags(argv: list[str], flags: set[str]) -> list[str]:
    out: list[str] = []
    skip = False
    for arg in argv:
        if skip:
            skip = False
            continue
        if arg in flags:
            skip = True
            continue
        out.append(arg)
    return out


def _env(max_tokens: int | None) -> dict[str, str]:
    """Environment overrides. All of them are defaults a caller can override.

    ``OPENCODE_EXPERIMENTAL_OUTPUT_TOKEN_MAX`` matters most: OpenCode computes
    ``min(model.limit.output, this)``, so a high sentinel falls through to the
    model's real ceiling instead of OpenCode's 32k default - which is what stops
    a large requirement set truncating mid-JSON. The two disable flags are pure
    cost on a pipeline making hundreds of calls, and a network dependency in the
    middle of a run.
    """
    return {
        "OPENCODE_EXPERIMENTAL_OUTPUT_TOKEN_MAX": str(max(max_tokens or 0, 1_000_000)),
        "OPENCODE_DISABLE_AUTOUPDATE": "true",
        "OPENCODE_DISABLE_LSP_DOWNLOAD": "true",
    }


def _install_answer_agent(cwd: Path) -> bool:
    """Write the tool-less agent definition into the scratch project.

    Best effort: without the file ``--agent`` would fail the call outright, so a
    failure here just means keeping OpenCode's default behaviour.
    """
    try:
        directory = cwd / ".opencode" / "agent"
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / f"{ANSWER_AGENT}.md"
        # Rewrite only on change - this runs on every single call.
        if not target.exists() or target.read_text(encoding="utf-8") != _ANSWER_AGENT_BODY:
            target.write_text(_ANSWER_AGENT_BODY, encoding="utf-8")
        return True
    except OSError as exc:
        logger.debug("could not install the opencode no-narration agent: %s", exc)
        return False


def _line_streamer(on_chunk):  # noqa: ANN001, ANN202
    """Turn OpenCode's NDJSON into normalised chunks, one line at a time."""
    if on_chunk is None:
        return None

    def handle(line: str) -> None:
        line = line.strip()
        if not line.startswith("{"):
            return
        try:
            event = json.loads(line)
        except ValueError:
            return
        kind = {"reasoning": "reasoning", "text": "text"}.get(event.get("type"))
        if not kind:
            return
        part = event.get("part") if isinstance(event.get("part"), dict) else event
        text = str(part.get("text") or "")
        if text:
            on_chunk(StreamChunk(kind=kind, text=text))

    return handle
