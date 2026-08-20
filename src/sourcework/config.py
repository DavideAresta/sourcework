"""Central configuration.

Everything is read from the environment with the ``SOURCEWORK_`` prefix and a
``__`` nesting delimiter, e.g. ``SOURCEWORK_LLM__DEFAULT_MODEL``.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from functools import lru_cache
from typing import Annotated, Any

from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from sourcework import paths

ROLES = ("default", "reasoning", "vision", "fast", "critic")

API_BACKEND_IDS = ("litellm", "azure", "bedrock", "vertex-ai", "openai")
"""Backends that reach a hosted provider over HTTPS.

The family a hosted distribution offers: their credentials are configuration a
server can hold, which is exactly what the CLIs and the local server are not.
``litellm`` is the general provider - ``azure``/``bedrock``/``vertex-ai``/
``openai`` exist as named, first-class options with their own credential
fields on the settings page. (``vertex-ai``, hyphenated like every other id;
LiteLLM spells its provider prefix ``vertex_ai``, which is a model-id matter.)
"""

CLI_BACKEND_IDS = ("claude-code", "opencode-cli", "copilot-cli", "codex-cli", "agy-cli")
"""Backends that drive a coding CLI on the machine the agent runs on. They carry
their own authentication, so they are local-first by construction: a hosted
deployment never offers them."""

BACKEND_IDS = API_BACKEND_IDS + ("llama-cpp",) + CLI_BACKEND_IDS
"""Every way of reaching a model.

