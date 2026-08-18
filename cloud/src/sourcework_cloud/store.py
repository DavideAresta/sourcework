"""Runs on Postgres, scoped to a tenant.

The UI's :class:`Store` protocol is six methods; this is the hosted
implementation of them, shaped like :class:`RunStore` so the two are
interchangeable behind the same routes. Where SQLite is one file for one
operator, Postgres is shared: every row carries ``tenant_id`` and every query
is run inside a transaction that first sets the tenant in the session. The
database enforces the same rule again with row-level security, so a query that
forgets the filter is refused rather than leaking - an empty result is a bug
you notice, a cross-tenant read is one you might not.

The tenant comes from a context variable filled by the request path, exactly
the way core threads per-run model choices. A query with no tenant set gets
nothing back, which is the safe direction to fail.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

from sourcework.ui.store import IN_FLIGHT, Run

logger = logging.getLogger(__name__)

DEFAULT_TENANT = "default"

_tenant: ContextVar[str] = ContextVar("sourcework_cloud_tenant", default=DEFAULT_TENANT)
"""Which tenant the current request is acting for, set by the cloud middleware
from the resolved session and read by every store call. One value per request,
never shared."""


def tenant_for(tenant_id: str) -> None:
    """Set the tenant for this request's duration (the request handler calls
    this once it has resolved who is asking)."""
    _tenant.set(tenant_id)


SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id          TEXT PRIMARY KEY,
    tenant_id   TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at TIMESTAMPTZ,
    title       TEXT NOT NULL,
    status      TEXT NOT NULL,
    request     TEXT NOT NULL,
    result      TEXT,
    error       TEXT,
    events      TEXT NOT NULL DEFAULT '[]',
    usage       TEXT,
    approval    TEXT
);
CREATE INDEX IF NOT EXISTS runs_tenant_created ON runs (tenant_id, created_at DESC);
CREATE TABLE IF NOT EXISTS tenant_settings (
    tenant_id   TEXT NOT NULL,
    key         TEXT NOT NULL,
    value       TEXT NOT NULL DEFAULT '',
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, key)
);
"""

RLS = """
ALTER TABLE runs ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON runs;
CREATE POLICY tenant_isolation ON runs
    USING (tenant_id = current_setting('app.tenant_id', true));
ALTER TABLE tenant_settings ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation_settings ON tenant_settings;
CREATE POLICY tenant_isolation_settings ON tenant_settings
    USING (tenant_id = current_setting('app.tenant_id', true));
"""


