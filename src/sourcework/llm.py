"""Provider-agnostic LLM access.

Every agent talks to models through :class:`LLM`. What sits behind it is a
config change, never a code change - and as of the backend layer that is true
of more than just the provider:

* **An HTTPS API**, via LiteLLM. Claude, GPT, Azure, Bedrock, Vertex, a local
  Ollama. Needs credentials.
* **A coding CLI** - ``claude``, ``opencode`` or ``copilot`` - driven as a
  subprocess in print mode. These carry *their own* authentication, so a
  developer already signed into Claude Code, or an OpenCode user with providers
  configured, runs the entire pipeline with no API key anywhere. Backend
  implementations live in :mod:`sourcework.backends`.

Four things are worth calling out:

* ``structured()`` returns a validated Pydantic object. It asks for JSON, and
  on a parse or validation failure it feeds the error back to the model and
  retries. Agents therefore never handle raw strings.
* **Failover.** When the active backend reports a usage limit - or exits
  cleanly having said nothing - the call moves to the next backend in
  ``SOURCEWORK_LLM__FAILOVER_ORDER`` rather than failing. The model does *not*
  travel with it: each backend uses the model configured for it, or its own
  default, because a model id from one backend is nonsense to another.
* **Images pick the backend.** A call carrying images only considers backends
  that can transport them. A text-only backend handed an image does not error,
  it answers about nothing - a far worse outcome than moving the call.
* ``stub=True`` short-circuits the whole thing with a deterministic response
  derived from the requested schema. That is what makes the end-to-end test
  runnable with no API key, no network, and no CLI installed.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from sourcework import stream
from sourcework import usage as usage_ledger
from sourcework.backends import build, resolve_chain
from sourcework.backends.base import (
    BackendError,
    BackendRequest,
    EmptyBackendResponseError,
    ImageInput,
    OutputTruncatedError,
)
from sourcework.config import LLMSettings, effective_llm

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

_JSON_BLOCK = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)

_THINK_BLOCK = re.compile(
    r"<(think|thinking|reasoning)>.*?</\1>|^.*?</(?:think|thinking|reasoning)>",
    re.DOTALL | re.IGNORECASE,
)
"""A reasoning model's scratchpad, when the server leaves it in ``content``
instead of splitting it into ``reasoning_content``.

