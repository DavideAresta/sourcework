"""Run history, on disk.

The A2A task store is in-memory, which is right for the agents - a task is a
request in flight - and useless for a UI, where the whole point is that a PRD
you generated on Tuesday is still there on Thursday. So the UI keeps its own
record: what was asked for, what came back, what it cost, and every progress
line along the way.

SQLite because it is in the standard library, needs no service, and a single
file is something a user can back up or delete. Writes go through a lock and
off the event loop; there is one writer (the run manager) and reads are cheap.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import threading
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id          TEXT PRIMARY KEY,
    created_at  TEXT NOT NULL,
    finished_at TEXT,
    title       TEXT NOT NULL,
    status      TEXT NOT NULL,
    request     TEXT NOT NULL,
    result      TEXT,
    error       TEXT,
    events      TEXT NOT NULL DEFAULT '[]',
    usage       TEXT
);
CREATE INDEX IF NOT EXISTS runs_created_at ON runs (created_at DESC);
"""


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def new_run_id() -> str:
    return uuid.uuid4().hex[:12]


@dataclass
class Run:
    """One generation, from submitted to finished."""

    id: str
    title: str
    status: str
    """queued | running | ok | failed | cancelled"""
    created_at: str
    request: dict[str, Any]
    finished_at: str | None = None
    result: dict[str, Any] | None = None
    error: str | None = None
    events: list[dict[str, Any]] = field(default_factory=list)
    usage: dict[str, Any] | None = None

    @property
    def done(self) -> bool:
        return self.status in {"ok", "failed", "cancelled"}

    def summary(self) -> dict[str, Any]:
        """The list-view shape: everything except the payloads, which are big."""
        stats = (self.result or {}).get("stats") or {}
        review = (self.result or {}).get("review") or {}
        return {
            "id": self.id,
            "title": self.title,
            "status": self.status,
            "created_at": self.created_at,
            "finished_at": self.finished_at,
            "error": self.error,
            "backend": ((self.request.get("llm") or {}).get("backend")) or "(configured default)",
            # Lineage lives in the request rather than a column: a refinement
            # IS its request, and a separate column could disagree with it.
            "parent_id": (self.request.get("baseline") or {}).get("run_id"),
            "requirements": stats.get("requirements"),
            "evidence": stats.get("evidence"),
            "sources": stats.get("sources"),
            "verdict": review.get("verdict") if isinstance(review, dict) else None,
            "usage": self.usage,
        }

    def as_dict(self) -> dict[str, Any]:
        return {
            **self.summary(),
            "request": self.request,
            "result": self.result,
            "events": self.events,
        }


@runtime_checkable
class Store(Protocol):
    """What the UI needs of a place to keep runs.

    Six methods, taken from what :class:`~sourcework.ui.runner.RunManager` and
    the routes actually call rather than from what :class:`RunStore` happens to
    expose - a protocol wider than its use is a promise nobody asked for and
    every implementation has to keep.

    Deliberately no notion of an owner. This is a single-user application and
    :class:`RunStore` is the right shape for it; an installation that needs runs
    to belong to people supplies its own implementation, and the day core grows
    a principal on the run record is the day to widen this, not before.
    """

    async def save(self, run: Run) -> None: ...

    async def get(self, run_id: str) -> Run | None: ...

    async def list(self, limit: int = 50) -> list[Run]: ...

    async def delete(self, run_id: str) -> bool: ...

    async def reap_orphans(self) -> int: ...

    def close(self) -> None: ...


class RunStore:
    """The one that ships: SQLite, one file, no service.

    Satisfies :class:`Store` structurally rather than by inheritance, so the
    protocol can describe it without this class having to know the protocol
    exists.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        # check_same_thread=False because writes are handed to a worker thread;
        # the lock below is what actually serialises them.
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.executescript(SCHEMA)
        self._db.commit()
        self._lock = threading.Lock()

    def close(self) -> None:
        with self._lock:
            self._db.close()

    # -- sync core (runs in a worker thread) -------------------------------

    def _save_sync(self, run: Run) -> None:
        with self._lock:
            self._db.execute(
                """INSERT INTO runs (id, created_at, finished_at, title, status, request,
                                     result, error, events, usage)
                   VALUES (?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(id) DO UPDATE SET
                     finished_at=excluded.finished_at, status=excluded.status,
                     result=excluded.result, error=excluded.error,
                     events=excluded.events, usage=excluded.usage""",
                (
                    run.id,
                    run.created_at,
                    run.finished_at,
                    run.title,
                    run.status,
                    json.dumps(run.request),
                    json.dumps(run.result) if run.result is not None else None,
                    run.error,
                    json.dumps(run.events),
                    json.dumps(run.usage) if run.usage is not None else None,
                ),
            )
            self._db.commit()

    def _get_sync(self, run_id: str) -> Run | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        return _row_to_run(row) if row else None

    def _list_sync(self, limit: int) -> list[Run]:
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM runs ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [_row_to_run(r) for r in rows]

    def _delete_sync(self, run_id: str) -> bool:
        with self._lock:
            cur = self._db.execute("DELETE FROM runs WHERE id = ?", (run_id,))
            self._db.commit()
        return cur.rowcount > 0

    # -- async surface ------------------------------------------------------

    async def save(self, run: Run) -> None:
        await asyncio.to_thread(self._save_sync, run)

    async def get(self, run_id: str) -> Run | None:
        return await asyncio.to_thread(self._get_sync, run_id)

    async def list(self, limit: int = 50) -> list[Run]:
        return await asyncio.to_thread(self._list_sync, limit)

    async def delete(self, run_id: str) -> bool:
        return await asyncio.to_thread(self._delete_sync, run_id)

    async def reap_orphans(self) -> int:
        """Mark runs that were in flight when the process died.

        A `running` row on start-up is a lie: nothing is running, the UI was
        restarted mid-run. Leaving it says "in progress" forever.
        """
        stale = [r for r in await self.list(limit=500) if r.status in {"queued", "running"}]
        for run in stale:
            run.status = "failed"
            run.error = "The UI restarted while this run was in flight."
            run.finished_at = now_iso()
            await self.save(run)
        return len(stale)


def _row_to_run(row: sqlite3.Row) -> Run:
    return Run(
        id=row["id"],
        title=row["title"],
        status=row["status"],
        created_at=row["created_at"],
        finished_at=row["finished_at"],
        request=json.loads(row["request"]),
        result=json.loads(row["result"]) if row["result"] else None,
        error=row["error"],
        events=json.loads(row["events"] or "[]"),
        usage=json.loads(row["usage"]) if row["usage"] else None,
    )
