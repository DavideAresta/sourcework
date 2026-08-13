# Backends, models and configuration

An agent calls `llm.structured(...)`. What sits behind that is configuration:

| Backend | How | Auth |
|---|---|---|
| `litellm` | HTTPS to a hosted API | provider credentials |
| `claude-code` | `claude -p --output-format json` | the CLI's own login |
| `opencode-cli` | `opencode run --format json` | OpenCode's provider config |
| `copilot-cli` | `copilot -p --output-format json` | `copilot login` |
| `codex-cli` | `codex exec --json` | `codex login` |
| `agy-cli` | `agy --print --output-format json` | its own sign-in |

The five CLI backends carry **their own** authentication, so if you are signed
into one of those tools the entire pipeline runs on that subscription with no
key plumbed through SourceWork:

```bash
SOURCEWORK_LLM__BACKEND=claude-code
SOURCEWORK_LLM__CLAUDE_CODE_MODELS__DEFAULT=haiku
SOURCEWORK_LLM__CLAUDE_CODE_MODELS__REASONING=sonnet
```

```bash
sourcework backends          # what is usable here
sourcework backends --check  # actually call each one (spends real quota)
```

They are driven as *generation* backends, not agents: no tools, no MCP servers,
no narration, one turn, answer only. A coding CLI left to its own devices works
the question agentically and glues its reasoning onto the reply an agent is
about to parse as JSON. Costs what you would expect — the same eight-agent run
takes ~14 minutes on a CLI backend against a couple of minutes on an API,
because every call starts a process.

**Failover.** When a backend hits a usage limit — or exits cleanly having said
nothing — the call moves on rather than failing:

```bash
SOURCEWORK_LLM__FAILOVER_ORDER=claude-code,opencode-cli,litellm
```

The model does *not* travel with it. `opencode-go/glm-5` means something to
OpenCode and nothing to the `claude` CLI, so each backend uses the model
configured for it, or its own default.

**Images pick the backend.** A call carrying images only considers backends
that can transport them. A text-only backend handed an image does not error, it
answers about nothing.


## Watching the model work

A run takes minutes, and a progress log tells you *which stage* is slow, never
*why*. The coding CLIs narrate themselves — the reasoning, then the answer
forming — and a run opened in the browser gets that narration live, tagged with
the agent that produced it.

What you actually see depends on the backend, and the difference is the
provider's, not ours:

| Backend | Reasoning | Answer |
|---|---|---|
| `opencode-cli` | **yes** — real text, arriving before the answer | complete, when the call ends |
| `copilot-cli` | **yes on OpenAI models** — token by token, via `--enable-reasoning-summaries`. An Anthropic model behind Copilot returns it encrypted instead | token by token |
| `claude-code` | no — thinking blocks arrive with `thinking: ""` and the content only present as an encrypted signature. The panel shows a live token estimate instead | **token by token** |
| `codex-cli` | yes — reasoning items as they complete. Costs **no extra flag**: `--json` is already how the answer is parsed | token by token |
| `agy-cli` | yes — `text_delta` events under `--output-format stream-json` | token by token |
| `litellm` | not streamed — the API path answers in one response | — |

Reasoning only appears when the model actually does some: a trivial question
answered at `--effort high` produces none, on any of them.

Two deliberate choices:

- **Opt-in, per run.** Narration costs a different output format on
  `claude-code` and a `--thinking` flag on `opencode-cli`, so the mesh's own
  internal calls do not ask for it. A run started from the UI does.
- **Never stored.** It is megabytes per run, nothing cites it, and every stored
  event rewrites the run record. Reload a finished run and the panel is empty —
  the PRD is the artifact, this was the model getting there.


## Configuration

All via env (`SOURCEWORK_` prefix, `__` nesting) — see `.env.example`.

**Models** are routed per role so you spend where it matters, and configured
per backend because a model id from one backend is nonsense to another:

```bash
# litellm — any LiteLLM provider: openai/…, azure/…, bedrock/…, vertex_ai/…, ollama/…
SOURCEWORK_LLM__REASONING_MODEL=anthropic/claude-opus-4-6      # analyst, writer
SOURCEWORK_LLM__DEFAULT_MODEL=anthropic/claude-sonnet-4-5      # extraction
SOURCEWORK_LLM__VISION_MODEL=anthropic/claude-sonnet-4-5       # images
SOURCEWORK_LLM__CRITIC_MODEL=anthropic/claude-opus-4-6         # the adversarial review
SOURCEWORK_LLM__FAST_MODEL=anthropic/claude-haiku-4-5

# per CLI backend
SOURCEWORK_LLM__CLAUDE_CODE_MODELS__REASONING=sonnet
SOURCEWORK_LLM__OPENCODE_MODELS__DEFAULT=opencode/claude-haiku-4-5
SOURCEWORK_LLM__COPILOT_MODELS__DEFAULT=auto
```

Anything unset means "let that backend pick its own default". Point
`SOURCEWORK_LLM__API_BASE` at a gateway for the litellm path. No code changes.

**Usage** — every call records what the backend reported (tokens, cache hits,
cost) into a per-process ledger. Costs are kept apart by unit: Claude Code
reports what tokens *would* have cost on the API, Copilot reports converted
credits, LiteLLM reports the provider's own figure. Adding those together
produces a number that means nothing.

**Confluence** — unscoped API token → `base_url` is
`https://<site>.atlassian.net/wiki`; scoped token → it must be
`https://api.atlassian.com/ex/confluence/<cloudId>/wiki`. Only the Confluence
agent ever sees the credentials.

**Inter-agent auth** — set `SOURCEWORK_SECURITY__ENFORCE=1` and every agent
declares an `apiKey` security scheme on its card and rejects unauthenticated
calls.

