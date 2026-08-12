"""What a backend is, and what every backend owes the caller.

:class:`~sourcework.llm.LLM` does not know how a model is reached. It hands a
backend a system prompt, a user prompt and possibly some images, and gets back
text plus whatever usage the provider was willing to report. That is the whole
contract, and it is what lets an agent coded against ``llm.structured(...)``
run unchanged against an HTTP API today and a coding CLI tomorrow.

Two backend families exist:

* **API** - :mod:`sourcework.backends.litellm_backend`. One HTTPS call, the
  provider returns a completion. Credentials live in the environment.
* **CLI** - ``claude-code``, ``opencode-cli``, ``copilot-cli``. A subprocess
  that carries *its own* authentication (the CLI's stored login or
  subscription) and answers in JSON. No API key is plumbed anywhere, which is
  the point: a developer already logged into Claude Code can run the whole
  pipeline on that subscription.

The CLI family brings failure modes an HTTP client never has - argv size
limits, a tool-using agent narrating its way to an answer, quota text that only
appears as a string on stderr - so they are handled once, here and in
:mod:`sourcework.backends.process`, rather than three times over.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, ClassVar

from pydantic import BaseModel

# ---------------------------------------------------------------------------
# Images
# ---------------------------------------------------------------------------


class ImageInput(BaseModel):
    """An image to send alongside the prompt.

    Held as base64 because that is what the API transport wants and what the
    A2A layer already carries. CLI backends stage it back out to a temp file
    (see :func:`sourcework.backends.process.staged_media`) because their
    attachment flags take paths, not bytes.
    """

    media_type: str
    data_b64: str
    label: str | None = None

    def as_content_part(self) -> dict[str, Any]:
        """OpenAI/LiteLLM multimodal content-part shape."""
        return {
            "type": "image_url",
            "image_url": {"url": f"data:{self.media_type};base64,{self.data_b64}"},
        }

    def raw_bytes(self) -> bytes:
        import base64

        return base64.b64decode(self.data_b64)

    def suffix(self) -> str:
        """A plausible file extension, for backends that need a real file."""
        subtype = self.media_type.split("/")[-1].split("+")[0].lower()
        return {"jpeg": ".jpg", "svg": ".svg"}.get(subtype, f".{subtype or 'png'}")


# ---------------------------------------------------------------------------
# Usage
# ---------------------------------------------------------------------------

# What `cost` is denominated in. These are NOT interchangeable and must never be
# summed together: a Copilot credit figure added to a dollar figure is how a $2
# run gets reported as a $200 one.
COST_USD = "usd"
"""Provider-billed dollars (LiteLLM providers, OpenCode)."""

COST_USD_API_EQUIVALENT = "usd-api-eq"
"""Claude Code's figure: what these tokens *would* have cost on the API. Under a
subscription that is not what anyone is charged - a four-token reply can report
several cents because it carried a large cached prefix."""

COST_USD_FROM_CREDITS = "usd-copilot"
"""Copilot AI credits converted at the published rate. Kept apart from
:data:`COST_USD` because the conversion is ours, not the provider's."""


@dataclass(slots=True)
class LLMUsage:
    """Token and cost accounting for one completed call.

    Every field is optional: backends report what they report, and pretending
    otherwise means inventing numbers. ``cost`` is only meaningful next to
    ``cost_unit``.
    """

    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_read_tokens: int | None = None
    cache_write_tokens: int | None = None
    reasoning_tokens: int | None = None
    cost: float | None = None
    cost_unit: str | None = None
    duration_ms: int | None = None
    credits: float | None = None
    """Raw provider credits, kept alongside the converted ``cost``. Storing
    credits in a field named for dollars is how a 200-credit run gets reported
    as a $200 one."""

    finish_reason: str | None = None

    @property
    def truncated(self) -> bool:
        """True when the backend said the response was cut at the output limit."""
        return (self.finish_reason or "").lower() in {"length", "max_tokens", "max_output_tokens"}

    @property
    def total_tokens(self) -> int:
        return (self.input_tokens or 0) + (self.output_tokens or 0)

    def merged_with(self, other: LLMUsage | None) -> LLMUsage:
        """Sum two usages. Costs only add when they are in the same unit."""
        if other is None:
            return self

        def add(a: int | float | None, b: int | float | None):  # noqa: ANN202
            return None if a is None and b is None else (a or 0) + (b or 0)

        same_unit = self.cost_unit == other.cost_unit or self.cost_unit is None or other.cost_unit is None
        return LLMUsage(
            input_tokens=add(self.input_tokens, other.input_tokens),
            output_tokens=add(self.output_tokens, other.output_tokens),
            cache_read_tokens=add(self.cache_read_tokens, other.cache_read_tokens),
            cache_write_tokens=add(self.cache_write_tokens, other.cache_write_tokens),
            reasoning_tokens=add(self.reasoning_tokens, other.reasoning_tokens),
            cost=add(self.cost, other.cost) if same_unit else self.cost,
            cost_unit=self.cost_unit or other.cost_unit,
            duration_ms=add(self.duration_ms, other.duration_ms),
            credits=add(self.credits, other.credits),
            finish_reason=other.finish_reason or self.finish_reason,
        )


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class BackendError(RuntimeError):
    """A backend could not answer. Carries any usage billed before it failed."""

    def __init__(
        self, message: str, *, backend: str | None = None, usage: LLMUsage | None = None
    ) -> None:
        super().__init__(message)
        self.backend = backend
        self.usage = usage


