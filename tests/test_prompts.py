"""The agent prompts live in ``agents/prompts/*.st``, not in the modules.

These guard the two ways that arrangement can rot: a prompt file that stops
being readable (or stops being shipped), and a prompt quietly copied back into
the Python where the file version then goes stale.
"""

from importlib.resources import files
from pathlib import Path

import pytest

from sourcework.agents.prompts import load, text

PROMPT_DIR = files("sourcework.agents.prompts")
AGENT_DIR = Path(__file__).resolve().parent.parent / "src" / "sourcework" / "agents"


def _stems() -> list[str]:
    return sorted(
        entry.name[: -len(".st")] for entry in PROMPT_DIR.iterdir() if entry.name.endswith(".st")
    )


def test_every_prompt_is_a_readable_non_empty_text_file():
    """A prompt that cannot be read is an agent that fails the moment it starts."""
    stems = _stems()
    assert stems, "no prompts found - the package data is not being shipped"
    for stem in stems:
        assert text(stem), stem


def test_a_placeholder_is_filled_from_the_callers_value():
    """Substitution is the point of .st: an unfilled ``$focus`` would reach the model."""
    text = load("extraction_focus", focus="pricing")
    assert "pricing" in text
    assert "$focus" not in text


def test_a_missing_placeholder_fails_loudly():
    """``substitute`` raising is what stops a half-built prompt being sent."""
    with pytest.raises(KeyError):
        load("extraction_focus")


@pytest.mark.parametrize(
    "phrase",
    [
        "You are a senior requirements analyst",
        "You write product requirements documents",
        "You are reviewing a PRD that was generated",
        "You read a meeting transcript",
        "You extract requirement-bearing evidence",
    ],
)
def test_prompt_prose_lives_in_the_files_not_the_modules(phrase):
    """Re-inlining a prompt is how the .st copy silently goes stale."""
    sources = "\n".join(
        path.read_text(encoding="utf-8") for path in AGENT_DIR.rglob("*.py")
    )
    assert phrase not in sources
