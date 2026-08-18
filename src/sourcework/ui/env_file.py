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
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol, runtime_checkable

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
    Field("SOURCEWORK_LLM__BACKEND", "Default backend", "Routing", "select",
          ("litellm", "llama-cpp", "claude-code", "opencode-cli", "copilot-cli", "codex-cli",
           "agy-cli", "stub"),
          help="What a run uses when it does not choose for itself."),
    Field("SOURCEWORK_LLM__FAILOVER_ORDER", "Failover order", "Routing",
          placeholder="claude-code,opencode-cli",
          help="Comma-separated, tried in order when the active backend fails. "
               "Worth setting: with none, one timeout ends the whole run."),
    Field("SOURCEWORK_LLM__STUB", "Stub mode", "Routing", "bool",
          help="Deterministic fake responses. No network, no keys, no CLI."),

    # -- models, as a backend x role grid -----------------------------------
    # A model id belongs to a backend and means nothing to another one, so the
    # grid is the honest shape: leaving a cell empty is "let that backend pick".
    Field("SOURCEWORK_LLM__DEFAULT_MODEL", "default", "Models",
          backend="litellm", role="default"),
    Field("SOURCEWORK_LLM__REASONING_MODEL", "reasoning", "Models",
          backend="litellm", role="reasoning"),
    Field("SOURCEWORK_LLM__VISION_MODEL", "vision", "Models",
          backend="litellm", role="vision"),
    Field("SOURCEWORK_LLM__CRITIC_MODEL", "critic", "Models",
          backend="litellm", role="critic"),

    Field("SOURCEWORK_LLM__LLAMA_CPP_MODELS__DEFAULT", "default", "Models",
          backend="llama-cpp", role="default"),
    Field("SOURCEWORK_LLM__LLAMA_CPP_MODELS__REASONING", "reasoning", "Models",
          backend="llama-cpp", role="reasoning"),
    Field("SOURCEWORK_LLM__LLAMA_CPP_MODELS__VISION", "vision", "Models",
          backend="llama-cpp", role="vision"),
    Field("SOURCEWORK_LLM__LLAMA_CPP_MODELS__CRITIC", "critic", "Models",
          backend="llama-cpp", role="critic"),

    Field("SOURCEWORK_LLM__CLAUDE_CODE_MODELS__DEFAULT", "default", "Models",
          backend="claude-code", role="default"),
    Field("SOURCEWORK_LLM__CLAUDE_CODE_MODELS__REASONING", "reasoning", "Models",
          backend="claude-code", role="reasoning"),
    Field("SOURCEWORK_LLM__CLAUDE_CODE_MODELS__VISION", "vision", "Models",
          backend="claude-code", role="vision"),
    Field("SOURCEWORK_LLM__CLAUDE_CODE_MODELS__CRITIC", "critic", "Models",
          backend="claude-code", role="critic"),

    Field("SOURCEWORK_LLM__OPENCODE_MODELS__DEFAULT", "default", "Models",
          backend="opencode-cli", role="default"),
    Field("SOURCEWORK_LLM__OPENCODE_MODELS__REASONING", "reasoning", "Models",
          backend="opencode-cli", role="reasoning"),
    Field("SOURCEWORK_LLM__OPENCODE_MODELS__VISION", "vision", "Models",
          backend="opencode-cli", role="vision"),
    Field("SOURCEWORK_LLM__OPENCODE_MODELS__CRITIC", "critic", "Models",
          backend="opencode-cli", role="critic"),

    Field("SOURCEWORK_LLM__COPILOT_MODELS__DEFAULT", "default", "Models",
          backend="copilot-cli", role="default"),
    Field("SOURCEWORK_LLM__COPILOT_MODELS__REASONING", "reasoning", "Models",
          backend="copilot-cli", role="reasoning"),
    Field("SOURCEWORK_LLM__COPILOT_MODELS__VISION", "vision", "Models",
          backend="copilot-cli", role="vision"),
    Field("SOURCEWORK_LLM__COPILOT_MODELS__CRITIC", "critic", "Models",
          backend="copilot-cli", role="critic"),

    Field("SOURCEWORK_LLM__CODEX_MODELS__DEFAULT", "default", "Models",
          backend="codex-cli", role="default"),
    Field("SOURCEWORK_LLM__CODEX_MODELS__REASONING", "reasoning", "Models",
          backend="codex-cli", role="reasoning"),
    Field("SOURCEWORK_LLM__CODEX_MODELS__VISION", "vision", "Models",
          backend="codex-cli", role="vision"),
    Field("SOURCEWORK_LLM__CODEX_MODELS__CRITIC", "critic", "Models",
          backend="codex-cli", role="critic"),

    Field("SOURCEWORK_LLM__AGY_MODELS__DEFAULT", "default", "Models",
          backend="agy-cli", role="default"),
    Field("SOURCEWORK_LLM__AGY_MODELS__REASONING", "reasoning", "Models",
          backend="agy-cli", role="reasoning"),
    Field("SOURCEWORK_LLM__AGY_MODELS__VISION", "vision", "Models",
          backend="agy-cli", role="vision"),
    Field("SOURCEWORK_LLM__AGY_MODELS__CRITIC", "critic", "Models",
          backend="agy-cli", role="critic"),

    # -- limits --------------------------------------------------------------
    Field("SOURCEWORK_LLM__EFFORT", "Reasoning effort", "Limits", "select",
          ("", "low", "medium", "high", "xhigh", "max"),
          help="CLI backends only; litellm ignores it. `max` is expensive: on one "
               "measured call it cost 13x the wall clock of `high` for an answer "
               "that then hit the output ceiling anyway."),
    Field("SOURCEWORK_LLM__MAX_TOKENS", "Max output tokens", "Limits", "number"),
    Field("SOURCEWORK_LLM__TEMPERATURE", "Temperature", "Limits", "number"),
    Field("SOURCEWORK_LLM__TIMEOUT_S", "API timeout (s)", "Limits", "number",
          help="litellm only."),
    Field("SOURCEWORK_LLM__CLI_TIMEOUT_S", "CLI timeout (s)", "Limits", "number",
          help="Per call, for the coding CLIs. A large analysis can legitimately "
               "run for minutes."),
    Field("SOURCEWORK_LLM__ANALYSIS_BATCH_ITEMS", "Analyst slice: evidence items", "Limits",
          "number",
          help="Above this, the analyst works in slices and merges them. This is "
               "the limit that usually matters - the answer grows with the item "
               "count even when the prompt stays small. 0 turns it off."),
    Field("SOURCEWORK_LLM__ANALYSIS_BATCH_CHARS", "Analyst slice: characters", "Limits",
          "number",
          help="The same, measured on the prompt instead. 0 turns it off."),
    Field("SOURCEWORK_LLM__CONSTRAINED_JSON", "Enforce the JSON schema", "Limits", "bool",
          help="Have the server constrain decoding to the schema rather than just "
               "describing it in the prompt. On llama.cpp, vLLM or Ollama this makes "
               "malformed JSON impossible instead of unlikely, which is what stops a "
               "small local model spending its retries re-answering. Backends that "
               "cannot enforce a schema ignore it."),
    Field("SOURCEWORK_LLM__LITELLM_RETRIES", "litellm retries per call", "Limits", "number",
          help="Retries inside a single API call. Worth lowering for a local server, "
               "where the usual failure is a timeout: 3 attempts at a 20-minute "
               "ceiling is an hour spent learning the same thing once."),

    # -- credentials -------------------------------------------------------
    Field("ANTHROPIC_API_KEY", "Anthropic API key", "Credentials", "password",
          help="Only needed by the litellm backend."),
    Field("OPENAI_API_KEY", "OpenAI API key", "Credentials", "password"),
    Field("SOURCEWORK_LLM__API_BASE", "LLM gateway base URL", "Credentials",
          placeholder="https://llm-gateway.internal/v1"),
    Field("SOURCEWORK_LLM__API_KEY", "LLM gateway key", "Credentials", "password"),
    Field("SOURCEWORK_LLM__LLAMA_CPP_API_BASE", "llama.cpp server URL", "Credentials",
          placeholder="http://127.0.0.1:8081/v1",
          help="The OpenAI-compatible endpoint from llama-server or llama-swap."),
    Field("SOURCEWORK_LLM__LLAMA_CPP_API_KEY", "llama.cpp server key", "Credentials", "password",
          help="Optional. llama-server accepts any value unless you enable its API key."),
    Field("SOURCEWORK_MODEL_DIRS", "Local model directories", "Credentials",
          placeholder="/home/you/.lmstudio/models:/srv/models",
          help="Colon-separated folders to scan recursively for GGUF models. Used by "
               "scripts/llama-models.py and llama-swap."),
    Field("SOURCEWORK_LLM__CODEX_HOME", "CODEX_HOME", "Credentials",
          help="A dedicated Codex config dir, so runs get a clean session. Note that "
               "an OPENAI_API_KEY in your environment makes Codex bill the API instead "
               "of your subscription - it is preferred over the stored login."),
    Field("SOURCEWORK_LLM__COPILOT_HOME", "COPILOT_HOME", "Credentials",
          help="A dedicated Copilot config dir, so runs skip your MCP servers."),

    # -- Confluence --------------------------------------------------------
    Field("SOURCEWORK_CONFLUENCE__BASE_URL", "Base URL", "Confluence",
          placeholder="https://your-site.atlassian.net/wiki",
          help="Scoped tokens use https://api.atlassian.com/ex/confluence/<cloudId>/wiki"),
    Field("SOURCEWORK_CONFLUENCE__EMAIL", "Account email", "Confluence"),
    Field("SOURCEWORK_CONFLUENCE__API_TOKEN", "API token", "Confluence", "password"),
    Field("SOURCEWORK_CONFLUENCE__DEFAULT_SPACE_KEY", "Default space key", "Confluence"),
    Field("SOURCEWORK_CONFLUENCE__DEFAULT_PARENT_ID", "Default parent page id", "Confluence"),

    # -- mesh --------------------------------------------------------------
    Field("SOURCEWORK_SECURITY__ENFORCE", "Require the shared secret", "Mesh", "bool"),
    Field("SOURCEWORK_SECURITY__API_KEY", "Shared secret", "Mesh", "password"),
    Field("SOURCEWORK_LOG_LEVEL", "Log level", "Mesh", "select",
          ("DEBUG", "INFO", "WARNING", "ERROR")),

    # -- quality & history ---------------------------------------------------
    Field("SOURCEWORK_QUALITY__EARS", "EARS syntax", "Quality", "bool",
          help="Analyst writes requirements in EARS shapes (When/While/If-then/"
               "Where/ubiquitous) and the critic flags statements that take none "
               "of them. Off: phrasing is free."),
    Field("SOURCEWORK_RUNS__RETENTION_DAYS", "Run retention (days)", "History", "number",
          help="Finished runs older than this are deleted when the UI starts. "
               "0 keeps everything. The store holds full source text, so a "
               "retention policy belongs here."),
)

