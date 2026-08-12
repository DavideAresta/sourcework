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

To run against real models, see the local-model section of the [README](README.md).

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

## Before you open a merge request

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
