"""Installing SourceWork into the desktop's own application launcher.

On Linux this is the whole of "make it a desktop app": a ``.desktop`` file in
``~/.local/share/applications`` puts an entry in the launcher, makes it
searchable, and lets it be pinned to a panel - no packaging, no signing, no
bundled runtime. macOS and Windows need a real bundle, which is a build step
rather than a file, so this refuses there rather than pretending.

Written per-user, never system-wide: it points at whichever interpreter is
running it, which is usually a virtualenv only this user can see.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

APP_ID = "com.ludusfaber.SourceWork"

ENTRY = """\
[Desktop Entry]
Type=Application
Version=1.0
Name=SourceWork
GenericName=Requirements from evidence
Comment=Turn documents, transcripts and images into a traceable PRD
Exec={exec_path} app
Icon={icon_path}
Terminal=false
Categories=Office;Documentation;
Keywords=PRD;requirements;documents;transcripts;AI;
StartupNotify=true
SingleMainWindow=true
"""


def _entry_dir() -> Path:
    base = os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local/share")
    return Path(base) / "applications"


def _icon_dir() -> Path:
    base = os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local/share")
    return Path(base) / "icons/hicolor/scalable/apps"


def _console_script() -> Path:
    """The ``sourcework`` command belonging to *this* interpreter.

    Not ``shutil.which``: a desktop entry launched from a menu has almost none
    of the shell's PATH, and resolving it now against the running interpreter is
    what makes the entry work from a session that never sourced a profile.
    """
    candidate = Path(sys.executable).parent / "sourcework"
    if candidate.is_file():
        return candidate
    found = shutil.which("sourcework")
    if found:
        return Path(found)
    raise FileNotFoundError(
        "Cannot find the `sourcework` command next to this interpreter. "
        "Install the package (pip install -e .) before installing a launcher entry."
    )


def install(*, quiet: bool = False) -> int:
    """Write the desktop entry and its icon. Returns a process exit code."""
    if sys.platform != "linux":
        print(
            f"Desktop entries are a Linux thing; this is {sys.platform}.\n"
            "On macOS and Windows a launcher needs a real app bundle - see "
            "docs/desktop.md, step 5.",
            file=sys.stderr,
        )
        return 2

    try:
        exec_path = _console_script()
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    icon_source = Path(__file__).parent / "ui/static/favicon.svg"
    icon_target = _icon_dir() / "sourcework.svg"
    icon_target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(icon_source, icon_target)

    entry_path = _entry_dir() / f"{APP_ID}.desktop"
    entry_path.parent.mkdir(parents=True, exist_ok=True)
    entry_path.write_text(
        ENTRY.format(exec_path=exec_path, icon_path=icon_target), encoding="utf-8"
    )
    entry_path.chmod(0o755)

    # Best-effort: KDE and GNOME both pick new entries up on their own, but the
    # database refresh makes it appear now rather than at next login.
    for command in (["update-desktop-database", str(entry_path.parent)],
                    ["gtk-update-icon-cache", "-f", "-t", str(icon_target.parents[2])]):
        if shutil.which(command[0]):
            subprocess.run(command, capture_output=True, check=False)

    if not quiet:
        print(f"Installed {entry_path}")
        print(f"          {icon_target}")
        print("\nSourceWork should now be in your application launcher.")
        print("Remove it with:  sourcework install-desktop-entry --remove")
    return 0


def remove(*, quiet: bool = False) -> int:
    removed = []
    for path in (_entry_dir() / f"{APP_ID}.desktop", _icon_dir() / "sourcework.svg"):
        if path.exists():
            path.unlink()
            removed.append(path)
    if not quiet:
        for path in removed:
            print(f"Removed {path}")
        if not removed:
            print("Nothing to remove.")
    return 0