BY_KEY = {f.key: f for f in FIELDS}


def model_roles() -> list[str]:
    """The roles a model can be chosen for, in the order the pages show them.

    Taken from the fields themselves rather than written out again: a role the
    settings page cannot configure is one a run has no business overriding, and
    the two lists drifting apart is exactly how the API came to advertise a
    ``fast`` role no agent has ever requested while omitting ``critic``, which
    every review runs on.
    """
    seen: list[str] = []
    for field in FIELDS:
        if field.role and field.role not in seen:
            seen.append(field.role)
    return seen


_ROLE_SUFFIXES = ("DEFAULT", "REASONING", "VISION", "CRITIC")


def _models(litellm: tuple[str, ...], claude: tuple[str, ...],
            opencode: tuple[str, ...], copilot: tuple[str, ...],
            codex: tuple[str, ...], agy: tuple[str, ...]) -> dict[str, str]:
    """One profile, as (default, reasoning, vision, critic) per backend."""
    keys = {
        "litellm": ("SOURCEWORK_LLM__DEFAULT_MODEL", "SOURCEWORK_LLM__REASONING_MODEL",
                    "SOURCEWORK_LLM__VISION_MODEL", "SOURCEWORK_LLM__CRITIC_MODEL"),
        "claude-code": tuple(f"SOURCEWORK_LLM__CLAUDE_CODE_MODELS__{r}" for r in _ROLE_SUFFIXES),
        "opencode-cli": tuple(f"SOURCEWORK_LLM__OPENCODE_MODELS__{r}" for r in _ROLE_SUFFIXES),
        "copilot-cli": tuple(f"SOURCEWORK_LLM__COPILOT_MODELS__{r}" for r in _ROLE_SUFFIXES),
        "codex-cli": tuple(f"SOURCEWORK_LLM__CODEX_MODELS__{r}" for r in _ROLE_SUFFIXES),
        "agy-cli": tuple(f"SOURCEWORK_LLM__AGY_MODELS__{r}" for r in _ROLE_SUFFIXES),
    }
    chosen = {"litellm": litellm, "claude-code": claude,
              "opencode-cli": opencode, "copilot-cli": copilot,
              "codex-cli": codex, "agy-cli": agy}
    return {k: v for backend, ks in keys.items() for k, v in zip(ks, chosen[backend], strict=True)}


