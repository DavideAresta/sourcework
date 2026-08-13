"""Where a finished PRD can be sent, and how to find more places.

Publishing already goes over A2A: the orchestrator calls ``publish_prd`` on the
Confluence agent, exactly as it calls ``extract_document`` on the ingestor. So a
second destination - Jira, Azure DevOps, a wiki nobody here has heard of - is
another agent, not another branch in the pipeline. Everything needed to build
one already exists; the only thing missing was a way to *name* it.

That is what this is. A target is an id, the agent that serves it and the skill
to call, which is the same shape :data:`~.pipeline.ROUTES` uses to send each
modality to the agent that handles it.

Out-of-tree targets register through the ``sourcework.publishers`` entry point,
so a publisher can be pip-installed beside SourceWork without editing it::

    [project.entry-points."sourcework.publishers"]
    jira = "sourcework_jira:TARGET"

A broken or missing plugin is logged and skipped rather than raised. An
installation that is half-configured should still publish to the places that do
work, and a run that got as far as having a document is not one to throw away
over a destination.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from importlib.metadata import entry_points

logger = logging.getLogger(__name__)

ENTRY_POINT_GROUP = "sourcework.publishers"

DEFAULT_SKILL = "publish_prd"
"""What a publishing agent advertises. Named rather than assumed so the
orchestrator can check the card before calling, the way it already does for
every other skill."""


@dataclass(frozen=True)
class PublishTarget:
    """One destination: which agent to ask, and what to ask it for."""

    id: str
    agent: str
    skill: str = DEFAULT_SKILL
    label: str = ""

    def __post_init__(self) -> None:
        if not self.id or not self.agent:
            raise ValueError("a publish target needs an id and an agent")

    @property
    def name(self) -> str:
        return self.label or self.id.replace("-", " ").title()


CONFLUENCE = PublishTarget(id="confluence", agent="confluence", label="Confluence")

CORE: tuple[PublishTarget, ...] = (CONFLUENCE,)
"""Shipped in this repository. Confluence stays here rather than becoming the
first plugin: the connector is written, tested and used, and moving it out to
prove the seam works would cost every existing user a second install."""


def targets() -> dict[str, PublishTarget]:
    """Every destination this installation knows about, core first.

    A plugin cannot displace a core target by claiming its id. Shadowing
    ``confluence`` would let an installed package silently redirect published
    documents somewhere else, and there is no reading of that which is a
    feature.
    """
    found = {t.id: t for t in CORE}
    for target in _from_entry_points():
        if target.id in found:
            logger.warning(
                "publisher plugin %r shadows a built-in target and was ignored", target.id
            )
            continue
        found[target.id] = target
    return found


def get(target_id: str | None) -> PublishTarget | None:
    """The named target, or ``None``. Unset means Confluence.

    Defaulting keeps every request written before targets existed working
    unchanged - ``publish=True`` meant Confluence, and still does.
    """
    return targets().get(target_id or CONFLUENCE.id)


def _from_entry_points() -> list[PublishTarget]:
    try:
        discovered = entry_points(group=ENTRY_POINT_GROUP)
    except Exception:  # noqa: BLE001 - a broken environment is not a broken run
        logger.warning("could not read %s entry points", ENTRY_POINT_GROUP, exc_info=True)
        return []

    found: list[PublishTarget] = []
    for entry in discovered:
        try:
            value = entry.load()
        except Exception:  # noqa: BLE001 - one bad plugin must not hide the rest
            logger.warning("publisher plugin %r failed to load", entry.name, exc_info=True)
            continue
        # One target or several: a package fronting two systems should not need
        # two entry points to say so.
        candidates = value if isinstance(value, (list, tuple, set)) else [value]
        for candidate in candidates:
            if isinstance(candidate, PublishTarget):
                found.append(candidate)
            else:
                logger.warning(
                    "publisher plugin %r gave %s, not a PublishTarget",
                    entry.name,
                    type(candidate).__name__,
                )
    return found
