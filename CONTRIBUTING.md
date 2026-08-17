# Contributing

Thanks for looking. This is a small project with opinions; the fastest way to
get a change merged is to understand which ones.

## Getting set up

```bash
make install      # venv + editable install with the ingest and dev extras
make test         # the suite: no API key and no network needed
make lint         # ruff
```

Everything runs against a deterministic fake model (`SOURCEWORK_LLM__STUB=1`, set
by `tests/conftest.py`), so the suite is fast and offline. `python scripts/demo.py`
drives all eight agents over real A2A JSON-RPC against that same fake, which is
the cheapest way to catch a wiring mistake.

To run against real models, see [docs/local-models.md](docs/local-models.md).

`SOURCEWORK_LLM__STUB=1` replaces every model call with a deterministic fake
derived from the requested schema. The whole pipeline still runs — real HTTP,
real A2A, real task lifecycle, real rendering — so CI verifies the wiring
without a key.

The backend tests never start a process: each CLI backend funnels through one
subprocess runner, which the tests replace with a stub that records the argv it
was handed and replays canned output. That is where these integrations actually
break — a variadic flag swallowing the prompt, a greedy `-f` eating a
positional, an event stream folded the wrong way — so that is what is asserted.

## Where things live

```
src/sourcework/
  models.py            the shared vocabulary (Evidence, Requirement, PRDDocument, …)
  llm.py               structured output, failover across backends, stub mode
  stream.py            live model output: the sink, its wire format, its rate
  checkpoint.py        per-stage run state, so an interruption is resumable
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

[ARCHITECTURE.md](ARCHITECTURE.md) has the design rationale and the message flow.

## What a good change looks like

**Say why, not what.** The code is commented at an unusual density and it is
deliberate. Comments here explain the decision — why a limit is 60,000
characters, why a model id does not travel to a failover backend, why the critic
falls back to the reasoning model and not the cheap one. A comment restating the
next line will be asked about; an absent comment on a non-obvious choice will be
asked about harder.

**Tests describe behaviour.** Test names are sentences about what the system
guarantees (`test_a_reasoning_trace_never_reaches_the_json_parser`), and the
docstring says what breaks if it regresses. Several existing tests exist because
something failed in a real run; if yours does too, say so in the docstring.

**Fail loudly, never silently.** Dropping a model because its id collided,
truncating a prompt, skipping a source — each has to be reported. A quiet
degradation looks identical to a feature that was never used.

**Keep the evidence rule.** Nothing enters the pipeline but evidence with a
locator; requirements cite evidence ids; citations are validated in code. A
change that lets a requirement assert something no source said breaks the one
guarantee this project makes.

## Before you open a pull request

- `make lint && make test` are green.
- New configuration is in `.env.example` **and** in the settings page allow-list
  (`src/sourcework/ui/env_file.py`) — a setting the UI cannot see is a setting
  nobody will find.
- No absolute paths from your machine. `scripts/llama-swap.yaml` and
  `scripts/llama-swap.d/` are gitignored for exactly this reason.
- No new dependency without checking its licence. This project is MIT and CI
  fails on a copyleft dependency; a permissive one is still worth justifying,
  since the front end has no build step and the back end has few dependencies on
  purpose.

## Scope

Happily accepted: backends, ingestion formats, rendering, local-model
ergonomics, documentation, and bug reports with a reproduction.

Discuss first in an issue: anything that changes the evidence/citation model,
adds a build step to the front end, or introduces a database.

By contributing you agree your work is licensed under the [MIT License](LICENSE).
