# SourceWork — architecture

**Scope:** one job. Take heterogeneous requirement material — documents,
meeting transcriptions, images, Confluence pages, loose notes — and turn it
into a Product Requirements Document that a reader can audit line by line. It
does not manage backlogs, plan sprints, or write code. It will put a T-shirt
size on each requirement when a run asks for one, marked throughout as model
inference — a planning hint attached to the document, not a plan.

---

## 1. Why agents at all

A single prompt could produce a PRD-shaped document. It could not produce a
*trustworthy* one, for three reasons that map directly onto the decomposition:

**Different modalities need different machinery.** A PDF needs a page-aware
parser; a VTT needs speaker diarisation and timestamp handling; a mockup needs
a vision model; a Confluence page needs an authenticated REST client and a
storage-format reader. Bundling these into one process means one dependency
set, one scaling profile, one failure domain, and one model choice for jobs
with wildly different cost profiles.

**The jobs have opposing biases.** Extraction must be maximally conservative —
it should never add anything. Analysis must be willing to infer — clustering
and prioritising *is* inference. Review must be adversarial toward both. Asking
one context to hold "never infer" and "infer well" simultaneously degrades
both. Separate agents get separate system prompts, separate models, and can be
evaluated independently.

**Failure should be partial.** Twelve inputs, one corrupt PDF. In a monolith
that is a failed run. Here it is a warning in the stats block and eleven
sources of evidence.

## 2. Why A2A specifically

A2A (v1.0) is the right protocol here because the boundaries are *between
agents*, not between an agent and its tools. MCP would be the right answer for
"give this agent a Confluence tool". This system's boundaries are different:
each component owns a task, runs long, streams progress, and returns artifacts.
That is exactly A2A's task model.

Concretely, A2A buys:

| A2A feature | What it does here |
|---|---|
| Agent cards at `/.well-known/agent-card.json` | The orchestrator hardcodes no capabilities. It resolves cards at run start and dispatches by advertised skill id — an unadvertised skill fails loudly at the caller, before any tokens are spent. |
| Task lifecycle (`submitted → working → completed/failed`) | Ingesting a 200-page PDF takes minutes. The caller gets a task id and streamed status, not a hung HTTP request. |
| SSE streaming of status updates | Progress from a nested agent (`"Analysing 157 evidence item(s)"`) propagates up to the end user through the orchestrator's own task stream — the pipeline passes each `pool.call` an `on_progress` relay, so a nine-minute analyst call reports what it is doing instead of going silent. |
| Artifacts with typed parts | Every result crosses the wire as a JSON DataPart validated against a Pydantic schema on both sides. |
| Security schemes on the card | Intra-mesh auth is declared, not documented. Set `SOURCEWORK_SECURITY__ENFORCE=1` and every agent advertises an `apiKey` scheme and enforces it. |
| Interop | Any A2A client drives the orchestrator. Equally, if a team already runs an A2A "Jira agent" or "design-system agent", it drops into the mesh by URL. |

Transport is JSON-RPC over HTTP with SSE — the SDK's default binding, mounted
on FastAPI so each agent also gets `/docs` and an OpenAPI schema for free.

## 3. The agents

```
                        ┌───────────────────────────┐
     any A2A client ───▶│  PRD Orchestrator  :8000  │  generate_prd, mesh_status
                        └─────────────┬─────────────┘
                                      │
      ┌──────────────┬────────────────┼───────────────┬──────────────┐
      ▼              ▼                ▼               ▼              ▼
 ┌─────────┐  ┌────────────┐  ┌─────────────┐  ┌────────────┐  ┌──────────┐
 │Ingestor │  │  Image     │  │  Meeting    │  │ Confluence │  │          │
 │ :8001   │  │  Analyst   │  │  Analyst    │  │ Connector  │  │          │
 │         │  │  :8002     │  │  :8003      │  │  :8004     │  │          │
 └────┬────┘  └─────┬──────┘  └──────┬──────┘  └─────┬──────┘  │          │
      └─────────────┴────────────────┴───────────────┘         │          │
                            evidence                            │          │
                               │                                │          │
                               ▼                                │          │
                    ┌──────────────────────┐                    │          │
                    │ Requirements Analyst │ :8005              │          │
                    └──────────┬───────────┘                    │          │
                               ▼                                │          │
                    ┌──────────────────────┐   review   ┌───────┴──────┐   │
                    │     PRD Writer :8006 │◀──────────▶│ Critic :8007 │   │
                    └──────────┬───────────┘            └──────────────┘   │
                               │                                           │
                               └────────── publish ────────────────────────┘
```

