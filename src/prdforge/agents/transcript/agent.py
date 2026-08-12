"""Meeting transcript agent (port 8003).

Parses VTT / SRT / JSON / pasted transcripts, then extracts evidence with
speaker attribution and timestamps. Also produces a separate decision log,
because in practice the single most valuable output of a requirements meeting
is "what did we actually decide, and who owns the follow-up".
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from pydantic import BaseModel, Field

from prdforge.a2a_common import Progress, SkillError, SkillExecutor, build_card, public_url, skill
from prdforge.agents.extraction import extract_evidence
from prdforge.agents.schemas import ExtractRequest
from prdforge.ingest import fetch, transcripts
from prdforge.llm import LLM
from prdforge.models import ExtractionResult, Modality, SourceDocument

logger = logging.getLogger(__name__)

PORT = 8003


class Decision(BaseModel):
    decision: str
    made_by: str | None = None
    timestamp: str | None = None
    supersedes: str | None = None


class ActionItem(BaseModel):
    action: str
    owner: str | None = None
    due: str | None = None
    timestamp: str | None = None


class MeetingDigest(BaseModel):
    purpose: str = ""
    participants: list[str] = Field(default_factory=list)
    decisions: list[Decision] = Field(default_factory=list)
    action_items: list[ActionItem] = Field(default_factory=list)
    unresolved: list[str] = Field(default_factory=list)


DIGEST_SYSTEM = """You read a meeting transcript and produce a decision log.

- A decision is something the group settled. Someone musing "we could maybe..."
  is not a decision. If it was reopened later, record only the final position
  and note what it superseded.
- An action item needs an owner where one was named. Do not invent owners.
- `unresolved` is for questions raised and never answered, and for disagreements
  left standing. These become open questions in the PRD, so be thorough.
- Use the timestamps present in the transcript verbatim.
"""


class TranscriptExecutor(SkillExecutor):
    def __init__(self) -> None:
        self.llm = LLM(role="default")
        self.skills = {
            "extract_transcript": self.extract_transcript,
            "meeting_digest": self.meeting_digest,
        }
        self.default_skill = "extract_transcript"
        super().__init__()

    async def _load(self, req: ExtractRequest) -> tuple[SourceDocument, list[transcripts.Cue]]:
        ref = req.ref
        try:
            data, media_type = await fetch.fetch(ref)
        except Exception as exc:  # noqa: BLE001
            raise SkillError(f"Could not read transcript {ref.uri}: {exc}") from exc

        filename = ref.title or Path(urlparse(ref.uri).path).name or "transcript"
        cues = transcripts.parse(data, media_type, filename)
        if not cues:
            raise SkillError(
                f"{filename} did not parse as a transcript. Supported: WebVTT, SRT, "
                "JSON exports, or '[00:12:34] Speaker: text' lines."
            )
        source = SourceDocument(
            uri=ref.uri,
            title=ref.title or filename,
            modality=Modality.TRANSCRIPT,
            media_type=media_type,
            byte_size=len(data),
            checksum=SourceDocument.checksum_of(data),
            metadata={
                "cue_count": len(cues),
                "speakers": sorted({c.speaker for c in cues if c.speaker}),
            },
        )
        return source, cues

    async def extract_transcript(
        self, payload: dict[str, Any], progress: Progress
    ) -> ExtractionResult:
        req = ExtractRequest.model_validate(payload)
        await progress("Parsing transcript")
        source, cues = await self._load(req)
        speakers = source.metadata.get("speakers") or []
        await progress(f"{len(cues)} cues, {len(speakers)} speaker(s) identified")

        blocks = transcripts.to_blocks(cues)
        evidence, summary, warnings = await extract_evidence(
            self.llm, source, blocks, focus=req.focus
        )
        await progress(f"{len(evidence)} evidence item(s) extracted")

        return ExtractionResult(
            source=source,
            evidence=evidence,
            summary=summary or f"Transcript with {len(cues)} cues.",
            warnings=warnings,
        )

    async def meeting_digest(self, payload: dict[str, Any], progress: Progress) -> MeetingDigest:
        req = ExtractRequest.model_validate(payload)
        await progress("Parsing transcript")
        source, cues = await self._load(req)
        body = "\n".join(
            f"{c.speaker or 'Unknown'} [{c.start or '--:--'}]: {c.text}" for c in cues
        )[:60000]
        await progress("Building decision log")
        return await self.llm.structured(
            DIGEST_SYSTEM,
            f"Transcript: {source.title}\n\n<<<CONTENT>>>\n{body}",
            MeetingDigest,
            role="reasoning",
        )


def card():  # noqa: ANN201
    return build_card(
        name="Meeting Analyst",
        description=(
            "Parses meeting transcripts (VTT, SRT, JSON exports, pasted text) into "
            "speaker- and timestamp-attributed evidence, plus a decision log."
        ),
        url=public_url(PORT),
        skills=[
            skill(
                "extract_transcript",
                "Extract transcript evidence",
                "Return evidence items from a meeting transcript, each attributed to a "
                "speaker and timestamp.",
                tags=["transcript", "meetings", "prd"],
                examples=[
                    '{"skill":"extract_transcript","payload":{"ref":{"uri":"file:///kickoff.vtt"}}}'
                ],
            ),
            skill(
                "meeting_digest",
                "Meeting decision log",
                "Return the decisions taken, action items with owners, and questions "
                "left unresolved.",
                tags=["transcript", "meetings", "decisions"],
            ),
        ],
    )


def executor() -> SkillExecutor:
    return TranscriptExecutor()