PROFILES: dict[str, dict[str, Any]] = {
    "cheap": {
        "label": "Cheap",
        "detail": "The small model everywhere. Fine for a first pass over clean material.",
        "models": _models(
            ("anthropic/claude-haiku-4-5", "anthropic/claude-sonnet-5", "anthropic/claude-haiku-4-5",
             "anthropic/claude-sonnet-5"),
            ("haiku", "sonnet", "haiku", "sonnet"),
            ("opencode/claude-haiku-4-5", "opencode/claude-haiku-4-5", "opencode/claude-haiku-4-5",
             "opencode/claude-haiku-4-5"),
            ("auto", "auto", "auto", "auto"),
            ("gpt-5.4-codex", "gpt-5.4-codex", "gpt-5.4-codex", "gpt-5.4-codex"),
            # agy fronts three model families, so the critic can be a different
            # lineage from the writer without configuring a second backend.
            ("gemini-3.6-flash-low", "gemini-3.6-flash-medium",
             "gemini-3.6-flash-low",  # never read: agy carries no images
             "gemini-3.6-flash-medium"),
        ),
    },
    "balanced": {
        "label": "Balanced",
        "detail": "The big model only where it earns its keep - the analyst, which is the "
                  "call that decides whether the PRD is any good.",
        "models": _models(
            ("anthropic/claude-sonnet-5", "anthropic/claude-opus-5", "anthropic/claude-sonnet-5",
             "anthropic/claude-opus-5"),
            ("sonnet", "opus", "sonnet", "opus"),
            ("opencode/claude-haiku-4-5", "opencode/claude-opus-5", "opencode/claude-sonnet-5",
             "opencode/claude-opus-5"),
            # gpt-5.4 rather than auto: it is the Copilot model that returns
            # readable reasoning, which is what the run view shows you live.
            ("auto", "gpt-5.4", "auto", "gpt-5.4"),
            ("gpt-5.4-codex", "gpt-5.4-codex", "gpt-5.4-codex", "gpt-5.4-codex"),
            ("gemini-3.6-flash-medium", "gemini-3.1-pro-high",
             "gemini-3.6-flash-medium", "claude-sonnet-4-6"),
        ),
    },
    "best": {
        "label": "Best",
        "detail": "The big model everywhere. Slower and dearer; worth it on messy, "
                  "contradictory source material.",
        "models": _models(
            ("anthropic/claude-opus-5", "anthropic/claude-opus-5", "anthropic/claude-opus-5",
             "anthropic/claude-opus-5"),
            ("opus", "opus", "opus", "opus"),
            ("opencode/claude-opus-5", "opencode/claude-opus-5", "opencode/claude-sonnet-5",
             "opencode/claude-opus-5"),
            ("gpt-5.4", "gpt-5.4", "gpt-5.4", "gpt-5.4"),
            ("gpt-5.4-codex", "gpt-5.4-codex", "gpt-5.4-codex", "gpt-5.4-codex"),
            ("gemini-3.1-pro-high", "gemini-3.1-pro-high",
             "gemini-3.1-pro-high", "claude-opus-4-6-thinking"),
        ),
    },
}
"""Curated model sets, so nobody has to know from memory that
``opencode/claude-opus-5`` is the one that reasons well and that an unset
opencode model fails outright. Every profile covers every backend, not just the
active one: a failover target with no model is a failover that does not work."""

