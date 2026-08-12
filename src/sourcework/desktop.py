"""SourceWork as a desktop application.

One process: the eight agents as threads, the web UI as a thread, and a tray
icon on the main thread because macOS insists. The browser the user already has
renders the UI, so there is no second browser to ship, no renderer process and
no IPC layer - the front end is plain ES modules served over loopback, exactly
as it is in development.

The tray is optional at runtime. Without ``pystray`` this still starts
everything and opens a browser, and waits for Ctrl-C - which is the right
behaviour for a developer and means the LGPL dependency can stay out of the
default install.
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
    """What the tray shows, and why."""

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


def _start_ui(port: int) -> None:
    import uvicorn

    from sourcework.ui.app import build_app

    config = uvicorn.Config(build_app(), host="127.0.0.1", port=port, log_level="warning")
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


def _icon_image(status: Status):  # noqa: ANN202 - PIL.Image, imported lazily
    """A flat disc in a colour that means something across the four states.

    Drawn rather than shipped as four PNGs: it has to be legible at 16px on
    light and dark menu bars, and a solid shape is the one thing that always is.
    """
    from PIL import Image, ImageDraw

    colour = {
        "ready": (46, 160, 67),
        "starting": (150, 150, 150),
        "no-engine": (219, 154, 4),
        "error": (218, 54, 51),
    }.get(status.state, (150, 150, 150))

    image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    ImageDraw.Draw(image).ellipse((8, 8, 56, 56), fill=colour)
    return image


def run(port: int | None = None, *, open_browser: bool = True, tray: bool = True) -> int:
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
    logging.basicConfig(level=settings().log_level, format="%(levelname)-7s %(name)s: %(message)s")

    _start_mesh()
    _start_ui(port)

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

    if tray:
        icon = _build_tray(port, url)
        if icon is not None:
            icon.run()  # blocks on the main thread, which macOS requires
            return 0
        print("  (no tray: pip install 'sourcework[app]' for a menu-bar icon)", flush=True)

    print("  Ctrl-C to stop.", flush=True)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        print("\nStopped.")
    return 0


def _build_tray(port: int, url: str):  # noqa: ANN202 - pystray.Icon
    """The menu, or None when pystray is not installed."""
    try:
        import pystray
    except ImportError:
        return None

    def refresh(icon: object) -> None:
        status = current_status(port)
        icon.icon = _icon_image(status)  # type: ignore[attr-defined]
        icon.title = f"SourceWork - {status.detail}"  # type: ignore[attr-defined]

    def on_open(icon: object, _item: object) -> None:
        webbrowser.open(url)

    def on_workspace(icon: object, _item: object) -> None:
        _reveal(paths.workspace())

    def on_log(icon: object, _item: object) -> None:
        _reveal(paths.log_file())

    def on_recheck(icon: object, _item: object) -> None:
        refresh(icon)

    def on_quit(icon: object, _item: object) -> None:
        # Daemon threads die with the process. An in-flight run is marked failed
        # on the next start by `reap_orphans`, so nothing is left claiming to be
        # running - but the work is lost, which is why this is the last item and
        # not the first.
        icon.stop()  # type: ignore[attr-defined]

    status = current_status(port)
    menu = pystray.Menu(
        pystray.MenuItem("Open SourceWork", on_open, default=True),
        pystray.MenuItem(lambda _: current_status(port).detail, on_recheck),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Open workspace folder", on_workspace),
        pystray.MenuItem("View log", on_log),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Quit SourceWork", on_quit),
    )
    return pystray.Icon("sourcework", _icon_image(status), f"SourceWork - {status.detail}", menu)


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