| Agent | Model role | Owns |
|---|---|---|
| Orchestrator | none | Routing, sequencing, fan-out, review loop, partial-failure policy |
| Document Ingestor | `default` | PDF/DOCX/PPTX/XLSX/CSV/HTML/MD/TXT parsing → evidence |
| Image Analyst | `vision` | Mockups, screenshots, whiteboards, diagrams → evidence |
| Meeting Analyst | `default` / `reasoning` | Transcript parsing, speaker attribution, decision log |
| Confluence Connector | `default` | The **only** holder of Atlassian credentials. CQL search, page + attachment read, idempotent publish |
| Requirements Analyst | `reasoning` | Clustering, de-duplication, MoSCoW, conflicts, open questions, glossary |
| PRD Writer | `reasoning` | Narrative, user stories, metrics, risks, milestones; Markdown + storage-format rendering |
| PRD Critic | `reasoning` | Deterministic traceability checks, then adversarial review |

## 4. The data model is the architecture

Everything crossing a boundary is one of a small set of Pydantic models
(`sourcework/models.py`). The important ones:

```
SourceDocument ─┬─▶ Evidence ──▶ SourceRef ──▶ Requirement ──▶ RequirementSet ──▶ PRDDocument
                │   (id, text,    (evidence_id,  (REQ-001,        (+ conflicts,      (+ narrative,
                │    locator,      source_id,     statement,       open questions,     sources,
                │    speaker,      locator,       acceptance,      glossary)           evidence)
                │    kind,         quote)         priority,
                │    confidence)                  derived)
```

### Evidence is the only admissible input

An `Evidence` record is an atomic, quotable claim with a **locator** pointing
back at where it came from. The locator is a free string because it means
something different per modality — `p.12`, `00:14:32`, `slide 4`,
`region: top-left panel`, `heading: Scope` — and it is rendered verbatim to the
reader, so a claim can always be checked at its source.

Extraction agents may only *report*. They are prompted to never infer, never
extrapolate, never fill gaps with domain knowledge. Anything ambiguous keeps a
lowered `confidence` and produces a warning rather than being resolved.

### Citations are validated in code, not trusted

The requirements analyst returns `evidence_ids` per requirement. Before those
become `SourceRef`s, each id is checked against the real evidence set:

```python
for ev_id in item.evidence_ids:
    evidence = by_id.get(ev_id)
    if evidence is None:
        logger.warning("dropping invented citation %r on %s", ev_id, req_id)
        continue
    refs.append(SourceRef(...))
```

A requirement left with zero valid refs is forced to `derived=True` and its
confidence capped at 0.5. `derived` renders as a visible badge in both Markdown
and Confluence. **A model cannot launder an invention into a sourced fact.**

### The writer cannot touch requirements

The writer receives the requirement set and returns *only* narrative plus
structures that reference requirements by id. The requirements table is
rendered from the analyst's structured data, not from the writer's prose.
Requirement ids the writer references are filtered against the real set before
assembly. This is what stops "improving the flow" from quietly rewording a
`must` into a `should`.

## 5. Pipeline

```
discover → route → ingest (concurrent) → analyse → write → review → revise → publish
```

**Discovery.** The orchestrator resolves every peer's agent card and builds
`{agent: [skill ids]}`. If `requirements` or `writer` is missing, the run aborts
immediately rather than half-way through. Optional agents degrade: no
`confluence` means CQL queries are skipped with a warning and publish is
skipped; no `critic` means the review loop is bypassed.

**Routing** is by modality, resolved in this order: explicit `modality` on the
input → `confluence://` scheme → `inline:` with text → file extension /
media type. The route table is data:

```python
ROUTES = {
    Modality.DOCUMENT:    ("ingestion",  "extract_document"),
    Modality.IMAGE:       ("vision",     "analyse_image"),
    Modality.TRANSCRIPT:  ("transcript", "extract_transcript"),
    Modality.CONFLUENCE:  ("confluence", "fetch_page"),
    ...
}
```

Adding a modality is a table entry plus an agent. Nothing else changes.