DEFAULT_PROFILE = "balanced"


@runtime_checkable
class SettingsBackend(Protocol):
    """Where the settings page reads and writes.

    The local one is a ``.env`` file. A hosted installation cannot rewrite the
    process's own environment — that is a privilege escalation waiting to happen,
    and one file cannot hold many tenants' settings anyway — so its backend keeps
    per-tenant values and answers the same four calls. Everything the settings
    routes and the page need is here; the allow-list itself (:data:`FIELDS`) is
    shared because the shape of what may be configured does not change.
    """

    label: str
    """What the page says under the form's title, where the local one names the
    file. A hosted backend names the tenant's own settings."""
    default_profile: str
    restartable: bool
    """Whether a change here needs the mesh restarted. True for ``.env`` - the
    agents read their configuration once, at start-up. False for a backend that
    resolves settings per request, where there is nothing to restart."""

    def read(self) -> dict[str, str]: ...

    def write(self, updates: dict[str, str]) -> list[str]: ...

    def describe(self) -> list[dict[str, Any]]: ...

    def profiles_for(self) -> dict[str, dict[str, Any]]: ...


class EnvFileBackend:
    """The settings page over the ``.env`` on disk, i.e. today's behaviour.

    ``path`` may be a path or a callable returning one: the file the page edits
    is the one the running configuration names, resolved when the page is asked
    rather than frozen when the app was built.
    """

    restartable = True
    default_profile = DEFAULT_PROFILE

    def __init__(self, path: Path | Callable[[], Path]) -> None:
        self._resolve = (lambda: path) if isinstance(path, Path) else path

    @property
    def path(self) -> Path:
        return self._resolve()

    @property
    def label(self) -> str:
        return str(self.path)

    def read(self) -> dict[str, str]:
        return read(self.path)

    def write(self, updates: dict[str, str]) -> list[str]:
        return write(self.path, updates)

    def describe(self) -> list[dict[str, Any]]:
        return describe(self.path)

    def profiles_for(self) -> dict[str, dict[str, Any]]:
        return profiles_for(self.path)


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
    ``SOURCEWORK_LLM__<field>`` keys - nested ones address a sub-model and are not
    worth reflecting into.
    """
    from sourcework.config import LLMSettings

    suffix = key.removeprefix("SOURCEWORK_LLM__")
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


def _points_at_a_private_endpoint(current: dict[str, str]) -> bool:
    """Is ``litellm`` aimed at something other than a public provider?

    ``api_base`` is the tell. Set, the endpoint is a local server or a gateway,
    and it serves whatever it serves - which is never ``anthropic/claude-…``.
    """
    # Judged only by the file being described, and deliberately not by
    # `os.environ` or `settings()`. Importing litellm calls `load_dotenv()`,
    # which pours the *process's own* .env into the environment - so consulting
    # it would make the suggestions for one file depend on whichever .env the
    # server happened to boot from, and on whether a backend had been imported
    # yet. A page that edits a file should answer for that file.
    #
    # The cost is a miss when API_BASE is set as a true environment variable and
    # appears in no .env at all: that install still sees hosted suggestions. A
    # stale suggestion is a far smaller problem than a page whose behaviour
    # depends on an import side effect.
    return bool(current.get("SOURCEWORK_LLM__API_BASE"))


def _suggestion(field: Field, current: dict[str, str]) -> str:
    """The model id to pre-fill an empty cell with, if any.

    The profiles are lists of hosted model ids, which is right for the common
    case and actively wrong for a local install: pre-filling
    ``anthropic/claude-opus-5`` into an empty cell means Save writes a model
    the operator has no key for, and the run fails on a value they never chose.
    A cell that stays empty and offers the endpoint's real models from the
    picker is the honest alternative.

    Local llama.cpp model ids are discovered from the selected server, so never
    pre-fill a hosted profile id into those cells.
    """
    if field.backend == "llama-cpp" or (field.backend == "litellm" and _points_at_a_private_endpoint(current)):
        return ""
    return PROFILES[DEFAULT_PROFILE]["models"].get(field.key, "")


def profiles_for(path: Path) -> dict[str, dict[str, Any]]:
    """:data:`PROFILES`, minus what would not work on this install.

    A profile is applied to every backend at once, on purpose - the failover
    target is exactly the one nobody remembers to configure. But when
    ``litellm`` points at a private endpoint, its three hosted ids are the one
    part of that sweep that cannot work, and writing them is worse than leaving
    those cells alone. The UI needs no special case: it already skips a cell a
    profile has nothing to say about.
    """
    if not _points_at_a_private_endpoint(read(path)):
        return PROFILES

    local_keys = {f.key for f in FIELDS if f.backend == "llama-cpp"}
    litellm_keys = {f.key for f in FIELDS if f.backend == "litellm"}
    excluded = local_keys | (litellm_keys if _points_at_a_private_endpoint(read(path)) else set())
    return {
        name: {
            **profile,
            "models": {k: v for k, v in profile["models"].items() if k not in excluded},
            "detail": profile["detail"] + " Leaves local endpoint models alone.",
        }
        for name, profile in PROFILES.items()
    }


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
            "suggested": _suggestion(f, current),
            "restart": f.restart,
            "backend": f.backend,
            "role": f.role,
            "value": _display_value(f, current),
            "set": bool(current.get(f.key)),
        }
        for f in FIELDS
    ]


def _means_unset(key: str, value: str) -> bool:
    """Is a blank value for ``key`` a request to fall back to the default?

    For a number it must be. ``MAX_TOKENS=`` reads back as the empty string,
    which is not an int, and pydantic-settings raises at construction - so a
    save that looked fine left every *subsequent* process unable to start. The
    running one survived on its cached settings, which is what made this look
    like an intermittent 500 rather than a broken config file.

    Left alone for text fields, where an empty value is a real one and clearing
    a box should not resurrect a default the operator was trying to remove.
    """
    field = BY_KEY.get(key)
    return field is not None and field.kind == "number" and not value.strip()


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
            value = remaining.pop(key)
            if _means_unset(key, value):
                # Commented out rather than written as `KEY=`. An empty value is
                # a valid *string*, so pydantic accepts `KEY=` for a text field
                # and rejects it for an int - which turned a successful save into
                # a config no process could load afterwards. The comment keeps the
                # line visible where the operator left it.
                lines[index] = f"# {key}="
            else:
                lines[index] = f"{key}={_quote(value)}"

    # A key that was never in the file and is being cleared has nothing to write.
    remaining = {k: v for k, v in remaining.items() if not _means_unset(k, v)}

    if remaining:
        if lines and lines[-1].strip():
            lines.append("")
        lines.append("# --- added by the SourceWork settings page ---")
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
