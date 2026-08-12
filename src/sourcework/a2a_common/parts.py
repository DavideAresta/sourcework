"""Helpers for moving typed JSON through A2A ``Part`` objects.

A2A Parts carry either text, raw bytes, a URL, or structured ``data``. This
module standardises on ``data`` parts for everything the agents exchange, with
a text-part fallback so a human poking at an agent with curl still gets a
sensible result.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any, TypeVar

from a2a.helpers import get_data_parts, get_text_parts, new_data_part, new_text_part
from a2a.types import Message, Part
from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)

SKILL_KEY = "skill"
PAYLOAD_KEY = "payload"

USAGE_KEY = "_sourcework_usage"
"""Side-channel key for token/cost accounting.

It rides as its own DataPart alongside the result rather than inside it, so no
response schema has to grow a field that is about billing rather than about
PRDs. The client picks it out by this key; anything that does not know about it
sees an extra data part and ignores it."""


def usage_part(totals: dict[str, Any]) -> Part:
    return json_part({USAGE_KEY: totals})


def model_part(model: BaseModel, media_type: str = "application/json") -> Part:
    """Serialise a Pydantic model into a DataPart (JSON-mode dump)."""
    return new_data_part(json.loads(model.model_dump_json()), media_type=media_type)


def json_part(obj: Any, media_type: str = "application/json") -> Part:
    return new_data_part(obj, media_type=media_type)


def envelope(skill: str, payload: BaseModel | dict[str, Any]) -> Part:
    """Wrap a skill invocation. Agents expose several skills on one endpoint."""
    body = json.loads(payload.model_dump_json()) if isinstance(payload, BaseModel) else payload
    return json_part({SKILL_KEY: skill, PAYLOAD_KEY: body})


def first_data(parts: Sequence[Part]) -> dict[str, Any] | None:
    for item in get_data_parts(list(parts)):
        if isinstance(item, dict):
            return item
    return None


def read_envelope(message: Message | None) -> tuple[str | None, dict[str, Any]]:
    """Pull ``(skill, payload)`` out of an inbound message.

    Accepts three shapes, most specific first:
      1. a DataPart ``{"skill": ..., "payload": {...}}``
      2. a bare DataPart, treated as the payload with no skill hint
      3. a TextPart containing JSON, or plain prose (becomes ``{"text": ...}``)
    """
    if message is None:
        return None, {}

    data = first_data(message.parts)
    if data is not None:
        if SKILL_KEY in data:
            payload = data.get(PAYLOAD_KEY)
            return str(data[SKILL_KEY]), payload if isinstance(payload, dict) else {}
        return None, data

    text = "\n".join(get_text_parts(list(message.parts))).strip()
    if not text:
        return None, {}
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return None, {"text": text}
    if isinstance(parsed, dict):
        if SKILL_KEY in parsed:
            payload = parsed.get(PAYLOAD_KEY)
            return str(parsed[SKILL_KEY]), payload if isinstance(payload, dict) else {}
        return None, parsed
    return None, {"value": parsed}


def parse_as(model: type[T], payload: dict[str, Any]) -> T:
    return model.model_validate(payload)


def text_summary_part(text: str) -> Part:
    return new_text_part(text)