**Ingestion is the only concurrent stage** (semaphore-bounded at 6). The inputs
are independent and this is the slow, token-heavy phase. Everything after it is
a barrier by necessity — you cannot normalise requirements from half the
evidence, and de-duplication is inherently global.

**Partial failure is the default.** A failed input is recorded in
`stats.failures` and the run continues. The run only aborts if *every* input
failed, or if the surviving evidence is empty.

**Review loop.** After the first draft, the critic runs. If it returns
blocking or major findings, they are folded into `revision_notes` and the
writer re-drafts with them in its system prompt. `review_rounds` bounds the
loop; it exits early when nothing blocking remains.

### 5.1 The analyst slices large evidence sets

The analyst is the one call whose **prompt and answer both** scale with the
size of the input, which makes it the first thing to fail on a real corpus.
Measured on the demo pack plus a synthetic expansion: 223 evidence items → a
129k-character prompt and a 33k-token answer, nearly ten minutes on a CLI
backend, and it still stopped at the model's output ceiling. Reproduced on the
bare CLI with no SourceWork involved, so it is not a transport problem and no
backend is exempt.

So above a threshold the analyst maps over slices of the evidence and reduces
the results:

* **Slicing splits on source boundaries** wherever it can. Evidence from one
  document read together produces one coherent requirement; the same evidence
  split across two calls produces two halves that the merge then has to guess
  were the same thing.
* **Two limits, because two different things blow up.** Characters bound the
  prompt. Item count bounds the *answer* — and that is the one that actually
  failed: 176 items rendered to 45k characters, comfortably under any prompt
  limit, while the requirement set covering them hit the output ceiling. A
  short prompt is no guarantee of a short reply.
* **Each slice is told it is a slice**, and instructed not to raise open
  questions about material it cannot see, nor conflicts against it.
* **The merge pass sees requirements, not evidence.** That is what keeps it
  bounded: the evidence was the large thing, and the requirements derived from
  it are an order of magnitude smaller, so the reduce step does not grow with
  the size of the input material.
* **The merge emits decisions, not content** — which keys are duplicates, what
  conflicts, which questions survive. Re-emitting every requirement would make
  its output as large as its input, which is the failure being avoided. The
  merging itself happens in code, because the thing being merged is citations:
  a merged requirement inherits the evidence ids of *every* draft folded into
  it, and asking a model to transcribe them back is asking it to drop some.

