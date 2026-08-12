"""Antigravity's CLI (``agy``), driven as a generation backend.

``agy --print`` answers one prompt non-interactively and prints a single JSON
object. It carries its own authentication, so a signed-in developer runs the
pipeline with no API key plumbed through SourceWork.

Two things make it unusual among the CLI backends here.

**It fronts three model families at once.** ``agy models`` lists Gemini, Claude
and GPT-OSS ids from one binary, which means the ``critic`` role can be a
genuinely different lineage from the writer *within a single backend* - the
thing that role exists for, without configuring a second backend.

**It can enforce a JSON schema.** ``--json-schema`` makes it the only CLI
backend that honours :attr:`BackendRequest.json_schema`, which is advisory
everywhere else. The result lands in a separate ``structured_output`` field
while ``response`` keeps the prose, so when a schema was asked for the
structured value *is* the answer. Confirmed against agy 1.1.12; a raw JSON
string works, so no temp file is needed.

Two smaller traps, both found by running it:

* ``--disable-slash-commands`` is close to mandatory. Print mode expands slash
  commands and skills *from the prompt text*, and these prompts carry arbitrary
  document content - a transcript line beginning ``/`` would otherwise be read
  as a command. No other backend has this hazard.
* ``--print-timeout`` defaults to five minutes, which is shorter than this
  project's CLI timeout. Left alone, agy's own clock silently cuts a long
  analyst call off before ``cli_timeout_s`` is reached, and the shortest
  timeout is the one that wins.
"""

from __future__ import annotations

import json
import logging
import subprocess

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

# agy takes three rungs; this project's vocabulary has five. The top two
# collapse because there is nothing above `high`. An unmapped value emits no
# flag, leaving agy's own default.
_EFFORT = {"low": "low", "medium": "medium", "high": "high", "xhigh": "high", "max": "high"}

_TIERED_SUFFIXES = ("-high", "-medium", "-low")
"""Most agy model ids end in a tier (``gemini-3.6-flash-low``) *and* there is a
separate ``--effort`` flag. Sending both is a contradiction the CLI has to
resolve somehow, so the id wins and the flag is only sent for an id that
carries no tier of its own, like ``claude-sonnet-4-6``."""


class AgyBackend(LLMBackend):
    id = "agy-cli"
    supports_vision = False
    """No image flag exists in ``agy --help``.

    Stated rather than fudged: :func:`~sourcework.backends.resolve_chain` drops
    a backend that cannot carry images from any call that has them, so the
    vision role fails over to one that can instead of this one answering
    confidently about a picture it never received."""

    def available(self) -> bool:
        return process.which("agy") is not None

    def list_models(self) -> list[str]:
        """Ask the binary, like the opencode backend does.

        Model ids here are versioned and tiered (``gemini-3.6-flash-medium``)
        and move with each release, so a hardcoded list would be wrong within a
        month.
        """
        try:
            result = subprocess.run(  # noqa: S603 - fixed argv, no shell
                ["agy", "models"], capture_output=True, text=True, timeout=30, check=False
            )
        except (OSError, subprocess.SubprocessError) as exc:
            logger.warning("could not list agy models: %s", exc)
            return []

        models = []
        for line in result.stdout.splitlines():
            # "gemini-3.6-flash-low\tGemini 3.6 Flash (Low)"
            model_id = line.split("\t")[0].strip()
            if model_id and " " not in model_id:
                models.append(model_id)
        return models

    async def generate(self, request: BackendRequest) -> BackendResult:
        prompt = f"SYSTEM:\n{request.system}\n\nUSER:\n{request.user}"
        oversized = process.exceeds_argv_limit(prompt)

        argv = self._argv(request, prompt, oversized=oversized)
        result = await process.run(
            argv,
            cwd=process.neutral_cwd(),
            stdin_text=prompt if oversized else None,
            timeout_s=request.timeout_s,
            on_line=_line_streamer(request.on_chunk),
        )

        if result.timed_out:
            raise BackendError(
                f"agy-cli timed out after {request.timeout_s:.0f}s", backend=self.id
            )

        text, usage, error = parse_output(result.stdout, want_structured=bool(request.json_schema))

        if result.exit_code != 0 or error:
            detail = error or process.error_detail(result)
            raise classify(
                f"agy-cli failed (exit {result.exit_code}): {detail}",
                backend=self.id,
                usage=usage,
            )
        if not text.strip():
            raise EmptyBackendResponseError(
                "agy-cli returned no response", backend=self.id, usage=usage
            )
        return BackendResult(text=text, usage=usage, model=request.model)

    def _argv(self, request: BackendRequest, prompt: str, *, oversized: bool) -> list[str]:
        argv = ["agy"]

        # Oversized prompts go on stdin, with no --print at all: agy reads a
        # piped stdin as the prompt, and passing both would send it twice.
        if oversized:
            logger.info(
                "agy-cli: prompt is %d bytes, sending it on stdin", len(prompt.encode())
            )
        else:
            argv += ["--print", prompt]

        argv += [
            "--output-format", "stream-json" if request.on_chunk else "json",
            # Our prompts are documents. A line starting with "/" is content,
            # not a command.
            "--disable-slash-commands",
            # agy's own default is 5 minutes, which would cut off a long
            # analyst call before this project's timeout ever applies.
            "--print-timeout", f"{int(request.timeout_s)}s",
        ]

        if request.model:
            argv += ["--model", request.model]
            tiered = request.model.endswith(_TIERED_SUFFIXES)
        else:
            tiered = False

        effort = _EFFORT.get((request.effort or "").strip().lower())
        if effort and not tiered:
            argv += ["--effort", effort]

        # The only CLI backend that can enforce the schema rather than just
        # describe it in the prompt.
        if request.json_schema:
            argv += ["--json-schema", json.dumps(request.json_schema)]

        return argv