``API_BACKEND_IDS`` reach hosted providers; ``llama-cpp`` reaches a local
``llama-server`` directly through its OpenAI-compatible API; the CLI backends
drive coding CLIs that carry their own authentication. The settings page offers
a subset of this per distribution: the local one everything, a hosted one only
:data:`API_BACKEND_IDS`.
"""


def normalise_backend(backend: str) -> str:
    """``claude_code`` and ``CLAUDE-CODE`` both mean ``claude-code``.

    Environment variables cannot contain hyphens in a nested key, so the same
    backend arrives spelled two ways depending on whether it was written in
    ``.env`` or in code.
    """
    return backend.strip().lower().replace("_", "-")


_MODEL_FIELDS = {
    "litellm": "litellm_models",
    "azure": "azure_models",
    "bedrock": "bedrock_models",
    "vertex-ai": "vertex_ai_models",
    "openai": "openai_models",
    "llama-cpp": "llama_cpp_models",
    "claude-code": "claude_code_models",
    "opencode-cli": "opencode_models",
    "copilot-cli": "copilot_models",
    "codex-cli": "codex_models",
    "agy-cli": "agy_models",
}


class BackendModels(BaseModel):
    """Per-role model ids *for one backend*.

    Kept per backend because a model choice does not travel. ``opencode-go/glm-5``
    means something to OpenCode and nothing to the ``claude`` CLI, so handing
    the running model to a failover target is how a working call turns into an
    "unknown model" error on the backend that was supposed to rescue it.

    Unset means "let that backend pick its own default", which is the safe
    behaviour and the one most people want.
    """

    default: str | None = None
    reasoning: str | None = None
    vision: str | None = None
    fast: str | None = None
    critic: str | None = None

    def for_role(self, role: str) -> str | None:
        if role == "critic":
            # A critic left unset follows `reasoning`, not `default`: reviewing
            # a PRD is the same weight of work as writing one, and silently
            # demoting it to the cheap model would make the review weaker than
            # the thing it reviews.
            return self.critic or self.reasoning or self.default
        return getattr(self, role, None) or self.default


class LLMSettings(BaseModel):
    """Model routing, across two families of backend.

    Roles exist so an operator can spend money where it matters (reasoning) and
    save it where it does not (fast). ``backend`` decides *how* the model is
    reached; ``failover_order`` decides where a call goes when that backend
    reports it is out of quota.
    """

    backend: str = "litellm"
    """One of :data:`BACKEND_IDS`. ``stub`` is accepted as an alias for
    ``stub=True`` so a single variable can switch the whole system off-network."""

    failover_order: Annotated[list[str], NoDecode] = Field(default_factory=list)
    """Backends to try, in order, when the active one fails. Empty (the default)
    means a failure is terminal - which is the honest behaviour when nobody has
    said what the alternative is.

    ``NoDecode`` so it can be written the way people actually write .env files
    (``a,b,c``); without it pydantic-settings JSON-decodes the value first and a
    comma-separated list dies with a parser stack trace."""

    # Per-backend role models, one field per backend rather than a dict keyed by
    # backend id: pydantic-settings cannot explode `SOURCEWORK_LLM__X__CLAUDE_CODE__
    # DEFAULT` into a dict-of-models, it tries to JSON-decode the leaf and fails.
    # Explicit fields also make the whole surface visible in .env.example.
    litellm_models: BackendModels = Field(default_factory=BackendModels)
    azure_models: BackendModels = Field(default_factory=BackendModels)
    bedrock_models: BackendModels = Field(default_factory=BackendModels)
    vertex_ai_models: BackendModels = Field(default_factory=BackendModels)
    openai_models: BackendModels = Field(default_factory=BackendModels)
    llama_cpp_models: BackendModels = Field(default_factory=BackendModels)
    claude_code_models: BackendModels = Field(default_factory=BackendModels)
    opencode_models: BackendModels = Field(default_factory=BackendModels)
    copilot_models: BackendModels = Field(default_factory=BackendModels)
    codex_models: BackendModels = Field(default_factory=BackendModels)
    agy_models: BackendModels = Field(default_factory=BackendModels)

    # LiteLLM role models. Kept as top-level fields rather than folded into
    # backend_models because they predate multi-backend support and are what
    # every existing .env sets.
    default_model: str = "anthropic/claude-sonnet-5"
    reasoning_model: str = "anthropic/claude-opus-5"
    vision_model: str = "anthropic/claude-sonnet-5"
    fast_model: str = "anthropic/claude-haiku-4-5"

    critic_model: str | None = None
    """The adversarial pass, kept separate from :attr:`reasoning_model` so it
    can be a *different family*.

    A critic built on the same model as the writer shares its blind spots: it
    was trained to find the same phrasing natural, so the sentence that reads
    fine to the writer reads fine to the reviewer too. Pointing this at another
    lineage is the cheapest way to make the review adversarial rather than
    confirmatory. Unset means "use the reasoning model", which is the old
    behaviour."""

    max_tokens: int = 8192
    temperature: float = 0.2
    api_base: str | None = None
    api_key: str | None = None
    llama_cpp_api_base: str = "http://127.0.0.1:8081/v1"
    """OpenAI-compatible endpoint exposed by ``llama-server`` or llama-swap."""

    llama_cpp_api_key: str | None = "local"
    """Optional bearer token for a local server. ``llama-server`` accepts any value by default."""
    timeout_s: float = 180.0
    """Timeout for an API call. CLI backends use :attr:`cli_timeout_s`."""

    cli_timeout_s: float = 600.0

    analysis_batch_chars: int = 60_000
    """Above this much rendered evidence, the analyst stops trying to do it in
    one call and works in slices, merging the results.

    It exists because the analyst is the one call whose *prompt and answer both*
    scale with the size of the input: 223 evidence items produced a 129k-char
    prompt and a 33k-token answer that took a CLI backend nearly ten minutes and
    still stopped at the output ceiling. Slicing bounds both. Raise it for a
    model with a large context and fast output; lower it if calls still run
    long. 0 disables this limit."""

    analysis_batch_items: int = 70
    """Evidence items per analyst slice.

    The companion to :attr:`analysis_batch_chars`, and the one that catches the
    case the character limit misses: 176 evidence items rendered to only 45k
    characters - well under any prompt limit - while the requirement set
    covering them ran to 33k output tokens and stopped at the model's ceiling.
    A short prompt is no guarantee of a short reply, and it is the reply that
    costs the ten minutes. 0 disables this limit."""
    """CLI backends are slower than an API call by construction - a process
    starts, loads its config, and may think for a while before saying anything -
    so they get their own, longer budget."""

    effort: str | None = None
    """Reasoning effort passed to backends that accept one (``--effort`` on
    claude-code and copilot, ``--variant`` on opencode). None = model default.
    Ignored by the litellm backend."""

    opencode_pure: bool = False
    """Run OpenCode with ``--pure`` (no external plugins). Faster, and it stops
    OpenCode re-installing a plugin tree into the scratch directory on every
    call - but it also disables user-global plugins, so it is opt-in."""

    copilot_profile: str | None = None
    """Which Copilot user/profile ``copilot-cli`` should run as.

    When set, SourceWork derives ``COPILOT_HOME`` as
    ``~/.copilot-<profile>`` unless :attr:`copilot_home` is explicitly set.
    This mirrors the profile pattern used in codegen-pipeline while keeping the
    setting surface one-field simple in this project.
    """

    copilot_home: str | None = None
    """``COPILOT_HOME`` for the copilot-cli backend. The CLI keeps credentials,
    MCP config, plugins and skills in one directory; pointing this at a copy
    holding only the credentials gives generation calls a clean, fast session
    instead of dialling whatever MCP servers the developer happens to have.
    Unset means "use the developer's own", which always works."""

    codex_home: str | None = None
    """``CODEX_HOME`` for the codex-cli backend. Codex keeps credentials, config
    and session history in one directory; pointing this at a copy holding only
    the credentials gives generation calls a clean session. Unset means "use the
    developer's own", which always works."""

    # Hosted-provider credentials, one named backend per provider. Each is the
    # surface the settings page shows for the azure/bedrock/vertex_ai/openai
    # backends; litellm's own backend reuses ANTHROPIC_API_KEY/OPENAI_API_KEY
    # and SOURCEWORK_LLM__API_BASE/API_KEY instead. LiteLLM reads the same
    # values from the standard environment variables when these are unset, so
    # nothing here is ever required in code - a deployment may credential the
    # provider through its own secret-injection instead.
    azure_api_key: str | None = None
    azure_api_base: str | None = None
    """``https://<resource>.openai.azure.com/``. LiteLLM derives the
    ``/openai/deployments/<name>`` path from the model id ``azure/<name>``."""
    azure_api_version: str | None = None

    aws_region_name: str | None = None
    aws_access_key_id: str | None = None
    aws_secret_access_key: str | None = None
    aws_session_token: str | None = None

    vertex_project: str | None = None
    vertex_location: str | None = None

    openai_api_base: str | None = None
    """Optional ``api_base`` for the openai backend; unset reaches openai.com.
    The key is the standard ``OPENAI_API_KEY``."""

    constrained_json: bool = True
    """Ask the backend to *enforce* the JSON schema, not just describe it.

    On an OpenAI-compatible server that grammar-constrains decoding (llama.cpp,
    vLLM, Ollama) this makes malformed JSON impossible rather than unlikely,
    which is what lets a small local model drive the pipeline: it never spends
    :attr:`max_retries` re-answering a call it already paid for. Backends that
    cannot enforce a schema ignore it, and the schema is in the prompt for them
    regardless. Turn it off for a provider whose schema support is worse than
    its prompt-following."""

    litellm_retries: int = 2
    """Retries LiteLLM makes inside a single call.

    Lower it for a local server, where the usual failure is a timeout rather
    than a blip: three attempts at a 20-minute ceiling is an hour spent
    learning the same thing once."""

    max_retries: int = 3
    """Attempts to get schema-valid JSON out of a model that answered."""

    empty_retries: int = 1
    """Extra attempts when a backend exits cleanly having said *nothing*.

    Distinct from :attr:`max_retries`, which handles a model that answered
    badly. An empty answer is usually transient - a reasoning-heavy model that
    spent its whole output budget thinking, or a CLI that dropped the final
    text event - and one cheap retry recovers it. Without this a single empty
    response kills an entire extraction, and with a reasoning model at high
    effort that happens often enough to lose whole runs."""

    stub: bool = False
    """When true, no network call is made and a deterministic fake response is
    produced. Keeps the whole pipeline runnable in CI without credentials."""

    @field_validator("failover_order", mode="before")
    @classmethod
    def _split_failover(cls, value: Any) -> Any:
        """Accept ``a,b,c`` as well as a JSON list - .env files write the former."""
        if isinstance(value, str):
            return [part.strip() for part in value.split(",") if part.strip()]
        return value

    @field_validator("backend", mode="before")
    @classmethod
    def _normalise_backend(cls, value: Any) -> Any:
        return normalise_backend(value) if isinstance(value, str) else value

    @field_validator("failover_order", mode="after")
    @classmethod
    def _normalise_chain(cls, value: list[str]) -> list[str]:
        return [normalise_backend(v) for v in value]

    @property
    def active_backend(self) -> str:
        """The backend id in force, resolving the ``stub`` alias."""
        return "stub" if self.stub or self.backend == "stub" else self.backend

    def models_for(self, backend: str) -> BackendModels:
        """Model overrides configured for ``backend``, empty if none."""
        field = _MODEL_FIELDS.get(normalise_backend(backend))
        return getattr(self, field) if field else BackendModels()

    def model_for(self, role: str, backend: str | None = None) -> str | None:
        """The model id to use for ``role`` on ``backend``.

        Returns None when nothing is configured, which every CLI backend reads
        as "use your own default" - the correct answer for a failover target
        nobody has picked a model for.
        """
        backend = normalise_backend(backend or self.active_backend)
        override = self.models_for(backend).for_role(role)
        if override:
            return override
        if backend == "litellm":
            return {
                "default": self.default_model,
                "reasoning": self.reasoning_model,
                "vision": self.vision_model,
                "fast": self.fast_model,
                "critic": self.critic_model or self.reasoning_model,
            }.get(role, self.default_model)
        return None

    def timeout_for(self, backend: str) -> float:
        api = API_BACKEND_IDS + ("llama-cpp",)
        return self.timeout_s if normalise_backend(backend) in api else self.cli_timeout_s