Stripped before the JSON hunt because that hunt is positional - first ``{`` to
last ``}`` - and a model that reasons *about* the schema writes braces while it
does so. The second alternative catches the common truncated case where the
opening tag never arrives but the closing one does."""

__all__ = ["LLM", "ImageInput", "LLMError", "register_stub"]


class LLMError(RuntimeError):
    pass


def _extract_json(text: str) -> str:
    """Models like to wrap JSON in prose or fences. Dig it out."""
    text = _THINK_BLOCK.sub("", text, count=1).strip()
    m = _JSON_BLOCK.search(text)
    if m:
        return m.group(1).strip()
    start = min((i for i in (text.find("{"), text.find("[")) if i != -1), default=-1)
    if start == -1:
        return text
    end = max(text.rfind("}"), text.rfind("]"))
    return text[start : end + 1] if end > start else text[start:]


class LLM:
    def __init__(self, cfg: LLMSettings | None = None, role: str = "default") -> None:
        self._cfg = cfg
        self.role = role

    @property
    def cfg(self) -> LLMSettings:
        """Resolved per call, not at construction.

        Agents build their ``LLM`` once, when the executor is created - long
        before any request arrives. Freezing the settings there would make
        per-run overrides (:func:`sourcework.config.llm_overrides`) invisible to
        every agent in the mesh. An explicitly passed ``cfg`` still wins.
        """
        return self._cfg if self._cfg is not None else effective_llm()

    # -- public ------------------------------------------------------------

    async def text(
        self,
        system: str,
        user: str,
        *,
        images: list[ImageInput] | None = None,
        role: str | None = None,
        max_tokens: int | None = None,
    ) -> str:
        if self.cfg.active_backend == "stub":
            return _stub_text(system, user)
        return await self._call(system, user, images or [], role or self.role, max_tokens)

    async def structured(
        self,
        system: str,
        user: str,
        schema: type[T],
        *,
        images: list[ImageInput] | None = None,
        role: str | None = None,
        max_tokens: int | None = None,
    ) -> T:
        """Ask for JSON matching ``schema`` and return a validated instance."""
        if self.cfg.active_backend == "stub":
            return _stub_structured(schema, system, user)

        schema_dict = schema.model_json_schema()
        schema_json = json.dumps(schema_dict, indent=2)
        sys_prompt = (
            f"{system}\n\n"
            "Respond with a single JSON object and nothing else. No prose, no "
            "markdown fences. It must validate against this JSON Schema:\n"
            f"{schema_json}"
        )

        # The prompt keeps the schema either way: a backend that cannot enforce
        # one still has to be told what to write, and enforcement is not
        # guidance - a grammar makes the *shape* inevitable, not the content
        # correct.
        enforced = schema_dict if self.cfg.constrained_json else None

        last_error: str | None = None
        for attempt in range(self.cfg.max_retries):
            prompt = user if last_error is None else (
                f"{user}\n\nYour previous answer was rejected: {last_error}\n"
                "Return corrected JSON only."
            )
            raw = await self._call(
                sys_prompt,
                prompt,
                images or [],
                role or self.role,
                max_tokens,
                json_schema=enforced,
                schema_name=schema.__name__,
            )
            try:
                return schema.model_validate_json(_extract_json(raw))
            except (ValidationError, json.JSONDecodeError, ValueError) as exc:
                last_error = str(exc)[:1500]
                logger.warning(
                    "structured() attempt %d/%d failed for %s: %s",
                    attempt + 1,
                    self.cfg.max_retries,
                    schema.__name__,
                    last_error,
                )
        raise LLMError(f"Could not obtain valid {schema.__name__}: {last_error}")

    # -- internals ---------------------------------------------------------

    async def _call(
        self,
        system: str,
        user: str,
        images: list[ImageInput],
        role: str,
        max_tokens: int | None,
        *,
        json_schema: dict[str, Any] | None = None,
        schema_name: str | None = None,
    ) -> str:
        chain = resolve_chain(self.cfg, needs_vision=bool(images))
        if not chain:
            raise LLMError(
                "no usable backend for this call"
                + (
                    " - it carries images and no configured backend can transport them"
                    if images
                    else f" (backend={self.cfg.active_backend!r})"
                )
            )

        failures: list[str] = []
        for index, backend_id in enumerate(chain):
            try:
                backend = build(backend_id, self.cfg)
            except BackendError as exc:
                failures.append(f"{backend_id}: {exc}")
                continue

            if not backend.available():
                failures.append(f"{backend_id}: not usable here (CLI missing, or not installed)")
                continue

            # The model belongs to the backend, not to the call. Carrying the
            # running model onto a failover target is how the backend that was
            # meant to rescue the call fails with "unknown model".
            model = self.cfg.model_for(role, backend_id)
            request = BackendRequest(
                system=system,
                user=user,
                images=images,
                model=model,
                max_tokens=max_tokens or self.cfg.max_tokens,
                temperature=self.cfg.temperature,
                timeout_s=self.cfg.timeout_for(backend_id),
                effort=self.cfg.effort,
                json_schema=json_schema,
                schema_name=schema_name,
                # None unless someone upstream asked to watch this run. Set, the
                # backend narrates itself as it works; the agents in between
                # never learn that the feature exists.
                on_chunk=stream.current_sink(),
            )

            logger.debug(
                "%s call: backend=%s model=%s (attempt %d of %d)",
                role,
                backend_id,
                model or "(backend default)",
                index + 1,
                len(chain),
            )

            result = None
            # An empty answer is worth one more go at the SAME backend before
            # moving on: it is usually a reasoning-heavy model that spent its
            # output budget thinking, not a backend that cannot answer. Every
            # other failure moves straight to the next backend, because
            # repeating a quota error or a timeout only costs time.
            for attempt in range(1 + max(0, self.cfg.empty_retries)):
                try:
                    result = await backend.generate(request)
                    break
                except OutputTruncatedError as exc:
                    # Not a backend problem - the answer was too big for the
                    # budget. Another backend with the same budget repeats it.
                    usage_ledger.record(backend_id, exc.usage)
                    raise LLMError(str(exc)) from exc
                except EmptyBackendResponseError as exc:
                    usage_ledger.record(backend_id, exc.usage)
                    retries_left = attempt < self.cfg.empty_retries
                    logger.warning(
                        "backend %r returned nothing (%s)%s",
                        backend_id,
                        exc,
                        " - retrying once" if retries_left else "",
                    )
                    if not retries_left:
                        failures.append(f"{backend_id}: returned no content ({exc})")
                except BackendError as exc:
                    usage_ledger.record(backend_id, exc.usage)
                    failures.append(f"{backend_id}: {exc}")
                    break

            if result is None:
                more = index + 1 < len(chain)
                logger.warning(
                    "backend %r could not answer%s",
                    backend_id,
                    " - trying the next configured backend" if more else " - none left to try",
                )
                continue

            usage_ledger.record(backend_id, result.usage)
            if index > 0:
                logger.warning(
                    "backend failover: %s -> %s. Billing differs per backend. Earlier failures: %s",
                    chain[0],
                    backend_id,
                    "; ".join(failures),
                )
            return result.text

        detail = " | ".join(failures)
        if all("returned no content" in f for f in failures):
            # The most common way this happens is a reasoning-heavy model at
            # high effort spending its whole output budget thinking. Saying so
            # is the difference between a fixable run and a mystery.
            detail += (
                ". A backend that exits cleanly with nothing is usually a reasoning model "
                "that spent its output budget on thinking - lower the reasoning effort, "
                "pick a different model for this role, or configure a failover backend."
            )
        raise LLMError("every configured backend failed: " + detail)


# ---------------------------------------------------------------------------
# Stub mode
# ---------------------------------------------------------------------------


def _stub_text(system: str, user: str) -> str:
    return f"[stub] {user.strip()[:280]}"


def _stub_structured(schema: type[T], system: str, user: str) -> T:
    """Build a minimally-valid instance of ``schema``.

    Registered builders produce something recognisable for the models that
    matter to the pipeline; everything else falls back to defaults.
    """
    builder = _STUB_BUILDERS.get(schema.__name__)
    if builder is not None:
        return schema.model_validate(builder(user))
    try:
        return schema()  # type: ignore[call-arg]
    except ValidationError as exc:  # pragma: no cover
        raise LLMError(
            f"No stub builder for {schema.__name__} and it has required fields: {exc}"
        ) from exc


def _stub_evidence_payload(user: str) -> dict[str, Any]:
    # Treat each non-empty line of the prompt tail as one claim.
    body = user.split("<<<CONTENT>>>")[-1]
    lines = [ln.strip("-* \t") for ln in body.splitlines() if len(ln.strip()) > 20][:12]
    return {
        "summary": f"[stub] {len(lines)} claims extracted.",
        "items": [
            {"text": ln, "locator": f"line {i + 1}", "kind": "statement", "confidence": 0.6}
            for i, ln in enumerate(lines)
        ],
        "warnings": ["LLM stub mode - content was not actually analysed."],
    }


_STUB_BUILDERS: dict[str, Any] = {
    "EvidenceDraft": _stub_evidence_payload,
}


def register_stub(schema_name: str, builder: Any) -> None:
    """Let a module teach the stub how to fake its own schema."""
    _STUB_BUILDERS[schema_name] = builder
