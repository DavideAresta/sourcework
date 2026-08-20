"""Fetching llama-swap, the one runtime this project will install for you.

SourceWork drives models; it does not manage your model runtime. That boundary
is why there is no installer for llama.cpp here and why there is not going to
be: its releases are a matrix of CUDA, ROCm, Vulkan, Metal and CPU builds, and
choosing wrong does not fail - it gives you a working binary that is an order of
magnitude slower, which is exactly the quiet degradation this project treats as
a defect. A link and a clear error beat a confident guess.

llama-swap is the exception, on three counts. The project already ships the
script that runs it (``scripts/llama-swap.sh``), a config for it and a generator
for that config, so the boundary was already crossed everywhere except the
download. It is one static binary with no accelerator variants, so there is
nothing to guess. And its releases carry a checksums file, so the fetch can be
*verified* rather than trusted.

That last point is the whole design. Downloading something from the internet and
putting it on PATH is a supply-chain step, so this module:

* pins a version rather than following "latest", so two machines get one binary
* verifies the SHA-256 against the published checksums file, and refuses on a
  mismatch rather than warning about it
* installs per-user, never system-wide
* will not overwrite an existing binary unless asked
* says the URL and the digest before it fetches, because an install you cannot
  audit afterwards is one you should be able to read beforehand

There is deliberately no automatic install: nothing calls this on first run, and
no HTTP route reaches it. The UI ships no authentication, and "a web request can
cause a binary to be downloaded and executed" is not a trade this program makes.
"""

from __future__ import annotations

import hashlib
import logging
import platform
import shutil
import tarfile
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

__all__ = ["LLAMA_SWAP_VERSION", "InstallError", "Plan", "plan_for", "install"]

LLAMA_SWAP_VERSION = "250"
"""The version fetched unless one is asked for.

Pinned, not resolved from the releases API. "Latest" means two machines set up a
week apart do not run the same binary, and it means an upstream release can
change what this command does without anything here changing.
"""

REPO = "mostlygeek/llama-swap"

_BASE = f"https://github.com/{REPO}/releases/download"

PUBLISHED = frozenset({
    ("linux", "amd64"), ("linux", "arm64"),
    ("darwin", "amd64"), ("darwin", "arm64"),
    ("freebsd", "amd64"), ("windows", "amd64"),
})
"""The (os, arch) pairs llama-swap actually ships at :data:`LLAMA_SWAP_VERSION`.

Written out rather than inferred, so an unsupported platform is a clear refusal
here instead of a 404 on a URL this module invented."""


class InstallError(RuntimeError):
    """Anything that stops the install. Always actionable, never a stack trace."""


@dataclass(frozen=True)
class Plan:
    """Exactly what an install would do, before it does any of it.

    Separated from :func:`install` so the command can print it, the tests can
    assert on it without a network, and a reader can check the URL against the
    project's own release page.
    """

    version: str
    asset: str
    url: str
    checksums_url: str
    destination: Path

    def describe(self) -> str:
        return (
            f"llama-swap {self.version}\n"
            f"  from      {self.url}\n"
            f"  verify    {self.checksums_url}\n"
            f"  install   {self.destination}"
        )


def _target() -> str:
    """``<os>_<arch>`` as llama-swap names its assets.

    Unknown platforms raise rather than guessing at the closest build: a wrong
    binary here is one that will not run at all, and saying so now is cheaper
    than a confusing exec failure later.
    """
    system = platform.system().lower()
    machine = platform.machine().lower()

    arch = {
        "x86_64": "amd64", "amd64": "amd64",
        "aarch64": "arm64", "arm64": "arm64",
    }.get(machine)
    if arch is not None and (system, arch) in PUBLISHED:
        return f"{system}_{arch}"

    raise InstallError(
        f"llama-swap publishes no build for this platform ({system}/{machine}). "
        f"Install it yourself from https://github.com/{REPO}/releases, "
        "or set LLAMA_SWAP_BIN to point at it."
    )


def default_destination() -> Path:
    """``~/.local/bin/llama-swap``.

    Per-user on purpose. A system-wide install needs root, and asking for root
    to run a PRD tool is a trade nobody agreed to.
    """
    return Path.home() / ".local" / "bin" / ("llama-swap.exe" if _is_windows() else "llama-swap")


def _is_windows() -> bool:
    return platform.system().lower() == "windows"


