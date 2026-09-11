#!/usr/bin/env python3
"""Fetch a relocatable CPython and install SourceWork into it.

The desktop shell is only self-contained if it carries its own interpreter, so
the release build runs this for the runner's target triple and then bundles the
result as a Tauri resource. A developer never runs it: `cargo tauri dev` still
uses the checkout's `.venv`, and the shell falls back to `PATH` when no bundled
runtime is present.

    scripts/fetch_python.py --dest desktop/src-tauri/python --install .

The build is pinned by release tag, never "latest": an artifact that changes
under a release is not a release. The exact patch version is read off the asset
name, so bumping the tag is the only change needed to move to a new Python.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path

OWNER = "astral-sh"
REPO = "python-build-standalone"

RELEASE = "20260901"
"""The pinned python-build-standalone release tag. Bump deliberately."""

SERIES = "3.12"
"""The Python line. The patch version travels inside the release."""

API = f"https://api.github.com/repos/{OWNER}/{REPO}/releases/tags/{RELEASE}"


def triple_for(system: str, machine: str) -> str:
    """The LLVM target triple python-build-standalone names its assets with.

    Derived from what the runner reports rather than passed in, so a build on a
    new architecture fails with "no triple" instead of silently fetching the
    x86_64 runtime."""
    machine = machine.lower()
    if system == "Linux":
        arch = {"x86_64": "x86_64", "amd64": "x86_64", "aarch64": "aarch64", "arm64": "aarch64"}
    elif system == "Darwin":
        arch = {"arm64": "aarch64", "aarch64": "aarch64", "x86_64": "x86_64"}
    elif system == "Windows":
        arch = {"amd64": "x86_64", "x86_64": "x86_64", "arm64": "aarch64"}
    else:
        arch = {}
    chosen = arch.get(machine)
    if chosen is None:
        raise LookupError(f"no python-build-standalone triple for {system}/{machine}")
    suffix = {
        "Linux": "unknown-linux-gnu",
        "Darwin": "apple-darwin",
        "Windows": "pc-windows-msvc",
    }[system]
    return f"{chosen}-{suffix}"


def select_asset(assets: list[dict], series: str, triple: str) -> dict:
    """The one ``install_only_stripped`` asset for ``series`` and ``triple``.

    Stripped, not the full archive: this ships inside an installer, and the full
    one carries debug symbols nobody will ever use."""
    matches = [
        asset
        for asset in assets
        if str(asset.get("name", "")).startswith(f"cpython-{series}.")
        and str(asset.get("name", "")).endswith(f"-{triple}-install_only_stripped.tar.gz")
    ]
    if len(matches) != 1:
        raise LookupError(
            f"expected exactly one cpython-{series} install_only_stripped asset for "
            f"{triple}, found {len(matches)}"
        )
    return matches[0]


def _get(url: str, token: str | None) -> urllib.request.Request:
    request = urllib.request.Request(
        url, headers={"User-Agent": "sourcework-build", "Accept": "application/vnd.github+json"}
    )
    if token:
        # Optional: without it GitHub rate-limits unauthenticated CI to 60
        # requests an hour, which a matrix of three runners can walk into.
        request.add_header("Authorization", f"Bearer {token}")
    return request


def release_assets(token: str | None) -> list[dict]:
    with urllib.request.urlopen(_get(API, token), timeout=60) as response:
        return list(json.load(response)["assets"])


def download(url: str, dest: Path, token: str | None) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(_get(url, token), timeout=300) as response, dest.open("wb") as out:
        shutil.copyfileobj(response, out)


def extract(archive: Path, dest: Path) -> None:
    """Unpack the runtime to ``dest``, dropping the archive's leading ``python/``.

    Extracted to a scratch directory first and moved into place, so a failed
    download never leaves a half-populated runtime for `cargo tauri build` to
    bundle."""
    with tempfile.TemporaryDirectory() as scratch:
        root = Path(scratch)
        with tarfile.open(archive, "r:gz") as tar:
            try:
                tar.extractall(root, filter="data")
            except TypeError:  # `filter` only exists on newer Pythons
                tar.extractall(root)
        source = root / "python"
        if not source.is_dir():
            raise RuntimeError(f"{archive.name} did not contain a python/ directory")
        shutil.rmtree(dest, ignore_errors=True)
        shutil.move(str(source), str(dest))


def interpreter(dest: Path, triple: str) -> Path:
    return dest / "python.exe" if "windows" in triple else dest / "bin" / "python3"


TCL_TK = (
    "python*/lib-dynload/_tkinter*",
    "python*/tkinter",
    "libtcl*",
    "libtk*",
    "tcl[0-9]*",
    "tk[0-9]*",
    "itcl*",
    "thread[0-9]*",
)


def prune(dest: Path) -> list[str]:
    """Remove the Tcl/Tk stack, returning what was removed.

    python-build-standalone builds CPython with ``_tkinter``, whose extension
    links ``libtcl9tk9.0.so`` without an RPATH. linuxdeploy cannot resolve that
    against the AppDir and fails the whole AppImage with "Could not find
    dependency", which Tauri reports only as "failed to run linuxdeploy".
    SourceWork is a webview app and never imports tkinter, so the stack is
    removed rather than explained to the bundler - and it is dead weight twice
    over.
    """
    library = dest / "lib"
    removed: list[str] = []
    for pattern in TCL_TK:
        for path in library.glob(pattern):
            if path.is_dir():
                shutil.rmtree(path, ignore_errors=True)
            else:
                path.unlink(missing_ok=True)
            removed.append(path.name)
    return removed


def install(dest: Path, triple: str, project: str, extras: str) -> None:
    python = interpreter(dest, triple)
    spec = str(Path(project).resolve())
    if extras:
        spec += f"[{extras}]"
    print(f"installing {spec} into {python}", flush=True)
    subprocess.run(
        [str(python), "-m", "pip", "install", "--no-input", "--disable-pip-version-check", spec],
        check=True,
    )


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dest", type=Path, required=True, help="where to unpack the runtime")
    parser.add_argument("--triple", help="target triple; derived from the host when omitted")
    parser.add_argument("--install", help="project directory to pip-install into the runtime")
    parser.add_argument("--extras", default="ingest", help="extras for --install (default: ingest)")
    args = parser.parse_args(argv[1:])

    triple = args.triple or triple_for(platform.system(), platform.machine())
    token = os.environ.get("GITHUB_TOKEN")
    asset = select_asset(release_assets(token), SERIES, triple)
    print(f"fetching {asset['name']}", flush=True)

    with tempfile.TemporaryDirectory() as scratch:
        archive = Path(scratch) / asset["name"]
        download(asset["browser_download_url"], archive, token)
        extract(archive, args.dest)

    dropped = prune(args.dest)
    if dropped:
        print(f"removed unused Tcl/Tk: {', '.join(sorted(dropped))}", flush=True)

    if args.install:
        install(args.dest, triple, args.install, args.extras)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