class ConfluenceSettings(BaseModel):
    base_url: str = "https://example.atlassian.net/wiki"
    email: str = ""
    api_token: str = ""
    default_space_key: str = "PRD"
    default_parent_id: str | None = None
    timeout_s: float = 30.0

    @property
    def configured(self) -> bool:
        return bool(self.email and self.api_token)


class PeerSettings(BaseModel):
    """Base URLs of every agent in the mesh, including the orchestrator so
    external clients (and the CLI) can address it by the same logical name."""

    orchestrator: str = "http://localhost:8000"
    ingestion: str = "http://localhost:8001"
    vision: str = "http://localhost:8002"
    transcript: str = "http://localhost:8003"
    confluence: str = "http://localhost:8004"
    requirements: str = "http://localhost:8005"
    writer: str = "http://localhost:8006"
    critic: str = "http://localhost:8007"

    def as_map(self) -> dict[str, str]:
        return self.model_dump()


class MeshSettings(BaseModel):
    """How long this process waits on another agent in the mesh.

    Separate from :class:`LLMSettings` because these are transport clocks, not
    model clocks: they bound how long a *connection* may be quiet, never how
    long a model may think.
    """

    connect_timeout_s: float = 10.0
    """Opening the socket, writing to it, and taking a connection from the pool.

    Every peer is either on this machine or on the same compose network, so a
    connection that has not been made in ten seconds is not going to be. This
    used to share the read timeout's ten minutes, which meant an unreachable
    agent - a wrong hostname, a firewalled port - held the caller for the length
    of a whole run before admitting it."""

    read_timeout_s: float | None = None
    """The longest an agent may say *nothing* before we treat it as gone.
    Unset (the default) derives it - see :meth:`read_timeout_for`.

    httpx applies its read timeout per chunk, so on the A2A stream this is a
    ceiling on silence rather than on the call. That distinction was the bug: at
    the old flat 600s the transport gave up at exactly the moment
    :attr:`LLMSettings.cli_timeout_s` still permitted a single CLI call to be
    thinking, and long before the retries and failover around it were spent.

    Set this only to make the mesh *less* patient than the derived value. Making
    it more patient than the calls it carries is the derivation's job, and doing
    it by hand is how the two drift apart again."""

    def read_timeout_for(self, llm: LLMSettings) -> float:
        """A silence ceiling that cannot be lower than the work it has to carry.

        Derived rather than defaulted, because a fixed number is the bug in a
        different costume: this project ships a 180s API budget and a 600s CLI
        one, and the first person to point it at a local model server raises
        :attr:`LLMSettings.timeout_s` to twenty minutes - at which point any
        constant chosen here is below the calls again, and a slow analyst once
        more looks exactly like a dead one.

        The factor is headroom, not a second budget. Two conditions have to fail
        together to reach this: the peer stops sending
        :data:`~sourcework.a2a_common.executor.KEEPALIVE_INTERVAL_S` ticks
        *and* the call it is making runs to its own timeout. What is left is a
        peer that is genuinely gone, which is the only thing this should ever
        report.
        """
        if self.read_timeout_s is not None:
            return self.read_timeout_s
        return max(600.0, max(llm.timeout_s, llm.cli_timeout_s) * 2)