class PostgresStore:
    """The hosted run store: one table, tenant-scoped, RLS on.

    JSON payloads live in TEXT columns for now - exactly the round-trip the
    SQLite store already makes, which is what keeps the two interchangeable
    behind the same routes. Querying into them (jsonb) is a refinement for
    when there is a migration test environment to prove it against.
    """

    def __init__(self, dsn: str, *, min_size: int = 1, max_size: int = 10) -> None:
        from psycopg_pool import ConnectionPool

        self._pool = ConnectionPool(
            dsn,
            min_size=min_size,
            max_size=max_size,
            open=False,
            kwargs={"autocommit": False},
        )
        self._pool.open()
        self._pool.wait()
        with self._pool.connection() as conn:
            conn.execute(SCHEMA)
            conn.execute(RLS)
            conn.commit()

    def close(self) -> None:
        self._pool.close()

    # -- every operation runs in a tenant-scoped transaction ----------------

    def _scoped(self, fn: Callable[..., Any], *args: Any) -> Any:
        """Run ``fn(conn, *args)`` with ``app.tenant_id`` set for one
        transaction. `set_config` with a `true` third argument is
        transaction-local, so the tenant never leaks onto the pooled
        connection for the next user of it - the transaction boundary is also
        the isolation boundary."""
        with self._pool.connection() as conn, conn.transaction():
            conn.execute("SELECT set_config('app.tenant_id', %s, true)", (_tenant.get(),))
            return fn(conn, *args)

    async def save(self, run: Run) -> None:
        def write(conn) -> None:
            conn.execute(
                """INSERT INTO runs (id, tenant_id, created_at, finished_at, title, status,
                                     request, result, error, events, usage, approval)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (id) DO UPDATE SET
                     finished_at=excluded.finished_at, status=excluded.status,
                     result=excluded.result, error=excluded.error,
                     events=excluded.events, usage=excluded.usage,
                     approval=excluded.approval""",
                (
                    run.id,
                    _tenant.get(),
                    run.created_at,
                    run.finished_at,
                    run.title,
                    run.status,
                    json.dumps(run.request),
                    json.dumps(run.result) if run.result is not None else None,
                    run.error,
                    json.dumps(run.events),
                    json.dumps(run.usage) if run.usage is not None else None,
                    json.dumps(run.approval) if run.approval is not None else None,
                ),
            )

        await asyncio.to_thread(self._scoped, write)

    async def get(self, run_id: str) -> Run | None:
        def read(conn) -> Run | None:
            row = conn.execute(
                "SELECT * FROM runs WHERE id = %s AND tenant_id = %s",
                (run_id, _tenant.get()),
            ).fetchone()
            return _row_to_run(row) if row else None

        return await asyncio.to_thread(self._scoped, read)

    async def list(self, limit: int = 50) -> list[Run]:
        def read_all(conn) -> list[Run]:
            rows = conn.execute(
                "SELECT * FROM runs WHERE tenant_id = %s ORDER BY created_at DESC LIMIT %s",
                (_tenant.get(), limit),
            ).fetchall()
            return [_row_to_run(r) for r in rows]

        return await asyncio.to_thread(self._scoped, read_all)

    async def delete(self, run_id: str) -> bool:
        def remove(conn) -> bool:
            cur = conn.execute(
                "DELETE FROM runs WHERE id = %s AND tenant_id = %s",
                (run_id, _tenant.get()),
            )
            return cur.rowcount > 0

        return await asyncio.to_thread(self._scoped, remove)

    async def reap_orphans(self) -> int:
        """Mark runs that were in flight when the process died.

        The same lie RunStore fixes on a laptop, per tenant: a ``running`` row
        after a restart says "in progress" forever. With a queue behind it this
        becomes a retry instead; until the workers exist it fails honestly.
        """

        def sweep(conn) -> int:
            # `IN (...)` expanded per status, not `IN %s`: Postgres will not
            # bind a parameter tuple into an IN-list.
            placeholders = ", ".join("%s" for _ in IN_FLIGHT)
            cur = conn.execute(
                f"""UPDATE runs SET status = 'failed',
                          error = 'The service restarted while this run was in flight.',
                          finished_at = now()
                   WHERE tenant_id = %s AND status IN ({placeholders})""",
                (_tenant.get(), *IN_FLIGHT),
            )
            return cur.rowcount

        return await asyncio.to_thread(self._scoped, sweep)

    # -- tenant settings ----------------------------------------------------

    def get_settings(self) -> dict[str, str]:
        """This tenant's saved settings, ``KEY -> value``.

        Synchronous on purpose: it serves the settings backend, whose protocol
        is synchronous (the local one reads a file, after all), and the page is
        a handful of rows. RLS applies like everywhere else - a call with no
        tenant set returns nothing, the safe direction.
        """
        def read_all(conn) -> dict[str, str]:
            rows = conn.execute(
                "SELECT key, value FROM tenant_settings WHERE tenant_id = %s",
                (_tenant.get(),),
            ).fetchall()
            return {key: value for key, value in rows}

        return self._scoped(read_all)

    def put_settings(self, updates: dict[str, str]) -> list[str]:
        """Save ``updates`` for this tenant, returning the keys that changed.

        An empty value *unsets*: the settings page sends ``""`` to clear a
        field, and storing a blank is how a config that was fine becomes one
        every later process chokes on (an empty ``MAX_TOKENS`` is not an int).
        Deleting the row means the code default applies, exactly what clearing
        a box means on the local page.
        """
        def write_all(conn) -> list[str]:
            changed: list[str] = []
            for key, value in updates.items():
                if value == "":
                    cur = conn.execute(
                        "DELETE FROM tenant_settings WHERE tenant_id = %s AND key = %s",
                        (_tenant.get(), key),
                    )
                else:
                    cur = conn.execute(
                        """INSERT INTO tenant_settings (tenant_id, key, value)
                           VALUES (%s, %s, %s)
                           ON CONFLICT (tenant_id, key) DO UPDATE SET
                             value = excluded.value, updated_at = now()
                           WHERE tenant_settings.value IS DISTINCT FROM excluded.value""",
                        (_tenant.get(), key, value),
                    )
                if cur.rowcount:
                    changed.append(key)
            return sorted(changed)

        return self._scoped(write_all)


def _row_to_run(row: tuple[Any, ...]) -> Run:
    return Run(
        id=row[0],
        title=row[4],
        status=row[5],
        created_at=_iso(row[2]),
        finished_at=_iso(row[3]),
        request=json.loads(row[6]),
        result=json.loads(row[7]) if row[7] else None,
        error=row[8],
        events=json.loads(row[9] or "[]"),
        usage=json.loads(row[10]) if row[10] else None,
        approval=json.loads(row[11]) if row[11] else None,
    )


def _iso(value: datetime | None) -> str | None:
    """A timestamp from Postgres, back to the ISO string the rest of the UI
    stores, so a run looks identical whichever store it came from."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat(timespec="seconds")
    return str(value)
