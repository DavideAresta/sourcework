"""Token and cost accounting for a stretch of work.

Every backend reports what it can about a completed call - tokens in, tokens
out, cache hits, sometimes a cost. That is worth keeping: with four backends in
play and three of them billing against a *subscription* rather than a card,
"which of these runs was expensive, and on whose account" stops being obvious.

Scope is one process. The agents are separate services, so an orchestrator's
ledger totals the orchestrator's own calls, not the mesh's - each agent keeps
its own and logs it. Making the totals travel would mean putting usage on every
A2A response payload, which is a bigger change than it is worth until someone
actually wants a per-run bill.

Costs are only summed within a unit. Claude Code reports what tokens *would*
have cost on the API, Copilot reports converted credits, LiteLLM reports the
provider's own figure - adding those together produces a number that means
nothing, so the summary keeps them apart.
"""

from __future__ import annotations

import contextlib
import logging
from collections import defaultdict
from collections.abc import Iterator
from contextvars import ContextVar
from dataclasses import dataclass, field

from sourcework.backends.base import LLMUsage

logger = logging.getLogger(__name__)


@dataclass
class BackendTotals:
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cost_by_unit: dict[str, float] = field(default_factory=lambda: defaultdict(float))
    credits: float = 0.0
    """Raw provider credits, kept next to the converted cost rather than
    replacing it - the conversion rate is ours and can change."""

    def add(self, usage: LLMUsage | None) -> None:
        self.calls += 1
        if usage is None:
            return
        self.input_tokens += usage.input_tokens or 0
        self.output_tokens += usage.output_tokens or 0
        self.cache_read_tokens += usage.cache_read_tokens or 0
        if usage.cost is not None and usage.cost_unit:
            self.cost_by_unit[usage.cost_unit] += usage.cost
        self.credits += usage.credits or 0.0


class UsageLedger:
    """Running totals, keyed by backend."""

    def __init__(self) -> None:
        self.by_backend: dict[str, BackendTotals] = defaultdict(BackendTotals)

    def record(self, backend: str, usage: LLMUsage | None) -> None:
        self.by_backend[backend].add(usage)

    def merge(self, serialised: dict) -> None:
        """Fold in another ledger's :meth:`as_dict`, from another process.

        The agents are separate services; this is how their spending reaches
        whoever asked for the run.
        """
        for backend, row in (serialised.get("backends") or {}).items():
            if not isinstance(row, dict):
                continue
            totals = self.by_backend[backend]
            totals.calls += int(row.get("calls") or 0)
            totals.input_tokens += int(row.get("input_tokens") or 0)
            totals.output_tokens += int(row.get("output_tokens") or 0)
            totals.cache_read_tokens += int(row.get("cache_read_tokens") or 0)
            totals.credits += float(row.get("credits") or 0)
            for unit, value in (row.get("cost") or {}).items():
                totals.cost_by_unit[unit] += float(value)

    @property
    def calls(self) -> int:
        return sum(t.calls for t in self.by_backend.values())

    def as_dict(self) -> dict[str, object]:
        return {
            "calls": self.calls,
            "backends": {
                backend: {
                    "calls": t.calls,
                    "input_tokens": t.input_tokens,
                    "output_tokens": t.output_tokens,
                    "cache_read_tokens": t.cache_read_tokens,
                    "cost": dict(t.cost_by_unit),
                    "credits": t.credits or None,
                }
                for backend, t in self.by_backend.items()
            },
        }

    def summary_line(self) -> str:
        if not self.by_backend:
            return "no LLM calls"
        chunks = []
        for backend, t in self.by_backend.items():
            cost = " ".join(f"{value:.4f} {unit}" for unit, value in t.cost_by_unit.items())
            chunks.append(
                f"{backend}: {t.calls} call(s), {t.input_tokens} in / {t.output_tokens} out"
                + (f", {cost}" if cost else "")
                + (f" ({t.credits:.3f} credits)" if t.credits else "")
            )
        return "; ".join(chunks)


_current: ContextVar[UsageLedger | None] = ContextVar("sourcework_usage_ledger", default=None)


def current() -> UsageLedger | None:
    return _current.get()


def record(backend: str, usage: LLMUsage | None) -> None:
    """Add one call to the active ledger, if there is one. A no-op otherwise."""
    ledger = _current.get()
    if ledger is not None:
        ledger.record(backend, usage)


@contextlib.contextmanager
def track(label: str = "") -> Iterator[UsageLedger]:
    """Collect usage for everything done inside the block.

    Nests: an inner block gets its own ledger and the outer one does not see
    those calls, which is what you want when timing one stage inside a run.
    """
    ledger = UsageLedger()
    token = _current.set(ledger)
    try:
        yield ledger
    finally:
        _current.reset(token)
        if ledger.calls:
            logger.info("LLM usage%s: %s", f" [{label}]" if label else "", ledger.summary_line())
