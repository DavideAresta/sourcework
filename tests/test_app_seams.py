"""The four ``build_app`` seams: run ids, execution, settings, authorization.

Each defaults to today's single-operator behaviour and lets a hosted
distribution supply its own. These tests prove a seam is real - that the
argument is used - rather than a parameter that does nothing. The local
defaults themselves are exercised by the rest of the suite; what is new here
is that a caller can replace them without editing core.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi.testclient import TestClient

from sourcework import auth
from sourcework.config import API_BACKEND_IDS, BACKEND_IDS, CLI_BACKEND_IDS
from sourcework.ui.app import build_app
from sourcework.ui.env_file import SettingsBackend
from sourcework.ui.runner import RunExecutor


class RecordingExecutor:
    """A ``RunExecutor`` that records what the routes asked of it and runs
    nothing, so the API layer can be driven without a mesh."""

    def __init__(self) -> None:
        self.started: list[tuple[str, str]] = []
        self.shut_down = False

    async def start(self, request, *, run_id: str | None = None):  # noqa: ANN001, ANN201
        from sourcework.ui.store import Run, now_iso

        self.started.append((run_id or "auto", request.title))
        return Run(
            id=run_id or "auto",
            title=request.title,
            status="queued",
            created_at=now_iso(),
            request={"title": request.title},
        )

    async def resume(self, run_id: str):
        return None

    async def cancel(self, run_id: str) -> bool:
        return False

    def is_active(self, run_id: str) -> bool:
        return False

    async def subscribe(self, run_id: str):
        """Nothing to replay: this executor never runs anything."""
        if False:  # pragma: no cover - the empty async generator
            yield None

    async def shutdown(self) -> None:
        self.shut_down = True


class DictBackend:
    """A ``SettingsBackend`` over a plain dict - the shape a hosted tenant's
    per-tenant settings would take, without any of the machinery."""

    label = "tenant settings"
    default_profile = "balanced"
    restartable = False
    allowed_backends = BACKEND_IDS

    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.writes: list[dict[str, str]] = []

    def read(self) -> dict[str, str]:
        return dict(self.values)

    def write(self, updates: dict[str, str]) -> list[str]:
        changed = sorted(k for k, v in updates.items() if v != self.values.get(k))
        self.values.update(updates)
        self.writes.append(dict(updates))
        return changed

    def describe(self) -> list[dict[str, Any]]:
        return [{"key": k, "value": v} for k, v in self.values.items()]

    def profiles_for(self) -> dict[str, dict[str, Any]]:
        return {}


class RejectingAuthorizer:
    """A policy that lets nobody in - the counter-example to NullAuth's open
    gate, proving the gate is real."""

    async def __call__(self, request, principal, method) -> bool:  # noqa: ANN001
        return False


class AllowingAuthorizer:
    """A policy that lets everyone in; still proves the argument is consulted."""

    async def __call__(self, request, principal, method) -> bool:  # noqa: ANN001
        return True


# --- the arguments are accepted -------------------------------------------


def test_an_executor_supplied_to_build_app_is_the_one_that_runs_runs(tmp_path):
    executor = RecordingExecutor()

    with TestClient(build_app(tmp_path, executor=executor)) as client:
        created = client.post(
            "/api/runs",
            data={"request": json.dumps({"title": "On the executor", "notes": ["a note"]})},
            headers={"X-SourceWork-UI": "1"},
        )
        body = created.json()

    assert created.status_code == 200, created.text
    assert executor.started == [(body["id"], "On the executor")]
    assert body["id"]


def test_the_default_executor_satisfies_the_protocol(tmp_path):
    from sourcework.ui.runner import RunManager
    from sourcework.ui.store import RunStore

    store = RunStore(tmp_path / "runs.db")
    try:
        assert isinstance(RunManager(store), RunExecutor)
        assert isinstance(RecordingExecutor(), RunExecutor)
    finally:
        store.close()


def test_run_ids_come_from_the_supplied_factory(tmp_path):
    """The route names the run before the executor sees it (uploads land in a
    directory named after it), so the factory has to be honoured there too."""
    executor = RecordingExecutor()
    ids = iter(["uuid-0001"])

    with TestClient(
        build_app(tmp_path, executor=executor, run_id_factory=lambda: next(ids))
    ) as client:
        created = client.post(
            "/api/runs",
            data={"request": json.dumps({"title": "T", "notes": ["x"]})},
            headers={"X-SourceWork-UI": "1"},
        )

    assert created.json()["id"] == "uuid-0001"
    assert executor.started == [("uuid-0001", "T")]


# --- settings backend ------------------------------------------------------


def test_a_settings_backend_supplied_to_build_app_is_the_one_served(tmp_path):
    backend = DictBackend()

    with TestClient(build_app(tmp_path, settings_backend=backend)) as client:
        read = client.get("/api/settings").json()
        written = client.put(
            "/api/settings",
            json={"SOURCEWORK_LLM__BACKEND": "llama-cpp"},
            headers={"X-SourceWork-UI": "1"},
        ).json()

    assert read == {
        "path": "tenant settings",
        "fields": [],
        "profiles": {},
        "default_profile": "balanced",
    }
    assert written["changed"] == ["SOURCEWORK_LLM__BACKEND"]
    # A backend that resolves settings per request has nothing to restart -
    # the mesh restarting on every tenant's save would take the whole service
    # down for one of them.
    assert written["restart_required"] is False
    assert backend.values == {"SOURCEWORK_LLM__BACKEND": "llama-cpp"}


def test_the_backends_route_offers_what_the_settings_backend_allows(tmp_path):
    """/api/backends advertises the distribution's actual offering.

    The route already proxied availability to the configured backends; the
    settings backend is what decides *which* backends count at all, and the
    CLI list is the same decision read differently - a hosted install has no
    CLIs to warn the user about.
    """

    class HostedBackend(DictBackend):
        allowed_backends = API_BACKEND_IDS

    with TestClient(build_app(tmp_path, settings_backend=HostedBackend())) as client:
        offered = client.get("/api/backends").json()

    assert {b["id"] for b in offered["backends"]} == set(API_BACKEND_IDS)
    assert offered["cli_backends"] == []
    assert offered["roles"]


def test_the_local_settings_backend_offers_the_cli_list(tmp_path):
    """The default install still warns that CLIs take minutes - the copy the
    hosted fix removed must not have been removed from the local page."""
    with TestClient(build_app(tmp_path)) as client:
        offered = client.get("/api/backends").json()

    assert offered["cli_backends"] == list(CLI_BACKEND_IDS)


def test_the_env_file_backend_satisfies_the_settings_protocol(tmp_path):
    from sourcework.ui.env_file import EnvFileBackend

    backend = EnvFileBackend(tmp_path / "env")
    backend.write({"SOURCEWORK_LLM__BACKEND": "litellm"})

    assert isinstance(backend, SettingsBackend)
    assert backend.read()["SOURCEWORK_LLM__BACKEND"] == "litellm"
    assert backend.restartable is True


# --- authorization ---------------------------------------------------------


def test_an_authorizer_that_refuses_closes_the_gate(tmp_path):
    """Without a policy everything is allowed, the local behaviour. With one
    that says no, even a signed-in principal gets a 403 - the two readings are
    the difference between ``None`` and a policy that denies."""
    with TestClient(build_app(tmp_path, authorizer=RejectingAuthorizer())) as client:
        refused = client.get("/api/runs")

    assert refused.status_code == 403
    assert refused.json()["roles"] == sorted(auth.LOCAL.roles)


def test_an_authorizer_sees_the_method_and_principal(tmp_path):
    seen: list[tuple[str, str, str]] = []

    async def spy(request, principal, method) -> bool:  # noqa: ANN001
        seen.append((principal.id, method, request.url.path))
        return True

    with TestClient(build_app(tmp_path, authorizer=spy)) as client:
        assert client.get("/api/runs").status_code == 200

    assert ("local", "GET", "/api/runs") in seen


def test_liveness_stays_open_whatever_the_policy_says(tmp_path):
    """The gate exists for the guarded routes; /healthz keeps answering without
    a principal so a load balancer can probe it."""
    with TestClient(build_app(tmp_path, authorizer=RejectingAuthorizer())) as client:
        assert client.get("/healthz").status_code == 200