def plan_for(version: str = LLAMA_SWAP_VERSION, destination: Path | None = None) -> Plan:
    """What :func:`install` would fetch, without fetching it."""
    target = _target()
    suffix = "zip" if target.startswith("windows") else "tar.gz"
    asset = f"llama-swap_{version}_{target}.{suffix}"
    return Plan(
        version=version,
        asset=asset,
        url=f"{_BASE}/v{version}/{asset}",
        checksums_url=f"{_BASE}/v{version}/llama-swap_{version}_checksums.txt",
        destination=destination or default_destination(),
    )


def _fetch(url: str, timeout: float) -> bytes:
    import httpx

    try:
        response = httpx.get(url, timeout=timeout, follow_redirects=True)
        response.raise_for_status()
    except Exception as exc:  # noqa: BLE001 - every failure is the same advice
        raise InstallError(f"could not download {url}: {exc}") from exc
    return response.content


def _expected_digest(checksums: str, asset: str) -> str:
    """The published SHA-256 for ``asset``.

    A missing line is a failure, not a reason to skip the check: an install that
    silently stops verifying the moment verification gets awkward provides no
    guarantee at all.
    """
    for line in checksums.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1] == asset:
            return parts[0].lower()
    raise InstallError(
        f"{asset} is not listed in the published checksums, so it cannot be verified"
    )


def _extract_binary(archive: Path, into: Path) -> Path:
    """The ``llama-swap`` binary out of the release archive.

    Members are matched by *basename* and extracted one at a time to a path this
    function chooses. Handing an archive's own member names to an extractor is
    how ``../`` in a tarball writes outside the directory you meant.
    """
    wanted = "llama-swap.exe" if _is_windows() else "llama-swap"
    out = into / wanted

    if archive.name.endswith(".zip"):
        with zipfile.ZipFile(archive) as bundle:
            names = [n for n in bundle.namelist() if Path(n).name == wanted]
            if not names:
                raise InstallError(f"no {wanted} inside {archive.name}")
            out.write_bytes(bundle.read(names[0]))
        return out

    with tarfile.open(archive) as bundle:
        members = [m for m in bundle.getmembers() if m.isfile() and Path(m.name).name == wanted]
        if not members:
            raise InstallError(f"no {wanted} inside {archive.name}")
        source = bundle.extractfile(members[0])
        if source is None:  # pragma: no cover - isfile() already excluded this
            raise InstallError(f"could not read {wanted} from {archive.name}")
        out.write_bytes(source.read())
    return out


def install(
    version: str = LLAMA_SWAP_VERSION,
    *,
    destination: Path | None = None,
    force: bool = False,
    timeout: float = 60.0,
) -> Path:
    """Download, verify and install llama-swap. Returns where it landed.

    Raises :class:`InstallError` for every foreseeable failure, with the fix in
    the message. Nothing is written until the digest matches.
    """
    plan = plan_for(version, destination)

    if plan.destination.exists() and not force:
        raise InstallError(
            f"{plan.destination} already exists. Pass --force to replace it, "
            "or remove it first."
        )

    checksums = _fetch(plan.checksums_url, timeout).decode("utf-8", "replace")
    expected = _expected_digest(checksums, plan.asset)

    payload = _fetch(plan.url, timeout)
    actual = hashlib.sha256(payload).hexdigest()
    if actual != expected:
        # Never "warn and continue". A mismatch means the bytes are not the ones
        # the project published, and there is no version of that worth running.
        raise InstallError(
            f"checksum mismatch for {plan.asset}\n"
            f"  expected {expected}\n"
            f"  got      {actual}\n"
            "Nothing was installed."
        )

    with tempfile.TemporaryDirectory() as scratch:
        work = Path(scratch)
        archive = work / plan.asset
        archive.write_bytes(payload)
        binary = _extract_binary(archive, work)
        binary.chmod(0o755)
        plan.destination.parent.mkdir(parents=True, exist_ok=True)
        # Replaces atomically where the filesystem allows it, and works across
        # the /tmp boundary where os.replace would not.
        shutil.move(str(binary), str(plan.destination))

    plan.destination.chmod(0o755)
    logger.info("installed llama-swap %s at %s", plan.version, plan.destination)
    return plan.destination


def on_path(destination: Path | None = None) -> bool:
    """Would a shell find the binary we just installed?

    ``~/.local/bin`` is on PATH for most desktop Linux and on almost no macOS,
    so an install that "worked" and a command that is still not found is a
    common enough ending to be worth saying out loud.
    """
    target = destination or default_destination()
    found = shutil.which(target.name)
    return found is not None and Path(found).resolve() == target.resolve()