Cross-document conflict detection survives this, which was the design
constraint: conflicts live in the requirement *statements* ("refund within 14
days" against "refund within 30 days"), and the merge pass is the first thing
to see all of them at once. A slice, seeing only its own material, is told not
to guess.

A set under both thresholds takes the original single call, unchanged.

## 6. The critic is half deterministic

Model-based review cannot be trusted to count. So the checks that must never be
wrong run in Python first:

- requirement with no `source_refs`
- `source_ref` pointing at a non-existent evidence id
- `must`/`should` requirement with no acceptance criteria
- unquantified wording (`fast`, `intuitive`, `scalable`, `as needed`, …) matched
  by regex
- user story or milestone referencing a `REQ-` id that does not exist
- empty summary / problem statement / goals
- unresolved conflicts, or blocking open questions → automatic `blocker`

Alongside those, a rule pack in `quality.py` checks how each requirement is
*written*, against published standards rather than taste — the ISO/IEC/IEEE
29148 characteristics and the INCOSE writing rules: escape clauses, open-ended
lists, absolutes, two obligations under one id, a priority that disagrees with
its own modal verb, nothing measurable anywhere, terms used but never defined.
EARS conformance joins them when `SOURCEWORK_QUALITY__EARS=1`, which also
changes what the analyst is asked to write — checking a shape nobody was asked
for would be a rule pack picking a fight with its own pipeline. These findings
are `minor` at worst: they exist to be fixed in the revision loop, not to block
a document over a semicolon. The basis is recorded on the report
(`standards`) and rendered with the review, because a quality claim without its
yardstick is just an adjective.

Only then does the model run, with the deterministic findings supplied as
"already recorded, do not repeat", scoped to the judgement calls: unsupported
narrative claims, contradictions, ambiguity, missing considerations, scope
creep. The final verdict is computed, not asked for — any blocker or major
forces `needs_revision` regardless of what the model said.

Coverage statistics (`cited_requirements`, `derived_share`, `evidence_used`,
`quality_clean`) ride along in the report, so a degrading pipeline is visible as
a number rather than a vibe. Wording and citation are scored separately: they
fail for different reasons and are fixed in different places, and one number
covering both would read as each and mean neither.

The writer runs before any of this, so the artifacts it returns carry no review
section. After the last round the orchestrator re-renders both — the renderers
are pure functions of (prd, review) — so the document that ships carries its own
verdict.

## 7. Confluence

The connector is the only component with Atlassian credentials — one container
to rotate, one place where the read/write boundary is auditable.

**Read:** CQL search (still v1 — there is no v2 equivalent), page fetch in
storage format, and non-image attachments downloaded and parsed inline (images
are skipped, since they belong to the vision agent). The storage-format reader
strips `ac:`/`ri:` markup down to `(heading, text)` blocks so headings become
locators.

**Write:** the PRD is rendered to storage-format XHTML — TOC macro, status
lozenges for MoSCoW priority, info/warning panels, expand macro for the
traceability matrix, tables for requirements and sources. Publishing is
`upsert`: titles are unique per space in Confluence, so a plain create would
400 on the second run of the same PRD. Regenerating is the normal case.

API details that are easy to get wrong, and are handled explicitly:

- request body is flat `{representation, value}`; response body is keyed by
  format (`body.storage.value`) — asymmetric
- `PUT` requires `version.number == current + 1` **and** a `title`, always
- attachment downloads 302 to a signed media host that rejects the Atlassian
  `Authorization` header — the redirect is followed manually with the header
  stripped
- scoped API tokens only work against `api.atlassian.com/ex/confluence/<cloudId>`;
  unscoped ones only against `<site>.atlassian.net`
- 429 handling honours `Retry-After` with exponential backoff and jitter
- v2 pagination is cursor-based; `_links.next` is relative to the *origin*, not
  to `_links.base`

## 8. Model layer

Every model call goes through `LLM` (`sourcework/llm.py`). Models are selected
per **role**, not per agent:

```
reasoning → analyst, writer, critic      (the expensive thinking)
default   → document/transcript/wiki extraction
vision    → images
fast      → cheap mechanical work
```

`structured()` returns a validated Pydantic object: it sends the JSON Schema,
extracts JSON from whatever the model wraps it in, and on a validation failure
feeds the error back and retries. Agents never handle raw strings.

**Stub mode** (`SOURCEWORK_LLM__STUB=1`) replaces every call with a deterministic
fake derived from the requested schema. The whole pipeline still runs — real
HTTP, real A2A, real task lifecycle, real rendering — so CI verifies the wiring
with no credentials. That is what the end-to-end test and `make demo` use.

### 8.1 Backends

`LLM` does not know how a model is reached. It hands a **backend** a system
prompt, a user prompt and possibly some images, and gets back text plus
whatever usage the provider was willing to report. Two families implement that
contract (`sourcework/backends/`):

| id | transport | auth |
|---|---|---|
| `litellm` | HTTPS to a hosted API | provider credentials |
| `claude-code` | `claude -p --output-format json` | the CLI's stored login |
| `opencode-cli` | `opencode run --format json` | OpenCode's provider config |
| `copilot-cli` | `copilot -p --output-format json` | `copilot login` |
| `codex-cli` | `codex exec --json` | `codex login` |
| `agy-cli` | `agy --print --output-format json` | its own sign-in |

The CLI family exists for one reason: **the credential problem disappears.**
These tools are already installed and already authenticated on a developer's
machine, and they bill against a subscription rather than a card. Pointing PRD
Forge at one means the whole eight-agent pipeline runs with no API key anywhere
in its configuration.

They are used as *generation* backends, not as agents. That distinction is the
bulk of the implementation:

- **No tools.** `--tools ""` on claude-code, a tool-less agent definition on
  opencode, `--available-tools=` on copilot. There is nothing to edit and
  nothing to run, but left alone a coding CLI works the question agentically
  and glues its inter-tool narration onto the answer — which the caller is
  about to parse as JSON. On OpenCode the tool-less agent also cut the prompt
  overhead from ~10,000 tokens per call to ~1,200.
- **No MCP servers.** `--strict-mcp-config` on claude-code. Without it the CLI
  loads whatever the developer has configured globally; an image-transcription
  call came back correct and then appended a paragraph about authorising Gmail
  and Drive connectors. Removing them also cut that call's reported cost 3×.
- **A neutral working directory.** These are coding agents: pointed at a
  repository they read its instruction files, honour its permission
  allowlists, and can write into it. Generation calls run in an empty scratch
  directory.
- **argv is not unlimited.** Linux caps a single argument at 128 KB; past it
  `execve` fails before the program starts. Oversized prompts travel on stdin
  for claude-code and opencode. Copilot's `-p` takes the prompt inline and the
  CLI does not read stdin, so that case is raised as a backend error — which
  the failover chain then routes around.

### 8.2 Failover

`SOURCEWORK_LLM__FAILOVER_ORDER` is an ordered list of backends to try when the
active one fails. A usage limit is the case it exists for: the prompt was fine,
the account was not, and another backend answers it. Quota text is recognised
across three vocabularies (`usage limit`, `insufficient balance`, `not enough
credits`) because each CLI words the same condition differently.

Two rules keep it from causing more damage than it prevents:

- **A model id does not travel.** Each backend uses the model configured for
  it, or its own default. Carrying the running model onto a failover target is
  how the backend meant to rescue a call fails with "unknown model".
- **Output truncation is not failed over.** A response cut at the model's
  output ceiling is a budget problem; another backend with the same budget
  repeats it. It is also never handed to the JSON parser — the largest
  recoverable object inside a truncated response is a *fragment of a different
  shape*, which then fails schema validation with a message describing a
  symptom three layers from the cause.

Images constrain the chain: a call carrying images only considers backends that
can transport them. A text-only backend handed an image does not error, it
answers about nothing.

### 8.3 Per-run overrides

The mesh is eight processes that read their configuration once, at start-up. So
"try this run on a different backend" would mean editing `.env` and restarting
everything — a two-minute round trip for a one-word change, and a setting that
no longer says anything about the run that used it.

Instead, a `PRDRequest` can carry an `llm` block: backend, per-role models,
failover order, effort. It travels as an envelope key that no skill schema
declares. Three small pieces make that work end to end:

- `AgentPool` attaches its overrides to every payload it sends, so the
  orchestrator's pool covers all seven downstream agents without a single
  `pool.call` site knowing about it.
- `SkillExecutor` reads the key and installs it in a context variable for the
  duration of the handler. It **reads** rather than pops — an early version
  popped it, which left `PRDRequest.llm` empty, so a run that asked for
  claude-code came back quietly built by whatever the mesh booted with.
- `LLM.cfg` resolves lazily. Agents build their `LLM` when the executor is
  constructed, long before a request exists; freezing the settings there would
  make the whole mechanism invisible to every agent in the mesh.

None of the seven specialist agents contains a line of code about this.

An explicit backend also turns stub mode **off**. Otherwise a mesh started with
`SOURCEWORK_LLM__STUB=1` would return a convincing fake to someone who explicitly
asked for a real model.

### 8.4 Usage

Backends report what they can, into a per-process ledger (`sourcework/usage.py`).
Costs are summed **only within a unit**, because the four backends do not
denominate them the same way: LiteLLM reports the provider's dollars, Claude
Code reports what the tokens would have cost on the API (under a subscription,
nobody's actual bill), Copilot reports AI credits converted at the published
$0.01 rate. Adding those together produces a number that means nothing and
looks alarming.

Per-process is not per-run, though: the orchestrator makes no model calls at
all, so its own ledger would always read zero. Each agent therefore returns
what it spent as an extra data part alongside its result, `AgentPool` folds
those into a running total, and the pipeline reports it in `stats.usage`. The
side-channel part means no response schema has to grow a field about billing.

### 4.1 Versions, not edits

A PRD gets refined: an open question is answered, a follow-up meeting happens,
someone adds a requirement. That is a **new run with the old one as its
baseline**, never a mutation of the existing document — same argument as
evidence, a document you cannot trace is a document you cannot trust.

`PRDRequest.baseline` carries the prior run's sources, evidence and requirement
set. The pipeline seeds the run with that evidence *without re-ingesting* it,
ingests only what is new, and hands the analyst the previous requirement set,
which switches its prompt from "produce a requirement set" to "produce the next
version of this one".

Three properties are enforced in code rather than asked for in the prompt,
because each one fails silently:

| Property | What breaks without it |
|---|---|
| Ids survive a refinement | Ids are positional on a first run. Inserting one requirement renumbers everything after it, and every citation in the PRD a reader has — and every ticket quoting a `REQ-` id — silently points at a different requirement. |
| Retired ids are never reused | New requirements allocate above the highest id ever issued. Reusing a dropped number is the same failure with an extra step. |
| Untouched requirements inherit their citations | The analyst re-cites what the new material justifies and lets the rest go. Left alone, every requirement it did not touch loses its refs and is forced to `derived` — the PRD starts telling the reader that facts it sourced last week were inferred. |

An answer to an open question enters as an ordinary inline source, phrased as a
self-contained statement (the extractor sees the text alone, so "yes, in scope"
without its question is evidence of nothing). It becomes evidence, and the
requirement it settles cites it.

### 4.2 Resuming, not restarting

A run is minutes of model calls arranged as a chain, and every artifact along
it used to live only in a local variable. A timeout in the last call of the
analysis phase unwound the stack and discarded 180 evidence items and 82
requirements — work that had already succeeded and had already been paid for.

Each stage now writes what it produced to `workspace/checkpoints/<run id>.json`,
and a resume reads it back instead of making the call again. Stages are
`ingest`, `analyse`, and `write:N` / `review:N` per revision round.

This is the opposite of a refinement: same run, same id, continued. A
refinement is a *different document* and earns its own id; an interrupted run
is one piece of work, and giving it a second id would put two rows in the
history for it.

Three rules, each because the obvious alternative is wrong:

| Rule | What breaks without it |
|---|---|
| Always write, only read when asked | Cancelling is a decision — most often "I picked the wrong model". A resume that happened automatically would rebuild the PRD from output the user had just rejected. Writing costs nothing and keeps the option open. |
| Every stage carries a fingerprint of what produced it | Change the backend, or edit a source document, and the stored evidence no longer corresponds to what a fresh run would produce. Without a fingerprint the result is a PRD that is half one configuration and half another, with nothing on its face to say so. A stale stage is recomputed. |
| Artifacts are stored, never recipes | Evidence ids are minted randomly, so re-extracting a document yields the same claims under new ids and breaks every citation in a PRD already written against them. The stored bytes *are* the state. |

A local file contributes its size and mtime to the fingerprint, so editing a
source between attempts discards the evidence taken from the old contents.
Contents are not hashed — these are documents, some large, and the fingerprint
is computed at every stage boundary.

Below `analyse` the analyst saves each **evidence slice** as it finishes, into
its own file under a *scope* (`<run id>.analyst.json`). This is the phase that
cannot be helped by a stage boundary, because it lives inside one: five slices
at a minute or more each, and an interruption used to cost all of them.

A slice is keyed by the prompt it answered, not by its position. Change the
batch size and the boundaries move — keying by index would hand slice 3 the
answer to a question that used to be slice 3 and is now half of slice 2. Keyed
by content, a reshuffle simply misses and recomputes.

Two processes writing one file would mean both doing read-modify-write on it,
safe today only because of a call-ordering invariant that lives in the *other*
process. Separate files per scope make that structural. `discard` removes every
scope, so a finished run does not leave the analyst's slices behind for the
whole retention period.

The ingest stage stores the routing map alongside the evidence, so a resumed
run still reports which agent handled which input; without it `stats.routed`
would come back empty and describe work that did happen as work that did not.

### 4.3 Cancelling

A cancel used to be a relabelling. `SkillExecutor.cancel` marked the A2A task
cancelled and never touched the coroutine doing the work, and no client ever
called it anyway — the UI's Cancel button cancelled its own local task. So a run
reported "cancelled" the instant the button was pressed and went on spending
tokens for another ten minutes.

Stopping it has to happen at three levels, because each one is a different
process and stopping one does nothing to the next:

| Level | What it does |
|---|---|
| `SkillExecutor` | Holds the asyncio task running each handler, so `cancel` stops the work rather than renaming it. |
| `AgentPool.call` | On `CancelledError`, sends a cancel for the remote task it was listening to. This is what makes one cancel travel the whole mesh — the pipeline is nested calls, and each agent is itself a caller whose await is now being cancelled. |
| `process.run` | Kills the child on cancellation. This is where the money is: the coroutine unwinding costs nothing, and the CLI answering a model prompt would otherwise run to completion and bill for every token of an answer nobody is waiting for. |

The child is killed by **process group**, not by pid: every backend here is a CLI
that shells out further, and killing only the direct child leaves the
grandchildren holding the work. `start_new_session=True` at spawn is what makes
the group addressable without signalling our own session.

The CLI treats **SIGTERM** like Ctrl-C. SIGINT already arrives as a cancellation
because `asyncio.run` converts it; SIGTERM ends the process outright, so nothing
unwinds and nothing tells the mesh to stop — and SIGTERM is what a process
manager sends, which makes it the likelier of the two to end a long run.

A cancelled run keeps its checkpoints, so cancelling and resuming is a normal
thing to do rather than a way to lose ten minutes.

**A client that vanishes without cancelling still leaves a run going.** A
closed browser, a killed container, a UI restarted mid-run: nothing unwinds, so
nothing cancels, and the run carries on here to completion — and resuming it would put two
pipelines on one checkpoint file and one piece of work.

The orchestrator therefore refuses a second run of an id it is already
executing, and reports its live ids through `mesh_status` so a client can find
out before asking. The refusal is the race-free backstop; the report is what
lets the answer arrive without a wasted round trip. The set is held in memory on
purpose: a lock file would have to answer "is the process that wrote this still
alive", and would strand a run behind a stale lock after a crash — exactly when
resuming is most wanted.

Anything reused is named in `stats.warnings`, because a reader is entitled to
know which parts of the document in front of them were produced by the run they
are looking at. Checkpoints are deleted once a run has a result: there is
nothing left to resume, and refining it is what a baseline is for.

## 9. The web UI

A ninth service (`sourcework/ui/`), and deliberately **not** mounted on the
orchestrator: the orchestrator is an A2A agent — one protocol, one contract,
drivable by anything that speaks it — and bolting a browser app onto it would
make it two things. The UI is just another A2A client, like the CLI.

What it adds is what a browser needs and a protocol does not:

- **Persistence.** The A2A task store is in-memory, which is right for an agent
  and useless for a UI. Runs go in SQLite: request, result, every progress line,
  and the cost.
- **Live progress.** `AgentPool.call` gained an `on_progress` callback, so the
  status messages the agents already emit reach the browser over SSE instead of
  being logged at debug and dropped. A late subscriber replays the stored
  events, then streams; sequence numbers keep the join seamless.
- **Narration.** Progress says which stage is running; narration says what the
  model is doing inside it. It rides the *same* status-update channel — which
  already reaches every caller in the mesh — behind a non-printable marker, so
  a reader that does not know about it sees an unrecognised progress line
  rather than a corrupted one. Three properties make it safe:

  *Opt-in.* An envelope key (`stream`) turns it on, exactly like `llm`. The
  backends charge for it — `claude-code` needs a different output format,
  `opencode-cli` needs `--thinking` — and nobody watches the mesh's internal
  calls.

  *Rate-controlled.* Chunks arrive per token, synchronously, from the loop
  draining the CLI's stdout pipe; each hop outward is an async status update
  over JSON-RPC. `stream.Narrator` sits between them, coalescing by kind and
  publishing on a timer. The sink itself can never raise: an exception there
  would stop the drain and deadlock the very call it was narrating.

  *Ephemeral.* It is published and never stored. Persisting a token stream
  would rewrite the whole run record to SQLite thousands of times per run, for
  something no requirement cites. Narration events carry `seq: null`, which is
  what keeps them out of the replay bookkeeping that makes a late subscriber
  seamless.

  What is actually visible is the provider's decision, not ours. OpenCode
  returns readable reasoning, and so does Copilot on OpenAI models once
  `--enable-reasoning-summaries` is passed. Claude Code does not and currently
  cannot: its `thinking_delta` events arrive with `thinking: ""` and the content
  present only as an encrypted signature, so what goes to the panel is the
  `estimated_tokens` count as a `step` — a status that supersedes the last one
  rather than accumulating. Reporting it as `reasoning` would put words the
  model never said under a heading bearing its name.

- **Uploads.** Files are written into the shared workspace and referenced as
  `file://` URIs — the path the agents already have. Inlining bytes as base64
  would avoid the shared volume but put a 20 MB PDF through JSON.
- **Settings.** A form over `.env`, behind an explicit allow-list, because an
  endpoint that writes any key is an environment-injection endpoint and the
  environment is where the API keys live. Secrets go out masked and a masked
  value coming back means "unchanged".
- **Sign-off.** A finished run can be approved or rejected. Recorded, not
  enforced: single-operator software, so the name is what the operator typed
  and the point is the trail, not a gate. The history is append-only — a
  rejected-then-approved run shows both — and the decision becomes the
  document's `status`, which is why approving re-renders the artifacts (with
  the stored review, or signing a document would quietly delete its review
  section).
- **The audit bundle.** The store already holds everything an auditor asks
  about, but as a SQLite row on one machine. `audit.py` packs a run into one
  zip — request, result, evidence, sources, events, and a manifest naming the
  backend, models, version and standards basis — with a SHA-256 per member and
  one over the set. Tamper-evidence, not tamper-proofing: the bundle makes the
  claim, the digests let anyone check it.
- **Retention.** `SOURCEWORK_RUNS__RETENTION_DAYS` deletes finished runs past a
  limit at start-up, checkpoints included — both hold the full source text, and
  erasing one while keeping the other would make the setting a half-truth. Runs
  in flight are never touched, and a purge is logged rather than silent. The
  capability is not on the `Store` protocol: a custom store may have no
  clock-based notion of "old", so an absent one is announced, not skipped.

The front end is plain ES modules with no build step: `make install` stays the
only setup, the Docker image gains nothing, and there is no lockfile to keep
patched. Markdown is rendered by a ~100-line parser covering exactly the
constructs the writer emits, rather than vendoring 50KB for constructs the
document never uses.

There is exactly one vendored dependency, and it is worth saying why it did not
get the same treatment. `js/vendor/autocomplete.js` (autocompleter 10.0.0, MIT,
1KB gzipped, no dependencies of its own) replaced `<datalist>` for model ids.
The datalist was the right first move — one attribute, no code — and the wrong
final one: the browser renders it as an unstyleable system menu that reads as a
different application sitting on top of the form. Hand-writing the replacement
is where the markdown analogy breaks down, because a combobox is not markup, it
is keyboard semantics: arrow keys with wrap-around, scroll-into-view,
blur-before-click, Escape, IME composition. Each is a small bug that only
appears on someone else's keyboard. Vendored rather than CDN-loaded so the UI
still works with no internet, and committed verbatim with its licence header,
because with no lockfile the file itself is the only record of what it is.

## 10. Deployment

Eight containers on one compose network, addressing each other by service name
(`SOURCEWORK_PUBLIC_HOST` is what an agent advertises in its own card, so it must
match how peers reach it). Only the orchestrator publishes a port. Each agent
has a `/healthz` probe.

Scaling follows the actual load shape: ingestion and vision are the hot path
and scale horizontally behind a service name; the analyst and writer are called
once per run.

State is currently in-memory (`InMemoryTaskStore`). For production, the SDK
ships `DatabaseTaskStore` — swap it in `build_app` and tasks survive restarts,
which also unlocks A2A push notifications for runs longer than a client is
willing to hold a connection open.

## 11. Known limits

- **No OCR.** A scanned PDF yields no text and produces a warning. Wiring
  Tesseract or a vision fallback into the ingestor is the obvious next step.
- **No audio.** Transcripts must already be transcribed. A Whisper front-end
  would slot in as a skill on the meeting analyst.
- **De-duplication is single-shot.** The analyst sees all evidence in one
  context. Past roughly 200k tokens of evidence it needs a map-reduce clustering
  stage.
- **In-memory task store.** Restarting an agent loses in-flight tasks.
- **The critic has no ground truth.** It can detect *unsupported*, not *wrong*.
  A confidently mis-transcribed number survives if it is cited consistently.
- **English-centric prompts.** The extraction prompts assume English normative
  language (`must`/`shall`/`should`). Other languages need per-locale hints.
- **CLI backends are slow.** Every call starts a process that loads its own
  config before it says anything. The same demo run takes ~14 minutes on
  `claude-code` against a couple of minutes on an API. Fine for a PRD, wrong
  for anything interactive.
- **The usage ledger is per-process.** Agents are separate services, so an
  orchestrator's ledger totals its own calls, not the mesh's. Making it add up
  across the mesh means putting usage on every A2A response payload.
- **CLI flag sets are version-coupled.** `--tools`, `--strict-mcp-config`,
  `--available-tools` and `--variant` are current-version flags on tools that
  move fast. `sourcework backends --check` is the canary.
