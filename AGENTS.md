# AGENTS.md

SourceWork: an eight-agent [A2A](https://a2a-protocol.org) mesh (Python 3.11+, src
layout) that turns documents, transcripts, images and Confluence pages into a
traceable PRD. Read `ARCHITECTURE.md` for the design rationale and message flow;
`CONTRIBUTING.md` has the directory tree and PR checklist. This file only covers
what those don't make obvious at the moment you're editing code.

## Commands

- `make install` — venv + `pip install -e ".[ingest,dev]"`. Both extras are
  required for the test suite.
- `make lint` / `make test` / `make fmt` — `ruff check src tests`, pytest, ruff
  format + autofix. **There is no typecheck gate**: mypy ships in the dev extra
  but is wired into neither make nor CI.
- Single test: `.venv/bin/pytest -q tests/test_analysis.py -k <name>`
  (`asyncio_mode = "auto"` — async tests need no marker).
- `make demo` (`SOURCEWORK_LLM__STUB=1 python scripts/demo.py`) — the whole
  eight-agent pipeline over real HTTP/A2A against a deterministic fake model,
  writing a PRD to `out/`. CI runs this as the smoke test; run it after touching
  anything in `a2a_common/`, `cli.py`, or agent dispatch — it catches wiring
  breaks unit tests can't see.
- Run the system: `sourcework serve-all` (dev, all agents in one process),
  `sourcework serve <name>` (names in `cli.py:AGENTS`), `sourcework doctor`
  (configured vs. reachable), `sourcework app` (mesh + web UI).

## CI parity

- `.github/workflows/ci.yml` and `.gitlab-ci.yml` are deliberate mirrors —
  change one, change the other, both must stay green.
- CI = ruff, pytest on 3.11/3.12/3.13, the demo smoke test, and a **licence
  gate** (`scripts/check_licences.py`, itself covered by `tests/test_licences.py`).
  The project is MIT; a copyleft dependency — even transitive — fails CI. Check
  before adding any dependency.
- Pre-1.0: a minor bump may break things (CHANGELOG states the rule).

## Testing quirks

- The suite needs no API key, network or CLI: `tests/conftest.py` sets
  `SOURCEWORK_LLM__STUB=1`, replacing every model call with a deterministic fake
  derived from the requested schema. If a test seems to need a real model, that
  is the bug.
- `tests/test_a2a_e2e.py` boots all eight agents on their **real ports
  8000–8007** and *silently skips* if any port is busy. Stop a running
  mesh/`serve-all` before the suite or the e2e coverage quietly disappears.
- Backend tests never spawn a process: the single subprocess runner
  (`backends/process.py`) is stubbed to record argv and replay canned output.
  CLI-flag changes are asserted on argv — a variadic flag swallowing the prompt
  is the actual failure mode being guarded.
- Test names are sentences about the guarantee
  (`test_a_reasoning_trace_never_reaches_the_json_parser`); the docstring says
  what breaks if it regresses. Match that style.

## Conventions that differ from defaults

- **The evidence rule is the product.** Nothing enters the pipeline but
  `Evidence` with a locator; requirement citations are validated in code,
  invented ids dropped, uncited requirements forced to render `derived`. A
  change that lets a requirement assert something no source said breaks the one
  guarantee this project makes (CONTRIBUTING says so verbatim).
- **Comments explain why, at unusual density, deliberately.** A comment
  restating the next line gets asked about; a missing comment on a non-obvious
  choice gets asked about harder. Preserve existing why-comments when editing.
- **Fail loudly, never silently.** Dropping a model, truncating a prompt,
  skipping a source — each must be reported (log/`stats.warnings`). Quiet
  degradation is treated as a defect.
- Ruff: line-length 100 with E501 ignored; `UP042` ignored because
  `(str, Enum)` is deliberate — StrEnum changes pydantic JSON-schema generation
  that consumers rely on. Don't "modernize" those enums.
- Each distribution's version lives only in its own package's `__init__.py`
  (`__version__`), read by hatch at build time: core in
  `src/sourcework/__init__.py`, the hosted sibling in
  `cloud/src/sourcework_cloud/__init__.py`. Bump there and only there; note it
  in `CHANGELOG.md`. Releases share one tag (`vX.Y.Z`); the changelog's
  `Hosted` sections name what changed for the cloud package. The PRD's default
  `version` label in `models.py` is bumped in step at release time.
- New configuration goes in `.env.example` **and** the settings-page allow-list
  (`src/sourcework/ui/env_file.py:FIELDS`) — a key the UI can't see is one nobody
  finds. Env prefix is `SOURCEWORK_` with `__` nesting
  (`SOURCEWORK_LLM__BACKEND`).
- The web UI's front end is plain ES modules with **no build step** — don't add
  one. The single vendored JS file (`ui/static/js/vendor/autocomplete.js`) is
  committed verbatim with its licence header: with no lockfile, the file is the
  only record of what it is.
- Docs describe only what exists and ships. Present-tense documentation of
  unbuilt features is against repo policy (`docs/multi-tenant.md` is gitignored
  for exactly this reason). Update README/docs when behavior changes.
- Never commit `.env`, `workspace/`, `out/`, or `*.db` — the UI's SQLite run
  store holds full source text and complete PRDs, and is the most sensitive
  artifact in the tree (gitignored; keep it that way).
- Commit style: short imperative sentence stating the why/benefit, e.g. "Make
  the run form ask two questions instead of fifteen".

## Layout pointers (where to start reading)

- `src/sourcework/models.py` — the shared vocabulary; everything crossing an
  agent boundary is one of these Pydantic models.
- `src/sourcework/llm.py` — the only model-call path (roles:
  reasoning/default/vision/fast, failover, stub mode); `backends/` implements
  the transports.
- `src/sourcework/agents/<name>/agent.py` + `__main__.py` per agent; the
  orchestrator's `pipeline.py` is the run sequence
  (discover → route → ingest → analyse → write → review → publish).
- `src/sourcework/quality.py` — the deterministic requirements-quality rule
  pack (ISO 29148 / INCOSE, optional EARS); the critic calls it before the
  model review. `src/sourcework/audit.py` — the per-run audit bundle builder.
- Extension seams (docs/extending.md): `sourcework.publishers` and
  `sourcework.auth` entry points, run store via `build_app(store=...)`. The
  store's SQLite schema version is `PRAGMA user_version` — bump it and migrate
  additively in `store.py:_migrate`.
