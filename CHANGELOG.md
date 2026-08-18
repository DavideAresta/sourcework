# Changelog

Notable changes, newest first. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html) — with the pre-1.0
caveat spelled out: **while the major version is 0, a minor bump may break
things.** `0.x` is where the seams are still moving. What will not move quietly
is the evidence rule; a release that changed what a citation guarantees would
say so at the top of its section.

The version lives in `src/sourcework/__init__.py` and is read from there at
build time, so `sourcework.__version__`, the installed distribution metadata,
and the version on every agent card are the same string by construction.

## [Unreleased]

### Added

- **The running version in the UI header.** Every page shows the same
  `__version__` the build reads, served by `/healthz` — so the label cannot
  drift from the code it labels.

## [0.2.0] — 2026-08-18

### Added

- **A direct llama.cpp backend** (`SOURCEWORK_LLM__BACKEND=llama-cpp`): model
  calls go straight to a local `llama-server` or llama-swap at
  `SOURCEWORK_LLM__LLAMA_CPP_API_BASE` with no LiteLLM proxy between. Roles map
  to models per role, and when the server is down the model picker still lists
  the GGUFs the configured scanner would serve.
- **The settings page restarts the mesh itself.** Saving a setting that needs a
  restart now asks every agent to re-exec itself with its original argv, so a
  save takes effect without touching a process supervisor; the page reloads
  when the mesh is back. Works under `serve-all`, a terminal, the desktop
  launcher and docker.
- **A requirements-quality rule pack**, checked deterministically in the critic
  before the model review runs. The rules follow ISO/IEC/IEEE 29148
  characteristics and the INCOSE Guide to Writing Requirements: escape clauses
  ("as appropriate", "TBD"), open-ended lists ("such as"), absolutes ("never",
  "100%"), compound statements (two obligations in one sentence), priority that
  disagrees with its own modal verb (a MUST that says "should"), requirements
  with nothing measurable anywhere, and terms used but not defined in the
  glossary. Every rule produces a finding that flows through the normal
  revision loop; a `quality_clean` score rides in the review's coverage stats.
  The review section of the PRD now states what it was checked against.
- **Optional EARS syntax** (`SOURCEWORK_QUALITY__EARS=1`, also on the settings
  page). On, the analyst is asked to write requirements in the five EARS shapes
  (ubiquitous / When / While / If-then / Where) and the critic flags statements
  that take none of them. Off by default: conforming phrasing is a team choice,
  not a defect.
- **Optional effort estimation** (`estimate: true` on the request, a checkbox on
  the run form, `--estimate` on the CLI). The analyst adds a T-shirt size
  (S/M/L/XL) and a one-line rationale per requirement; renderers show the sizes
  marked with `≈` as model inference, rollups per milestone are counted in
  code, a merged requirement keeps the *larger* of its parts' sizes, and a
  refinement keeps the estimate of a requirement whose statement it did not
  change. Off by default: a consumer that re-estimates (a codegen pipeline,
  JIRA with story points) should not pay for numbers it will recompute.
- **An audit bundle per run** (`GET /api/runs/{id}/audit`, "Audit bundle" button
  on the run view). One zip: the request, the result, evidence, sources, every
  progress event, and a manifest recording the backend, models, SourceWork
  version, standards basis and approval state - with a SHA-256 per member and a
  whole-bundle digest, so an edited bundle no longer matches its own manifest.
- **Approval / sign-off** on finished runs (`POST /api/runs/{id}/approval`,
  Approve/Reject buttons on the run view, a chip in the history list). Recorded
  rather than authenticated - this is single-operator software - and the history
  is append-only: a rejected-then-approved run shows both. The decision flows
  into the rendered PRD's status, so the Confluence lozenge says what was
  decided. The store migrates older databases in place.
- **Run retention** (`SOURCEWORK_RUNS__RETENTION_DAYS`, settings page under
  "History"). Finished runs older than the limit are deleted when the UI
  starts; runs in flight are never touched, and a purge is logged, never
  silent. Deleting a run now returns an erasure record naming what was removed
  and what was left (uploaded files stay in the shared workspace, listed under
  `left_in_place`).
- **A theme control in the web UI**, cycling Auto, Light and Dark from the header
  of every page. Three states rather than a switch, because a two-way toggle
  makes *follow the system* unreachable the moment somebody clicks it once. The
  choice is remembered and applied before first paint, so the wrong theme never
  flashes.
