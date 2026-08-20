"""Fetching llama-swap.

This is the one place the project downloads something and puts it on your PATH,
so the tests are mostly about what it refuses to do. Nothing here touches the
network: every download is stubbed, because a test that needs GitHub to be up is
a test that fails for reasons that have nothing to do with the code.
"""

from __future__ import annotations

import hashlib
import io
import tarfile
from pathlib import Path

import pytest

from sourcework import installer

BINARY = b"#!/bin/sh\necho llama-swap\n"


def _tarball(name: str = "llama-swap") -> bytes:
    """A release archive shaped like the real one: the binary in a directory."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as bundle:
        info = tarfile.TarInfo(f"llama-swap_250_linux_amd64/{name}")
        info.size = len(BINARY)
        info.mode = 0o755
        bundle.addfile(info, io.BytesIO(BINARY))
    return buffer.getvalue()


def _serve(monkeypatch, payload: bytes, digest: str | None = None, asset: str | None = None):
    """Answer both fetches - the checksums file, then the archive."""
    plan = installer.plan_for()
    listed = asset or plan.asset
    checksums = f"{digest or hashlib.sha256(payload).hexdigest()}  {listed}\n"

    def fake_fetch(url: str, timeout: float) -> bytes:
        return checksums.encode() if url.endswith("checksums.txt") else payload

    monkeypatch.setattr(installer, "_fetch", fake_fetch)
    return plan


def test_the_download_is_verified_against_the_published_checksum(monkeypatch, tmp_path: Path):
    """A verified fetch is the whole reason this is allowed to exist.

    Without it this command is "download a binary over the internet and run it",
    which is not a thing a PRD tool should teach anyone to do.
    """
    _serve(monkeypatch, _tarball())
    where = installer.install(destination=tmp_path / "llama-swap")
    assert where.read_bytes() == BINARY
    assert where.stat().st_mode & 0o111  # executable, or it was pointless


def test_a_checksum_mismatch_installs_nothing(monkeypatch, tmp_path: Path):
    """Refused, not warned about.

    A mismatch means the bytes are not the ones the project published. There is
    no version of that worth running, so there is no "continue anyway" path -
    and nothing may be left on disk for a later run to find and trust.
    """
    _serve(monkeypatch, _tarball(), digest="0" * 64)
    target = tmp_path / "llama-swap"
    with pytest.raises(installer.InstallError, match="checksum mismatch"):
        installer.install(destination=target)
    assert not target.exists()


def test_an_asset_missing_from_the_checksums_is_a_failure(monkeypatch, tmp_path: Path):
    """Not "skip the check for this one".

    Verification that lapses whenever it becomes inconvenient is not
    verification; it is a comment.
    """
    _serve(monkeypatch, _tarball(), asset="something-else.tar.gz")
    with pytest.raises(installer.InstallError, match="cannot be verified"):
        installer.install(destination=tmp_path / "llama-swap")


def test_an_existing_binary_is_not_replaced_without_being_asked(monkeypatch, tmp_path: Path):
    """The one already there may be newer, patched, or built by hand.

    Silently overwriting a binary somebody put on their own PATH is not an
    install, it is a surprise.
    """
    target = tmp_path / "llama-swap"
    target.write_bytes(b"mine")
    _serve(monkeypatch, _tarball())

    with pytest.raises(installer.InstallError, match="--force"):
        installer.install(destination=target)
    assert target.read_bytes() == b"mine"

    installer.install(destination=target, force=True)
    assert target.read_bytes() == BINARY


def test_an_archive_without_the_binary_is_reported_not_ignored(monkeypatch, tmp_path: Path):
    """An empty install that returns success is the worst of both outcomes."""
    _serve(monkeypatch, _tarball(name="something-else"))
    with pytest.raises(installer.InstallError, match="no llama-swap inside"):
        installer.install(destination=tmp_path / "llama-swap")


def test_an_unsupported_platform_refuses_instead_of_guessing(monkeypatch):
    """A wrong build does not run at all, so saying so now is the cheap outcome.

    The alternative - reaching for the nearest architecture - produces a 404 on
    a URL this module invented, which reads like the release is missing rather
    than like the platform is unsupported.
    """
    monkeypatch.setattr("platform.system", lambda: "SunOS")
    monkeypatch.setattr("platform.machine", lambda: "sparc")
    with pytest.raises(installer.InstallError, match="no build for this platform"):
        installer.plan_for()


def test_the_version_is_pinned_rather_than_resolved():
    """Two machines set up a week apart must get the same binary.

    Following "latest" also lets an upstream release change what this command
    does without anything here changing, which is not a property you want in
    the one function that downloads and executes something.
    """
    plan = installer.plan_for()
    assert installer.LLAMA_SWAP_VERSION in plan.url
    assert plan.url.startswith("https://github.com/mostlygeek/llama-swap/releases/download/")
    # The digest source must come from the same release, not a moving pointer.
    assert installer.LLAMA_SWAP_VERSION in plan.checksums_url
    assert plan.checksums_url.startswith("https://")


def test_the_plan_can_be_read_before_anything_is_fetched(monkeypatch):
    """`--dry-run` and the printed preamble both depend on this.

    An install you cannot audit afterwards should at least be one you can read
    beforehand, so building the plan must not touch the network.
    """
    def explode(url: str, timeout: float) -> bytes:  # pragma: no cover - must not run
        raise AssertionError(f"plan_for reached the network: {url}")

    monkeypatch.setattr(installer, "_fetch", explode)
    described = installer.plan_for().describe()
    assert "llama-swap" in described
    assert "https://" in described
