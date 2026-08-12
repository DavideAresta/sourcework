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
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx

from sourcework.config import ConfluenceSettings, settings

logger = logging.getLogger(__name__)


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
                wait = float(resp.headers.get("Retry-After", delay))
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
        origin = f"{urlparse(self.base).scheme}://{urlparse(self.base).netloc}"
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
            next_path, next_params = origin + link, None
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
        """v1 redirect endpoint; the media host rejects our auth header."""
        url = (
            f"{self.base}/rest/api/content/{page_id}/child/attachment/"
            f"{attachment_id}/download"
        )
        resp = await self.http.get(url, follow_redirects=False)
        if resp.status_code in (301, 302, 303, 307, 308):
            location = resp.headers["Location"]
            async with httpx.AsyncClient(timeout=120.0, follow_redirects=True) as anon:
                signed = await anon.get(location)
                signed.raise_for_status()
                return signed.content
        if resp.status_code >= 400:
            raise ConfluenceError(f"attachment download -> {resp.status_code}: {resp.text[:300]}")
        return resp.content

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
        space = segments[segments.index("spaces") + 1] if "spaces" in segments else None
        page_id = None
        if "pages" in segments:
            idx = segments.index("pages")
            if idx + 1 < len(segments) and segments[idx + 1].isdigit():
                page_id = segments[idx + 1]
        if page_id is None:
            page_id = (parse_qs(parsed.query).get("pageId") or [None])[0]
        return space, page_id
