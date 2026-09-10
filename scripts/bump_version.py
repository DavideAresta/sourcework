#!/usr/bin/env python3
"""Bump every file that carries the release version, and prepend the changelog.

The release workflow runs this on every push to main that passed CI. It reads
the current version from ``src/sourcework/__init__.py`` - the one string hatch
reads at build time - computes the next patch, and writes that version to every
place it is written down: the PRD default, the README badge, the desktop
manifest and crate, and the changelog. A release that shipped with the README
still advertising the previous version is the bug this exists to prevent.

The cloud distribution has its own version and moves only when ``cloud/``
changed: republishing an unchanged ``sourcework-cloud`` under a fresh number
would claim a release that did not happen.

    scripts/bump_version.py --notes-file notes.txt
    scripts/bump_version.py --notes-file notes.txt \
        --cloud-changed --cloud-notes-file cloud-notes.txt

Prints the new core version on stdout, so the caller can tag it.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from collections.abc import Callable
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

CORE_VERSION = ROOT / "src" / "sourcework" / "__init__.py"
CLOUD_VERSION = ROOT / "cloud" / "src" / "sourcework_cloud" / "__init__.py"
PRD_MODELS = ROOT / "src" / "sourcework" / "models.py"
README = ROOT / "README.md"
TAURI_CONF = ROOT / "desktop" / "src-tauri" / "tauri.conf.json"
CARGO_TOML = ROOT / "desktop" / "src-tauri" / "Cargo.toml"
CARGO_LOCK = ROOT / "desktop" / "src-tauri" / "Cargo.lock"
CHANGELOG = ROOT / "CHANGELOG.md"

VERSION = r"\d+\.\d+\.\d+"


def next_patch(version: str) -> str:
    """The next release in the line. Patches only: an automated release cannot
    decide that a breaking change happened, and guessing wrong is worse than a
    version number that moves in small steps."""
    major, minor, patch = (int(part) for part in version.strip().split("."))
    return f"{major}.{minor}.{patch + 1}"


def read_core_version(text: str) -> str:
    """The ``__version__`` in a package ``__init__.py``."""
    match = re.search(rf'__version__\s*=\s*"({VERSION})"', text)
    if match is None:
        raise ValueError("no __version__ assignment found")
    return match.group(1)


def _replace_once(text: str, pattern: str, replacement: str, *, what: str) -> str:
    updated, count = re.subn(pattern, replacement, text, count=1)
    if count != 1:
        raise ValueError(f"{what}: expected exactly one match, found {count}")
    return updated


def set_core_version(text: str, version: str) -> str:
    """``__version__`` in an ``__init__.py``. Used for core and for cloud, which
    spell the same assignment."""
    return _replace_once(
        text,
        rf'(__version__\s*=\s*)"{VERSION}"',
        rf'\g<1>"{version}"',
        what="__version__",
    )


def set_prd_version(text: str, version: str) -> str:
    """The default ``version`` label every generated PRD carries."""
    return _replace_once(
        text,
        rf'(\n\s*version:\s*str\s*=\s*)"{VERSION}"',
        rf'\g<1>"{version}"',
        what="PRDDocument.version",
    )


def set_readme_version(text: str, version: str) -> str:
    """The version badge: alt text, shields.io path, and the release link."""
    for pattern, replacement in (
        (rf"Version: {VERSION}", f"Version: {version}"),
        (rf"badge/version-{VERSION}-", f"badge/version-{version}-"),
        (rf"releases/tag/v{VERSION}", f"releases/tag/v{version}"),
    ):
        text = _replace_once(text, pattern, replacement, what="README version badge")
    return text


def set_tauri_version(text: str, version: str) -> str:
    """The desktop bundle's product version."""
    data = json.loads(text)
    data["version"] = version
    ending = "\n" if text.endswith("\n") else ""
    return json.dumps(data, indent=2) + ending


def set_cargo_version(text: str, version: str) -> str:
    """Only the ``[package]`` version - not a dependency's, which sits in the
    same file and would be corrupted by a blanket replace."""
    head, section, tail = text.partition("[package]")
    if not section:
        raise ValueError("Cargo.toml has no [package] section")
    updated, count = re.subn(
        rf'(^version\s*=\s*)"{VERSION}"', rf'\g<1>"{version}"', tail, count=1, flags=re.M
    )
    if count != 1:
        raise ValueError(f"Cargo.toml [package] version: expected one match, found {count}")
    return head + section + updated


