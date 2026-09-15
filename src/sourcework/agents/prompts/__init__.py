"""Agent prompts, kept out of the Python that uses them.

Every file here is a :class:`string.Template`: ``$name`` is filled by
:func:`load`, and a literal dollar sign is written ``$$``. Prompts are prose an
operator may want to read or tune without opening a module, which is the whole
reason they live apart from the code - the Python around them is wiring, the
wording is here.

They are read through :mod:`importlib.resources`, not a path relative to
``__file__``, so they travel inside the wheel and the desktop bundle rather than
existing only in a source checkout.
"""

from __future__ import annotations

from importlib.resources import files
from string import Template

__all__ = ["load", "text"]

_DIR = files(__package__)


def text(name: str) -> str:
    """The raw contents of ``<name>.st``, trimmed, with placeholders untouched."""
    return _DIR.joinpath(f"{name}.st").read_text(encoding="utf-8").strip()


def load(name: str, **values: object) -> str:
    """Prompt ``name`` with any ``$placeholder`` filled from ``values``.

    ``name`` is the file stem: ``load("writer_system")`` reads
    ``writer_system.st``. Surrounding whitespace is trimmed so callers can join
    prompts with an explicit ``"\\n\\n"`` instead of relying on stray blank
    lines in the file. A placeholder with no matching value raises, deliberately:
    a half-filled prompt is a defect, and failing at the call is how it is found.
    """
    return Template(text(name)).substitute(**{key: str(value) for key, value in values.items()})
