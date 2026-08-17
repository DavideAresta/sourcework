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