class BackendUnavailableError(BackendError):
    """The backend cannot run at all here - CLI not on PATH, no credentials."""


class BackendQuotaError(BackendError):
    """Usage limit, exhausted balance, expired entitlement.

    Distinct from a generic failure because it is the one error where trying a
    *different* backend is the right move: the prompt was fine, the account
    was not.
    """


class EmptyBackendResponseError(BackendError):
    """The backend exited cleanly and said nothing.

    Retrying the same backend rarely helps - CLIs return this when a tool loop
    swallowed the answer - so it feeds the failover chain like a quota hit.
    """


class OutputTruncatedError(BackendError):
    """The response stopped at the model's output ceiling.

    Never hand a truncated response to a JSON parser. The recovered fragment
    parses fine and is the wrong shape, which turns a budget problem into a
    schema-validation mystery three layers away.
    """


# Signatures across backends: claude-code says "You've reached your usage limit";
# OpenCode words an exhausted wallet its own way; Copilot talks about credits.
# Same condition, three vocabularies - all of them mean "try another backend".
_QUOTA_SIGNATURE = re.compile(
    r"usage limit|limit resets|reached your limit|quota (exceeded|exhausted)"
    r"|out of (free )?quota|credit limit reached|insufficient (balance|credits)"
    r"|not enough credits|payment required|billing (issue|required)"
    r"|rate.?limit|429",
    re.IGNORECASE,
)


def looks_like_quota(detail: str | None) -> bool:
    return bool(detail) and bool(_QUOTA_SIGNATURE.search(detail or ""))


def classify(
    detail: str, *, backend: str | None = None, usage: LLMUsage | None = None
) -> BackendError:
    """A quota error when the text carries the signature, else a plain one.

    For backends whose only error channel is a string: a CLI's exit detail, an
    SDK exception message.
    """
    cls = BackendQuotaError if looks_like_quota(detail) else BackendError
    return cls(detail, backend=backend, usage=usage)


# ---------------------------------------------------------------------------
# The contract
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class StreamChunk:
    """A fragment of the model working, as it happens.

    Normalised across three CLIs that each describe the same thing differently.
    ``kind`` is deliberately coarse - a reader wants to know "is this the model
    thinking or the model answering", not which vendor's event name produced it.
    """

    kind: str
    """reasoning | text | step"""
    text: str


ON_CHUNK = Callable[[StreamChunk], None]
"""Sink for :class:`StreamChunk`. Synchronous and must never raise: it is called
from the subprocess reader, and an exception there would stop draining the pipe
and deadlock the call it was supposed to be narrating."""


@dataclass(slots=True)
class BackendRequest:
    """One generation call, backend-independent."""

    system: str
    user: str
    images: list[ImageInput] = field(default_factory=list)
    model: str | None = None
    """Backend-specific model id. None means "let the backend pick its default"
    - which is the correct value on a failover target, because a model choice
    does not travel: ``opencode-go/glm-5`` means something to OpenCode and
    nothing to the ``claude`` CLI."""
    max_tokens: int | None = None
    temperature: float = 0.2
    timeout_s: float = 300.0
    effort: str | None = None
    """Reasoning effort, in each backend's own vocabulary (``--effort`` for
    claude-code and copilot, ``--variant`` for opencode). None = model default."""

    json_schema: dict[str, Any] | None = None
    """JSON Schema the answer must satisfy, when the caller wants one.

    Advisory: the schema is *already* in the system prompt for every backend, so
    ignoring this field costs nothing and every CLI backend does. A backend that
    can enforce it - grammar-constrained decoding on an OpenAI-compatible
    server - turns "ask nicely and retry" into "cannot emit anything else",
    which is the difference between a local 9B model being usable here and
    burning all of :attr:`~sourcework.config.LLMSettings.max_retries` on JSON that
    does not parse.
    """

    schema_name: str | None = None
    """Name for :attr:`json_schema`, since the API shape requires one."""

    on_chunk: ON_CHUNK | None = None
    """When set, the backend streams the model's working as it arrives. Costs a
    little: claude-code needs a different output format for it, opencode needs
    ``--thinking``. Left unset, every backend behaves exactly as before."""


@dataclass(slots=True)
class BackendResult:
    text: str
    usage: LLMUsage | None = None
    model: str | None = None


class LLMBackend(ABC):
    """One way of reaching a model."""

    id: ClassVar[str]
    supports_vision: ClassVar[bool] = False
    """Whether images can be *transported* at all. Orthogonal to whether the
    chosen model can see them - a text-only model on a vision-capable backend
    still ignores them."""

    def available(self) -> bool:
        """Cheap local check: is this backend usable on this machine?

        Must not make network calls - it runs in the ``backends`` CLI command
        and in failover-chain construction.
        """
        return True

    def list_models(self) -> list[str]:
        """Selectable model ids, best-effort. Empty means "free-text only"."""
        return []

    @abstractmethod
    async def generate(self, request: BackendRequest) -> BackendResult:
        """Answer ``request``, or raise a :class:`BackendError` subclass."""

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<{type(self).__name__} id={self.id!r}>"
