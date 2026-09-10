"""Resolve an :class:`InputRef` into bytes + a media type.

Supported URI shapes:
  ``file:///abs/path``            local file (also bare ``/abs/path``)
  ``https://...``                 fetched over HTTP
  ``confluence://SPACE/12345``    handled by the Confluence agent, not here
  ``inline:``                     the payload is in ``content_b64`` or ``text``
"""

from __future__ import annotations

import base64
import ipaddress
import mimetypes
import socket
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx

from sourcework.models import InputRef, Modality

MAX_BYTES = 64 * 1024 * 1024
MAX_REDIRECTS = 5


_EXT_MODALITY = {
    ".pdf": Modality.DOCUMENT,
    ".docx": Modality.DOCUMENT,
    ".doc": Modality.DOCUMENT,
    ".md": Modality.DOCUMENT,
    ".txt": Modality.DOCUMENT,
    ".rtf": Modality.DOCUMENT,
    ".html": Modality.DOCUMENT,
    ".htm": Modality.DOCUMENT,
    ".pptx": Modality.DOCUMENT,
    ".xlsx": Modality.SPREADSHEET,
    ".csv": Modality.SPREADSHEET,
    ".png": Modality.IMAGE,
    ".jpg": Modality.IMAGE,
    ".jpeg": Modality.IMAGE,
    ".gif": Modality.IMAGE,
    ".webp": Modality.IMAGE,
    ".bmp": Modality.IMAGE,
    ".vtt": Modality.TRANSCRIPT,
    ".srt": Modality.TRANSCRIPT,
    ".json": Modality.TRANSCRIPT,
}


class FetchError(RuntimeError):
    pass


class FetchRefused(FetchError):
    """The URI resolves somewhere ingestion is not allowed to go.

    A subclass, not a sibling: every caller already handles FetchError by
    failing that one source and carrying on, and a refusal that escaped as an
    unhandled exception would take the whole run down instead.
    """


def guess_modality(uri: str, media_type: str | None = None) -> Modality:
    if media_type:
        if media_type.startswith("image/"):
            return Modality.IMAGE
        if media_type in ("text/vtt", "application/x-subrip"):
            return Modality.TRANSCRIPT
    suffix = Path(urlparse(uri).path).suffix.lower()
    return _EXT_MODALITY.get(suffix, Modality.DOCUMENT)


def guess_media_type(uri: str) -> str:
    guessed, _ = mimetypes.guess_type(urlparse(uri).path)
    return guessed or "application/octet-stream"


def _public_addresses(host: str | None, uri: str) -> list[str]:
    """Resolve ``host`` and refuse unless every address is globally routable.

    Returns the resolved addresses so the caller can connect to one of *these*,
    rather than letting httpx resolve the name a second time. That second
    resolution is the gap the old check left open: a name can answer with a
    public address for the check and ``127.0.0.1`` for the connect (DNS
    rebinding). Everything that is not globally routable is refused, which
    covers loopback, link-local, the private ranges, CGNAT ``100.64.0.0/10``
    and reserved space in one predicate instead of a hand-maintained list.
    """
    from sourcework.config import settings

    if not host:
        raise FetchRefused(f"No host to check in {uri!r}")

    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except OSError as exc:
        raise FetchError(f"Cannot resolve {host!r}: {exc}") from exc

    addresses = [info[4][0] for info in infos]
    if not addresses:
        raise FetchError(f"Cannot resolve {host!r}")

    if settings().security.allow_private_fetch:
        return addresses

    for raw in addresses:
        try:
            address = ipaddress.ip_address(raw)
        except ValueError as exc:  # pragma: no cover - getaddrinfo yields literals
            raise FetchRefused(f"Refusing to fetch {uri!r}: unparseable address {raw!r}") from exc
        # Checked per resolved address, not on the hostname: a name that
        # resolves to 127.0.0.1 is the standard way around a string-matching
        # blocklist, and a name with several A records only needs one bad one.
        if not address.is_global:
            raise FetchRefused(
                f"Refusing to fetch {uri!r}: {host} resolves to {address}, which is not "
                "a public address. Set SOURCEWORK_SECURITY__ALLOW_PRIVATE_FETCH=1 if your "
                "documents really do live there."
            )
    return addresses


def _refuse_private_target(host: str | None, uri: str) -> None:
    """Refuse a destination that is not globally routable. See :func:`_public_addresses`."""
    _public_addresses(host, uri)


