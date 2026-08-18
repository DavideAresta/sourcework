"""The backend registry.

Which backends exist, how to build one from settings, and which order to try
them in. Everything is constructed on demand and imported lazily so that adding
a backend never slows down an agent that will not use it.
"""

from __future__ import annotations

import logging

from sourcework.backends.base import (
    BackendError,
    BackendQuotaError,
    BackendRequest,
    BackendResult,
    BackendUnavailableError,
    EmptyBackendResponseError,
    ImageInput,
    LLMBackend,
    LLMUsage,
    OutputTruncatedError,
    classify,
    looks_like_quota,
)
from sourcework.config import BACKEND_IDS, LLMSettings, normalise_backend

logger = logging.getLogger(__name__)

__all__ = [
    "BACKEND_IDS",
    "BackendError",
    "BackendQuotaError",
    "BackendRequest",
    "BackendResult",
    "BackendUnavailableError",
    "EmptyBackendResponseError",
    "ImageInput",
    "LLMBackend",
    "LLMUsage",
    "OutputTruncatedError",
    "build",
    "classify",
    "looks_like_quota",
    "probe",
    "resolve_chain",
]


def build(backend_id: str, cfg: LLMSettings) -> LLMBackend:
    """Instantiate ``backend_id``. Raises for an unknown id."""
    wanted = normalise_backend(backend_id)

    if wanted == "litellm":
        from sourcework.backends.litellm_backend import LiteLLMBackend

        return LiteLLMBackend(
            api_base=cfg.api_base, api_key=cfg.api_key, num_retries=cfg.litellm_retries
        )
    if wanted == "azure":
        from sourcework.backends.litellm_backend import AzureBackend

        return AzureBackend(
            api_base=cfg.azure_api_base,
            api_key=cfg.azure_api_key,
            api_version=cfg.azure_api_version,
            num_retries=cfg.litellm_retries,
        )
    if wanted == "bedrock":
        from sourcework.backends.litellm_backend import BedrockBackend

        return BedrockBackend(
            region_name=cfg.aws_region_name,
            access_key_id=cfg.aws_access_key_id,
            secret_access_key=cfg.aws_secret_access_key,
            session_token=cfg.aws_session_token,
            num_retries=cfg.litellm_retries,
        )
    if wanted == "vertex-ai":
        from sourcework.backends.litellm_backend import VertexAIBackend

        return VertexAIBackend(
            project=cfg.vertex_project,
            location=cfg.vertex_location,
            num_retries=cfg.litellm_retries,
        )
    if wanted == "openai":
        from sourcework.backends.litellm_backend import OpenAIBackend

        return OpenAIBackend(api_base=cfg.openai_api_base, num_retries=cfg.litellm_retries)
    if wanted == "llama-cpp":
        from shutil import which

        from sourcework.backends.litellm_backend import LiteLLMBackend

        class LlamaCppBackend(LiteLLMBackend):
            """The local ``llama-server`` route, without requiring a LiteLLM proxy."""

            def available(self) -> bool:
                # Unlike a hosted API, this choice promises a local llama.cpp
                # installation. Do not probe the server here: availability is
                # deliberately a cheap, network-free check used on every page load.
                return which("llama-server") is not None and super().available()

            def unavailable_detail(self) -> str:
                return "" if which("llama-server") else "llama-server is not installed or is not on PATH"

            def list_models(self) -> list[str]:
                # A running server is authoritative: llama-swap may serve
                # models that are downloaded on demand and are not on disk yet.
                served = super().list_models()
                if served:
                    return served

                # When the server is down, still make the Settings picker useful
                # by discovering the GGUFs the configured scanner will serve.
                from sourcework.localmodels import discover, model_dirs

                roots = model_dirs()
                if not roots:
                    return []
                models, warnings = discover(roots)
                for warning in warnings:
                    logger.warning("llama.cpp model discovery: %s", warning)
                # LiteLLM needs the openai/ provider prefix when routing this
                # OpenAI-compatible request to the local endpoint.
                return sorted(f"openai/{model.id}" for model in models)

        return LlamaCppBackend(
            api_base=cfg.llama_cpp_api_base or "http://127.0.0.1:8081/v1",
            api_key=cfg.llama_cpp_api_key,
            num_retries=cfg.litellm_retries,
            backend_id="llama-cpp",
        )
    if wanted == "claude-code":
        from sourcework.backends.claude_code import ClaudeCodeBackend

        return ClaudeCodeBackend()
    if wanted == "opencode-cli":
        from sourcework.backends.opencode import OpenCodeBackend

        return OpenCodeBackend(pure=cfg.opencode_pure)
    if wanted == "copilot-cli":
        from sourcework.backends.copilot import CopilotBackend

        return CopilotBackend(home=cfg.copilot_home)

    if wanted == "codex-cli":
        from sourcework.backends.codex import CodexBackend

        return CodexBackend(home=cfg.codex_home)
    if wanted == "agy-cli":
        from sourcework.backends.agy import AgyBackend

        return AgyBackend()

    raise BackendUnavailableError(
        f"unknown backend {backend_id!r}. Known backends: {', '.join(BACKEND_IDS)}"
    )


def resolve_chain(cfg: LLMSettings, *, needs_vision: bool = False) -> list[str]:
    """The ordered backends to try for one call.

    The active backend first, then ``failover_order`` minus duplicates. When
    the call carries images, backends that cannot transport them are dropped -
    a text-only backend handed an image does not fail, it answers *about
    nothing*, which is far worse than moving the call somewhere that can see.
    """
    chain: list[str] = [cfg.active_backend]
    chain += [b for b in cfg.failover_order if b not in chain]
    chain = [b for b in chain if b in BACKEND_IDS]

    if needs_vision:
        vision_capable = []
        for backend_id in chain:
            try:
                if build(backend_id, cfg).supports_vision:
                    vision_capable.append(backend_id)
                else:
                    logger.warning(
                        "backend %r cannot carry images - skipping it for this call", backend_id
                    )
            except BackendUnavailableError:
                continue
        chain = vision_capable

    return chain


def probe(cfg: LLMSettings, *, allowed: tuple[str, ...] | None = None) -> list[dict[str, object]]:
    """Local availability of the known backends, for the ``backends`` command
    and the settings page.

    ``allowed`` narrows the probe to one distribution's offer - a hosted
    installation passes :data:`API_BACKEND_IDS` so its page never advertises a
    CLI backend it cannot run. Does not call anything over the network: a
    backend is "available" when it could run here, not when it has been proven
    to answer.
    """
    rows: list[dict[str, object]] = []
    for backend_id in allowed or BACKEND_IDS:
        try:
            backend = build(backend_id, cfg)
        except BackendUnavailableError as exc:
            rows.append({"id": backend_id, "available": False, "detail": str(exc), "models": []})
            continue
        available = backend.available()
        row: dict[str, object] = {
            "id": backend_id,
            "available": available,
            "vision": backend.supports_vision,
            "models": backend.list_models() if available else [],
            "configured_model": cfg.model_for("default", backend_id),
        }
        if not available and hasattr(backend, "unavailable_detail"):
            row["detail"] = backend.unavailable_detail()
        rows.append(row)
    return rows
