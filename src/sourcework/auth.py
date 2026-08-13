"""Who is asking, when anybody is.

SourceWork binds to loopback and serves one operator, so core ships no login and
needs none: :class:`NullAuth` says "you are the person at this machine" and
every route behaves exactly as it did before this module existed.

The seam exists because *enforcement* belongs here and *identity* does not. An
installation that puts this on a network needs sign-in, and the wrong way to add
it is a package that reaches in and patches every route - the day someone adds a
route and forgets, it is unauthenticated and nothing says so. So core owns the
rule (every request resolves a principal, and an unresolved one is refused) and
an :data:`ENTRY_POINT_GROUP` plugin owns the answer.

**Ambiguity fails closed.** Two authenticators installed is an operator mistake
with two possible readings, and the safe one is not "carry on unauthenticated" -
that is precisely the configuration where an open UI would be least expected and
most damaging. It raises at start-up instead, before anything is served.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from importlib.metadata import entry_points
from typing import Any, Protocol, runtime_checkable

logger = logging.getLogger(__name__)

ENTRY_POINT_GROUP = "sourcework.auth"


@dataclass(frozen=True)
class Principal:
    """Whoever this request is acting as."""

    id: str
    name: str = ""
    email: str = ""
    roles: frozenset[str] = field(default_factory=frozenset)

    @property
    def display(self) -> str:
        return self.name or self.email or self.id

    def has(self, role: str) -> bool:
        return role in self.roles


LOCAL = Principal(id="local", name="You", roles=frozenset({"owner"}))
"""The single operator of a loopback installation.

Given the ``owner`` role so that code written against roles works unchanged on
an installation that has no notion of them - the alternative is every future
check needing an "unless auth is off" branch, which is the thing this module
exists to avoid."""


@runtime_checkable
class Authenticator(Protocol):
    """Resolves a request to a principal, or to nobody.

    ``None`` means *refuse*, not *carry on anonymously*. A returned principal is
    trusted from here on, so whatever validates a token or a session does it
    behind this method and not after it.
    """

    id: str

    async def principal(self, request: Any) -> Principal | None:  # noqa: ANN401
        ...

    def challenge(self) -> dict[str, str]:
        """Headers to send with a refusal - ``WWW-Authenticate``, a redirect for
        an interactive flow. Empty is a bare 401, which is correct for an API
        and unhelpful for a browser, so an interactive plugin should fill it."""
        ...


class NullAuth:
    """No sign-in, because there is nobody else here.

    Never returns ``None``: on a loopback single-user installation an
    unauthenticated request is not a thing that exists, and pretending otherwise
    would put a 401 in front of an app with no way to answer it.
    """

    id = "none"

    async def principal(self, request: Any) -> Principal:  # noqa: ANN401, ARG002
        return LOCAL

    def challenge(self) -> dict[str, str]:
        return {}


class AmbiguousAuthenticator(RuntimeError):
    """More than one authenticator is installed and none can be preferred."""


def build() -> Authenticator:
    """The authenticator this installation runs with.

    Called once, at application build time, so a misconfiguration is a start-up
    failure rather than a surprise on the first request that matters.
    """
    installed = _from_entry_points()
    if not installed:
        return NullAuth()
    if len(installed) > 1:
        names = ", ".join(sorted(name for name, _ in installed))
        raise AmbiguousAuthenticator(
            f"{len(installed)} authenticators are installed ({names}) and only one can be "
            f"in force. Uninstall the ones you do not want - continuing would mean guessing "
            f"which of them is supposed to be protecting this installation."
        )
    name, authenticator = installed[0]
    logger.info("authentication provided by %r", name)
    return authenticator


def _from_entry_points() -> list[tuple[str, Authenticator]]:
    """Every installed authenticator.

    Failures raise rather than being skipped, which is the opposite of the rule
    for publishers: a publisher that will not load costs a destination, and an
    authenticator that will not load costs the lock on the door.
    """
    found: list[tuple[str, Authenticator]] = []
    for entry in entry_points(group=ENTRY_POINT_GROUP):
        value = entry.load()
        authenticator = value() if isinstance(value, type) else value
        if not isinstance(authenticator, Authenticator):
            raise TypeError(
                f"authentication plugin {entry.name!r} gave "
                f"{type(authenticator).__name__}, which is not an Authenticator"
            )
        found.append((entry.name, authenticator))
    return found
