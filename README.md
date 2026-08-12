# PRD Forge

Eight cooperating agents that turn documents, meeting transcriptions, images
and Confluence pages into a **traceable** Product Requirements Document — and
publish it back to Confluence.

They talk to each other over the [A2A protocol](https://a2a-protocol.org)
(v1.0), so each one is an independently deployable service with a published
agent card, and any A2A-speaking client can drive the whole thing.

```
                       ┌──────────────────────────┐
   your client ──A2A──▶│   PRD Orchestrator :8000 │
                       └────────────┬─────────────┘
                                    │ A2A (JSON-RPC + SSE)
        ┌───────────┬───────────┬───┴───────┬─────────────┬──────────┐
        ▼           ▼           ▼           ▼             ▼          ▼
   Ingestor    Image       Meeting     Confluence   Requirements  PRD Writer
    :8001      Analyst     Analyst     Connector      Analyst       :8006
               :8002        :8003        :8004         :8005          │
        └───────────┴───────────┴───────────┘                         ▼
                    evidence                                    PRD Critic
                                                                   :8007
```

## The one idea worth knowing

Nothing enters the pipeline except **evidence**: an atomic, quotable claim with
a locator (`p.12`, `00:14:32`, `slide 4`, `heading: Scope`) pointing back at
where it came from. Requirements cite evidence ids. Citations are validated in
code — a model that invents an id loses it, and any requirement left uncited is
forced to render as `derived`, meaning *inferred, not stated*.

That single constraint is what makes the output auditable, gives you a real
traceability matrix at the bottom of every PRD, and lets the critic agent
mechanically detect unsupported claims rather than being asked to feel bad
about them.

## Quick start

```bash
make install                 # venv + editable install
cp .env.example .env         # fill in model + Confluence credentials

make demo                    # full pipeline, no API key needed (stub LLM)
make test                    # unit + end-to-end over real A2A
```

Run the mesh for real:

```bash
prdforge-agent serve-all     # all eight agents, one process (dev)
prdforge-agent ui            # the web UI, on http://localhost:8080
prdforge-agent status        # which agents are up, and their skills
prdforge-agent backends      # which LLM backends this machine can use
```

Or from the command line:

```bash
prdforge-agent generate "Invoice reconciliation" \
  -i ~/meetings/kickoff.vtt \
  -i ~/docs/rfp.pdf \
  -i ~/designs/checkout-flow.png \
  -q 'space = PRD AND text ~ "reconciliation"' \
  --publish --space PRD
```

Or in containers:

```bash
docker compose up -d --build
curl localhost:8000/.well-known/agent-card.json
```

## The agents

| Agent | Port | Skills | Does |
|---|---|---|---|
| **Orchestrator** | 8000 | `generate_prd`, `mesh_status` | Routes inputs, sequences the pipeline, runs the review loop |
| **Document Ingestor** | 8001 | `extract_document`, `list_supported_formats` | PDF, DOCX, PPTX, XLSX, CSV, HTML, MD, TXT → evidence |
| **Image Analyst** | 8002 | `analyse_image` | Mockups, screenshots, whiteboards, diagrams → evidence |
| **Meeting Analyst** | 8003 | `extract_transcript`, `meeting_digest` | VTT/SRT/JSON/pasted transcripts → evidence + decision log |
| **Confluence Connector** | 8004 | `search_pages`, `fetch_page`, `publish_prd` | CQL search, page + attachment read, idempotent publish |
| **Requirements Analyst** | 8005 | `analyse_requirements` | Cluster, de-dup, MoSCoW, conflicts, open questions |
| **PRD Writer** | 8006 | `write_prd`, `render_prd` | Narrative + Markdown + Confluence storage XHTML |
| **PRD Critic** | 8007 | `review_prd` | Deterministic traceability checks, then adversarial review |

Every agent serves `/.well-known/agent-card.json`, `/healthz`, and `/docs`
(auto-generated OpenAPI, since the A2A routes are mounted on FastAPI).

## Calling it over A2A

```python
from prdforge.a2a_common import AgentPool
from prdforge.models import InputRef, PRDRequest, PRDResult

async with AgentPool() as pool:
    data = await pool.call("orchestrator", "generate_prd", PRDRequest(
        title="Invoice reconciliation",
        inputs=[
            InputRef(uri="file:///kickoff.vtt"),
            InputRef(uri="file:///rfp.pdf"),
            InputRef(uri="confluence://PRD/393220"),
            InputRef(uri="inline:note", text="Must ship before year-end close."),
        ],
        confluence_queries=['space = PRD AND label = "reconciliation"'],
        template="standard",     # standard | lean | technical | discovery
        review_rounds=1,
        publish=True,
    ))

result = PRDResult.model_validate(data)
print(result.markdown)          # also: result.prd (JSON), result.confluence_storage
```

Or straight JSON-RPC, no SDK:

```bash
curl -s localhost:8000/ -H 'Content-Type: application/json' -H 'A2A-Version: 1.0' -d '{
  "jsonrpc":"2.0","id":"1","method":"message/send",
  "params":{"message":{"role":"ROLE_USER","parts":[{"data":{
     "skill":"generate_prd",
     "payload":{"title":"Demo","inputs":[{"uri":"file:///workspace/notes.md"}]}
  }}]}}}'
```

## The web UI

`prdforge-agent ui` serves a browser front end on :8080. It is an A2A client,
not a ninth agent — the mesh runs fine without it.

- **New run** — drag files in, add URIs, notes and CQL, watch progress stream
  live while it works.
- **Model output** — the reasoning and the prose as the model produces them,
  in their own panel below the progress log. See *Watching the model work*.
- **Result** — the PRD rendered, plus a Requirements view showing each one next
  to the evidence that licenses it (and flagging the ones with none), the
  evidence table, the critic's findings, and per-backend token/cost totals.
- **History** — past runs, kept in SQLite; downloads and Confluence publishing.
- **Settings** — a form over `.env`, with the backends this machine can
  actually use probed live.

**Pick the backend per run.** The model controls on the run form become an
override that travels inside the A2A request to every agent, so one run can go
to `claude-code` and the next to `opencode-cli` with nothing restarted — and
the run records what produced it. Leaving a control on "configured default"
omits it, so the environment still decides.

Saved settings are different: the agents read their configuration once, at
start-up, so the settings page says *restart the mesh* rather than pretending
otherwise. Secrets are shown masked and a masked value is never written back.

### Watching the model work

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

## Refining a PRD

A PRD is never finished on the first pass — it ends by telling you what it
could not determine. The **Refine** tab on a finished run is where you answer
that:

- **Open questions** get an answer box each. Your answer becomes a new source,
  so the requirement it justifies can *cite* it like any other evidence.
- **New requirements and decisions** as free text, one per line.
- **New documents, transcripts or images** — a follow-up meeting, an addendum.

It produces a **new run**, not an edit. The old PRD stays exactly as it was and
the new one records the version it came from, so you can always see what
changed and why. Three things it gets right:

- **`REQ-` ids survive.** Carried requirements keep their id even when the
  wording changes — the id identifies the need, not the sentence. New needs are
  numbered above the highest ever issued, so a retired id is never reused and
  a ticket quoting `REQ-014` never silently repoints.
- **Evidence is carried, not re-read.** Re-ingesting the original sources would
  cost the tokens again and mint new evidence ids, breaking every citation in
  the document you already have.
- **Untouched requirements keep their citations.** The analyst re-cites what the
  new material justifies and lets the rest go; the previous version's citations
  are inherited so nothing sourced gets quietly demoted to `derived`.

Answered questions drop off, resolved conflicts get applied, and anything still
open stays open.

### Large inputs

The analyst is the call that scales worst with input size — its prompt *and*
its answer both grow with the evidence — so above a threshold it analyses the
evidence in slices and merges the results, rather than asking one call to hold
everything. This is backend-independent: the underlying limit is the model's,
not the transport's, and the same corpus times out on the bare CLI.

Two knobs, both in `.env`:

```
PRDFORGE_LLM__ANALYSIS_BATCH_ITEMS=70    # evidence items per slice (0 = off)
PRDFORGE_LLM__ANALYSIS_BATCH_CHARS=60000 # rendered characters per slice (0 = off)
```

Item count is the one that usually bites: 176 evidence items render to only 45k
characters, well under any prompt limit, while the requirement set covering them
runs past the model's output ceiling. A short prompt is no guarantee of a short
reply. Raise both for a model with a large context and fast output; lower them
if analyst calls still run long.

## Backends: an API key is optional

An agent calls `llm.structured(...)`. What sits behind that is configuration:

| Backend | How | Auth |
|---|---|---|
| `litellm` | HTTPS to a hosted API | provider credentials |
| `claude-code` | `claude -p --output-format json` | the CLI's own login |
| `opencode-cli` | `opencode run --format json` | OpenCode's provider config |
| `copilot-cli` | `copilot -p --output-format json` | `copilot login` |

The three CLI backends carry **their own** authentication, so if you are signed
into one of those tools the entire pipeline runs on that subscription with no
key plumbed through PRD Forge:

```bash
PRDFORGE_LLM__BACKEND=claude-code
PRDFORGE_LLM__CLAUDE_CODE_MODELS__DEFAULT=haiku
PRDFORGE_LLM__CLAUDE_CODE_MODELS__REASONING=sonnet
```

```bash
prdforge-agent backends          # what is usable here
prdforge-agent backends --check  # actually call each one (spends real quota)
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
PRDFORGE_LLM__FAILOVER_ORDER=claude-code,opencode-cli,litellm
```

The model does *not* travel with it. `opencode-go/glm-5` means something to
OpenCode and nothing to the `claude` CLI, so each backend uses the model
configured for it, or its own default.

**Images pick the backend.** A call carrying images only considers backends
that can transport them. A text-only backend handed an image does not error, it
answers about nothing.

## Configuration

All via env (`PRDFORGE_` prefix, `__` nesting) — see `.env.example`.

**Models** are routed per role so you spend where it matters, and configured
per backend because a model id from one backend is nonsense to another:

```bash
# litellm — any LiteLLM provider: openai/…, azure/…, bedrock/…, vertex_ai/…, ollama/…
PRDFORGE_LLM__REASONING_MODEL=anthropic/claude-opus-4-6      # analyst, writer, critic
PRDFORGE_LLM__DEFAULT_MODEL=anthropic/claude-sonnet-4-5      # extraction
PRDFORGE_LLM__VISION_MODEL=anthropic/claude-sonnet-4-5       # images
PRDFORGE_LLM__FAST_MODEL=anthropic/claude-haiku-4-5

# per CLI backend
PRDFORGE_LLM__CLAUDE_CODE_MODELS__REASONING=sonnet
PRDFORGE_LLM__OPENCODE_MODELS__DEFAULT=opencode/claude-haiku-4-5
PRDFORGE_LLM__COPILOT_MODELS__DEFAULT=auto
```

Anything unset means "let that backend pick its own default". Point
`PRDFORGE_LLM__API_BASE` at a gateway for the litellm path. No code changes.

**Usage** — every call records what the backend reported (tokens, cache hits,
cost) into a per-process ledger. Costs are kept apart by unit: Claude Code
reports what tokens *would* have cost on the API, Copilot reports converted
credits, LiteLLM reports the provider's own figure. Adding those together
produces a number that means nothing.

**Confluence** — unscoped API token → `base_url` is
`https://<site>.atlassian.net/wiki`; scoped token → it must be
`https://api.atlassian.com/ex/confluence/<cloudId>/wiki`. Only the Confluence
agent ever sees the credentials.

**Inter-agent auth** — set `PRDFORGE_SECURITY__ENFORCE=1` and every agent
declares an `apiKey` security scheme on its card and rejects unauthenticated
calls.

## Testing without credentials

`PRDFORGE_LLM__STUB=1` replaces every model call with a deterministic fake
derived from the requested schema. The whole pipeline still runs — real HTTP,
real A2A, real task lifecycle, real rendering — so CI can verify the wiring
without a key. That is what `make demo` and the end-to-end test use.

The backend tests never start a process: each CLI backend funnels through one
subprocess runner, which the tests replace with a stub that records the argv it
was handed and replays canned output. That is where these integrations actually
break — a variadic flag swallowing the prompt, a greedy `-f` eating a
positional, an event stream folded the wrong way — so that is what is asserted.

## Layout

```
src/prdforge/
  models.py            the shared vocabulary (Evidence, Requirement, PRDDocument, …)
  llm.py               structured output, failover across backends, stub mode
  stream.py            live model output: the sink, its wire format, its rate
  backends/            base.py (contract, usage, errors), process.py (subprocess
                       runner, argv limits, neutral cwd), one module per backend
  usage.py             token/cost ledger, kept honest about currency units
  config.py            env-driven settings + per-run LLM overrides
  a2a_common/          card builder, skill-dispatching executor, server, client pool
  ingest/              fetch + document parsers + transcript parsers
  confluence/          REST client + storage-format renderer/reader
  render/              Markdown renderer
  agents/<name>/       agent.py (card + executor) and __main__.py per agent
  ui/                  the web UI: FastAPI (REST + SSE), SQLite run store,
                       .env editor, and static/ — plain ES modules, no build
```

See `ARCHITECTURE.md` for the design rationale and the message flow.
