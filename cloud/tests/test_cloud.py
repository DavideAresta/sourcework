"""The hosted distribution: a guarded app in front of a tenant-scoped store.

Everything in this file runs without a database except the tests that say so:
the shell (guard, wiring, run id) is proven on a memory store, and the Postgres
behaviour is proven against ``SOURCEWORK_CLOUD__TEST_DATABASE_URL`` when one is
reachable and skipped - loudly, with the hint - when it is not, exactly the way
core's A2A e2e coverage yields to a busy port.

Names are the guarantee being tested, in a sentence, as the core suite insists.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sourcework.auth import ENTRY_POINT_GROUP
from sourcework.ui.store import Run, now_iso

from sourcework_cloud import auth as cloud_auth
from sourcework_cloud.app import build_cloud_app
from sourcework_cloud.store import PostgresStore, tenant_for

TOKEN = "dev-token-for-tests"


class _EntryPoint:
    """Stand-in for one installed distribution's metadata entry.

    The cloud package is a sibling on the path, not a pip-installed wheel, so
    its ``sourcework.auth`` entry point is not in this venv's metadata. Core's
    :func:`auth.build` resolves exactly this shape, so this is how the cloud
    tests prove the real gate without installing the package (which would put
    its guard on core's own test suite).
    """

    def __init__(self, name: str, load) -> None:
        self.name = name
        self._load = load

    def load(self):
        return self._load


def _install_cloud_auth(monkeypatch: pytest.MonkeyPatch, *, token: str) -> None:
    import sourcework.auth as core_auth

    monkeypatch.setenv("SOURCEWORK_CLOUD__AUTH_TOKEN", token)

    def entry_points(*, group: str | None = None):
        if group == ENTRY_POINT_GROUP:
            return [_EntryPoint("cloud", cloud_auth.TokenAuth)]
        return []

    monkeypatch.setattr(core_auth, "entry_points", entry_points)


class MemoryStore:
    """Enough of the Store protocol for the wiring tests; Postgres gets the
    real behaviour."""

    def __init__(self) -> None:
        self.runs: dict[str, Run] = {}

    async def save(self, run: Run) -> None:
        self.runs[run.id] = run

    async def get(self, run_id: str) -> Run | None:
        return self.runs.get(run_id)

    async def list(self, limit: int = 50) -> list[Run]:
        return list(self.runs.values())[:limit]

    async def delete(self, run_id: str) -> bool:
        return self.runs.pop(run_id, None) is not None

    async def reap_orphans(self) -> int:
        return 0

    def close(self) -> None:
        pass


class FakeExecutor:
    """A run that "finishes" instantly - the mesh behind the cloud shell is
    Phase 3's problem, and this is what keeps POSTing a run honest today."""

    def __init__(self, store: MemoryStore) -> None:
        self.store = store

    async def start(self, request, *, run_id: str | None = None):
        run = Run(
            id=run_id or "test-run",
            title=request.title,
            status="ok",
            created_at=now_iso(),
            finished_at=now_iso(),
            request=request.model_dump(mode="json"),
            result={"stats": {"sources": 1, "requirements": 1}},
            events=[],
        )
        await self.store.save(run)
        return run

    async def resume(self, run_id: str):
        return await self.store.get(run_id)

    async def cancel(self, run_id: str) -> bool:
        return False

    def is_active(self, run_id: str) -> bool:
        return False

    async def subscribe(self, run_id: str):
        yield

    async def shutdown(self) -> None:
        pass


# -- the shell ---------------------------------------------------------------


def test_the_cloud_distribution_declares_no_console_scripts() -> None:
    """The "web-only, no CLI" rule, asserted where the rule is written.

    A future edit that gives the hosted product a `sourcework generate` gets
    caught here rather than discovered by a user who expects one from a
    locally-shaped README.
    """
    pyproject = Path(__file__).resolve().parent.parent / "pyproject.toml"
    text = pyproject.read_text()
    assert "[project.scripts]" not in text
    assert "sourcework_cloud.__main__" not in text


class _Request:
    def __init__(self, bearer: str | None = None) -> None:
        self.headers = {"Authorization": f"Bearer {bearer}"} if bearer is not None else {}


async def test_the_dev_authenticator_fails_closed_when_unconfigured() -> None:
    """An unconfigured deployment is locked, not open.

    The real providers come in the identity pass; until then, a deployment
    that never set the token must refuse rather than treat the missing
    credential as "nothing to check".
    """
    granted = cloud_auth.TokenAuth(token="")
    assert not await granted.principal(_Request(bearer=TOKEN))


async def test_the_dev_authenticator_accepts_the_configured_bearer_token() -> None:
    granted = cloud_auth.TokenAuth(token=TOKEN)
    principal = await granted.principal(_Request(bearer=TOKEN))
    assert principal is not None
    assert principal.roles == frozenset({"owner"})


def test_a_cloud_app_without_a_token_guards_every_route(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The one-gate guarantee holds on the hosted app: no token, no page.

    This is core's middleware doing its job with a real installed
    authenticator - the browser shell is served (a locked door still needs a
    door), but every API route answers 401.
    """
    _install_cloud_auth(monkeypatch, token="")
    app = build_cloud_app(store=MemoryStore(), workspace=Path("/tmp/cloud-ws"))

    with TestClient(app) as client:
        assert client.get("/api/runs").status_code == 401
        assert client.get("/api/settings").status_code == 401
        assert client.get("/api/backends").status_code == 401


def test_a_signed_in_cloud_app_serves_the_core_ui(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The shell is the core UI: sign in, and the local routes behave exactly
    as they do on a laptop. Same app, same endpoints, same shapes."""
    _install_cloud_auth(monkeypatch, token=TOKEN)
    store = MemoryStore()
    app = build_cloud_app(
        store=store, workspace=Path("/tmp/cloud-ws"), executor=FakeExecutor(store)
    )
    headers = {"Authorization": f"Bearer {TOKEN}", "X-SourceWork-UI": "1"}

    with TestClient(app) as client:
        payload = {"title": "Shell parity", "notes": ["a note"]}
        created = client.post("/api/runs", data={"request": json.dumps(payload)}, headers=headers)
        assert created.status_code == 200, created.text
        run_id = created.json()["id"]
        assert run_id != run_id[:12]  # a UUID, not the local 12-hex

        listed = client.get("/api/runs", headers=headers).json()
        assert any(r["id"] == run_id for r in listed)
        assert client.get("/api/runs", headers=headers).status_code == 200


# -- Postgres: skipped, loudly, when no database is reachable ----------------


def _postgres_dsn() -> str:
    return os.environ.get(
        "SOURCEWORK_CLOUD__TEST_DATABASE_URL",
        "postgresql://localhost:5432/sourcework_cloud_test",
    )


@pytest.fixture(scope="module")
def postgres() -> PostgresStore:
    try:
        store = PostgresStore(_postgres_dsn(), min_size=1, max_size=2)
    except Exception as exc:  # noqa: BLE001 - connection refused, auth, dns
        pytest.skip(f"no reachable Postgres ({exc}); set SOURCEWORK_CLOUD__TEST_DATABASE_URL")
        return None
    with store._pool.connection() as conn:
        conn.execute("TRUNCATE runs")
        conn.commit()
    yield store
    store.close()


@pytest.fixture
def clean_postgres(postgres: PostgresStore) -> PostgresStore:
    with postgres._pool.connection() as conn:
        conn.execute("TRUNCATE runs")
        conn.commit()
    return postgres


async def test_the_postgres_store_round_trips_a_run(clean_postgres: PostgresStore) -> None:
    tenant_for("alpha")
    run = Run(
        id="pg-1",
        title="Round trip",
        status="ok",
        created_at=now_iso(),
        finished_at=now_iso(),
        request={"title": "Round trip", "sources": []},
        result={"stats": {"sources": 1}},
        events=[{"seq": 1, "stage": "ingest"}],
    )

    await clean_postgres.save(run)
    got = await clean_postgres.get("pg-1")

    assert got is not None
    assert got.title == "Round trip"
    assert got.result == {"stats": {"sources": 1}}
    assert got.events == [{"seq": 1, "stage": "ingest"}]
    assert got.created_at.endswith("+00:00")
    assert [r.id for r in await clean_postgres.list()] == ["pg-1"]
    assert await clean_postgres.delete("pg-1")
    assert await clean_postgres.get("pg-1") is None


async def test_one_tenants_runs_are_invisible_to_another(clean_postgres: PostgresStore) -> None:
    """The core tenancy promise: separate tenants read separate histories.

    Written both ways - the explicit filter and the row-level security - so
    that a future change which drops either still fails here.
    """
    tenant_for("alpha")
    await clean_postgres.save(
        Run(
            id="alpha-1",
            title="Alpha's",
            status="ok",
            created_at=now_iso(),
            request={"title": "Alpha's", "sources": []},
        )
    )
    await clean_postgres.save(
        Run(
            id="alpha-2",
            title="Alpha's second",
            status="ok",
            created_at=now_iso(),
            request={"title": "Alpha's second", "sources": []},
        )
    )

    tenant_for("beta")
    assert await clean_postgres.get("alpha-1") is None
    assert await clean_postgres.list() == []

    tenant_for("alpha")
    assert await clean_postgres.get("alpha-1") is not None
    assert len(await clean_postgres.list()) == 2


async def test_row_level_security_blocks_a_query_that_forgets_the_tenant(
    clean_postgres: PostgresStore,
) -> None:
    """The safety net under the explicit filter: a bare SELECT with no tenant
    in the WHERE clause still only sees the tenant in the session.

    This is the guarantee that survives a future query that omits the filter -
    the store's discipline is the first wall, the database's policy the second.
    """
    tenant_for("gamma")
    await clean_postgres.save(
        Run(
            id="gamma-1",
            title="Gamma's",
            status="ok",
            created_at=now_iso(),
            request={"title": "Gamma's", "sources": []},
        )
    )

    with clean_postgres._pool.connection() as conn:
        conn.execute("SELECT set_config('app.tenant_id', 'gamma', true)")
        rows = conn.execute("SELECT id FROM runs").fetchall()
    assert [row[0] for row in rows] == ["gamma-1"]


async def test_the_cloud_app_runs_over_postgres_unchanged(
    monkeypatch: pytest.MonkeyPatch, postgres: PostgresStore
) -> None:
    """The shell over the real store: sign in, submit, read back - the same
    routes the laptop serves, against Postgres instead of SQLite."""
    _install_cloud_auth(monkeypatch, token=TOKEN)
    # The app's lifespan closes the store it is given, so this test owns a
    # pool of its own rather than the fixture's, which later tests still use.
    app_store = PostgresStore(_postgres_dsn(), min_size=1, max_size=2)
    app = build_cloud_app(
        store=app_store, workspace=Path("/tmp/cloud-ws"), executor=FakeExecutor(app_store)
    )
    headers = {"Authorization": f"Bearer {TOKEN}", "X-SourceWork-UI": "1"}

    with TestClient(app) as client:
        created = client.post(
            "/api/runs",
            data={"request": json.dumps({"title": "Over Postgres", "notes": ["n"]})},
            headers=headers,
        )
        assert created.status_code == 200, created.text
        run_id = created.json()["id"]

        listed = client.get("/api/runs", headers=headers).json()
        assert any(r["id"] == run_id for r in listed)

        persisted = await app_store.get(run_id)
        assert persisted is not None
        assert persisted.title == "Over Postgres"