- The README now carries the web UI itself — the five views, the per-run backend
  override, and how Resume and Refine differ — so the front page answers that
  question rather than forwarding it — and opens with a screenshot of a real
  run: three sources end to end in 2m 41s on a local 8B model, no API key.

### Changed

- **The shipped PRD now carries its review.** The writer renders before the
  critic runs, so the Markdown and Confluence artifacts used to omit the review
  section entirely; after the last review round both are re-rendered with the
  verdict, findings and standards basis attached.
- **The light theme is now the palette of the project's own page** — bond-paper
  ground, carbon violet for anything actionable, red kept for what was thrown
  away or flagged. It was warm paper and terracotta, which belonged to no other
  surface this project has, and on a machine set to dark almost nobody had ever
  seen it. Dark is unchanged.

### Removed

- Documentation for things that were never built. `docs/multi-tenant.md` was a
  design for a hosted service explicitly considered and not taken, and
  `docs/desktop.md` carried a packaging plan (PyInstaller, signing, an order of
  work) alongside the launcher that actually ships. What is left describes the
  code as it is. `sourcework app --help` also stopped advertising a tray icon
  that was removed before the first release.

### Fixed

- **The container UI was unreachable.** The UI container bound the loopback
  interface, which a published port cannot reach, and every agent's healthcheck
  probed port 8000 regardless of the port it actually serves. Compose now sets
  a per-service `PORT` and the UI binds all interfaces.
- **The test suite leaked the developer's `.env`.** Importing LiteLLM calls
  `load_dotenv()`, which seeded tests with whatever the machine's `.env`
  happened to hold; the suite now scrubs `SOURCEWORK_*` from the environment
  before every test, so a passing run does not depend on the machine it runs
  on.

## [0.1.0] — 2026-08-17

First public release. Everything below already worked; this is the version that
puts a number on it.

### The pipeline

- Eight agents over **A2A v1.0** JSON-RPC — orchestrator, ingestion, vision,
  transcript, Confluence, requirements, writer, critic — each publishing an
  agent card at `/.well-known/agent-card.json`. The orchestrator hardcodes no
  agent's abilities: it resolves cards at start-up and dispatches by skill id,
  so an agent that advertises a skill is an agent that gets the work.
- **Traceability is structural, not requested.** Only `Evidence` with a locator
  enters the pipeline, every `Requirement` cites evidence ids, citations are
  validated in code, an invented id is dropped, and an uncited requirement is
  forced to render as `derived`. The PRD ends in a traceability matrix that
  points at page numbers, timestamps, headings and image regions.
- Ingestion of PDF, DOCX/DOC/RTF, PPTX, XLSX/CSV/TSV, Markdown, HTML, JSON,
  plain text, VTT/SRT transcripts, PNG/JPEG/GIF/BMP/WebP images, and Confluence
  pages — each with the locator its modality actually has.
- Output as Markdown, Confluence storage XHTML, or JSON; publishing to
  Confluence over REST.

### Running it

- **No API key required.** Model calls go through LiteLLM with failover across
  backends, and the local-model path drives llama.cpp/llama-swap with discovered
  rather than hand-written config. Five CLI backends are supported alongside
  LiteLLM — `claude`, `codex`, `agy`, `copilot` and `opencode` — each funnelled
  through one subprocess runner, which is where these integrations actually
  break and therefore where the tests point.
- A **desktop mode** with per-user paths, engine detection, a launcher entry and
  a real log file, alongside the CLI and the web UI.
- **Runs are resumable.** Each stage checkpoints, `--resume` picks an
  interrupted run back up rather than paying for it twice, resuming a run that
  is still going is refused, and a cancel actually stops the work.
- A web UI on `127.0.0.1` with live model output over SSE, a SQLite run store,
  and a settings page that edits `.env` with secrets masked both ways.

### Extending it

Three seams: publishers arrive as `sourcework.publishers` entry points,
authenticators as `sourcework.auth` entry points, and the run store as a
`build_app(store=...)` argument. A publisher that will not load is skipped; an
authenticator that will not load raises — the same decision made twice in
opposite directions, because one costs a destination and the other costs the
lock on the door.

### Security posture

Stated in full in [SECURITY.md](SECURITY.md), and worth reading before you point
this at anything you care about. In short: it is a **local, single-operator
tool**. The UI binds loopback and ships no authentication; the agent mesh ships
a publicly known shared secret and does not enforce it by default; ingestion
refuses private-network targets; and model output is treated as untrusted input.

[Unreleased]: https://github.com/DavideAresta/sourcework/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/DavideAresta/sourcework/releases/tag/v0.1.0
