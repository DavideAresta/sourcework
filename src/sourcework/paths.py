"""Where SourceWork keeps its configuration and its work.

Two answers, and which one applies is decided by looking at the directory the
process started in.

**A developer checkout** keeps everything beside the code: ``.env`` and
``workspace/`` in the repository, which is what every existing instruction says
and what the tests assume.

**A packaged application** cannot. It is launched from Finder or a Start menu
with a working directory of ``/`` or ``C:\\``, and writing ``./workspace`` there
either fails or litters a directory nobody chose. It gets the platform's own
per-user location instead.

The distinction is drawn by *evidence in the working directory*, never by asking
"am I frozen": a developer running the packaged binary in their checkout should
still get the checkout, and someone who copied a ``.env`` next to the app is
telling us where they want it kept.
"""

from __future__ import annotations

import os
from pathlib import Path

APP_NAME = "SourceWork"

ENV_FILENAME = ".env"
WORKSPACE_DIRNAME = "workspace"


def _platform_dirs() -> tuple[Path, Path]:
    """``(config, data)`` for this platform, without a hard dependency.

    ``platformdirs`` is the right library and is a declared dependency, but this
    module is imported during start-up on a machine that may have a broken
    install, and falling back to something reasonable beats a traceback before
    any log exists.
    """
    try:
        import platformdirs

        return (
            Path(platformdirs.user_config_dir(APP_NAME, appauthor=False)),
            Path(platformdirs.user_data_dir(APP_NAME, appauthor=False)),
        )
    except ImportError:  # pragma: no cover - platformdirs is declared
        home = Path.home()
        if os.name == "nt":
            base = Path(os.environ.get("APPDATA", home / "AppData/Roaming")) / APP_NAME
            return base, base
        return home / ".config" / APP_NAME.lower(), home / ".local/share" / APP_NAME.lower()


def is_project_checkout(directory: Path | None = None) -> bool:
    """Does ``directory`` look like somewhere SourceWork is *developed*?

    An existing ``.env`` counts on its own - it is an explicit statement about
    where configuration lives. Otherwise the marker is this project's own
    ``pyproject.toml``, checked by name so that being run from inside some other
    Python project does not capture its directory.
    """
    directory = directory or Path.cwd()
    if (directory / ENV_FILENAME).is_file():
        return True
    pyproject = directory / "pyproject.toml"
    if not pyproject.is_file():
        return False
    try:
        return 'name = "sourcework"' in pyproject.read_text(encoding="utf-8")
    except OSError:  # pragma: no cover - unreadable file is not a checkout
        return False


def env_file(directory: Path | None = None) -> Path:
    """The ``.env`` this installation reads and the settings page writes."""
    directory = directory or Path.cwd()
    if is_project_checkout(directory):
        return directory / ENV_FILENAME
    return _platform_dirs()[0] / ENV_FILENAME


def workspace(directory: Path | None = None) -> Path:
    """Run history, the database, and uploaded files.

    Separate from the config directory because it grows without bound - every
    document anyone ever ingested is in here - and the platforms that
    distinguish the two do so precisely to keep that out of a settings folder
    that gets synced or backed up.
    """
    directory = directory or Path.cwd()
    if is_project_checkout(directory):
        return directory / WORKSPACE_DIRNAME
    return _platform_dirs()[1] / WORKSPACE_DIRNAME


def log_file() -> Path:
    """Where a packaged app writes its log, so the tray menu can open it."""
    return workspace().parent / "sourcework.log"


def ensure(path: Path) -> Path:
    """Create ``path`` as a directory and return it."""
    path.mkdir(parents=True, exist_ok=True)
    return path
