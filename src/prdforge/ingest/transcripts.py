"""Meeting transcript parsing.

Handles WebVTT, SRT, the JSON exports most conferencing tools emit, and the
plain ``[00:12:34] Speaker: text`` format people paste into chat. Output is a
list of :class:`Cue` with a timestamp locator and a speaker, because "who said
it and when" is the most valuable metadata a PRD can carry back to a decision.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

_TS = r"(\d{1,2}:\d{2}(?::\d{2})?(?:[.,]\d{1,3})?)"
VTT_CUE = re.compile(rf"{_TS}\s*-->\s*{_TS}")
SPEAKER_TAG = re.compile(r"^<v\s+([^>]+)>\s*(.*)$", re.IGNORECASE)
BRACKET_LINE = re.compile(rf"^\[?{_TS}\]?\s*[-–]?\s*(?:([^:]{{1,60}}):)?\s*(.*)$")
NAME_LINE = re.compile(r"^([A-Z][\w .'-]{1,48}):\s+(.*)$")


@dataclass
class Cue:
    start: str | None
    speaker: str | None
    text: str

    @property
    def locator(self) -> str:
        if self.start and self.speaker:
            return f"{self.start} {self.speaker}"
        return self.start or (self.speaker or "transcript")


def parse(data: bytes, media_type: str = "", filename: str = "") -> list[Cue]:
    text = data.decode("utf-8", errors="replace").strip()
    name = filename.lower()
    if name.endswith(".json") or text.startswith(("{", "[")):
        cues = _json(text)
        if cues:
            return cues
    if text.upper().startswith("WEBVTT") or name.endswith(".vtt"):
        return _vtt(text)
    if name.endswith(".srt") or re.match(r"^\d+\s*\n\d{2}:\d{2}", text):
        return _srt(text)
    return _plain(text)


def _vtt(text: str) -> list[Cue]:
    cues: list[Cue] = []
    start: str | None = None
    buffer: list[str] = []
    speaker: str | None = None

    def flush() -> None:
        nonlocal buffer, speaker, start
        body = " ".join(b for b in buffer if b).strip()
        if body:
            cues.append(Cue(start, speaker, body))
        buffer, speaker = [], None

    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.upper().startswith(("WEBVTT", "NOTE", "STYLE")):
            flush()
            continue
        m = VTT_CUE.search(stripped)
        if m:
            # WebVTT allows an optional *cue identifier* on the line directly
            # before the timing line - usually a sequence number, and what most
            # tools emit. It is not spoken content: buffered and flushed like
            # text it became one junk evidence item per cue ("1", "2", "3"...),
            # each carrying the PREVIOUS cue's timestamp as its locator, so it
            # looked citable. Drop that line and flush whatever came before it,
            # which is only non-empty in files that omit the blank separators.
            if buffer:
                buffer.pop()
            flush()
            start = _norm(m.group(1))
            continue
        tag = SPEAKER_TAG.match(stripped)
        if tag:
            speaker = tag.group(1).strip()
            stripped = tag.group(2)
        elif speaker is None:
            named = NAME_LINE.match(stripped)
            if named:
                speaker = named.group(1).strip()
                stripped = named.group(2)
        buffer.append(re.sub(r"<[^>]+>", "", stripped))
    flush()
    return cues


def _srt(text: str) -> list[Cue]:
    cues: list[Cue] = []
    for block in re.split(r"\n\s*\n", text):
        lines = [ln for ln in block.splitlines() if ln.strip()]
        if not lines:
            continue
        if lines[0].strip().isdigit():
            lines = lines[1:]
        if not lines:
            continue
        m = VTT_CUE.search(lines[0])
        start = _norm(m.group(1)) if m else None
        body_lines = lines[1:] if m else lines
        body = " ".join(body_lines).strip()
        speaker = None
        named = NAME_LINE.match(body)
        if named:
            speaker, body = named.group(1).strip(), named.group(2)
        if body:
            cues.append(Cue(start, speaker, body))
    return cues


def _json(text: str) -> list[Cue]:
    try:
        blob = json.loads(text)
    except json.JSONDecodeError:
        return []
    entries = blob
    if isinstance(blob, dict):
        for key in ("segments", "transcript", "results", "entries", "cues", "monologues"):
            if isinstance(blob.get(key), list):
                entries = blob[key]
                break
        else:
            return []
    cues: list[Cue] = []
    for item in entries:
        if not isinstance(item, dict):
            continue
        body = _first(item, ("text", "content", "utterance", "transcript", "value"))
        if not body:
            continue
        start = _first(item, ("start", "start_time", "startTime", "timestamp", "offset"))
        speaker = _first(item, ("speaker", "speaker_label", "speakerName", "participant", "name"))
        cues.append(Cue(_norm(str(start)) if start is not None else None, speaker, str(body).strip()))
    return cues


def _plain(text: str) -> list[Cue]:
    cues: list[Cue] = []
    speaker: str | None = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        m = BRACKET_LINE.match(line)
        if m and m.group(1):
            if m.group(2):
                speaker = m.group(2).strip()
            cues.append(Cue(_norm(m.group(1)), speaker, m.group(3).strip()))
            continue
        named = NAME_LINE.match(line)
        if named:
            speaker = named.group(1).strip()
            cues.append(Cue(None, speaker, named.group(2).strip()))
            continue
        if cues:
            cues[-1].text = f"{cues[-1].text} {line}".strip()
        else:
            cues.append(Cue(None, speaker, line))
    return cues


def _norm(value: str) -> str:
    value = value.strip().replace(",", ".")
    if re.fullmatch(r"\d+(\.\d+)?", value):  # seconds float
        total = int(float(value))
        return f"{total // 3600:02d}:{total % 3600 // 60:02d}:{total % 60:02d}"
    parts = value.split(".")[0].split(":")
    if len(parts) == 2:
        parts = ["00", *parts]
    return ":".join(p.zfill(2) for p in parts)


def _first(item: dict, keys: tuple[str, ...]) -> object | None:
    for key in keys:
        if item.get(key) not in (None, ""):
            return item[key]
    return None


def to_blocks(cues: list[Cue], window: int = 25) -> list[tuple[str, str]]:
    """Group cues into readable windows for the LLM, keeping locators."""
    blocks: list[tuple[str, str]] = []
    for start in range(0, len(cues), window):
        group = cues[start : start + window]
        if not group:
            continue
        locator = group[0].locator
        body = "\n".join(
            f"{c.speaker or 'Unknown'} [{c.start or '--:--'}]: {c.text}" for c in group
        )
        blocks.append((locator, body))
    return blocks
