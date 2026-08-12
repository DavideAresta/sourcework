"""Vision agent (port 8002).

Screenshots, mockups, whiteboard photos, architecture diagrams and flow charts
in; evidence out. The prompt is deliberately anti-speculative because the
failure mode with UI mockups is a model confidently describing behaviour that
is not visible anywhere in the picture.
"""

from __future__ import annotations

import base64
import logging
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from sourcework.a2a_common import Progress, SkillError, SkillExecutor, build_card, public_url, skill
from sourcework.agents.extraction import extract_evidence
from sourcework.agents.schemas import ExtractRequest
from sourcework.ingest import fetch
from sourcework.llm import LLM, ImageInput
from sourcework.models import ExtractionResult, Modality, SourceDocument

logger = logging.getLogger(__name__)

PORT = 8002
MAX_IMAGE_BYTES = 20 * 1024 * 1024

TRANSCRIBE_BLOCK = (
    "Work through the image systematically before extracting:\n"
    "- Name the artefact type (wireframe, hi-fi mockup, screenshot, whiteboard, "
    "sequence diagram, architecture diagram, flow chart, spreadsheet capture).\n"
    "- Transcribe every piece of visible text verbatim: labels, field names, "
    "button copy, column headers, error states, annotations, sticky notes.\n"
    "- Describe the structure: regions, panels, ordering, arrows and what they "
    "connect, states and transitions.\n"
    "- Record numbers, thresholds and units exactly as shown.\n"
    "Anything you cannot read clearly goes in `warnings`, not in an item."
)


class VisionExecutor(SkillExecutor):
    def __init__(self) -> None:
        self.llm = LLM(role="vision")
        self.skills = {"analyse_image": self.analyse_image}
        self.default_skill = "analyse_image"
        super().__init__()

    async def analyse_image(self, payload: dict[str, Any], progress: Progress) -> ExtractionResult:
        req = ExtractRequest.model_validate(payload)
        ref = req.ref

        await progress(f"Fetching image {ref.uri}")
        try:
            data, media_type = await fetch.fetch(ref)
        except Exception as exc:  # noqa: BLE001
            raise SkillError(f"Could not read image {ref.uri}: {exc}") from exc

        if not media_type.startswith("image/"):
            media_type = fetch.guess_media_type(ref.uri)
        if not media_type.startswith("image/"):
            raise SkillError(f"{ref.uri} is not an image ({media_type}).")
        if len(data) > MAX_IMAGE_BYTES:
            raise SkillError(f"Image is {len(data) // 1024 // 1024} MB; the limit is 20 MB.")

        filename = ref.title or Path(urlparse(ref.uri).path).name or "image"
        source = SourceDocument(
            uri=ref.uri,
            title=ref.title or filename,
            modality=Modality.IMAGE,
            media_type=media_type,
            byte_size=len(data),
            checksum=SourceDocument.checksum_of(data),
        )

        image = ImageInput(media_type=media_type, data_b64=base64.b64encode(data).decode())
        context_lines = [TRANSCRIBE_BLOCK]
        if ref.notes:
            context_lines.append(f"Caller's note about this image: {ref.notes}")

        await progress(f"Analysing {filename} with the vision model")
        evidence, summary, warnings = await extract_evidence(
            self.llm,
            source,
            [("image", "\n\n".join(context_lines))],
            focus=req.focus,
            images=[image],
            role="vision",
        )
        await progress(f"{len(evidence)} evidence item(s) from {filename}")

        return ExtractionResult(
            source=source,
            evidence=evidence,
            summary=summary or f"Analysed {filename}.",
            warnings=warnings,
        )


def card():  # noqa: ANN201
    return build_card(
        name="Image Analyst",
        description=(
            "Reads screenshots, mockups, whiteboard photos and diagrams, transcribing "
            "visible content into evidence without speculating about behaviour."
        ),
        url=public_url(PORT),
        default_input_modes=["application/json", "image/png", "image/jpeg", "image/webp"],
        skills=[
            skill(
                "analyse_image",
                "Analyse an image",
                "Extract requirement-bearing evidence from a UI mockup, screenshot, "
                "whiteboard photo or diagram.",
                tags=["vision", "images", "prd"],
                examples=[
                    '{"skill":"analyse_image","payload":{"ref":{"uri":"file:///checkout.png",'
                    '"notes":"proposed checkout flow"}}}'
                ],
                input_modes=["application/json"],
            )
        ],
    )


def executor() -> SkillExecutor:
    return VisionExecutor()
