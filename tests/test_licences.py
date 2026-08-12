"""The licence gate.

Every case here comes from a real licence string a package has actually
shipped. The gate this replaced passed all of the blocked ones.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location(
    "check_licences", Path(__file__).resolve().parent.parent / "scripts" / "check_licences.py"
)
check_licences = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check_licences)


@pytest.mark.parametrize("licence", [
    "GNU General Public License v3 (GPLv3)",   # the one the old grep let through
    "GPL-3.0-only",
    "GPLv2",
    "GNU Affero General Public License v3",
    "AGPL-3.0",
    "SSPL-1.0",
    "Server Side Public License",
    "BUSL-1.1",
])
def test_copyleft_is_blocked(licence: str):
    assert check_licences.classify(licence) == "blocked", licence


@pytest.mark.parametrize("licence", [
    "MIT",
    "MIT License",
    "BSD-3-Clause",
    "Apache Software License",
    "Apache-2.0",
    "ISC",
    "Python Software Foundation License",
])
def test_permissive_passes(licence: str):
    assert check_licences.classify(licence) == "ok", licence


def test_a_dual_licence_takes_the_permissive_branch():
    # The user may choose MIT, so it is not a blocker - but it is reported.
    assert check_licences.classify("MIT OR GPL-2.0") == "dual"
    assert check_licences.classify("Apache-2.0 OR GPL-3.0") == "dual"


def test_an_empty_report_is_a_failure_not_a_pass(tmp_path: Path):
    """A licence step that produced nothing has not proven anything. Exiting 0
    here would show the same green tick as a real check."""
    empty = tmp_path / "licences.csv"
    empty.write_text("Name,Version,License\n", encoding="utf-8")
    assert check_licences.main(["x", str(empty)]) == 2


def test_a_clean_report_passes_and_a_dirty_one_does_not(tmp_path: Path):
    clean = tmp_path / "clean.csv"
    clean.write_text(
        "Name,Version,License\nfastapi,0.141.1,MIT License\nlxml,6.1.1,BSD-3-Clause\n",
        encoding="utf-8",
    )
    assert check_licences.main(["x", str(clean)]) == 0

    dirty = tmp_path / "dirty.csv"
    dirty.write_text(
        "Name,Version,License\nfastapi,0.141.1,MIT License\n"
        "evil,1.0,GNU General Public License v3 (GPLv3)\n",
        encoding="utf-8",
    )
    assert check_licences.main(["x", str(dirty)]) == 1


def test_a_missing_report_does_not_pass(tmp_path: Path):
    assert check_licences.main(["x", str(tmp_path / "nope.csv")]) == 2


@pytest.mark.parametrize("licence", ["UNKNOWN", "", "unknown license", "None"])
def test_an_unstated_licence_is_not_a_pass(licence: str):
    """Unknown is not permissive. A package stating no licence is exactly the
    one worth a human look, and passing it silently makes the gate report
    success for something nobody checked."""
    assert check_licences.classify(licence) == "unverified"


def test_a_report_full_of_unknowns_fails(tmp_path: Path):
    report = tmp_path / "l.csv"
    report.write_text("Name,Version,License\nmystery,1.0,UNKNOWN\n", encoding="utf-8")
    assert check_licences.main(["x", str(report)]) == 1


@pytest.mark.parametrize("licence", [
    "LGPL-3.0", "LGPLv3", "GNU Lesser General Public License v3",
    "GNU Lesser General Public License v3 (LGPLv3)",   # what pip-licenses prints
    "GNU Library or Lesser General Public License (LGPL)",
])
def test_a_lesser_licence_passes_but_is_named(licence: str):
    """pystray is LGPL and this project ships a desktop build. Linking is fine;
    bundling carries a relink obligation, and a gate that stays silent about it
    is how that obligation gets discovered by someone else."""
    assert check_licences.classify(licence) == "lesser"