def parse_output(stdout: str, *, want_structured: bool = False) -> tuple[str, LLMUsage | None, str | None]:
    """``(text, usage, error)`` from agy's output.

    One JSON object for ``--output-format json``; for ``stream-json`` the same
    object arrives inside the final ``{"event":"result","result":{…}}`` line.
    Shape confirmed against agy 1.1.12::

        {"conversation_id":"…","status":"SUCCESS","response":"OK\\n",
         "duration_seconds":2.18,"num_turns":1,
         "usage":{"input_tokens":17744,"output_tokens":26,"thinking_tokens":22,
                  "cache_read_tokens":0,"total_tokens":17770}}
    """
    node = _result_node(stdout)
    if node is None:
        return "", None, None

    status = str(node.get("status") or "").upper()
    error = None
    if status and status != "SUCCESS":
        error = str(node.get("error") or node.get("message") or f"status {status}")

    text = str(node.get("response") or "")
    # When a schema was requested the conforming value is the answer, and
    # `response` is the prose the model wrote around it.
    if want_structured and node.get("structured_output") is not None:
        text = json.dumps(node["structured_output"])

    return text, _usage_from(node.get("usage")), error


def _result_node(stdout: str) -> dict | None:
    """The result object, from either output format."""
    text = stdout.strip()
    if not text:
        return None

    # Plain json: the whole of stdout is the object.
    if text.startswith("{") and "\n" not in text.strip():
        try:
            return json.loads(text)
        except ValueError:
            pass

    # stream-json: walk back for the result event; a plain object also parses
    # here when it happens to span lines.
    last: dict | None = None
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if event.get("event") == "result" and isinstance(event.get("result"), dict):
            last = event["result"]
        elif "response" in event and "status" in event:
            last = event
    return last


def _usage_from(usage: object) -> LLMUsage | None:
    """Tokens only. agy reports no cost, so none is invented."""
    if not isinstance(usage, dict):
        return None

    def get(name: str) -> int | None:
        value = usage.get(name)
        return int(value) if isinstance(value, (int, float)) else None

    return LLMUsage(
        input_tokens=get("input_tokens"),
        output_tokens=get("output_tokens"),
        cache_read_tokens=get("cache_read_tokens"),
        reasoning_tokens=get("thinking_tokens"),
    )


def _line_streamer(on_chunk):  # noqa: ANN001, ANN202
    """Narrate as agy works, or ``None`` when nobody is watching.

    Streaming costs one flag change - ``--output-format stream-json`` - so the
    non-streaming invocation is otherwise identical.
    """
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

        if event.get("event") != "step_update":
            return
        step = event.get("step_update") or {}
        delta = step.get("text_delta")
        if delta:
            on_chunk(StreamChunk("text", str(delta)))
        elif step.get("state") == "ACTIVE" and step.get("step_type") not in (None, "agent_response"):
            on_chunk(StreamChunk("step", str(step["step_type"]).replace("_", " ")))

    return handle
