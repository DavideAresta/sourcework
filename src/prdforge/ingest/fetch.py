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
from urllib.parse import urlparse

import httpx

from prdforge.models import InputRef, Modality

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


def _refuse_private_target(host: str | None, uri: str) -> None:
    """Refuse loopback, link-local, and private-range destinations.

    Ingestion fetches a URI somebody handed the system, and the ability to make
    the *server* issue that request is the whole of SSRF: ``169.254.169.254``
    is cloud credentials, ``127.0.0.1`` is every unauthenticated admin port on
    the box, and a private range is the rest of the network the server can see
    and the caller cannot.

    Nothing legitimate ingests a document from a link-local address, so this is
    a refusal rather than a warning. ``PRDFORGE_SECURITY__ALLOW_PRIVATE_FETCH``
    exists for the deployment whose document store genuinely is on 10.x, and it
    has to be turned on deliberately.
    """
    from prdforge.config import settings

    if settings().security.allow_private_fetch:
        return
    if not host:
        raise FetchRefused(f"No host to check in {uri!r}")

    try:
        infos = socket.getaddrinfo(host, None)
    except OSError as exc:
        raise FetchError(f"Cannot resolve {host!r}: {exc}") from exc

    for info in infos:
        address = ipaddress.ip_address(info[4][0])
        # Checked per resolved address, not on the hostname: a name that
        # resolves to 127.0.0.1 is the standard way around a string-matching
        # blocklist, and a name with several A records only needs one bad one.
        if (address.is_private or address.is_loopback or address.is_link_local
                or address.is_reserved or address.is_multicast):
            raise FetchRefused(
                f"Refusing to fetch {uri!r}: {host} resolves to {address}, which is not "
                "a public address. Set PRDFORGE_SECURITY__ALLOW_PRIVATE_FETCH=1 if your "
                "documents really do live there."
            )

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
        _refuse_private_target(parsed.hostname, ref.uri)
        # Redirects are followed one hop at a time so every hop is checked. With
        # `follow_redirects=True` the check above guards only the first URL, and
        # a public host that answers 302 -> http://169.254.169.254/ walks
        # straight past it into the cloud metadata service.
        async with httpx.AsyncClient(timeout=60.0, follow_redirects=False) as http:
            url = ref.uri
            for _ in range(MAX_REDIRECTS + 1):
                resp = await http.get(url)
                if resp.is_redirect and resp.headers.get("location"):
                    url = str(resp.next_request.url if resp.next_request else resp.headers["location"])
                    _refuse_private_target(urlparse(url).hostname, url)
                    continue
                resp.raise_for_status()
                if len(resp.content) > MAX_BYTES:
                    raise FetchError(f"{ref.uri} exceeds the size limit")
                media = resp.headers.get("content-type", "").split(";")[0].strip()
                return resp.content, ref.media_type or media or guess_media_type(ref.uri)
            raise FetchError(f"{ref.uri} redirected more than {MAX_REDIRECTS} times")

    raise FetchError(f"Unsupported URI scheme {scheme!r} for {ref.uri!r}")
