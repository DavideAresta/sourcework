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
import re
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


_SCHEMA_REFUSAL = re.compile(
    r"response_format|json_schema|grammar|unsupported.*schema|invalid.*schema",
    re.IGNORECASE,
)


_NO_VISION = re.compile(r"image input is not supported|mmproj|does not support image", re.IGNORECASE)


def _blind_model_error(exc: Exception, model: str | None) -> str | None:
    """Turn "image input is not supported" into an instruction.

    A server that refuses images says so clearly, but it says it about a model
    id, and the operator is looking at a role. Naming the setting is the
    difference between a two-minute fix and reading backend logs.
    """
    if not _NO_VISION.search(str(exc)):
        return None
    return (
        f"{model or 'the configured model'} cannot see images. Point "
        f"PRDFORGE_LLM__VISION_MODEL at a multimodal model - for a local server that means "
        f"one started with --mmproj. Original error: {str(exc)[:200]}"
    )


def _looks_like_schema_refusal(exc: Exception) -> bool:
    """Did the server object to the schema, rather than to the request?

    Narrow on purpose. Retrying a quota error or a timeout without the schema
    only wastes the timeout a second time; retrying a schema the server could
    not compile recovers the call.
    """
    return bool(_SCHEMA_REFUSAL.search(str(exc)))


class LiteLLMBackend(LLMBackend):
    id = "litellm"
    supports_vision = True

    def __init__(
        self,
        *,
        api_base: str | None = None,
        api_key: str | None = None,
        num_retries: int = 2,
    ) -> None:
        self.api_base = api_base
        self.api_key = api_key
        self.num_retries = num_retries
        """LiteLLM's own retries, *inside* one call. Worth lowering for a local
        server: there the usual failure is a timeout, and three attempts at a
        20-minute ceiling is an hour spent discovering the same thing."""

    def available(self) -> bool:
        try:
            import litellm  # noqa: F401
        except ImportError:
            return False
        return True

    def list_models(self) -> list[str]:
        """What the configured endpoint says it serves.

        Only asked of an explicit ``api_base``: that is a local server or a
        gateway, where the answer is a handful of ids nobody can guess and the
        round trip is a millisecond away. Against a hosted provider the same
        call returns hundreds of ids the account may not even be entitled to,
        so there the picker stays free text.

        Best-effort by contract - the model list is a convenience in the UI, and
        a settings page must still render when the model server is down.
        """
        if not self.api_base:
            return []

        import httpx

        url = self.api_base.rstrip("/") + "/models"
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        try:
            response = httpx.get(url, headers=headers, timeout=2.0)
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:  # noqa: BLE001 - an unreachable server is not an error here
            logger.debug("could not list models at %s: %s", url, exc)
            return []

        served = [
            str(entry["id"])
            for entry in (payload.get("data") or [])
            if isinstance(entry, dict) and entry.get("id")
        ]
        # `openai/` is what routes LiteLLM at api_base instead of at OpenAI, so
        # an id copied straight out of this list has to carry it - otherwise the
        # picker hands you a value that fails the moment you save it.
        return sorted(f"openai/{name}" for name in served)

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
            "num_retries": self.num_retries,
        }
        if self.api_base:
            kwargs["api_base"] = self.api_base
        if self.api_key:
            kwargs["api_key"] = self.api_key

        # Constrained decoding, when the caller asked for a schema. `strict` is
        # deliberately absent: llama.cpp, vLLM and Ollama build their grammar
        # from the schema either way, while OpenAI's strict mode rejects the
        # optional fields these Pydantic models legitimately have.
        constrained = request.json_schema is not None
        if constrained:
            kwargs["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": request.schema_name or "response",
                    "schema": request.json_schema,
                },
            }

        try:
            response = await litellm.acompletion(**kwargs)
        except Exception as exc:  # noqa: BLE001 - provider exceptions are unbounded
            # A server that cannot compile this particular schema rejects the
            # request outright. The prompt still carries the schema in prose, so
            # the unconstrained call is a real answer rather than a lost one -
            # worth one retry before declaring the backend unable to help.
            if constrained and _looks_like_schema_refusal(exc):
                logger.warning(
                    "%s rejected the response_format schema (%s) - retrying without it",
                    request.model,
                    str(exc)[:200],
                )
                kwargs.pop("response_format")
                try:
                    response = await litellm.acompletion(**kwargs)
                except Exception as retry_exc:  # noqa: BLE001
                    raise classify(
                        f"litellm call failed: {retry_exc}", backend=self.id
                    ) from retry_exc
            else:
                blind = _blind_model_error(exc, request.model) if request.images else None
                if blind:
                    raise BackendError(blind, backend=self.id) from exc
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
