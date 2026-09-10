"""The release bumper.

These tests run against the real files, because the failure they guard is a
version written down in a place the bumper does not know about: the README badge
that still said 0.6.0 while the package said 0.6.1, or a Cargo.lock that
disagrees with its manifest after the release commit.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "bump_version", Path(__file__).resolve().parent.parent / "scripts" / "bump_version.py"
)
bump_version = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bump_version)

CORE = bump_version.CORE_VERSION
README = bump_version.README
TAURI = bump_version.TAURI_CONF
CARGO = bump_version.CARGO_TOML
CARGO_LOCK = bump_version.CARGO_LOCK


def _current() -> str:
    return bump_version.read_core_version(CORE.read_text(encoding="utf-8"))


def test_the_next_release_is_a_patch():
    assert bump_version.next_patch("1.2.3") == "1.2.4"
    assert bump_version.next_patch("1.9.9") == "1.9.10"


def test_the_version_is_read_from_the_assignment_hatch_reads():
    assert bump_version.read_core_version('__version__ = "1.2.3"\n') == "1.2.3"


def test_the_prd_default_moves_with_the_version():
    text = 'class PRDDocument(BaseModel):\n    version: str = "1.2.3"\n'
    updated = bump_version.set_prd_version(text, "9.9.9")
    assert 'version: str = "9.9.9"' in updated


def test_only_the_package_version_in_cargo_toml_is_touched():
    text = '[package]\nname = "x"\nversion = "1.2.3"\n\n[dependencies]\nserde = "1.2.3"\n'
    updated = bump_version.set_cargo_version(text, "9.9.9")
    assert 'version = "9.9.9"' in updated
    assert 'serde = "1.2.3"' in updated


def test_the_lockfile_entry_moves_with_the_manifest():
    text = (
        '[[package]]\nname = "other"\nversion = "1.2.3"\n\n'
        '[[package]]\nname = "sourcework-desktop"\nversion = "1.2.3"\ndependencies = []\n'
    )
    updated = bump_version.set_cargo_lock_version(text, "9.9.9")
    assert 'name = "sourcework-desktop"\nversion = "9.9.9"' in updated
    assert 'name = "other"\nversion = "1.2.3"' in updated


def test_the_tauri_manifest_keeps_its_shape():
    updated = bump_version.set_tauri_version(TAURI.read_text(encoding="utf-8"), "9.9.9")
    data = json.loads(updated)
    assert data["version"] == "9.9.9"
    assert data["productName"] == "SourceWork"
    assert updated.endswith("\n")


def test_the_readme_badge_moves_with_the_version():
    current = _current()
    updated = bump_version.set_readme_version(README.read_text(encoding="utf-8"), "9.9.9")
    assert "Version: 9.9.9" in updated
    assert "badge/version-9.9.9-success.svg" in updated
    assert "releases/tag/v9.9.9" in updated
    assert current not in updated


def test_every_real_file_that_writes_the_version_down_is_bumped():
    """Run the bumper's own transforms over the real files - a transform that
    misses its pattern on the actual content is the bug, and it must fail here
    rather than in a release that ships two different version numbers."""
    for path, transform in bump_version._updates("9.9.9", "9.9.9").items():
        original = path.read_text(encoding="utf-8")
        updated = transform(original)
        assert updated != original, f"{path.name}: the transform matched nothing"
        assert "9.9.9" in updated, f"{path.name} was not bumped"


def test_no_new_file_carries_the_version_without_the_bumper_knowing():
    """The regression guard. A version string added to a file the bumper does
    not update is exactly how the README badge drifted before this existed."""
    current = _current()
    known = set(bump_version._updates("9.9.9", None))
    skip_dirs = {
        ".git", ".venv", "venv", "target", "node_modules", "out", "workspace",
        "__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache",
        # Tests quote versions on purpose; they are not shipped artifacts.
        "tests",
    }
    suffixes = {".py", ".md", ".json", ".toml", ".lock", ".rs", ".yml", ".yaml"}

    found: set[Path] = set()
    for path in bump_version.ROOT.rglob("*"):
        if not path.is_file() or path.suffix not in suffixes:
            continue
        if any(part in skip_dirs for part in path.parts):
            continue
        # The changelog names every past version by design; it is written by
        # the bumper directly, not by one of these transforms.
        if path.name == "CHANGELOG.md":
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if current in text:
            found.add(path)

    unknown = found - known
    assert not unknown, (
        f"version {current} is written in files the bumper does not update: "
        f"{sorted(str(p.relative_to(bump_version.ROOT)) for p in unknown)}"
    )


def test_a_changelog_section_lists_what_landed():
    section = bump_version.changelog_section("0.6.2", "2026-09-10", ["Fix the critic"], None, [])
    assert section.startswith("## [0.6.2] — 2026-09-10")
    assert "- Fix the critic" in section


def test_an_empty_release_says_so_rather_than_rendering_nothing():
    section = bump_version.changelog_section("0.6.2", "2026-09-10", [], None, [])
    assert "Maintenance release" in section


def test_the_hosted_subsection_appears_only_when_cloud_moved():
    without = bump_version.changelog_section("0.6.2", "2026-09-10", ["a"], None, [])
    assert "Hosted" not in without
    hosted = bump_version.changelog_section("0.6.2", "2026-09-10", ["a"], "0.2.1", ["c"])
    assert "`sourcework-cloud` 0.2.1" in hosted
    assert "- c" in hosted


def test_the_new_section_goes_above_the_previous_release():
    text = "# Changelog\n\nHeader.\n\n## [0.6.0] — 2026-01-01\n\nOld.\n"
    updated = bump_version.prepend_changelog(text, "## [0.6.1] — 2026-02-02\n\nNew.\n\n")
    assert updated.index("## [0.6.1]") < updated.index("## [0.6.0]")
    assert updated.startswith("# Changelog\n\nHeader.\n\n")
