"""Resolve an :class:`InputRef` into bytes + a media type.

Supported URI shapes:
  ``file:///abs/path``            local file (also bare ``/abs/path``)
  ``https://...``                 fetched over HTTP
  ``confluence://SPACE/12345``    handled by the Confluence agent, not here
  ``inline:``                     the payload is in ``content_b64`` or ``text``
"""

from __future__ import annotations

import base64
import mimetypes
from pathlib import Path
from urllib.parse import urlparse

import httpx

from prdforge.models import InputRef, Modality

MAX_BYTES = 64 * 1024 * 1024

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
        async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as http:
            resp = await http.get(ref.uri)
            resp.raise_for_status()
            if len(resp.content) > MAX_BYTES:
                raise FetchError(f"{ref.uri} exceeds the size limit")
            media = resp.headers.get("content-type", "").split(";")[0].strip()
            return resp.content, ref.media_type or media or guess_media_type(ref.uri)

    raise FetchError(f"Unsupported URI scheme {scheme!r} for {ref.uri!r}")