class RunsSettings(BaseModel):
    """The UI's run history."""

    retention_days: int = 0
    """Delete finished runs older than this, on UI start-up.

    0 (the default) keeps everything. The run store holds the full text of
    every ingested source and the complete PRDs built from them, so an
    installation with a data-retention policy needs a number here, not a
    reminder to come back and delete things by hand. Only finished runs are
    touched; anything in flight is left alone."""


class QualitySettings(BaseModel):
    """The requirements-quality rule pack (``sourcework.quality``)."""

    ears: bool = False
    """Write and check requirements in EARS patterns (WHEN/WHILE/IF-THEN/
    WHERE/ubiquitous).

    Off by default: conforming phrasing is a team choice, not a defect, and
    turning it on changes what the analyst is asked to write, not just what the
    critic flags. On, the analyst's prompt asks for EARS-shaped statements and
    the critic flags statements that do not take one of the five shapes."""


DEFAULT_MESH_KEY = "dev-local-shared-secret"
"""The shipped inter-agent secret. Published in a public repository, therefore
known to everyone - which is fine while it is only ever used on a laptop, and
is why :attr:`SecuritySettings.enforce` refuses to start with it."""


class SecuritySettings(BaseModel):
    api_key: str = DEFAULT_MESH_KEY
    header: str = "X-SourceWork-Key"
    enforce: bool = False

    allow_private_fetch: bool = False
    """Let ingestion fetch loopback, link-local and private-range addresses.

    Off, because the ability to make the server issue a request is the whole of
    SSRF and nothing legitimate ingests a document from 169.254.169.254. On for
    the deployment whose document store really is on an internal address."""

    def guard(self) -> None:
        """Refuse to enforce with the secret everybody already has.

        Enforcement with the published default is worse than none: it looks
        like authentication in a config review and stops nobody.
        """
        if self.enforce and self.api_key == DEFAULT_MESH_KEY:
            raise RuntimeError(
                "SOURCEWORK_SECURITY__ENFORCE is on but the API key is still the shipped "
                "default, which is published in this repository and therefore public. "
                "Set SOURCEWORK_SECURITY__API_KEY to something you generated:\n"
                "    python -c 'import secrets; print(secrets.token_urlsafe(32))'"
            )


