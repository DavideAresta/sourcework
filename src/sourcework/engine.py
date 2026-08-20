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

import asyncio
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

    # The endpoint the *active backend* will use, not `api_base` alone. Reading
    # only the latter meant an installation on llama-cpp (or azure, or openai)
    # had no configured endpoint as far as this function was concerned, so it
    # fell through to the candidate list and reported whatever else was
    # listening - a server the run would never call. See
    # `LLMSettings.endpoint_for`.
    configured = settings().llm.endpoint_for()
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
    configured = cfg.endpoint_for()
    return {
        "engine": engine,
        "backend": cfg.active_backend,
        "configured_base": configured,
        # Stated separately from `engine`, because "something answered" and
        # "the thing you configured answered" are different facts and only the
        # second one predicts whether a run will work. When they disagree, the
        # caller has to say so rather than present the stand-in as the answer.
        "configured_reachable": bool(engine and engine.configured),
        "hosted_credentials": has_hosted_credentials(),
        "roles": {role: cfg.model_for(role) for role in ("default", "reasoning", "vision", "critic")},
        "probed": [c.base_url for c in CANDIDATES],
    }


async def preflight(timeout: float = PROBE_TIMEOUT_S) -> str | None:
    """Why no configured backend can answer this run, or None if one can.

    Run once, before a run touches a source. Without it the first thing a dead
    model server meets is the extraction of source 1, which spends
    ``litellm_retries`` attempts at ``timeout_s`` each discovering that nothing
    is listening, and then does it again for sources 2 to 5 - so "the server is
    not running" arrives as a wall of stack traces many minutes late. The
    information was available in two seconds.

    Deliberately conservative: it reports a problem only when *every* backend in
    the chain is ruled out, and only on evidence. A backend with no endpoint to
    probe (the CLIs, bedrock, vertex) counts as usable whenever ``available()``
    says so - guessing beyond that would block runs that would have worked,
    which is a worse failure than the one this prevents.
    """
    from sourcework.backends import BackendUnavailableError, build, resolve_chain
    from sourcework.config import effective_llm

    cfg = effective_llm()
    if cfg.active_backend == "stub":
        return None

    reasons: list[str] = []
    for backend_id in resolve_chain(cfg):
        try:
            backend = build(backend_id, cfg)
        except BackendUnavailableError as exc:
            reasons.append(f"{backend_id}: {exc}")
            continue

        if not backend.available():
            detail_for = getattr(backend, "unavailable_detail", None)
            detail = detail_for() if callable(detail_for) else ""
            reasons.append(f"{backend_id}: {detail or 'not usable here'}")
            continue

        endpoint = cfg.endpoint_for(backend_id)
        if endpoint is None:
            return None  # nothing to probe, and available() already said yes

        # Off the loop: `probe` is synchronous httpx, and this runs inside the
        # orchestrator's event loop alongside seven live A2A connections.
        candidate = Candidate(backend_id, endpoint, endpoint.rstrip("/") + "/models")
        if await asyncio.to_thread(probe, candidate, timeout) is not None:
            return None
        reasons.append(f"{backend_id}: nothing is listening at {endpoint}")

    if not reasons:
        return "no backend is configured for this run"
    return "no configured backend can answer - " + "; ".join(reasons)
