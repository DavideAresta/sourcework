"""Finding something that will answer a model call.

The single most common way a local install fails is that nothing is listening.
The symptom - a run that dies several agents deep with a connection error - is
a long way from the cause, so this module asks the question up front and in the
operator's terms: *is there an engine, where, and what does it serve?*

Probing is loopback-only and deliberately dumb. Every candidate is an
OpenAI-compatible server on a conventional port, and the check is whether it
lists models. There is no attempt to identify a product beyond the port it
chose, because the answer that matters is "this URL works", not "this is
LM Studio".
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import httpx

logger = logging.getLogger(__name__)

# httpx logs every request at INFO. Probing five ports would print five lines of
# noise above a diagnostic whose whole job is to be readable.
logging.getLogger("httpx").setLevel(logging.WARNING)

PROBE_TIMEOUT_S = 0.6
"""Per candidate. Long enough for a loaded local server to answer, short enough
that probing five of them is not a pause anyone notices."""


@dataclass(frozen=True)
class Candidate:
    name: str
    base_url: str
    """What goes in ``SOURCEWORK_LLM__API_BASE`` - already including ``/v1``."""

    probe_url: str
    """What to GET. Kept separate because the two are not the same URL: Ollama
    is configured at ``/v1`` but lists its models at ``/api/tags``, and blindly
    appending ``/v1/models`` to a base that already ends in ``/v1`` asks for
    ``/v1/v1/models`` and gets a 404 that reads like "nothing is running"."""

    parse: str = "openai"
    """``openai`` for ``{"data": [{"id": ...}]}``; ``ollama`` for its own
    ``{"models": [{"name": ...}]}``."""


# Conventional ports, in the order a local install is most likely to have them.
# llama-swap first because it is what this project's own scripts start, and
# because it fronts several models where the others front one.
CANDIDATES: tuple[Candidate, ...] = (
    Candidate("llama-swap or llama-server", "http://127.0.0.1:8081/v1",
              "http://127.0.0.1:8081/v1/models"),
    Candidate("LM Studio", "http://127.0.0.1:1234/v1", "http://127.0.0.1:1234/v1/models"),
    Candidate("Ollama", "http://127.0.0.1:11434/v1", "http://127.0.0.1:11434/api/tags",
              parse="ollama"),
)


@dataclass
class Engine:
    """Something that answered."""

    name: str
    base_url: str
    models: list[str] = field(default_factory=list)
    configured: bool = False
    """True when this is the endpoint already in the settings, rather than one
    found by probing. The difference matters in a message: "your configured
    endpoint is not answering" is a different problem from "nothing is running"."""

    def summary(self) -> str:
        count = f"{len(self.models)} model(s)" if self.models else "no models listed"
        return f"{self.name} at {self.base_url} - {count}"


def _list_models(candidate: Candidate, timeout: float) -> list[str] | None:
    """Model ids, or None when nothing answered."""
    url = candidate.probe_url
    try:
        response = httpx.get(url, timeout=timeout)
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:  # noqa: BLE001 - every failure means "not here"
        logger.debug("no engine at %s: %s", url, exc)
        return None

    if candidate.parse == "ollama":
        entries = payload.get("models") or []
        return [str(e["name"]) for e in entries if isinstance(e, dict) and e.get("name")]
    entries = payload.get("data") or []
    return [str(e["id"]) for e in entries if isinstance(e, dict) and e.get("id")]


def probe(candidate: Candidate, timeout: float = PROBE_TIMEOUT_S) -> Engine | None:
    models = _list_models(candidate, timeout)
    if models is None:
        return None
    return Engine(name=candidate.name, base_url=candidate.base_url, models=models)


def detect(timeout: float = PROBE_TIMEOUT_S) -> Engine | None:
    """The first engine that answers, configured endpoint first.

    Returns None when nothing is reachable, which is the signal to onboard
    rather than to start and fail later.
    """
    from sourcework.config import settings

    configured = settings().llm.api_base
    if configured:
        # The configured base already ends in /v1 by convention, so the model
        # list hangs directly off it.
        found = probe(
            Candidate("configured endpoint", configured, configured.rstrip("/") + "/models"),
            timeout,
        )
        if found:
            found.configured = True
            return found
        logger.warning("configured endpoint %s did not answer", configured)

    for candidate in CANDIDATES:
        found = probe(candidate, timeout)
        if found:
            return found
    return None


def has_hosted_credentials() -> bool:
    """Is there a key for a hosted provider?

    Checked separately from the probes because a hosted provider cannot be
    detected - the absence of a key is the only local evidence there is, and its
    presence only means a run *might* work.
    """
    import os

    from sourcework.config import settings

    if settings().llm.api_key:
        return True
    return any(
        os.environ.get(name)
        for name in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "AZURE_API_KEY", "GEMINI_API_KEY")
    )


def report(timeout: float = PROBE_TIMEOUT_S) -> dict[str, object]:
    """Everything `sourcework doctor` and the first-run page need, in one call."""
    from sourcework.config import settings

    cfg = settings().llm
    engine = detect(timeout)
    return {
        "engine": engine,
        "backend": cfg.active_backend,
        "configured_base": cfg.api_base,
        "hosted_credentials": has_hosted_credentials(),
        "roles": {role: cfg.model_for(role) for role in ("default", "reasoning", "vision", "critic")},
        "probed": [c.base_url for c in CANDIDATES],
    }
