"""Confluence agent (port 8004).

The only component holding Atlassian credentials. Everything else in the mesh
reaches Confluence through this agent's four skills, which keeps the blast
radius of a leaked token to one container and makes the read/write boundary
auditable in one place.
"""

from __future__ import annotations

import logging
from typing import Any

from prdforge.a2a_common import Progress, SkillError, SkillExecutor, build_card, public_url, skill
from prdforge.agents.extraction import extract_evidence
from prdforge.agents.schemas import (
    ConfluenceFetchRequest,
    ConfluenceHit,
    ConfluenceSearchRequest,
    ConfluenceSearchResult,
    PublishRequest,
    PublishResult,
)
from prdforge.config import settings
from prdforge.confluence import ConfluenceClient, ConfluenceError, storage_to_blocks
from prdforge.ingest import documents
from prdforge.llm import LLM
from prdforge.models import ExtractionResult, Modality, SourceDocument

logger = logging.getLogger(__name__)

PORT = 8004


class ConfluenceExecutor(SkillExecutor):
    def __init__(self) -> None:
        self.llm = LLM(role="default")
        self.skills = {
            "search_pages": self.search_pages,
            "fetch_page": self.fetch_page,
            "publish_prd": self.publish_prd,
        }
        self.default_skill = "fetch_page"
        super().__init__()

    # -- read ---------------------------------------------------------------

    async def search_pages(self, payload: dict[str, Any], progress: Progress) -> ConfluenceSearchResult:
        req = ConfluenceSearchRequest.model_validate(payload)
        await progress(f"CQL: {req.cql}")
        try:
            async with ConfluenceClient() as cc:
                results = await cc.search(req.cql, req.limit)
                site = cc.base
        except ConfluenceError as exc:
            raise SkillError(str(exc)) from exc

        hits = []
        for item in results:
            content = item.get("content") or {}
            if content.get("type") not in (None, "page", "blogpost"):
                continue
            webui = (content.get("_links") or {}).get("webui") or item.get("url") or ""
            hits.append(
                ConfluenceHit(
                    page_id=str(content.get("id") or ""),
                    title=content.get("title") or item.get("title") or "(untitled)",
                    url=f"{site}{webui}" if webui.startswith("/") else webui,
                    space_key=(content.get("space") or {}).get("key"),
                    excerpt=item.get("excerpt"),
                    last_modified=item.get("lastModified"),
                )
            )
        return ConfluenceSearchResult(
            hits=hits, summary=f"{len(hits)} page(s) matched `{req.cql}`."
        )

    async def fetch_page(self, payload: dict[str, Any], progress: Progress) -> ExtractionResult:
        req = ConfluenceFetchRequest.model_validate(payload)
        space, page_id = ConfluenceClient.parse_confluence_uri(req.uri)
        if page_id is None and req.uri.isdigit():
            page_id = req.uri
        if page_id is None:
            raise SkillError(
                f"Could not find a page id in {req.uri!r}. Use confluence://SPACE/12345, "
                "a page URL, or a bare page id."
            )

        await progress(f"Fetching Confluence page {page_id}")
        try:
            async with ConfluenceClient() as cc:
                page = await cc.get_page(page_id)
                xhtml = cc.storage_body(page)
                url = cc.page_url(page)
                blocks = storage_to_blocks(xhtml)

                if req.include_attachments:
                    attachments = await cc.list_attachments(page_id)
                    await progress(f"{len(attachments)} attachment(s)")
                    for att in attachments[:10]:
                        media = att.get("mediaType", "")
                        if media.startswith("image/"):
                            continue  # images belong to the vision agent
                        try:
                            data = await cc.download_attachment(page_id, str(att["id"]))
                            extra, _ = documents.extract(data, media, att.get("title", ""))
                            blocks.extend(
                                (f"attachment {att.get('title')} / {loc}", text)
                                for loc, text in extra
                            )
                        except Exception as exc:  # noqa: BLE001
                            logger.warning("attachment %s failed: %s", att.get("title"), exc)
        except ConfluenceError as exc:
            raise SkillError(str(exc)) from exc

        source = SourceDocument(
            uri=url or req.uri,
            title=page.get("title") or f"Confluence page {page_id}",
            modality=Modality.CONFLUENCE,
            media_type="text/html",
            metadata={
                "page_id": str(page_id),
                "space_key": space,
                "version": (page.get("version") or {}).get("number"),
            },
        )
        if not blocks:
            return ExtractionResult(
                source=source, summary="Page is empty.", warnings=["no content"]
            )

        await progress(f"Extracting evidence from {len(blocks)} section(s)")
        evidence, summary, warnings = await extract_evidence(
            self.llm, source, blocks, focus=req.focus
        )
        return ExtractionResult(
            source=source,
            evidence=evidence,
            summary=summary or source.title,
            warnings=warnings,
        )

    # -- write --------------------------------------------------------------

    async def publish_prd(self, payload: dict[str, Any], progress: Progress) -> PublishResult:
        req = PublishRequest.model_validate(payload)
        cfg = settings().confluence
        space = req.space_key or cfg.default_space_key
        parent = req.parent_id or cfg.default_parent_id

        if not req.storage_xhtml.strip():
            raise SkillError("Refusing to publish an empty page.")

        await progress(f"Publishing '{req.title}' to space {space}")
        try:
            async with ConfluenceClient() as cc:
                existing = await cc.find_page_by_title(space, req.title)
                page = await cc.upsert_page(space, req.title, req.storage_xhtml, parent)
                url = cc.page_url(page)
        except ConfluenceError as exc:
            raise SkillError(str(exc)) from exc

        version = int((page.get("version") or {}).get("number") or 1)
        created = existing is None
        return PublishResult(
            page_id=str(page["id"]),
            url=url,
            version=version,
            created=created,
            summary=f"{'Created' if created else 'Updated to v' + str(version)}: {url}",
        )


def card():  # noqa: ANN201
    return build_card(
        name="Confluence Connector",
        description=(
            "Reads Confluence pages and attachments as PRD source material, and "
            "publishes finished PRDs back as Confluence pages. The only agent holding "
            "Atlassian credentials."
        ),
        url=public_url(PORT),
        skills=[
            skill(
                "search_pages",
                "Search Confluence",
                "Run a CQL query and return matching pages with titles and URLs.",
                tags=["confluence", "search", "read"],
                examples=[
                    '{"skill":"search_pages","payload":{"cql":"space=PRD AND text ~ \\"checkout\\"","limit":10}}'
                ],
            ),
            skill(
                "fetch_page",
                "Fetch a Confluence page",
                "Fetch a page by URI or id, convert its storage format to text, "
                "optionally pull in non-image attachments, and extract evidence.",
                tags=["confluence", "read", "ingestion"],
                examples=['{"skill":"fetch_page","payload":{"uri":"confluence://PRD/393220"}}'],
            ),
            skill(
                "publish_prd",
                "Publish a PRD page",
                "Create or update a Confluence page from storage-format XHTML. "
                "Idempotent: re-publishing the same title bumps the version instead of "
                "failing on the unique-title constraint.",
                tags=["confluence", "write", "publish"],
            ),
        ],
    )


def executor() -> SkillExecutor:
    return ConfluenceExecutor()
