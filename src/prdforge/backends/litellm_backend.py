"""Hosted model APIs, via LiteLLM (``litellm``).

The original - and still the default - way PRD Forge reaches a model. LiteLLM
normalises the request/response shape across providers, so ``anthropic/…``,
``openai/…``, ``azure/…``, ``bedrock/…``, ``vertex_ai/…`` and ``ollama/…`` are
all the same code path and a provider swap is a config change.

Unlike the CLI backends, this one needs credentials: whatever environment
variable the chosen provider expects, or an explicit ``api_base``/``api_key``
pointed at a gateway.
"""

from __future__ import annotations

import logging
from typing import Any

from prdforge.backends.base import (
    COST_USD,
    BackendError,
    BackendRequest,
    BackendResult,
    BackendUnavailableError,
    EmptyBackendResponseError,
    LLMBackend,
    LLMUsage,
    classify,
)

logger = logging.getLogger(__name__)


class LiteLLMBackend(LLMBackend):
    id = "litellm"
    supports_vision = True

    def __init__(self, *, api_base: str | None = None, api_key: str | None = None) -> None:
        self.api_base = api_base
        self.api_key = api_key

    def available(self) -> bool:
        try:
            import litellm  # noqa: F401
        except ImportError:
            return False
        return True

    async def generate(self, request: BackendRequest) -> BackendResult:
        try:
            import litellm  # imported lazily: keeps agent start-up fast
        except ImportError as exc:  # pragma: no cover - declared dependency
            raise BackendUnavailableError(
                "litellm is not installed", backend=self.id
            ) from exc

        if not request.model:
            raise BackendError(
                "the litellm backend needs an explicit model id - there is no provider "
                "default to fall back to. Set PRDFORGE_LLM__DEFAULT_MODEL (or the per-role "
                "equivalent), or PRDFORGE_LLM__BACKEND_MODELS__LITELLM__<ROLE> when litellm is "
                "a failover target.",
                backend=self.id,
            )

        litellm.drop_params = True  # tolerate providers that lack a given knob

        content: list[dict[str, Any]] | str
        if request.images:
            content = [
                {"type": "text", "text": request.user},
                *[image.as_content_part() for image in request.images],
            ]
        else:
            content = request.user

        kwargs: dict[str, Any] = {
            "model": request.model,
            "messages": [
                {"role": "system", "content": request.system},
                {"role": "user", "content": content},
            ],
            "max_tokens": request.max_tokens,
            "temperature": request.temperature,
            "timeout": request.timeout_s,
            "num_retries": 2,
        }
        if self.api_base:
            kwargs["api_base"] = self.api_base
        if self.api_key:
            kwargs["api_key"] = self.api_key

        try:
            response = await litellm.acompletion(**kwargs)
        except Exception as exc:  # noqa: BLE001 - provider exceptions are unbounded
            # A rate limit or an exhausted balance arrives as a provider
            # exception here, and it is the one class of failure where another
            # backend is the right answer rather than another retry.
            raise classify(f"litellm call failed: {exc}", backend=self.id) from exc

        try:
            choice = response.choices[0]
            text = choice.message.content or ""
        except (AttributeError, IndexError) as exc:
            raise BackendError(
                f"unexpected LLM response shape: {response!r}", backend=self.id
            ) from exc

        usage = _usage_from(response, getattr(choice, "finish_reason", None))
        if not text.strip():
            raise EmptyBackendResponseError(
                f"{request.model} returned no content", backend=self.id, usage=usage
            )
        return BackendResult(text=text, usage=usage, model=request.model)


def _usage_from(response: object, finish_reason: str | None) -> LLMUsage | None:
    raw = getattr(response, "usage", None)
    if raw is None:
        return None

    def get(name: str) -> int | None:
        value = getattr(raw, name, None)
        return int(value) if isinstance(value, (int, float)) else None

    cost = None
    try:
        import litellm

        cost = litellm.completion_cost(completion_response=response)
    except Exception:  # noqa: BLE001 - pricing tables lag new models; a miss is not an error
        cost = None

    return LLMUsage(
        input_tokens=get("prompt_tokens"),
        output_tokens=get("completion_tokens"),
        cost=cost,
        cost_unit=COST_USD if cost is not None else None,
        finish_reason=finish_reason,
    )