def set_cargo_lock_version(text: str, version: str) -> str:
    """The workspace crate's entry in Cargo.lock. Without this the lockfile and
    the manifest disagree after a bump, and the next `cargo` invocation rewrites
    a line the release commit already claimed was final."""
    return _replace_once(
        text,
        rf'(name = "sourcework-desktop"\nversion = )"{VERSION}"',
        rf'\g<1>"{version}"',
        what="Cargo.lock sourcework-desktop version",
    )


def changelog_section(
    core: str,
    date: str,
    entries: list[str],
    cloud: str | None,
    cloud_entries: list[str],
) -> str:
    """One Keep-a-Changelog section, built from the commit subjects CI passed.
    Not a hand-written summary - an automated release cannot write one - but an
    honest list of what landed, with the cloud distribution named only when its
    own version moved."""
    lines = [f"## [{core}] — {date}", ""]
    if entries:
        lines += ["### Changed", ""]
        lines += [f"- {entry}" for entry in entries]
    else:
        lines += ["Maintenance release with no code changes."]
    if cloud is not None:
        lines += ["", f"#### Hosted — `sourcework-cloud` {cloud}", ""]
        if cloud_entries:
            lines += [f"- {entry}" for entry in cloud_entries]
        else:
            lines += ["Published alongside core."]
    return "\n".join(lines) + "\n\n"


def prepend_changelog(text: str, section: str) -> str:
    """Insert ``section`` before the newest existing release section, so the
    newest entry stays first."""
    lines = text.splitlines(keepends=True)
    for index, line in enumerate(lines):
        if line.startswith("## ["):
            return "".join(lines[:index]) + section + "".join(lines[index:])
    return text.rstrip("\n") + "\n\n" + section


def _read_notes(path: Path | None) -> list[str]:
    if path is None or not path.is_file():
        return []
    return [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _updates(core: str, cloud: str | None) -> dict[Path, Callable[[str], str]]:
    """Every file that carries a version, and how to set it. One place, so a
    new version string cannot be added to the project and forgotten here."""
    updates: dict[Path, Callable[[str], str]] = {
        CORE_VERSION: lambda text: set_core_version(text, core),
        PRD_MODELS: lambda text: set_prd_version(text, core),
        README: lambda text: set_readme_version(text, core),
        TAURI_CONF: lambda text: set_tauri_version(text, core),
        CARGO_TOML: lambda text: set_cargo_version(text, core),
        CARGO_LOCK: lambda text: set_cargo_lock_version(text, core),
    }
    if cloud is not None:
        updates[CLOUD_VERSION] = lambda text: set_core_version(text, cloud)
    return updates


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--notes-file", type=Path, help="core commit subjects, one per line")
    parser.add_argument(
        "--cloud-changed",
        action="store_true",
        help="bump and publish sourcework-cloud too (only when cloud/ changed)",
    )
    parser.add_argument("--cloud-notes-file", type=Path, help="cloud commit subjects")
    parser.add_argument("--date", default=dt.date.today().isoformat())
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv[1:])

    core = next_patch(read_core_version(CORE_VERSION.read_text(encoding="utf-8")))
    cloud = None
    if args.cloud_changed:
        cloud = next_patch(read_core_version(CLOUD_VERSION.read_text(encoding="utf-8")))

    updates = _updates(core, cloud)
    if args.dry_run:
        for path in updates:
            print(f"would update {path.relative_to(ROOT)}")
        print(core)
        return 0

    for path, transform in updates.items():
        path.write_text(transform(path.read_text(encoding="utf-8")), encoding="utf-8")

    section = changelog_section(
        core,
        args.date,
        _read_notes(args.notes_file),
        cloud,
        _read_notes(args.cloud_notes_file),
    )
    CHANGELOG.write_text(
        prepend_changelog(CHANGELOG.read_text(encoding="utf-8"), section), encoding="utf-8"
    )
    print(core)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
