"""The hosted app: core's UI wrapped in the seams a service needs.

Nothing here edits core - the web app is :func:`sourcework.ui.app.build_app`
called with the arguments a hosted deployment passes: a Postgres store, UUID
run ids (the local 12-hex collide at service scale), and an authorizer that
resolves which tenant a signed-in principal acts for. The authenticator is
installed as a ``sourcework.auth`` entry point, exactly as core expects, so
the one-gate guarantee (add a route, it is still guarded) holds unchanged.

The authorizer is where tenancy enters the request path: core resolves who is
asking, then this policy says what tenant that maps to before the route reads
the store. Phase 0 is one tenant for everyone; the identity pass replaces the
constant with the principal's org.
"""

from __future__ import annotations

import os
import tempfile
import uuid
from pathlib import Path

from sourcework.ui.app import build_app

from sourcework_cloud.store import DEFAULT_TENANT, PostgresStore, tenant_for

DATABASE_URL_ENV = "SOURCEWORK_CLOUD__DATABASE_URL"
WORKSPACE_ENV = "SOURCEWORK_CLOUD__WORKSPACE"


def _dsn() -> str:
    dsn = os.environ.get(DATABASE_URL_ENV)
    if not dsn:
        raise RuntimeError(
            f"{DATABASE_URL_ENV} is not set. Point the cloud service at its Postgres "
            "database - there is no local fallback here, on purpose."
        )
    return dsn


def _default_workspace() -> Path:
    """Where uploads land until object storage replaces the shared volume.

    Not durable by design - the workspace is only staging for files the agents
    will ingest, and the run store (the durable half) lives in Postgres.
    """
    return Path(os.environ.get(WORKSPACE_ENV) or tempfile.mkdtemp(prefix="sourcework-cloud-"))


def build_cloud_app(
    store: PostgresStore | None = None,
    workspace: Path | None = None,
    *,
    executor: object | None = None,
):
    """The hosted SourceWork service. ``store``/``workspace``/``executor`` are
    injectable for tests; production reads them from the environment."""

    async def policy(request, principal, method) -> bool:
        # The tenant hook. Phase 0 is one tenant for everybody - the point of
        # this phase is the shell, not the tenancy. The identity pass maps the
        # principal's organisation here and layers role checks on top.
        tenant_for(DEFAULT_TENANT)
        return True

    return build_app(
        workspace=workspace or _default_workspace(),
        store=store or PostgresStore(_dsn()),
        executor=executor,
        authorizer=policy,
        run_id_factory=lambda: str(uuid.uuid4()),
    )


def serve(host: str = "0.0.0.0", port: int = 8080) -> None:
    """Run the service. An ops entry point, not a user CLI - the hosted
    distribution has exactly one door, and it is the browser."""
    import uvicorn

    uvicorn.run(build_cloud_app(), host=host, port=port)
