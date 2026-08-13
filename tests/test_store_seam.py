"""The store is an interface, so a different one can be supplied."""

from __future__ import annotations

from fastapi.testclient import TestClient

from sourcework.ui.app import build_app


class MemoryStore:
    """A Store that keeps runs in a dict and touches no disk.

    Written as a test double, but it is also the proof: nothing here inherits
    from RunStore, imports SQLite, or knows where the workspace is.
    """

    def __init__(self) -> None:
        self.runs: dict = {}
        self.closed = False

    async def save(self, run) -> None:  # noqa: ANN001
        self.runs[run.id] = run

    async def get(self, run_id: str):  # noqa: ANN201
        return self.runs.get(run_id)

    async def list(self, limit: int = 50):  # noqa: ANN201
        return list(self.runs.values())[:limit]

    async def delete(self, run_id: str) -> bool:
        return self.runs.pop(run_id, None) is not None

    async def reap_orphans(self) -> int:
        return 0

    def close(self) -> None:
        self.closed = True


def test_the_sqlite_store_is_one_implementation_of_the_interface(tmp_path):
    from sourcework.ui.store import RunStore, Store

    disk = RunStore(tmp_path / "runs.db")
    try:
        assert isinstance(disk, Store)
        assert isinstance(MemoryStore(), Store)
    finally:
        disk.close()


def test_an_installation_can_supply_its_own_store(tmp_path):
    """The point of the seam: runs belonging to different people needs a
    different store, and it should arrive from outside rather than by editing
    build_app."""
    from sourcework.ui.store import Run, now_iso

    memory = MemoryStore()
    memory.runs["r1"] = Run(
        id="r1", title="From somewhere else", status="ok",
        created_at=now_iso(), request={"title": "From somewhere else"},
    )

    with TestClient(build_app(workspace=tmp_path, store=memory)) as c:
        listed = c.get("/api/runs").json()
        detail = c.get("/api/runs/r1").json()

    assert [r["title"] for r in listed] == ["From somewhere else"]
    assert detail["id"] == "r1"
    assert not (tmp_path / "runs.db").exists(), "the default store was built anyway"
