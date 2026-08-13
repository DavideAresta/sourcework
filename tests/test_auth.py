"""Enforcement in core, identity from a plugin."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from sourcework import auth
from sourcework.ui.app import build_app


class FakeEntryPoint:
    def __init__(self, name: str, value) -> None:  # noqa: ANN001
        self.name = name
        self._value = value

    def load(self):  # noqa: ANN201
        return self._value


def _installed(monkeypatch, *entries: FakeEntryPoint) -> None:
    monkeypatch.setattr(auth, "entry_points", lambda group: list(entries))


class Doorman:
    """An authenticator that only lets through a request carrying a token."""

    id = "doorman"

    async def principal(self, request) -> auth.Principal | None:  # noqa: ANN001
        if request.headers.get("X-Token") == "open-sesame":
            return auth.Principal(id="ada", name="Ada", roles=frozenset({"reviewer"}))
        return None

    def challenge(self) -> dict[str, str]:
        return {"WWW-Authenticate": "Bearer"}


# --- resolution ------------------------------------------------------------


def test_an_installation_with_no_plugin_has_no_sign_in():
    assert isinstance(auth.build(), auth.NullAuth)


def test_a_plugin_takes_over_authentication(monkeypatch):
    _installed(monkeypatch, FakeEntryPoint("doorman", Doorman))

    assert isinstance(auth.build(), Doorman)


def test_two_authenticators_refuse_to_start(monkeypatch):
    """The unsafe reading of an ambiguous configuration is "carry on
    unauthenticated", which is exactly where an open UI is least expected."""
    _installed(monkeypatch, FakeEntryPoint("a", Doorman), FakeEntryPoint("b", Doorman))

    with pytest.raises(auth.AmbiguousAuthenticator, match="only one can be in force"):
        auth.build()


def test_a_plugin_that_is_not_an_authenticator_refuses_to_start(monkeypatch):
    """The opposite rule to publishers: one that will not load costs a
    destination, this one costs the lock on the door."""
    _installed(monkeypatch, FakeEntryPoint("junk", lambda: "not an authenticator"))

    with pytest.raises(TypeError, match="not an Authenticator"):
        auth.build()


# --- through the app -------------------------------------------------------


@pytest.fixture
def client(tmp_path):
    with TestClient(build_app(workspace=tmp_path)) as c:
        yield c


def test_core_stays_exactly_as_it_was(client):
    """NullAuth never refuses. A loopback single-user app has no way to answer a
    401, so putting one in front of it would be a locked door with no key."""
    assert client.get("/api/runs").status_code == 200

    me = client.get("/api/me").json()
    assert me["id"] == "local"
    assert me["authentication"] == "none"


def test_an_installed_authenticator_guards_every_route(monkeypatch, tmp_path):
    """One gate rather than a dependency per route: the route somebody forgets
    to decorate is the one that is unauthenticated with nothing to say so."""
    _installed(monkeypatch, FakeEntryPoint("doorman", Doorman))

    with TestClient(build_app(workspace=tmp_path)) as guarded:
        refused = guarded.get("/api/runs")
        assert refused.status_code == 401
        assert refused.headers["WWW-Authenticate"] == "Bearer"

        allowed = guarded.get("/api/runs", headers={"X-Token": "open-sesame"})
        assert allowed.status_code == 200


def test_liveness_and_the_shell_stay_reachable(monkeypatch, tmp_path):
    """A load balancer has no credentials, and the page that would let you sign
    in cannot itself require having signed in."""
    _installed(monkeypatch, FakeEntryPoint("doorman", Doorman))

    with TestClient(build_app(workspace=tmp_path)) as guarded:
        assert guarded.get("/healthz").status_code == 200
        assert guarded.get("/").status_code == 200
        # ...but the data the shell then asks for is not.
        assert guarded.get("/api/dashboard").status_code == 401


def test_the_principal_reaches_the_route(monkeypatch, tmp_path):
    _installed(monkeypatch, FakeEntryPoint("doorman", Doorman))

    with TestClient(build_app(workspace=tmp_path)) as guarded:
        me = guarded.get("/api/me", headers={"X-Token": "open-sesame"}).json()

    assert me["name"] == "Ada"
    assert me["roles"] == ["reviewer"]
    assert me["authentication"] == "doorman"

