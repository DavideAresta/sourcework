"""Reading and writing ``.env`` from the settings page.

Two rules, both of them about not doing damage:

* **An explicit allow-list.** Only the keys declared in :data:`FIELDS` can be
  written. A settings endpoint that accepts any key is an arbitrary
  environment-injection endpoint, and the environment is where the API keys
  live.

* **Secrets go out masked and come back ignored.** A password field that
  round-trips its real value through the browser is a password field that has
  been leaked to anything watching. Secret keys are sent as
  :data:`MASK`; a value that comes back still equal to the mask means "leave it
  alone", which is also exactly what an untouched form control does.

Edits are surgical - the existing line is rewritten in place, so comments,
ordering and anything the UI does not know about survive.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

MASK = "••••••••"

_LINE = re.compile(r"^(?P<key>[A-Za-z_][A-Za-z0-9_]*)\s*=(?P<value>.*)$")


@dataclass(frozen=True)
class Field:
    key: str
    label: str
    group: str
    kind: Literal["text", "password", "number", "bool", "select"] = "text"
    options: tuple[str, ...] = ()
    help: str = ""
    placeholder: str = ""
    restart: bool = True
    """Whether changing this needs the mesh restarted. Almost everything does -
    the agents read their settings once, at start-up. The exceptions are the
    LLM defaults, which a run can override per request anyway."""

    backend: str = ""
    role: str = ""
    """Set together on the per-backend model ids, and only on those. A model
    choice is two-dimensional - which backend, which role - and a flat list of
    controls labelled "opencode · reasoning" leaves the reader to reconstruct
    that grid in their head. Carrying the two axes as data lets the page draw
    the grid instead."""

    @property
    def secret(self) -> bool:
        return self.kind == "password"


FIELDS: tuple[Field, ...] = (
    # -- routing -----------------------------------------------------------
    Field("PRDFORGE_LLM__BACKEND", "Default backend", "Routing", "select",
          ("litellm", "claude-code", "opencode-cli", "copilot-cli", "stub"),
          help="What a run uses when it does not choose for itself."),
    Field("PRDFORGE_LLM__FAILOVER_ORDER", "Failover order", "Routing",
          placeholder="claude-code,opencode-cli",
          help="Comma-separated, tried in order when the active backend fails. "
               "Worth setting: with none, one timeout ends the whole run."),
    Field("PRDFORGE_LLM__STUB", "Stub mode", "Routing", "bool",
          help="Deterministic fake responses. No network, no keys, no CLI."),

    # -- models, as a backend x role grid -----------------------------------
    # A model id belongs to a backend and means nothing to another one, so the
    # grid is the honest shape: leaving a cell empty is "let that backend pick".
    Field("PRDFORGE_LLM__DEFAULT_MODEL", "default", "Models",
          backend="litellm", role="default"),
    Field("PRDFORGE_LLM__REASONING_MODEL", "reasoning", "Models",
          backend="litellm", role="reasoning"),
    Field("PRDFORGE_LLM__VISION_MODEL", "vision", "Models",
          backend="litellm", role="vision"),
    Field("PRDFORGE_LLM__CRITIC_MODEL", "critic", "Models",
          backend="litellm", role="critic"),

    Field("PRDFORGE_LLM__CLAUDE_CODE_MODELS__DEFAULT", "default", "Models",
          backend="claude-code", role="default"),
    Field("PRDFORGE_LLM__CLAUDE_CODE_MODELS__REASONING", "reasoning", "Models",
          backend="claude-code", role="reasoning"),
    Field("PRDFORGE_LLM__CLAUDE_CODE_MODELS__VISION", "vision", "Models",
          backend="claude-code", role="vision"),
    Field("PRDFORGE_LLM__CLAUDE_CODE_MODELS__CRITIC", "critic", "Models",
          backend="claude-code", role="critic"),

    Field("PRDFORGE_LLM__OPENCODE_MODELS__DEFAULT", "default", "Models",
          backend="opencode-cli", role="default"),
    Field("PRDFORGE_LLM__OPENCODE_MODELS__REASONING", "reasoning", "Models",
          backend="opencode-cli", role="reasoning"),
    Field("PRDFORGE_LLM__OPENCODE_MODELS__VISION", "vision", "Models",
          backend="opencode-cli", role="vision"),
    Field("PRDFORGE_LLM__OPENCODE_MODELS__CRITIC", "critic", "Models",
          backend="opencode-cli", role="critic"),

    Field("PRDFORGE_LLM__COPILOT_MODELS__DEFAULT", "default", "Models",
          backend="copilot-cli", role="default"),
    Field("PRDFORGE_LLM__COPILOT_MODELS__REASONING", "reasoning", "Models",
          backend="copilot-cli", role="reasoning"),
    Field("PRDFORGE_LLM__COPILOT_MODELS__VISION", "vision", "Models",
          backend="copilot-cli", role="vision"),
    Field("PRDFORGE_LLM__COPILOT_MODELS__CRITIC", "critic", "Models",
          backend="copilot-cli", role="critic"),

    # -- limits --------------------------------------------------------------
    Field("PRDFORGE_LLM__EFFORT", "Reasoning effort", "Limits", "select",
          ("", "low", "medium", "high", "xhigh", "max"),
          help="CLI backends only; litellm ignores it. `max` is expensive: on one "
               "measured call it cost 13x the wall clock of `high` for an answer "
               "that then hit the output ceiling anyway."),
    Field("PRDFORGE_LLM__MAX_TOKENS", "Max output tokens", "Limits", "number"),
    Field("PRDFORGE_LLM__TEMPERATURE", "Temperature", "Limits", "number"),
    Field("PRDFORGE_LLM__TIMEOUT_S", "API timeout (s)", "Limits", "number",
          help="litellm only."),
    Field("PRDFORGE_LLM__CLI_TIMEOUT_S", "CLI timeout (s)", "Limits", "number",
          help="Per call, for the coding CLIs. A large analysis can legitimately "
               "run for minutes."),
    Field("PRDFORGE_LLM__ANALYSIS_BATCH_ITEMS", "Analyst slice: evidence items", "Limits",
          "number",
          help="Above this, the analyst works in slices and merges them. This is "
               "the limit that usually matters - the answer grows with the item "
               "count even when the prompt stays small. 0 turns it off."),
    Field("PRDFORGE_LLM__ANALYSIS_BATCH_CHARS", "Analyst slice: characters", "Limits",
          "number",
          help="The same, measured on the prompt instead. 0 turns it off."),
    Field("PRDFORGE_LLM__CONSTRAINED_JSON", "Enforce the JSON schema", "Limits", "bool",
          help="Have the server constrain decoding to the schema rather than just "
               "describing it in the prompt. On llama.cpp, vLLM or Ollama this makes "
               "malformed JSON impossible instead of unlikely, which is what stops a "
               "small local model spending its retries re-answering. Backends that "
               "cannot enforce a schema ignore it."),
    Field("PRDFORGE_LLM__LITELLM_RETRIES", "litellm retries per call", "Limits", "number",
          help="Retries inside a single API call. Worth lowering for a local server, "
               "where the usual failure is a timeout: 3 attempts at a 20-minute "
               "ceiling is an hour spent learning the same thing once."),

    # -- credentials -------------------------------------------------------
    Field("ANTHROPIC_API_KEY", "Anthropic API key", "Credentials", "password",
          help="Only needed by the litellm backend."),
    Field("OPENAI_API_KEY", "OpenAI API key", "Credentials", "password"),
    Field("PRDFORGE_LLM__API_BASE", "LLM gateway base URL", "Credentials",
          placeholder="https://llm-gateway.internal/v1"),
    Field("PRDFORGE_LLM__API_KEY", "LLM gateway key", "Credentials", "password"),
    Field("PRDFORGE_LLM__COPILOT_HOME", "COPILOT_HOME", "Credentials",
          help="A dedicated Copilot config dir, so runs skip your MCP servers."),

    # -- Confluence --------------------------------------------------------
    Field("PRDFORGE_CONFLUENCE__BASE_URL", "Base URL", "Confluence",
          placeholder="https://your-site.atlassian.net/wiki",
          help="Scoped tokens use https://api.atlassian.com/ex/confluence/<cloudId>/wiki"),
    Field("PRDFORGE_CONFLUENCE__EMAIL", "Account email", "Confluence"),
    Field("PRDFORGE_CONFLUENCE__API_TOKEN", "API token", "Confluence", "password"),
    Field("PRDFORGE_CONFLUENCE__DEFAULT_SPACE_KEY", "Default space key", "Confluence"),
    Field("PRDFORGE_CONFLUENCE__DEFAULT_PARENT_ID", "Default parent page id", "Confluence"),

    # -- mesh --------------------------------------------------------------
    Field("PRDFORGE_SECURITY__ENFORCE", "Require the shared secret", "Mesh", "bool"),
    Field("PRDFORGE_SECURITY__API_KEY", "Shared secret", "Mesh", "password"),
    Field("PRDFORGE_LOG_LEVEL", "Log level", "Mesh", "select",
          ("DEBUG", "INFO", "WARNING", "ERROR")),
)

BY_KEY = {f.key: f for f in FIELDS}


_ROLE_SUFFIXES = ("DEFAULT", "REASONING", "VISION", "CRITIC")


def _models(litellm: tuple[str, ...], claude: tuple[str, ...],
            opencode: tuple[str, ...], copilot: tuple[str, ...]) -> dict[str, str]:
    """One profile, as (default, reasoning, vision, critic) per backend."""
    keys = {
        "litellm": ("PRDFORGE_LLM__DEFAULT_MODEL", "PRDFORGE_LLM__REASONING_MODEL",
                    "PRDFORGE_LLM__VISION_MODEL", "PRDFORGE_LLM__CRITIC_MODEL"),
        "claude-code": tuple(f"PRDFORGE_LLM__CLAUDE_CODE_MODELS__{r}" for r in _ROLE_SUFFIXES),
        "opencode-cli": tuple(f"PRDFORGE_LLM__OPENCODE_MODELS__{r}" for r in _ROLE_SUFFIXES),
        "copilot-cli": tuple(f"PRDFORGE_LLM__COPILOT_MODELS__{r}" for r in _ROLE_SUFFIXES),
    }
    chosen = {"litellm": litellm, "claude-code": claude,
              "opencode-cli": opencode, "copilot-cli": copilot}
    return {k: v for backend, ks in keys.items() for k, v in zip(ks, chosen[backend], strict=True)}


PROFILES: dict[str, dict[str, Any]] = {
    "cheap": {
        "label": "Cheap",
        "detail": "The small model everywhere. Fine for a first pass over clean material.",
        "models": _models(
            ("anthropic/claude-haiku-4-5", "anthropic/claude-sonnet-4-5", "anthropic/claude-haiku-4-5",
             "anthropic/claude-sonnet-4-5"),
            ("haiku", "sonnet", "haiku", "sonnet"),
            ("opencode/claude-haiku-4-5", "opencode/claude-haiku-4-5", "opencode/claude-haiku-4-5",
             "opencode/claude-haiku-4-5"),
            ("auto", "auto", "auto", "auto"),
        ),
    },
    "balanced": {
        "label": "Balanced",
        "detail": "The big model only where it earns its keep - the analyst, which is the "
                  "call that decides whether the PRD is any good.",
        "models": _models(
            ("anthropic/claude-sonnet-4-5", "anthropic/claude-opus-4-6", "anthropic/claude-sonnet-4-5",
             "anthropic/claude-opus-4-6"),
            ("sonnet", "opus", "sonnet", "opus"),
            ("opencode/claude-haiku-4-5", "opencode/claude-opus-4-6", "opencode/claude-sonnet-4-5",
             "opencode/claude-opus-4-6"),
            # gpt-5.4 rather than auto: it is the Copilot model that returns
            # readable reasoning, which is what the run view shows you live.
            ("auto", "gpt-5.4", "auto", "gpt-5.4"),
        ),
    },
    "best": {
        "label": "Best",
        "detail": "The big model everywhere. Slower and dearer; worth it on messy, "
                  "contradictory source material.",
        "models": _models(
            ("anthropic/claude-opus-4-6", "anthropic/claude-opus-4-6", "anthropic/claude-opus-4-6",
             "anthropic/claude-opus-4-6"),
            ("opus", "opus", "opus", "opus"),
            ("opencode/claude-opus-4-6", "opencode/claude-opus-4-6", "opencode/claude-sonnet-4-5",
             "opencode/claude-opus-4-6"),
            ("gpt-5.4", "gpt-5.4", "gpt-5.4", "gpt-5.4"),
        ),
    },
}
"""Curated model sets, so nobody has to know from memory that
``opencode/claude-opus-4-6`` is the one that reasons well and that an unset
opencode model fails outright. Every profile covers every backend, not just the
active one: a failover target with no model is a failover that does not work."""

DEFAULT_PROFILE = "balanced"


def read(path: Path) -> dict[str, str]:
    """Every ``KEY=value`` in the file. Later wins, as dotenv does."""
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = _LINE.match(stripped)
        if match:
            values[match.group("key")] = _unquote(match.group("value").strip())
    return values


def _builtin_default(key: str) -> str:
    """What the code falls back to when this key is unset.

    Shown as the placeholder on the limits, so an empty box reads as "600s,
    because that is the default" rather than as an unknown. Only for the plain
    ``PRDFORGE_LLM__<field>`` keys - nested ones address a sub-model and are not
    worth reflecting into.
    """
    from prdforge.config import LLMSettings

    suffix = key.removeprefix("PRDFORGE_LLM__")
    if suffix == key or "__" in suffix:
        return ""
    field = LLMSettings.model_fields.get(suffix.lower())
    default = getattr(field, "default", None) if field is not None else None
    return "" if default is None or isinstance(default, (list, dict)) else str(default)


def _display_value(field: Field, current: dict[str, str]) -> str:
    """What the control should show for ``field``.

    A checkbox has no "unset" position, so an absent boolean has to render as
    something - and rendering it unticked is a lie whenever the code default is
    true. The form posts every control it drew, so that lie is written back to
    .env the first time anyone saves the page: opening settings and pressing
    Save would silently turn the setting off. Text inputs do not have this
    problem because empty means unset, and the default is already shown as the
    placeholder.
    """
    if field.secret:
        return MASK if current.get(field.key) else ""
    if field.kind == "bool" and not current.get(field.key):
        return _builtin_default(field.key)
    return current.get(field.key, "")


def describe(path: Path) -> list[dict[str, Any]]:
    """The settings form: every allowed field, with its current value masked."""
    current = read(path)
    return [
        {
            "key": f.key,
            "label": f.label,
            "group": f.group,
            "kind": f.kind,
            "options": list(f.options),
            "help": f.help,
            "placeholder": f.placeholder or _builtin_default(f.key),
            "suggested": PROFILES[DEFAULT_PROFILE]["models"].get(f.key, ""),
            "restart": f.restart,
            "backend": f.backend,
            "role": f.role,
            "value": _display_value(f, current),
            "set": bool(current.get(f.key)),
        }
        for f in FIELDS
    ]


def write(path: Path, updates: dict[str, str]) -> list[str]:
    """Apply ``updates`` to the file. Returns the keys that actually changed.

    Unknown keys are dropped rather than rejected: the form posts what it has,
    and a stale browser tab should not fail the whole save.
    """
    current = read(path)
    effective: dict[str, str] = {}
    for key, value in updates.items():
        field = BY_KEY.get(key)
        if field is None:
            continue
        if field.secret and value == MASK:
            continue  # untouched masked field
        value = "" if value is None else str(value).strip()
        if value == current.get(key, ""):
            continue
        effective[key] = value

    if not effective:
        return []

    lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
    remaining = dict(effective)

    for index, line in enumerate(lines):
        match = _LINE.match(line.strip())
        if match and match.group("key") in remaining:
            key = match.group("key")
            lines[index] = f"{key}={_quote(remaining.pop(key))}"

    if remaining:
        if lines and lines[-1].strip():
            lines.append("")
        lines.append("# --- added by the PRD Forge settings page ---")
        lines.extend(f"{key}={_quote(value)}" for key, value in remaining.items())

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return sorted(effective)


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def _quote(value: str) -> str:
    # Only when it would otherwise be ambiguous - a quoted value everywhere
    # makes the file annoying to hand-edit, which people still do.
    return f'"{value}"' if value != value.strip() or "#" in value else value