class LLMOverrides(BaseModel):
    """Per-run model settings, chosen by the caller instead of the environment.

    These travel *inside* the A2A request. The mesh is eight separate
    processes, so the alternative - editing ``.env`` and restarting everything -
    means you cannot try a run on a different backend without a two-minute
    round trip. Carrying them in the payload also means a run records what
    produced it, which is the same argument the rest of this system makes about
    evidence.

    Every field is optional and unset means "leave the environment's value
    alone". :class:`~sourcework.a2a_common.AgentPool` injects whatever it was
    given into every downstream call, so one object covers the whole run.
    """

    backend: str | None = None
    failover_order: list[str] | None = None
    effort: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    models: dict[str, str] = Field(default_factory=dict)
    """role -> model id, applied to whichever backend this run uses. Roles are
    the usual four; anything else is ignored by :meth:`LLMSettings.model_for`."""

    def applied_to(self, cfg: LLMSettings) -> LLMSettings:
        """``cfg`` with these overrides on top. Returns ``cfg`` itself if empty."""
        update: dict[str, Any] = {}

        backend = normalise_backend(self.backend) if self.backend else cfg.active_backend
        if self.backend:
            update["backend"] = backend
            # An explicit backend has to be able to turn stub mode off, or a
            # mesh booted with SOURCEWORK_LLM__STUB=1 would silently fake every
            # run the UI submits.
            update["stub"] = backend == "stub"
        if self.failover_order is not None:
            update["failover_order"] = [normalise_backend(b) for b in self.failover_order]
        if self.effort:
            update["effort"] = self.effort
        if self.temperature is not None:
            update["temperature"] = self.temperature
        if self.max_tokens is not None:
            update["max_tokens"] = self.max_tokens

        chosen = {role: model for role, model in self.models.items() if model}
        if chosen:
            field = _MODEL_FIELDS.get(backend)
            if field:
                update[field] = getattr(cfg, field).model_copy(update=chosen)

        return cfg.model_copy(update=update) if update else cfg


