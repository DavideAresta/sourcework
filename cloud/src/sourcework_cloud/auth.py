"""Who is asking, on the hosted distribution.

Phase 0 ships a development stand-in: a bearer token in the operator's own
environment, because the *seam* is what this phase proves, not the identity
provider. Google and GitHub login (OpenID Connect for the former, OAuth2 with
a userinfo call for the latter) replace this in the identity pass - the
provider's job is exactly what this class does: turn the request into a
:class:`~sourcework.auth.Principal`, or into ``None``.

The shape is deliberate and matches core's contract. ``None`` means *refuse*,
never *carry on anonymously*; the core middleware owns enforcement, so a route
added in the future cannot accidentally open a hole. The fail-closed default
matters here more than on a laptop: a hosted service that stops requiring
login is a hosted service showing strangers everyone's documents.
"""

from __future__ import annotations

import os

from sourcework.auth import Principal

TOKEN_ENV = "SOURCEWORK_CLOUD__AUTH_TOKEN"
"""Phase 0's stand-in for the OIDC pass. Empty means nobody can get in."""


class TokenAuth:
    """Lets through exactly the bearer token the operator configured.

    A real provider would verify a JWT against its signing keys; this compares
    a constant. The rest - mapping the claim to a :class:`Principal`, refusing
    when there is nothing to map, failing closed when unconfigured - is the
    shape that survives the swap.

    The token is read when the class is *constructed*, which is when core's
    :func:`sourcework.auth.build` instantiates the installed plugin, so the
    check happens against the environment of the app that is actually about
    to serve, not of whatever process happened to import the module first.
    """

    id = "cloud-dev-token"

    def __init__(
        self, token: str | None = None, email: str = "operator@sourcework.example"
    ) -> None:
        self.token = os.environ.get(TOKEN_ENV, "") if token is None else token
        self.email = email

    async def principal(self, request) -> Principal | None:
        if not self.token:
            return None  # unconfigured is locked, not open
        authz = request.headers.get("Authorization", "")
        if authz != f"Bearer {self.token}":
            return None
        return Principal(
            id=self.email, name="Operator", email=self.email, roles=frozenset({"owner"})
        )

    def challenge(self) -> dict[str, str]:
        return {"WWW-Authenticate": "Bearer"}
