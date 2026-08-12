"""Running as a desktop app: where things live, and what is listening.

Nothing here starts a tray or a server. The parts worth testing are the two
decisions made before anything starts - which directory owns the config, and
whether an engine exists - because both fail silently and both fail at launch,
where there is no log yet to read.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from sourcework import desktop, engine, paths

# ---------------------------------------------------------------------------
# Where things live
# ---------------------------------------------------------------------------


def test_a_checkout_keeps_everything_beside_the_code(tmp_path: Path):
    """Every existing instruction says ./env and ./workspace, and the tests
    assume it. A packaged app must not change that for developers."""
    (tmp_path / "pyproject.toml").write_text('name = "sourcework"\n', encoding="utf-8")

    assert paths.is_project_checkout(tmp_path)
    assert paths.env_file(tmp_path) == tmp_path / ".env"
    assert paths.workspace(tmp_path) == tmp_path / "workspace"


def test_an_existing_env_file_settles_it(tmp_path: Path):
    """A .env someone put here is a statement about where config lives, and it
    outranks any guess about whether this looks like a checkout."""
    (tmp_path / ".env").write_text("SOURCEWORK_LOG_LEVEL=INFO\n", encoding="utf-8")

    assert paths.is_project_checkout(tmp_path)
    assert paths.env_file(tmp_path) == tmp_path / ".env"


def test_another_project_does_not_capture_the_directory(tmp_path: Path):
    """Run from inside some unrelated Python project, this is not a checkout -
    which is why the marker is the name in pyproject, not its existence."""
    (tmp_path / "pyproject.toml").write_text('name = "somebody-elses-tool"\n', encoding="utf-8")

    assert not paths.is_project_checkout(tmp_path)


def test_outside_a_checkout_nothing_lands_in_the_working_directory(tmp_path: Path):
    """The case that motivates the module: launched from Finder, cwd is `/`.
    Writing ./workspace there either fails or litters a directory nobody chose.
    """
    assert not paths.is_project_checkout(tmp_path)

    env, workspace = paths.env_file(tmp_path), paths.workspace(tmp_path)
    for resolved in (env, workspace):
        assert not resolved.is_relative_to(tmp_path), f"{resolved} is in the working directory"
        assert resolved.is_absolute()
    # Config and data are separated because the platforms that distinguish them
    # do so to keep an unbounded pile of ingested documents out of a settings
    # folder that gets synced.
    assert env.parent != workspace.parent


# ---------------------------------------------------------------------------
# Finding an engine
# ---------------------------------------------------------------------------


class _Response:
    def __init__(self, payload: dict, status: int = 200) -> None:
        self._payload = payload
        self.status = status

    def raise_for_status(self) -> None:
        if self.status >= 400:
            raise RuntimeError(f"HTTP {self.status}")

    def json(self) -> dict:
        return self._payload


@pytest.fixture
def answered(monkeypatch):
    """Let specific URLs answer; everything else refuses to connect."""
    served: dict[str, dict] = {}

    def fake_get(url, **kwargs):  # noqa: ANN001, ANN003, ANN202
        if url in served:
            return _Response(served[url])
        raise OSError("connection refused")

    monkeypatch.setattr(engine.httpx, "get", fake_get)
    return served


def test_an_openai_shaped_server_is_recognised(answered):
    answered["http://127.0.0.1:1234/v1/models"] = {"data": [{"id": "gemma"}, {"id": "qwen"}]}

    found = engine.probe(engine.CANDIDATES[1])

    assert found is not None
    assert found.models == ["gemma", "qwen"]
    assert found.base_url == "http://127.0.0.1:1234/v1"


def test_ollama_is_read_in_its_own_shape(answered):
    """Ollama lists at /api/tags under `models`/`name`, not /v1/models under
    `data`/`id`, and is configured at a third URL again."""
    answered["http://127.0.0.1:11434/api/tags"] = {"models": [{"name": "qwen3:8b"}]}

    found = engine.probe(engine.CANDIDATES[2])

    assert found is not None
    assert found.models == ["qwen3:8b"]
    assert found.base_url.endswith("/v1"), "what goes in api_base, not what we probed"


def test_the_configured_endpoint_is_probed_without_doubling_v1(answered, monkeypatch):
    """The bug this caught: api_base already ends in /v1, so appending
    /v1/models asks for /v1/v1/models and gets a 404 that reads exactly like
    "nothing is running"."""
    from sourcework import config

    monkeypatch.setattr(
        config, "settings",
        lambda: config.Settings(llm=config.LLMSettings(api_base="http://127.0.0.1:9999/v1")),
    )
    answered["http://127.0.0.1:9999/v1/models"] = {"data": [{"id": "local"}]}

    found = engine.detect()

    assert found is not None
    assert found.configured is True
    assert found.models == ["local"]


def test_nothing_reachable_is_reported_as_nothing(answered, monkeypatch):
    """The signal to onboard. Returning a plausible-looking default here is how
    a first run dies several agents deep with a connection error instead."""
    from sourcework import config

    monkeypatch.setattr(config, "settings", lambda: config.Settings())

    assert engine.detect() is None


def test_a_configured_endpoint_that_is_down_falls_through_to_probing(answered, monkeypatch):
    from sourcework import config

    monkeypatch.setattr(
        config, "settings",
        lambda: config.Settings(llm=config.LLMSettings(api_base="http://127.0.0.1:9999/v1")),
    )
    answered["http://127.0.0.1:8081/v1/models"] = {"data": [{"id": "found-anyway"}]}

    found = engine.detect()

    assert found is not None
    assert found.configured is False
    assert found.models == ["found-anyway"]


# ---------------------------------------------------------------------------
# Launching
# ---------------------------------------------------------------------------


def test_a_stranger_on_the_port_is_not_mistaken_for_us(monkeypatch):
    """A second launch must raise the window someone already has - but only if
    what is answering is actually SourceWork, not whatever else took 8080."""
    monkeypatch.setattr(
        desktop.httpx, "get",
        lambda url, **kw: _Response({"status": "ok", "service": "somebody-elses-app"}),
    )
    assert desktop.already_running(8080) is False

    monkeypatch.setattr(
        desktop.httpx, "get",
        lambda url, **kw: _Response({"status": "ok", "service": "sourcework-ui"}),
    )
    assert desktop.already_running(8080) is True


def test_nothing_listening_is_not_running(monkeypatch):
    def refuse(url, **kwargs):  # noqa: ANN001, ANN003, ANN202
        raise OSError("connection refused")

    monkeypatch.setattr(desktop.httpx, "get", refuse)
    assert desktop.already_running(8080) is False


def test_every_state_has_a_marker():
    """The icon is the only status a user sees once they tab away from a run
    that takes minutes."""
    markers = {desktop.Status(state, "x").marker for state in
               ("ready", "starting", "no-engine", "error")}
    assert len(markers) == 4, "four states must be four distinguishable markers"