def _pin(uri: str) -> tuple[str, dict[str, str], dict[str, object]]:
    """Rewrite ``uri`` to connect to the address that was actually vetted.

    Returns ``(url, headers, extensions)``. The URL names the vetted IP so
    httpx cannot re-resolve to something else; ``Host``/SNI carry the original
    name so virtual hosting and TLS certificate validation still work.
    """
    parsed = urlparse(uri)
    addresses = _public_addresses(parsed.hostname, uri)
    host = parsed.hostname or ""
    ip = addresses[0]
    port = parsed.port
    default_port = 443 if parsed.scheme == "https" else 80
    netloc = f"[{ip}]" if ":" in ip else ip
    if port and port != default_port:
        netloc = f"{netloc}:{port}"
    pinned = parsed._replace(netloc=netloc).geturl()
    headers = {"Host": host if not port or port == default_port else f"{host}:{port}"}
    extensions: dict[str, object] = {"sni_hostname": host} if parsed.scheme == "https" else {}
    return pinned, headers, extensions


async def read_capped(
    resp: httpx.Response, url: str, max_bytes: int = MAX_BYTES
) -> tuple[bytes, str]:
    """Read ``resp`` fully but refuse to exceed ``max_bytes``.

    Streams, so the limit is enforced while the body arrives: reading
    ``resp.content`` first would buffer an unbounded response and only then
    compare it to the cap, which is not a cap.
    """
    declared = resp.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > max_bytes:
        raise FetchError(f"{url} exceeds the size limit")
    chunks: list[bytes] = []
    total = 0
    async for chunk in resp.aiter_bytes():
        total += len(chunk)
        if total > max_bytes:
            raise FetchError(f"{url} exceeds the size limit")
        chunks.append(chunk)
    media = resp.headers.get("content-type", "").split(";")[0].strip()
    return b"".join(chunks), media


async def fetch_bytes(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    timeout: float = 60.0,
    max_bytes: int = MAX_BYTES,
) -> tuple[bytes, str]:
    """GET ``url`` through the SSRF policy, returning capped ``(data, content_type)``.

    Public so the Confluence client can borrow exactly the same guarantees for
    its signed media download: every redirect hop is vetted (and pinned to the
    address that was vetted), and the body is streamed against ``max_bytes``
    rather than buffered first.
    """
    # Redirects are followed one hop at a time so every hop is vetted and
    # pinned. With `follow_redirects=True` only the first URL is checked, and a
    # public host that answers 302 -> http://169.254.169.254/ walks straight
    # past it into the cloud metadata service.
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as http:
        current = url
        for _ in range(MAX_REDIRECTS + 1):
            pinned, host_headers, extensions = _pin(current)
            request_headers = dict(headers or {})
            request_headers.update(host_headers)
            async with http.stream(
                "GET", pinned, headers=request_headers, extensions=extensions
            ) as resp:
                if resp.is_redirect:
                    location = resp.headers.get("location")
                    if location:
                        current = urljoin(current, location)
                        continue
                resp.raise_for_status()
                return await read_capped(resp, current, max_bytes)
        raise FetchError(f"{url} redirected more than {MAX_REDIRECTS} times")


async def fetch(ref: InputRef) -> tuple[bytes, str]:
    """Return ``(data, media_type)`` for an input reference."""
    if ref.content_b64:
        return base64.b64decode(ref.content_b64), ref.media_type or guess_media_type(ref.uri)
    if ref.text is not None:
        return ref.text.encode("utf-8"), ref.media_type or "text/plain"

    parsed = urlparse(ref.uri)
    scheme = parsed.scheme.lower()

    if scheme in ("", "file"):
        path = Path(parsed.path if scheme == "file" else ref.uri)
        if not path.is_file():
            raise FetchError(f"No such file: {path}")
        if path.stat().st_size > MAX_BYTES:
            raise FetchError(f"{path} exceeds the {MAX_BYTES // 1024 // 1024} MB limit")
        return path.read_bytes(), ref.media_type or guess_media_type(ref.uri)

    if scheme in ("http", "https"):
        data, media = await fetch_bytes(ref.uri)
        return data, ref.media_type or media or guess_media_type(ref.uri)

    raise FetchError(f"Unsupported URI scheme {scheme!r} for {ref.uri!r}")
