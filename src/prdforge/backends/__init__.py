"""The backend registry.

Which backends exist, how to build one from settings, and which order to try
them in. Everything is constructed on demand and imported lazily so that adding
a backend never slows down an agent that will not use it.
"""

from __future__ import annotations

import logging

from prdforge.backends.base import (
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
from prdforge.config import BACKEND_IDS, LLMSettings, normalise_backend

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
        from prdforge.backends.litellm_backend import LiteLLMBackend

        return LiteLLMBackend(api_base=cfg.api_base, api_key=cfg.api_key)
    if wanted == "claude-code":
        from prdforge.backends.claude_code import ClaudeCodeBackend

        return ClaudeCodeBackend()
    if wanted == "opencode-cli":
        from prdforge.backends.opencode import OpenCodeBackend

        return OpenCodeBackend(pure=cfg.opencode_pure)
    if wanted == "copilot-cli":
        from prdforge.backends.copilot import CopilotBackend

        return CopilotBackend(home=cfg.copilot_home)

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


def probe(cfg: LLMSettings) -> list[dict[str, object]]:
    """Local availability of every known backend, for the ``backends`` command.

    Does not call anything over the network: a backend is "available" when it
    could run here, not when it has been proven to answer.
    """
    rows: list[dict[str, object]] = []
    for backend_id in BACKEND_IDS:
        try:
            backend = build(backend_id, cfg)
        except BackendUnavailableError as exc:
            rows.append({"id": backend_id, "available": False, "detail": str(exc), "models": []})
            continue
        available = backend.available()
        rows.append(
            {
                "id": backend_id,
                "available": available,
                "vision": backend.supports_vision,
                "models": backend.list_models() if available else [],
                "configured_model": cfg.model_for("default", backend_id),
            }
        )
    return rows
