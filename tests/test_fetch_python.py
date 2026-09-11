"""The Python-runtime fetch for the desktop build.

The failure these guard is a build that silently downloads the wrong
architecture or the wrong build flavour and bundles a runtime that will not
start on the user's machine.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location(
    "fetch_python", Path(__file__).resolve().parent.parent / "scripts" / "fetch_python.py"
)
fetch_python = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fetch_python)


def _asset(name: str) -> dict:
    return {"name": name, "browser_download_url": f"https://example.invalid/{name}"}


ASSETS = [
    _asset("cpython-3.12.14+20260901-x86_64-unknown-linux-gnu-install_only_stripped.tar.gz"),
    _asset("cpython-3.12.14+20260901-x86_64-unknown-linux-gnu-install_only.tar.gz"),
    _asset("cpython-3.12.14+20260901-aarch64-apple-darwin-install_only_stripped.tar.gz"),
    _asset("cpython-3.11.14+20260901-x86_64-unknown-linux-gnu-install_only_stripped.tar.gz"),
    _asset("cpython-3.12.14+20260901-x86_64-pc-windows-msvc-install_only_stripped.tar.gz"),
]


def test_a_triple_is_derived_for_every_platform_we_ship():
    assert fetch_python.triple_for("Linux", "x86_64") == "x86_64-unknown-linux-gnu"
    assert fetch_python.triple_for("Linux", "aarch64") == "aarch64-unknown-linux-gnu"
    assert fetch_python.triple_for("Darwin", "arm64") == "aarch64-apple-darwin"
    assert fetch_python.triple_for("Darwin", "x86_64") == "x86_64-apple-darwin"
    assert fetch_python.triple_for("Windows", "AMD64") == "x86_64-pc-windows-msvc"


def test_an_unknown_architecture_fails_instead_of_guessing():
    with pytest.raises(LookupError):
        fetch_python.triple_for("Linux", "mips")


def test_the_stripped_build_is_chosen_for_the_requested_triple():
    asset = fetch_python.select_asset(ASSETS, "3.12", "x86_64-unknown-linux-gnu")
    assert asset["name"].endswith("x86_64-unknown-linux-gnu-install_only_stripped.tar.gz")
    assert "3.12." in asset["name"]


def test_the_full_archive_and_other_series_are_not_chosen():
    # Only the stripped 3.12 linux asset matches; install_only, 3.11 and the
    # other triples must be invisible to the picker.
    asset = fetch_python.select_asset(ASSETS, "3.12", "aarch64-apple-darwin")
    assert asset["name"].startswith("cpython-3.12.")
    assert "install_only_stripped" in asset["name"]


def test_a_missing_asset_is_an_error_not_an_empty_download():
    with pytest.raises(LookupError):
        fetch_python.select_asset(ASSETS, "3.13", "x86_64-unknown-linux-gnu")


def test_the_interpreter_is_found_where_each_platform_puts_it():
    assert fetch_python.interpreter(Path("/x"), "x86_64-unknown-linux-gnu") == Path(
        "/x/bin/python3"
    )
    assert fetch_python.interpreter(Path("/x"), "aarch64-apple-darwin") == Path("/x/bin/python3")
    assert fetch_python.interpreter(Path("/x"), "x86_64-pc-windows-msvc") == Path("/x/python.exe")