_overrides: ContextVar[LLMOverrides | None] = ContextVar("sourcework_llm_overrides", default=None)


@contextmanager
def llm_overrides(overrides: LLMOverrides | None) -> Iterator[None]:
    """Apply ``overrides`` to every :class:`~sourcework.llm.LLM` built in this block.

    A context variable rather than an argument because the agents build their
    ``LLM`` once, at construction, long before any request arrives. Threading a
    per-request config down to them would mean changing all seven.
    """
    token = _overrides.set(overrides)
    try:
        yield
    finally:
        _overrides.reset(token)


def effective_llm() -> LLMSettings:
    """The LLM settings in force right now: the environment, plus any overrides."""
    overrides = _overrides.get()
    base = settings().llm
    return overrides.applied_to(base) if overrides is not None else base


class Settings(BaseSettings):
    # `env_file` is resolved rather than hardcoded: a packaged application is
    # launched with a working directory of `/`, where a relative ".env" is
    # neither found nor writable. `paths` returns the checkout's file when there
    # is a checkout and the platform's per-user location otherwise.
    model_config = SettingsConfigDict(
        env_prefix="SOURCEWORK_",
        env_nested_delimiter="__",
        env_file=str(paths.env_file()),
        extra="ignore",
    )

    llm: LLMSettings = Field(default_factory=LLMSettings)
    confluence: ConfluenceSettings = Field(default_factory=ConfluenceSettings)
    peers: PeerSettings = Field(default_factory=PeerSettings)
    security: SecuritySettings = Field(default_factory=SecuritySettings)
    quality: QualitySettings = Field(default_factory=QualitySettings)
    runs: RunsSettings = Field(default_factory=RunsSettings)
    mesh: MeshSettings = Field(default_factory=MeshSettings)

    max_concurrent_runs: int = 2
    """Runs executing at once; the rest queue.

    A run is a queue of calls to one model server, not CPU work the OS can
    interleave. Against a local server that is a single GPU holding a single
    model, so two runs wanting different models make it thrash - both finish
    later than if they had queued. Raise it when the backend is a hosted API
    that parallelises on its own side."""

    ui_workspace: str = Field(default_factory=lambda: str(paths.workspace()))
    """Where the UI keeps run history and uploads. Must be a directory the
    ingestion agents can also read - in compose that is the mounted
    ``./workspace``, which is why a checkout resolves to the same place."""

    env_file: str = Field(default_factory=lambda: str(paths.env_file()))
    """The file the settings page edits. Explicit because the UI writes to it,
    and writing to a path you inferred is how you overwrite the wrong one."""

    log_level: str = "INFO"
    public_host: str = "localhost"
    """Hostname advertised in agent cards. In docker-compose this is the
    service name so peers can reach each other."""


@lru_cache(maxsize=1)
def settings() -> Settings:
    return Settings()
