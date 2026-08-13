# SourceWork

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

That single constraint is what makes the output auditable and gives you a real
traceability matrix at the bottom of every PRD.

Be precise about what it guarantees, because the difference matters when you
are the one signing off the document. Enforced in code: every citation resolves
to evidence that really was extracted, an invented id is dropped, and a
requirement left uncited renders as `derived`. **Not** enforced: that the cited
evidence actually *supports* the claim. A model may cite a real id for a
statement it does not justify, and no mechanical check will catch that - the
traceability matrix exists so a human can, in one pass, with the quote next to
the requirement.

## Quick start

Run it as a desktop app — the whole mesh plus the UI in one process:

```bash
sourcework app               # starts everything, opens your browser
sourcework doctor            # what is configured, and what is actually reachable
```

No tray icon and no extra to install: a finished run raises a browser
notification, and Quit is in the interface.

On Linux, put it in the application launcher:

```bash
sourcework install-desktop-entry     # --remove to undo
```

```bash
make install                 # venv + editable install
cp .env.example .env         # fill in model + Confluence credentials

make demo                    # full pipeline, no API key needed (stub LLM)
make test                    # unit + end-to-end over real A2A
```

Run the mesh for real:

```bash
sourcework serve-all     # all eight agents, one process (dev)
sourcework ui            # the web UI, on http://localhost:8080
sourcework status        # which agents are up, and their skills
sourcework backends      # which LLM backends this machine can use
```

Or from the command line:

```bash
sourcework generate "Invoice reconciliation" \
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
from sourcework.a2a_common import AgentPool
from sourcework.models import InputRef, PRDRequest, PRDResult

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

`sourcework ui` serves a browser front end on :8080. It is an A2A client,
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

## Resuming an interrupted run

A run that dies — a timeout, a cancel, a restart of the app — keeps whatever it
had already finished. Open it and press **Resume**: it picks up from the last
completed stage instead of re-reading every document and re-analysing every
piece of evidence.

Resuming is never automatic, because cancelling a run usually means the
configuration was wrong, and quietly reusing what that configuration produced
would hand back the document you had just rejected. Any stage whose inputs have
changed since — a different backend, an edited source file — is recomputed
regardless, and whatever *was* reused is recorded in the run's stats.

A finished run has nothing to resume. That one wants **Refine**.

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
SOURCEWORK_LLM__ANALYSIS_BATCH_ITEMS=70    # evidence items per slice (0 = off)
SOURCEWORK_LLM__ANALYSIS_BATCH_CHARS=60000 # rendered characters per slice (0 = off)
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

## Running it on your own hardware

Every model can be local. The `litellm` backend speaks to any OpenAI-compatible
server, so [llama.cpp](https://github.com/ggml-org/llama.cpp)'s `llama-server`
is a configuration change, not a code change:

```bash
SOURCEWORK_LLM__BACKEND=litellm
SOURCEWORK_LLM__API_BASE=http://127.0.0.1:8081/v1
SOURCEWORK_LLM__API_KEY=local
SOURCEWORK_LLM__DEFAULT_MODEL=openai/<model-id>   # `openai/` is what points
SOURCEWORK_LLM__REASONING_MODEL=openai/<model-id> # LiteLLM at API_BASE
SOURCEWORK_LLM__TIMEOUT_S=1200                    # minutes per call, not seconds
```

Four things decide whether this works at all, and all four are the difference
between a clean run and a mystifying one:

**Enforce the schema, do not describe it.** `SOURCEWORK_LLM__CONSTRAINED_JSON=1`
(the default) sends the JSON Schema as `response_format`, so a server that
grammar-constrains decoding — llama.cpp, vLLM, Ollama — makes malformed JSON
*impossible* rather than unlikely. On a measured 15-call run this was the
difference between zero retries and a small model spending its whole retry
budget re-answering calls that had already been paid for.

**Give it a real context window.** The analyst's prompt *and its answer* both
grow with the evidence count. A server started at a 4k window silently truncates
and returns something that parses fine and is wrong. Serve at 32k and lower the
slice limits to match:

```bash
SOURCEWORK_LLM__ANALYSIS_BATCH_CHARS=24000
SOURCEWORK_LLM__ANALYSIS_BATCH_ITEMS=30
```

**Stop hybrid models from thinking away their output budget.** A reasoning model
at default effort will spend all 8k tokens in its scratchpad and return *empty
content*, which the pipeline can only report as "the backend said nothing".
`-rea off` on llama-server fixes it for most; harmony-format models (gpt-oss)
ignore that flag and need `--chat-template-kwargs '{"reasoning_effort":"low"}'`
instead.

**Let the model fit.** `--fit` only adjusts arguments you did *not* set, so
pinning `-ngl 99` on a model larger than your VRAM turns a slow run into an
out-of-memory crash.

### Several models, one endpoint

`llama-server` serves one model per process, so per-role models need
[llama-swap](https://github.com/mostlygeek/llama-swap) in front. The scripts
handle the wiring:

```bash
export SOURCEWORK_MODEL_DIRS=~/models:/srv/models   # where your GGUFs live
scripts/llama-models.py list                      # what you have, and what fits
scripts/llama-models.py scan                      # generate the serving config
cp scripts/llama-swap.example.yaml scripts/llama-swap.yaml   # then edit its paths
scripts/llama-swap.sh                             # serve them all on :8081
```

`scan` pairs vision projectors with their models, collapses split models to one
entry, and asks for full GPU offload only where it demonstrably fits. Adding a
model is dropping a file into a scanned directory and rescanning — llama-swap
reloads by itself. To fetch one you do not have yet:

```bash
scripts/llama-models.py add unsloth/gemma-3-27b-it-GGUF:Q4_K_M
```

It downloads on first use (llama.cpp resumes and caches; set `HF_TOKEN` for a
gated repo). Anything needing flags the scan cannot infer goes in
`llama-swap.yaml`, which the generator then leaves alone.

### A critic from another family

`SOURCEWORK_LLM__CRITIC_MODEL` exists so the adversarial pass can be a *different
model* from the one that wrote the PRD. A critic trained alongside the writer
finds the same phrasing natural and tends to confirm rather than challenge.
Pointing it at another lineage is the cheapest way to make the review real —
on the demo pack, a Qwen critic reviewing a Gemma-written PRD caught the writer
turning a wireframe's sample data (an order number, product names, a `-15 min`
countdown) into requirements. Left unset it follows the reasoning model, so
nothing changes until you ask for it.

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

## Testing without credentials

`SOURCEWORK_LLM__STUB=1` replaces every model call with a deterministic fake
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
src/sourcework/
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

## Security

The UI has **no authentication** and its settings endpoint rewrites `.env`,
provider keys included; the runs endpoint returns the full text of everything
you have ingested. It binds `127.0.0.1` for that reason — bind wider only behind
a proxy that authenticates. The agent mesh ships with a shared default secret
and `ENFORCE=0`, which is fine on a laptop and wrong on a shared network. See
[SECURITY.md](SECURITY.md).

## Design notes

Decisions that shaped the project, kept because the reasoning outlives the
decision:

- [docs/desktop.md](docs/desktop.md) — running as a local desktop app: the tray
  launcher, engine detection, and why not Electron.
- [docs/multi-tenant.md](docs/multi-tenant.md) — what a hosted multi-tenant
  service would take. Considered and not taken; kept because "why isn't this a
  SaaS?" deserves an answer with a schema attached.

## Licence

[MIT](LICENSE). Third-party components and the tools the local-model path drives
are listed in [THIRD_PARTY.md](THIRD_PARTY.md); note that **model weights carry
their own licences**, which this one does not cover.

Contributions welcome — see [CONTRIBUTING.md](CONTRIBUTING.md).
