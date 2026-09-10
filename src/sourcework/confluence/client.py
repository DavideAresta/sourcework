"""Confluence Cloud client - the read and the write half.

API notes that bit us and are encoded here so they do not bite again:

* v2 has no CQL search and no attachment download. Both fall back to v1.
* On write the body is flat ``{representation, value}``; on read it comes back
  keyed by format (``body.storage.value``). Asymmetric on purpose, apparently.
* ``PUT`` requires ``version.number == current + 1`` and a ``title``, always.
* Attachment downloads 302 to a signed media host that rejects the Atlassian
  ``Authorization`` header, so the redirect must be followed manually with the
  header stripped.
* Scoped API tokens only work against ``api.atlassian.com/ex/confluence/<cloudId>``;
  unscoped ones only against ``<site>.atlassian.net``. Set ``base_url`` to match.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import random
import time
from datetime import UTC
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import parse_qs, urljoin, urlparse

import httpx

from sourcework.config import ConfluenceSettings, settings
from sourcework.ingest.fetch import MAX_BYTES, FetchError, fetch_bytes, read_capped

logger = logging.getLogger(__name__)


def _retry_after(value: str | None, fallback: float) -> float:
    """Seconds to wait, from either form RFC 7231 allows.

    ``Retry-After`` is either delay-seconds or an HTTP-date. Only the first was
    handled, so a server sending the date form raised ``ValueError`` out of a
    path whose callers catch only ``ConfluenceError``. Capped so a hostile or
    mistaken header cannot park the client for hours.
    """
    if value:
        try:
            return max(0.0, min(float(value), 60.0))
        except ValueError:
            pass
        try:
            when = parsedate_to_datetime(value)
        except (TypeError, ValueError):
            when = None
        if when is not None:
            if when.tzinfo is None:
                when = when.replace(tzinfo=UTC)
            try:
                delta = when.timestamp() - time.time()
            except (OverflowError, OSError, ValueError):
                return fallback
            return max(0.0, min(delta, 60.0))
    return fallback


class ConfluenceError(RuntimeError):
    pass


class ConfluenceClient:
    def __init__(self, cfg: ConfluenceSettings | None = None) -> None:
        self.cfg = cfg or settings().confluence
        self.base = self.cfg.base_url.rstrip("/")
        self._client: httpx.AsyncClient | None = None

    # -- plumbing ----------------------------------------------------------

    def _auth_header(self) -> str:
        token = base64.b64encode(f"{self.cfg.email}:{self.cfg.api_token}".encode()).decode()
        return f"Basic {token}"

    async def __aenter__(self) -> ConfluenceClient:
        if not self.cfg.configured:
            raise ConfluenceError(
                "Confluence is not configured: set SOURCEWORK_CONFLUENCE__EMAIL and "
                "SOURCEWORK_CONFLUENCE__API_TOKEN."
            )
        self._client = httpx.AsyncClient(
            timeout=self.cfg.timeout_s,
            headers={
                "Authorization": self._auth_header(),
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
        )
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    @property
    def http(self) -> httpx.AsyncClient:
        if self._client is None:
            raise ConfluenceError("Use ConfluenceClient as an async context manager.")
        return self._client

    async def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        """One request with 429/5xx backoff that honours ``Retry-After``."""
        url = path if path.startswith("http") else f"{self.base}{path}"
        delay = 1.0
        for attempt in range(5):
            resp = await self.http.request(method, url, **kwargs)
            if resp.status_code == 429 or 500 <= resp.status_code < 600:
                wait = _retry_after(resp.headers.get("Retry-After"), delay)
                wait += random.uniform(0, 0.5)  # noqa: S311 - jitter, not crypto
                logger.warning(
                    "Confluence %s %s -> %s, retrying in %.1fs (attempt %d)",
                    method,
                    path,
                    resp.status_code,
                    wait,
                    attempt + 1,
                )
                await asyncio.sleep(wait)
                delay = min(delay * 2, 30)
                continue
            if resp.status_code >= 400:
                raise ConfluenceError(
                    f"{method} {url} -> {resp.status_code}: {resp.text[:600]}"
                )
            return resp
        raise ConfluenceError(f"{method} {url} still failing after retries")

    async def _paginate(self, path: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        next_path: str | None = path
        next_params: dict[str, Any] | None = params
        while next_path:
            resp = await self._request("GET", next_path, params=next_params)
            body = resp.json()
            out.extend(body.get("results", []))
            link = body.get("_links", {}).get("next")
            if not link or len(out) >= params.get("_max", 1000):
                break
            # `_links.next` is server-controlled. Following it with the
            # authenticated client would send the Atlassian credentials to
            # whatever host the link names, so only same-origin links continue.
            candidate = urljoin(self.base, link)
            if urlparse(candidate).netloc != urlparse(self.base).netloc:
                logger.warning("Confluence pagination: refusing cross-origin next link %r", link)
                break
            next_path, next_params = candidate, None
        return out

    # -- read --------------------------------------------------------------

    async def space_id(self, key: str) -> str:
        resp = await self._request("GET", "/api/v2/spaces", params={"keys": key, "limit": 1})
        results = resp.json().get("results", [])
        if not results:
            raise ConfluenceError(f"No space with key {key!r}")
        return str(results[0]["id"])

    async def get_page(self, page_id: str, body_format: str = "storage") -> dict[str, Any]:
        resp = await self._request(
            "GET", f"/api/v2/pages/{page_id}", params={"body-format": body_format}
        )
        return resp.json()

    async def search(self, cql: str, limit: int = 25) -> list[dict[str, Any]]:
        """CQL search. Still v1 - there is no v2 equivalent."""
        resp = await self._request(
            "GET", "/rest/api/search", params={"cql": cql, "limit": min(limit, 100)}
        )
        return resp.json().get("results", [])

    async def list_pages_in_space(self, space_key: str, limit: int = 100) -> list[dict[str, Any]]:
        sid = await self.space_id(space_key)
        return await self._paginate(
            f"/api/v2/spaces/{sid}/pages",
            {"limit": min(limit, 250), "status": "current", "_max": limit},
        )

    async def list_attachments(self, page_id: str) -> list[dict[str, Any]]:
        return await self._paginate(
            f"/api/v2/pages/{page_id}/attachments", {"limit": 250, "_max": 250}
        )

    async def download_attachment(self, page_id: str, attachment_id: str) -> bytes:
        """v1 redirect endpoint; the media host rejects our auth header.

        The first hop carries the Atlassian credentials; the signed redirect is
        fetched without them, through the same SSRF policy and byte cap as any
        other remote document (:func:`~sourcework.ingest.fetch.fetch_bytes`).
        """
        url = (
            f"{self.base}/rest/api/content/{page_id}/child/attachment/"
            f"{attachment_id}/download"
        )
        async with self.http.stream("GET", url, follow_redirects=False) as resp:
            if resp.status_code in (301, 302, 303, 307, 308):
                location = resp.headers.get("Location")
                if not location:
                    raise ConfluenceError("attachment download redirect had no Location")
                signed_url = urljoin(url, location)
            elif resp.status_code >= 400:
                body = (await resp.aread())[:300]
                raise ConfluenceError(
                    f"attachment download -> {resp.status_code}: {body.decode('utf-8', 'replace')}"
                )
            else:
                # Returned inline rather than redirecting: still capped.
                return (await read_capped(resp, url, MAX_BYTES))[0]

        try:
            data, _ = await fetch_bytes(signed_url, timeout=120.0, max_bytes=MAX_BYTES)
        except FetchError as exc:
            raise ConfluenceError(f"attachment download failed: {exc}") from exc
        return data

    # -- write -------------------------------------------------------------

    async def find_page_by_title(self, space_key: str, title: str) -> dict[str, Any] | None:
        sid = await self.space_id(space_key)
        resp = await self._request(
            "GET",
            "/api/v2/pages",
            params={"space-id": sid, "title": title, "status": "current", "limit": 1},
        )
        results = resp.json().get("results", [])
        return results[0] if results else None

    async def create_page(
        self,
        space_key: str,
        title: str,
        storage_xhtml: str,
        parent_id: str | None = None,
    ) -> dict[str, Any]:
        sid = await self.space_id(space_key)
        payload: dict[str, Any] = {
            "spaceId": sid,
            "status": "current",
            "title": title,
            "body": {"representation": "storage", "value": storage_xhtml},
        }
        if parent_id:
            payload["parentId"] = str(parent_id)
        resp = await self._request("POST", "/api/v2/pages", json=payload)
        return resp.json()

    async def update_page(
        self,
        page_id: str,
        title: str,
        storage_xhtml: str,
        version_message: str = "Updated by SourceWork",
    ) -> dict[str, Any]:
        current = await self.get_page(page_id)
        payload = {
            "id": str(page_id),
            "status": "current",
            "title": title,
            "spaceId": str(current["spaceId"]),
            "body": {"representation": "storage", "value": storage_xhtml},
            "version": {
                "number": int(current["version"]["number"]) + 1,
                "message": version_message,
            },
        }
        if current.get("parentId"):
            payload["parentId"] = str(current["parentId"])
        resp = await self._request("PUT", f"/api/v2/pages/{page_id}", json=payload)
        return resp.json()

    async def upsert_page(
        self,
        space_key: str,
        title: str,
        storage_xhtml: str,
        parent_id: str | None = None,
    ) -> dict[str, Any]:
        """Create, or bump the version of an existing same-titled page.

        Titles are unique per space in Confluence, so a plain create would 400
        on the second run of the same PRD. Idempotency matters here because
        regenerating a PRD is the normal case, not the exception.
        """
        existing = await self.find_page_by_title(space_key, title)
        if existing:
            return await self.update_page(str(existing["id"]), title, storage_xhtml)
        return await self.create_page(space_key, title, storage_xhtml, parent_id)

    # -- helpers -----------------------------------------------------------

    def page_url(self, page: dict[str, Any]) -> str:
        links = page.get("_links", {})
        base = links.get("base") or self.base
        return f"{base}{links.get('webui', '')}"

    @staticmethod
    def storage_body(page: dict[str, Any]) -> str:
        return (page.get("body") or {}).get("storage", {}).get("value", "") or ""

    @staticmethod
    def parse_confluence_uri(uri: str) -> tuple[str | None, str | None]:
        """``confluence://SPACE/12345`` or a browser URL -> ``(space, page_id)``."""
        if uri.startswith("confluence://"):
            rest = uri[len("confluence://") :].strip("/")
            parts = rest.split("/")
            if len(parts) == 1:
                return parts[0], None
            return parts[0], parts[1]
        parsed = urlparse(uri)
        segments = [s for s in parsed.path.split("/") if s]
        space = None
        if "spaces" in segments:
            idx = segments.index("spaces")
            # Bounds-checked like "pages" below: a URL ending in `/spaces`
            # used to raise IndexError instead of returning (None, ...).
            if idx + 1 < len(segments):
                space = segments[idx + 1]
        page_id = None
        if "pages" in segments:
            idx = segments.index("pages")
            if idx + 1 < len(segments) and segments[idx + 1].isdigit():
                page_id = segments[idx + 1]
        if page_id is None:
            page_id = (parse_qs(parsed.query).get("pageId") or [None])[0]
        return space, page_id
