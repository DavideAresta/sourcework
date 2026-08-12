"""SourceWork as a desktop application.

One process: the eight agents as threads and the web UI as another. The browser
the user already has renders the interface, so there is no second browser to
ship, no renderer process and no IPC layer - the front end is plain ES modules
served over loopback, exactly as it is in development.

There is deliberately no tray icon. The three things one would do are covered
without a GUI toolkit: the packaged app's own Dock or taskbar entry says it is
running, a second launch re-opens the browser rather than starting a rival, and
a finished run raises a *notification from the page* - which says which document
and whether it worked, where an icon changing colour says only "something".
That removes a platform-specific backend and an LGPL dependency from a project
whose licence is otherwise wholly permissive.
"""

from __future__ import annotations

import logging
import threading
import time
import webbrowser
from dataclasses import dataclass

import httpx

from sourcework import engine, paths
from sourcework.config import settings

logger = logging.getLogger(__name__)

STARTUP_TIMEOUT_S = 30.0


@dataclass
class Status:
    """Whether this installation can actually do anything, and why not."""

    state: str
    """starting | ready | no-engine | error"""
    detail: str

    @property
    def marker(self) -> str:
        return {"ready": "●", "starting": "◌", "no-engine": "○", "error": "✕"}.get(self.state, "○")


def already_running(port: int, timeout: float = 0.4) -> bool:
    """Is a SourceWork UI already answering on this port?

    Checked by asking rather than by binding: a second launch should raise the
    window someone already has, not fail with "address in use" and leave them
    wondering which of the two is real. The health endpoint names the service,
    so an unrelated process on 8080 is not mistaken for us.
    """
    try:
        response = httpx.get(f"http://127.0.0.1:{port}/healthz", timeout=timeout)
        return response.json().get("service") == "sourcework-ui"
    except Exception:  # noqa: BLE001 - anything that is not an answer is a no
        return False


def _start_mesh() -> None:
    """Every agent, in this process, on its own port."""
    import uvicorn

    from sourcework.a2a_common import build_app
    from sourcework.cli import AGENTS, _load

    for name in AGENTS:
        module = _load(name)
        app = build_app(module.card(), module.executor())
        # Loopback only. The mesh has no authentication by default and this is
        # someone's own machine, not a compose network where agents must reach
        # each other across containers.
        config = uvicorn.Config(app, host="127.0.0.1", port=module.PORT, log_level="warning")
        threading.Thread(target=uvicorn.Server(config).run, daemon=True).start()


def _start_ui(port: int, on_shutdown=None) -> None:  # noqa: ANN001
    import uvicorn

    from sourcework.ui.app import build_app

    app = build_app(on_shutdown=on_shutdown)
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    threading.Thread(target=uvicorn.Server(config).run, daemon=True).start()


def _wait_until_ready(port: int, timeout: float = STARTUP_TIMEOUT_S) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if already_running(port):
            return True
        time.sleep(0.25)
    return False


def current_status(port: int) -> Status:
    if not already_running(port):
        return Status("error", "the interface is not answering")
    found = engine.detect()
    if found:
        return Status("ready", found.summary())
    if engine.has_hosted_credentials():
        return Status("ready", f"hosted API ({settings().llm.active_backend})")
    return Status("no-engine", "no model server found - open SourceWork to set one up")


def _configure_logging() -> None:
    """Log to a file as well as the console.

    Launched from a desktop entry there is no console at all, so stderr goes
    wherever the session manager decided - which is not a thing anyone can be
    told to look at. The failure message names this file; something has to
    actually write it.
    """
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    try:
        paths.ensure(paths.log_file().parent)
        handlers.append(logging.FileHandler(paths.log_file(), encoding="utf-8"))
    except OSError as exc:  # noqa: BLE001 - an unwritable log must not stop the app
        print(f"(could not open {paths.log_file()}: {exc})", flush=True)

    logging.basicConfig(
        level=settings().log_level,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        handlers=handlers,
        force=True,
    )


def run(port: int | None = None, *, open_browser: bool = True) -> int:
    """Start everything and stay up. Returns a process exit code."""
    from sourcework.ui.app import PORT

    port = port or PORT
    url = f"http://127.0.0.1:{port}"

    if already_running(port):
        print(f"SourceWork is already running - opening {url}", flush=True)
        if open_browser:
            webbrowser.open(url)
        return 0

    paths.ensure(paths.workspace())
    _configure_logging()

    stopping = threading.Event()
    _start_mesh()
    _start_ui(port, on_shutdown=stopping.set)

    if not _wait_until_ready(port):
        print(
            f"SourceWork did not come up within {STARTUP_TIMEOUT_S:.0f}s. "
            f"See {paths.log_file()}",
            flush=True,
        )
        return 1

    status = current_status(port)
    print(f"SourceWork  {url}", flush=True)
    print(f"  {status.marker} {status.detail}", flush=True)
    if open_browser:
        webbrowser.open(url)

    print("  Quit from the app, or Ctrl-C here.", flush=True)
    try:
        # Woken by the Quit control in the UI, or by Ctrl-C. Daemon threads die
        # with the process either way; an interrupted run is marked failed on
        # the next start by `reap_orphans`, so nothing is left claiming to run.
        while not stopping.wait(timeout=1.0):
            pass
        print("Stopped.", flush=True)
    except KeyboardInterrupt:
        print("\nStopped.", flush=True)
    return 0


def _reveal(target) -> None:  # noqa: ANN001 - Path
    """Show a file or folder in the platform's file manager."""
    import subprocess
    import sys

    target = str(target)
    try:
        if sys.platform == "darwin":
            subprocess.Popen(["open", "-R" if "." in target.rsplit("/", 1)[-1] else "", target])
        elif sys.platform == "win32":
            subprocess.Popen(["explorer", target.replace("/", "\\")])
        else:
            subprocess.Popen(["xdg-open", target])
    except Exception as exc:  # noqa: BLE001 - a missing file manager is not fatal
        logger.warning("could not reveal %s: %s", target, exc)
