"""Document ingestion agent (port 8001).

Takes PDF / DOCX / PPTX / XLSX / CSV / HTML / Markdown / plain text and returns
locator-annotated evidence.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from sourcework.a2a_common import Progress, SkillError, SkillExecutor, build_card, public_url, skill
from sourcework.agents.extraction import extract_evidence
from sourcework.agents.schemas import ExtractRequest
from sourcework.ingest import documents, fetch
from sourcework.llm import LLM
from sourcework.models import ExtractionResult, Modality, SourceDocument

logger = logging.getLogger(__name__)

PORT = 8001


class IngestionExecutor(SkillExecutor):
    def __init__(self) -> None:
        self.llm = LLM(role="default")
        self.skills = {
            "extract_document": self.extract_document,
            "list_supported_formats": self.list_supported_formats,
        }
        self.default_skill = "extract_document"
        super().__init__()

    async def extract_document(
        self, payload: dict[str, Any], progress: Progress
    ) -> ExtractionResult:
        req = ExtractRequest.model_validate(payload)
        ref = req.ref

        await progress(f"Fetching {ref.uri}")
        try:
            data, media_type = await fetch.fetch(ref)
        except Exception as exc:  # noqa: BLE001
            raise SkillError(f"Could not read {ref.uri}: {exc}") from exc

        filename = ref.title or Path(urlparse(ref.uri).path).name or "input"
        source = SourceDocument(
            uri=ref.uri,
            title=ref.title or filename,
            modality=ref.modality or fetch.guess_modality(ref.uri, media_type),
            media_type=media_type,
            byte_size=len(data),
            checksum=SourceDocument.checksum_of(data),
            metadata={"notes": ref.notes} if ref.notes else {},
        )
        if source.modality == Modality.IMAGE:
            raise SkillError(
                "This is an image. Route it to the vision agent (skill: analyse_image)."
            )

        await progress(f"Parsing {filename} ({len(data) // 1024} KB, {media_type})")
        try:
            blocks, warnings = documents.extract(data, media_type, filename)
        except documents.UnsupportedDocument as exc:
            raise SkillError(str(exc)) from exc

        if not blocks:
            return ExtractionResult(
                source=source,
                summary=f"{filename} yielded no extractable text.",
                warnings=warnings or ["no text extracted"],
            )

        await progress(f"Extracting evidence from {len(blocks)} block(s)")
        evidence, summary, extra = await extract_evidence(
            self.llm, source, blocks, focus=req.focus
        )
        await progress(f"{len(evidence)} evidence item(s) from {filename}")

        return ExtractionResult(
            source=source,
            evidence=evidence,
            summary=summary or f"Parsed {filename}.",
            warnings=[*warnings, *extra],
        )

    async def list_supported_formats(self, payload: dict[str, Any]) -> ExtractionResult:
        source = SourceDocument(uri="about:formats", title="Supported formats", modality=Modality.FREETEXT)
        return ExtractionResult(
            source=source,
            summary="pdf, docx, pptx, xlsx, csv, html, markdown, txt. "
            "Images go to the vision agent; transcripts to the transcript agent.",
        )


def card():  # noqa: ANN201
    return build_card(
        name="Document Ingestor",
        description=(
            "Parses documents into locator-annotated evidence for PRD synthesis. "
            "Handles PDF, Word, PowerPoint, Excel, CSV, HTML, Markdown and plain text."
        ),
        url=public_url(PORT),
        skills=[
            skill(
                "extract_document",
                "Extract document evidence",
                "Fetch a document by URI or inline bytes, parse it, and return evidence "
                "items each tagged with a page/slide/section locator.",
                tags=["ingestion", "documents", "prd"],
                examples=[
                    '{"skill":"extract_document","payload":{"ref":{"uri":"file:///rfp.pdf"}}}'
                ],
            ),
            skill(
                "list_supported_formats",
                "List supported formats",
                "Report which document formats this agent can parse.",
                tags=["ingestion", "capability"],
            ),
        ],
    )


def executor() -> SkillExecutor:
    return IngestionExecutor()
