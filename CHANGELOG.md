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
and the version on every agent card are the same string by construction. The
hosted sibling `sourcework-cloud` carries its own `__version__` the same way.
One release tag covers both distributions; the sections below name which one a
change belongs to.

## [0.6.3] — 2026-09-11

### Changed

- Treat a release as a retry only when HEAD is its tag
- Match the version as a whole token, not a substring
- Embed a Python runtime in the desktop installers

## [0.6.2] — 2026-09-11

### Changed

- Grant the desktop job what its reusable workflow requests
- Cut a release on every green push to main
- Keep the sections that answered when one critic pass fails
- Attach bundles with an explicit repo, since the job has no checkout

## [0.6.1] — 2026-09-10

A repair release for the critic: a long PRD reviewed in several sections no
longer fails the whole stage when one section's model call dies.

### Fixed

- **One dead section no longer discards the rest of the review.** Reviewing a
  long PRD in sections made a single slow or failed model call (a CLI timing out
  at `cli_timeout_s`, a provider blip) fail `review_prd` outright, throwing away
  every section that had already been read. A failed section is now reported as
  an `unreviewed` major finding and the findings from the sections that answered
  are kept; the review degrades to a partial one that says which part was not
  read, rather than passing silently or losing the work. If *no* section could be
  reviewed the stage still fails loudly, because there is no partial review to
  stand behind.

#### Hosted — `sourcework-cloud` 0.2.0 (unchanged)

The hosted package's own code did not change, so its version does not move. It
mounts core's critic, so the fix arrives with the core it is built against.

## [0.6.0] — 2026-09-10

The local app becomes an app: `desktop/` is a Rust/Tauri shell — a window, a
tray and native notifications — around the same backend and the same web UI,
and this release ships installers for Linux, Windows and macOS. The header
became a real navigation bar with icon controls, and the critic now reviews a
long PRD in sections instead of truncating it.

### Added

- **A desktop shell, so the local app is an app and not a browser tab.**
  `desktop/` is a Rust/Tauri window around the same backend and the same web
  UI: a tray icon (Show · New PRD · Quit), close-to-tray, single-instance, and
  native notifications when a run finishes and the window is not focused. It
  starts `python -m sourcework app --no-browser --port <ephemeral>` and points
  the webview at loopback, so the browser path (`sourcework app`, `--browser`,
  `sourcework ui`, compose, hosted) is untouched and the front end still has no
  build step and calls no Tauri APIs. Bundles for the current OS (`deb`/
  `AppImage`, `msi`/`nsis`, `dmg`), unsigned for now. Bundling a Python runtime
  as a sidecar, to drop the "Python must be installed" requirement, is planned.
- `python -m sourcework` as an entry point alongside the `sourcework` console
  script, so a launcher that only knows an interpreter can start the app.

### Changed

- **The header is a real nav, and the Quit control is gone.** The sections are
  now one segmented control of `<a>` links — the old markup wrapped a
  `<button>` in an `<a>`, which is invalid HTML and behaves differently across
  browsers — the brand links home, and the page you are on is marked with
  `aria-current` so the selected style and the accessibility signal cannot
  disagree. Stopping the app is the window's or the tray's job, so the in-page
  Quit button (and its `/api/shutdown` page call) is removed; the endpoint
  stays for the shell. The theme control and the dashboard's refresh are icon
  buttons (sun/moon/auto, and a refresh that spins while it reads), named by
  `aria-label` and `title` rather than by visible words.
- Inside the shell the page sees `?shell=1` and stands down its own browser
  notifications, so there is exactly one for a finished run: the shell's.

### Fixed

- **The critic reviews a long PRD in sections instead of truncating it.** A
  92 KB document was cut at the prompt cap, and the run said so: the tail was
  not reviewed adversarially. That warning was honest and the gap was real. The
  review now splits on `## ` section boundaries and reads every part in
  several passes, merging and de-duplicating the findings and folding the
  verdicts (the worst section wins); the split is a partition, so no character
  is dropped. The progress line says how many sections, in place of the old
  "the tail was not reviewed".

#### Hosted — `sourcework-cloud` 0.2.0 (unchanged)

The hosted package's own code did not change, so its version does not move. It
mounts core's web UI, so the new header, the icon controls and the
notification guard arrive with the core it is built against.

## [0.5.0] — 2026-09-10

A hardening and ergonomics release. A security pass closed a run-id path
traversal, an environment-injection hole in the settings writer, SSRF and
unbounded-fetch gaps, and enabled the hosted store's row-level security for
real; the settings page was cut into tabs and stopped hiding the live model
lists, which now refresh on entry; and a run's stage railway no longer showed
the wrong stage for the whole analysis phase. Nothing here changes what a
citation guarantees.

### Added

- **`sourcework install-llama-swap`** fetches the one runtime this project is
  willing to install for you. llama-swap is what serves several models behind
  one endpoint, which is what per-role models need — and until now `doctor` and
  the settings page could tell you precisely that it was missing and then hand
  you a link. It pins a release rather than following "latest", verifies the
  SHA-256 against the checksums file published beside the asset, refuses on a
  mismatch instead of warning, installs per-user into `~/.local/bin`, and will
  not replace an existing binary without `--force`. `--dry-run` prints the URL
  and the digest source and downloads nothing.

  Nothing calls it automatically and no HTTP route reaches it: the web UI ships
  no authentication, and "a web request can cause a binary to be downloaded and
  executed" is not a trade this program makes. There is deliberately no
  equivalent for llama.cpp — its releases are a matrix of CUDA, ROCm, Vulkan,
  Metal and CPU builds, and choosing wrong there does not fail, it silently runs
  an order of magnitude slower, which is the degradation this project treats as
  a defect.
- **The local-model scan stops generating a config that cannot run.** Three
  faults, found by following one failure all the way down. It emitted `-rea
  off`, a short form only newer llama.cpp builds have, while resolving
  `llama-server` from `PATH` — so on a machine whose first build was older,
  llama-server rejected the flag and exited in a quarter of a second and
  llama-swap reported only `upstream command exited prematurely`. It is now
  `--reasoning-budget 0`: the long form, accepted by every build that has the
  feature, and the flag that says the thing meant (`-rea` is an alias for
  `--reasoning-format`, which decides where thoughts go, not whether they
  happen). The scan also checks that the binary it is writing commands for
  actually lists the flags those commands use, and says so loudly when it does
  not.

  It also ignored the `server` macro in `llama-swap.yaml` — the place someone
  has already written down *which* build to run — and resolved `PATH`
  independently, so on a machine with two builds the generated entries and the
  hand-tuned ones pointed at different binaries. The macro now wins.

  What made this expensive to find is that every check upstream stayed green:
  the endpoint was up, `/v1/models` listed everything, `doctor` said
  `[configured]`, the pre-flight passed. All true. It only failed when a model
  was actually requested and the process behind it died — which no probe can
  detect without loading a model, and none should.
- **`SOURCEWORK_MODEL_DIRS` works from `.env`.** It has been documented in
  `.env.example` all along, and `localmodels.model_dirs()` read only
  `os.environ` — so a value written where the documentation puts it was
  invisible unless something in the process had already called `load_dotenv`.
  Importing litellm does, which is why this appeared to work from inside the app
  and not from `scripts/llama-models.py`. It is now a real setting, read through
  the settings object, with the environment still winning when set.
- **`doctor` now says what to do next, and checks before saying it.** It used to
  end by recommending `scripts/llama-swap.sh` whatever the machine had; on one
  without llama-swap that script's first act is to fail, so the advice was a
  dead end. It now names the install command when the tool is missing and the
  scan-and-serve sequence when it is there.

### Changed

- **The settings page is split into tabs.** It had grown into one long scroll:
  a probe card that repeated the active backend, a routing card, then eleven
  near-identical backend cards, then a twelve-field "Limits" grab-bag, then
  ad-hoc cards. It is now **Overview** (active backend, profiles, and what this
  machine can actually reach), **Models** (the backend cards, each carrying its
  own status, with shared credentials), **Advanced** ("Limits" split into model
  calls, analyst slicing, mesh & concurrency, and runs & quality),
  **Integrations** and **Security**. The tab and each field's tab come from the
  server (`/api/settings` → `tabs`), so the local and hosted installs share one
  navigation, and the active tab is in the URL (`/settings#models`). Switching
  tabs only hides panels — every control stays mounted, so Save still posts
  them all and a half-typed value survives a look at another section.

### Security

- **A run id is validated before it becomes a path.** `run_id` is
  caller-controlled and reaches `workspace/checkpoints/{run_id}.json` and a
  glob pattern, so `../../…` wrote or deleted outside the workspace and `*`
  made the delete route remove *every* run's checkpoints. It is now constrained
  to `[A-Za-z0-9_-]`, the checkpoint layer refuses a path outside the
  checkpoints directory and escapes the glob, and the same rules apply to the
  route that never passed through `PRDRequest`.
- **The settings allow-list now governs the value, not only the key.** A value
  containing a newline ended the line early and the next line was parsed as
  another key — `SOURCEWORK_SECURITY__ENFORCE=false` injected through a field
  that was never on the page. Control characters are rejected, and a
  wrong-typed value (a non-number in a number field) is refused rather than
  written and turned into a config no later process can load.
- **SSRF hardening and real size limits on fetched documents.** The private-
  address check resolved the hostname and httpx resolved it again, so a name
  could answer publicly for the check and `127.0.0.1` for the connect; the
  check is now `is_global` and the connection is pinned to the address that was
  vetted (`Host`/SNI keep TLS and virtual hosting working). Response bodies,
  Confluence attachments and redirected bodies are streamed against the size
  cap instead of buffered first. Confluence pagination ignores a cross-origin
  `_links.next` rather than sending it the account token.
- **Row-level security is enforced in the hosted store.** The tables were
  created by the same role that queries them, and Postgres exempts a table's
  owner from its own policies, so `ENABLE` was decoration; it is now `FORCE`.
- Cloud bearer-token comparison is constant-time, server-supplied publish URLs
  are allow-listed to `http(s)` before becoming an `href`, the audit bundle
  redacts inline bytes and local paths, and OOXML uploads are refused if they
  expand past a cap.

### Fixed

- **The model picker stops fighting where it opens.** The vendored combobox
  anchored to the document and only ever opened downward, so a lower model cell
  got a sliver of a list; it now flips above the field when there is more room
  there, and the list is wider than the cell so a long model id is not clipped.
  It no longer opens on focus — clicking a cell dropped sixty rows over the
  form — opening instead on a click, on typing, or on ArrowDown. The "current"
  badge, which tested an attribute nothing ever set, now marks the saved value.
- **Model choices follow the backend on the run form.** Its four role cells are
  shared across backends, so a model id picked for one was carried into the
  next: the new backend's list filtered against a foreign id and showed "No
  match", and the stale id would have been submitted under the wrong backend.
  Each backend's overrides are now remembered separately and restored when you
  switch back. The suggestion list also stopped truncating at 60 — opencode-cli
  reports about 139 ids, and the cap cut them alphabetically, hiding models that
  were not in the first slice.
- **The stage railway can no longer be dragged backwards by a specialist's
  line.** The strip advances by matching progress lines, and the ingest
  pattern's unanchored `evidence item(s) from ` matched the analyst's own
  relayed `Analysing 220 evidence item(s) from 5 source(s)` — so after
  `Normalising requirements` had honestly moved the strip to Analyse, the very
  next line pulled it back to Ingest, and a run spent its whole analysis phase
  showing the wrong stage. The page now matches on the line with the
  specialist's `[agent]` tag lifted off, every alternative is anchored to the
  line's start, and the analyst's relayed lines (including its slice lines)
  are what Analyse advances on. Two tests pin it: every pattern still matches
  something the system says (the corpus now includes the specialists whose
  lines are relayed), and a relayed line can never move the strip backwards.
- **The shared Anthropic key no longer reaches the `claude` CLI.** The
  `claude-code` backend authenticates through the CLI's stored login, but the
  process also carries `ANTHROPIC_API_KEY` for a different job — litellm's
  `anthropic/…` ids and the model listing — and the CLI treats it as an auth
  source that takes precedence over the login and refuses to run, failing whole
  extraction stages with "claude.ai connectors are disabled because
  ANTHROPIC_API_KEY … is set". The key is now withheld from the subprocess
  (`process.run` grew a `without` exclusion), the backend says once that it did
  so and where the key still applies, and the settings page states the rule
  where the key is entered.
- **The model lists can be refreshed, and entering Settings always does.** The
  lists come from outside the app — a CLI's own catalogue, a model server — so
  a page opened before `opencode` gained a model, or before a server was
  started, showed a stale set. Entering Settings now re-probes with
  `?refresh=1`, which drops the server's 30-second reachability cache and tells
  a backend with a cache of its own — OpenCode's models.dev registry — to
  update it (`opencode models --refresh`). The run form's Model section grew a
  **Refresh models** button, and returning to an open tab re-reads them.
  `/api/backends` is served `Cache-Control: no-store`, so the browser cannot
  answer from its own cache either.
- **Models are offered by name as well as by id, and OpenCode's list is read
  with `--verbose`.** `opencode-go/deepseek-flash` is what the engine needs, but
  the model is called **DeepSeek V4.1 Flash**, and a picker showing only ids
  left no way to find it: the person searching for "v4.1" and the string in the
  list never met. The picker now searches and displays the display name, with
  the id beside it, for every backend that knows one (OpenCode does). Model
  names ride in `/api/backends` as `model_names`, and the ids-and-names lookup
  is one `opencode models --verbose` call, not a second subprocess.

- **The final PRD is reviewed as the final PRD.** The review loop broke before
  reviewing the last revision, so the revised document shipped carrying the
  previous draft's verdict; each draft is now reviewed before the loop decides
  whether to revise again.
- **“Nothing enters without a locator” holds at the citation.** Evidence with
  no locator, and citations inherited from a baseline that name evidence this
  run does not have, are dropped exactly like an invented id — the requirement
  renders `derived` rather than sourced against a blank location.
- **A response truncated at the output limit is reported, not parsed.** Only
  `claude-code` raised; every backend that reports `finish_reason` through
  usage is now caught centrally, so a JSON fragment no longer surfaces as a
  misleading schema error.
- **Prompts that were shortened say so.** The writer's evidence cap and the
  critic's markdown/evidence caps now emit a warning instead of quietly
  reviewing or drafting from a subset.
- Checkpoint reads and writes run off the event loop (they embed the full
  source text) and are serialised so the analyst's concurrent slices cannot
  lose each other's write.
- Cancelling a run now waits for it to unwind, so deleting a running run cannot
  have its row re-created by the cancelled task's final save.
- `sourcework ui --host 0.0.0.0` is detected as a wider bind, so exception
  detail is withheld as on any other non-loopback bind.
- Silent drops are reported: Confluence attachments past the cap and each
  failed attachment, truncated spreadsheet rows, and failed analyst slices all
  reach the run's warnings.
- Unsupported binaries (`.doc`, `.rtf`, …) are refused instead of being decoded
  as text into citable mojibake, and a single-column CSV no longer fails
  outright on sniffing.
- The single-model helper and docs use `--reasoning-budget 0`, the long flag the
  scanner already uses, rather than the `-rea off` short form older
  `llama-server` builds reject.
- A Bedrock target credentialed by an IAM role or `AWS_PROFILE` is no longer
  reported unavailable, `structured()` still makes one attempt when
  `max_retries` is 0, child process groups are reaped after a clean exit, and
  staged image extensions are allow-listed.
- Hosted compose no longer publishes the unauthenticated orchestrator port;
  the local compose file no longer requires a `.env` that a fresh clone does
  not have; CI lints `cloud` too and fails (rather than skips) the e2e suite
  when a port is busy.

#### Hosted — `sourcework-cloud` 0.2.0

The package moved because the shell was hardened, not because it gained a
feature. Its tables now `FORCE ROW LEVEL SECURITY` — the role that creates them
owns them, and Postgres exempts a table's owner from its own policies, so the
tenant policy that was meant to back up the explicit filter had never actually
run; the development bearer token is compared in constant time; and the app is
told the host it is bound to, so a wider bind withholds exception detail as on
a laptop. Its test fixtures also stop leaking `tenant_settings` between tests.

## [0.4.1] — 2026-08-20

A repair release. Everything below is something that was already broken:
runs that timed out on a model still thinking, a UI that stopped agreeing
with the run it was showing, a `docker compose up` that could not parse its
own file — and, underneath two of them, tools that reported a component
healthy without having checked the thing that would fail.

### Added

- **A working agent now says so every 15 seconds**, whether or not it has
  anything to report (`a2a_common.executor.KEEPALIVE_INTERVAL_S`). Narration was
  the only thing putting bytes on the A2A channel during a model call, and it is
  opt-in, requested only for runs somebody is watching, and never emitted at all
  by the litellm backend or the hosted providers built on it — so a nine-minute
  analyst call was nine minutes of silence to every idle-connection clock
  between the agent and the browser. The tick carries no message, so it cannot
  appear as a line in anybody's progress log.
- **The run event stream sends a heartbeat every 20 seconds** while a run has
  nothing to say. It goes out as an SSE comment, which `EventSource` discards
  without raising an event. nginx and an AWS ALB close an idle connection at
  60s and Cloudflare at 100s, which is well inside a single pipeline stage.
- **`SOURCEWORK_MESH__READ_TIMEOUT_S` and `SOURCEWORK_MESH__CONNECT_TIMEOUT_S`**,
  both on the settings page. The read timeout is left empty by default and
  derived from the LLM budgets — twice the longest call you allow, at least
  600s — so raising `SOURCEWORK_LLM__TIMEOUT_S` for a slow local model server
  raises the transport's patience with it instead of leaving the two to drift.

### Fixed

- **Runs no longer time out at ten minutes of thinking.** The mesh's HTTP client
  set one flat 600-second timeout for connect, read, write and pool alike. On a
  streaming response httpx applies the read timeout *per chunk*, so that was not
  "a call may take ten minutes" but "ten minutes of silence ends this call" —
  against work `SOURCEWORK_LLM__CLI_TIMEOUT_S` already permits to spend exactly
  600 seconds in one attempt, before the empty-response retry, the schema
  retries and the failover chain around it. Anyone who had raised
  `SOURCEWORK_LLM__TIMEOUT_S` for a local model server was over the line before
  a run started. The four clocks are now set separately, and connecting to a
  peer fails in ten seconds rather than ten minutes.
- **Cancelling a queued run leaves a record.** It used to leave none: the
  cancellation landed while the run was still waiting for a slot, so the code
  that writes a terminal status and closes the open streams never ran. The row
  read `queued` for ever, every watching tab hung on work that was already dead,
  and only a restart corrected it — filing it under "the UI restarted while this
  run was in flight", which was not what happened.
- **The run header, railway and hero follow the run.** They were rendered from
  the run as fetched when the page loaded and never updated, so a run opened
  while it was `queued` displayed "queued" for its whole life, pulsing dot and
  all, while the log beside it scrolled. Every event already carried the status
  it was emitted at; nothing read it.
- **A dropped event stream reconnects.** It used to be fatal: the browser closed
  the connection and re-read the run exactly once, so a run that was still going
  left the page frozen on its last line with the elapsed clock stopped,
  recoverable only by a manual reload. Reconnection is backed off, gives up when
  the run turns out to have finished or been deleted, and re-syncs the header
  from the run row each time — replayed events are already filtered by `seq`.
- **Opening the settings page no longer freezes a run in another tab.**
  `/api/backends` ran the backend probe on the event loop, and that probe shells
  out to every CLI backend with a 30-second ceiling apiece and opens a socket to
  the local model server. For as long as the slowest one took, nothing else on
  the server progressed — including the SSE streams feeding every watched run.
- **The mesh indicator answers like a health check.** It borrowed the pool's
  run-length timeouts and probed the eight agents one after another, so a host
  that hung rather than refusing could hold a 30-second-polled status pill for
  the better part of an hour. It now probes all eight at once with three seconds
  of patience.
- **The backend list stops calling an unreachable backend "available".** That
  word answered a narrower question than it looked like: `available()` is
  deliberately network-free — a binary on PATH, a library importable — and says
  nothing about whether a server is up. So llama-cpp showed a green pill on a
  machine where `llama-server` was installed, nothing was listening, and every
  run failed. The model list made it more convincing still: with the server
  down, `list_models()` falls back to scanning GGUFs on disk, so the picker
  offered **21** models where a live server would have listed a handful. Rows
  now carry `reachable` (True/False, or None for the CLIs and bedrock/vertex,
  which have no endpoint to ask) and `models_from` (`server`, `disk` or `cli`),
  and the page renders three states — ready, **installed, not answering**, not
  here — with disk-scanned models labelled as such. The probe is cached for 30s,
  because `probe()` runs on every page load and that is the same constraint
  which kept `available()` from checking in the first place.
- **A dead model server is reported before a run reads anything.** The pipeline
  now asks once, after mesh discovery and before the first source, whether any
  backend in the failover chain can actually answer — a two-second check. What
  it replaces: the first thing an unreachable server met was the extraction of
  source 1, which spent its retries at the full `timeout_s` discovering nothing
  was listening, then repeated that for every remaining source, so "the server
  is not running" arrived many minutes late wearing five stack traces. It
  reports only when *every* backend is ruled out and only on evidence — stub
  mode, CLI backends and reachable endpoints all pass — because blocking a run
  that would have worked costs more than the minutes this saves.
- **`sourcework doctor` names the endpoint your backend will really call.**
  Everything asking "where do the models come from" read `SOURCEWORK_LLM__API_BASE`,
  which is the general litellm setting and is empty on any install that picked
  llama-cpp, azure or openai. So the probe found no configured endpoint, fell
  through to the conventional ports, and reported whatever else happened to be
  listening — doctor announcing a healthy server on one port while every run
  failed against a dead one on another. `LLMSettings.endpoint_for()` now gives
  the endpoint in force per backend, and doctor states plainly when the
  configured one is unreachable and something else answered, without dressing
  the stand-in up as the answer.
- **`docker compose up` works again, and the mesh it starts can reach itself.**
  Two separate faults in the compose files, neither covered by any test. The
  `ui` service carried two `command:` keys — the second added to make the
  container reachable, the first left behind — which older parsers resolved
  last-one-wins and Compose v5 rejects outright, so the file would not load at
  all. Underneath that, every service declared its own `environment` to set
  `PUBLIC_HOST` and `PORT`, and a YAML merge key merges one level only: each
  service replaced the shared block wholesale rather than adding to it, so all
  eight agents and the UI lost every `SOURCEWORK_PEERS__*` URL and fell back to
  the `localhost` defaults in `.env` — where, inside a container, localhost is
  the container. The mesh came up, every healthcheck passed, and not one service
  could reach another. The hosted file lost `SOURCEWORK_LLM__STUB=1` the same
  way, so it asked for real credentials it had promised not to need. The shared
  block is now an `x-mesh-env` anchor merged *into* each service's environment,
  and `tests/test_compose.py` fails on either fault.
- **The event stream tears down cleanly when a browser navigates away.** The
  `end` frame was emitted from a `finally`, which on generator close raises
  `async generator ignored GeneratorExit` — over a connection with nobody left
  to read the frame.

#### Hosted — `sourcework-cloud` 0.1.0 (unchanged)

- The package itself did not change, so its version does not move; the shared
  `v0.4.1` tag covers it at `0.1.0`. What did change for the hosted
  distribution is `docker-compose.cloud.yml`, which had both faults described
  above — and one of its own, since the mesh block it lost carried
  `SOURCEWORK_LLM__STUB=1`, so the deployment asked for credentials it had
  promised not to need before you supplied any.

## [0.4.0] — 2026-08-18

### Added

- **An architecture view** (`/architecture`, in the header beside Dashboard).
  The eight-agent mesh drawn as a diagram rather than summarised as an `8/8`
  pill: who reads what, who hands what to whom, and the revision loop between
  writer and critic. Health comes from `/api/mesh`, so an agent that stops
  answering goes dashed and red; while a run is going the working agent glows
  and the edges into it animate. Clicking a node gives its role, its
  consumes/produces contract, the skills its agent card actually advertises and
  a link to its API docs.
- **A railway across the top of a run**, one stop per pipeline stage: Discover,
  Ingest, Analyse, Write, Review, Publish, Done. Finished runs are drawn from
  the timings the orchestrator measured, so each stop carries what it cost and a
  stage that never ran says **not run** rather than showing a tick. Live runs
  advance on the orchestrator's own progress lines — and a line that matches
  nothing leaves the strip where it was, because the strip may lag but may never
  lie. A test asserts every pattern still matches something the pipeline says.
- **A hero line while a run works**: which agent is working, on what, and for
  how long, in the largest type on the page. The elapsed clock moved here from
  the tail of the log.
- **The progress log reads as a feed**: one sticky header per minute instead of
  a timestamp on every row, the agent as a chip rather than as four more
  characters of prose, and failures railed in red and wrapped in monospace
  because the detail is the point of an error line. It only follows the tail
  when you are already at it.
- **Copilot CLI can run under a selected profile.**
  `SOURCEWORK_LLM__COPILOT_PROFILE` now lets the copilot-cli backend switch
  account by deriving `COPILOT_HOME` from the profile name
  (`~/.copilot-<profile>`) or from `COPILOT_HOME_<PROFILE>` when that mapping
  is exported; `SOURCEWORK_LLM__COPILOT_HOME` still overrides it explicitly.

### Changed

- **The run view's tabs are a segmented control**, one choice rather than six
  underlined words, and the model-output panel is a native disclosure with a
  copy button like every other terminal-shaped thing in the app.

- **Settings grouped per backend.** The settings page draws one card per backend
  holding that backend's four model cells *and* the credentials only it reads
  (Azure's keys, AWS's, Vertex's, llama.cpp's server, the CLI home dirs), with
  the active backend's card open and the rest collapsed. The genuinely shared
  keys — the gateway, Anthropic's and OpenAI's — live in a "Shared credentials"
  card instead of a flat list. The grouping comes from the field data, so the
  hosted install gets the same cards (just fewer of them), and the backend
  filter now also drops a CLI backend's exclusive keys along with its model
  cells.

### Fixed

- **Saving settings now creates the platform config directory on first run**
  before writing `.env`, so a first-time save on packaged installs no longer
  fails with `FileNotFoundError` for `~/.config/SourceWork/.env`.
- **A save that requires restart but reaches no peers now says so explicitly.**
  The settings response names that running agents are still on the previous
  values and that a manual mesh restart is required.
- **Unavailable-backend errors now name the backend's real missing dependency.**
  A failed `llama-cpp` call now reports the `llama-server` availability detail
  instead of the generic CLI-missing wording.

## [0.3.0] — 2026-08-18

The release is the distribution split: the local install keeps everything —
hosted APIs, the coding CLIs, `llama.cpp` — while the hosted one offers the API
family only (`litellm`, `azure`, `bedrock`, `vertex-ai`, `openai`), and a
tenant's settings now live per-tenant in Postgres instead of a shared `.env`.

### Added

- **Backends split by distribution.** The settings page, model profiles and
  backend probe now honour an `allowed_backends` on the settings backend: the
  local distribution offers everything (hosted APIs *and* the coding CLIs and
  `llama-cpp`), while the hosted one offers the API family only — `litellm`,
  `azure`, `bedrock`, `vertex-ai`, `openai`. A tenant cannot even save a CLI
  backend by posting it by hand: the value is dropped, not stored. The hosted
  settings page now keeps its values per tenant in Postgres
  (`TenantSettingsBackend` over a new `tenant_settings` table, RLS-scoped like
  runs) instead of a shared `.env`.
- **Four named hosted-provider backends.** `azure`, `bedrock`, `vertex-ai` and
  `openai` wrap LiteLLM with their own credential fields and per-role model
  cells on the settings page. Each probes "available" only when its credential
  is configured, and the settings page says exactly which key is missing.
  `openai` joins the curated profiles (its ids are portable); `azure`/`bedrock`/
  `vertex-ai` are deliberately left out, because a deployment name only the
  operator knows cannot be pre-filled honestly.
- **`build_app` grows the four seams a hosted deployment needs.** `executor`
  (runs driven from somewhere other than this process), `settings_backend`
  (per-tenant settings instead of rewriting the process's own `.env`),
  `authorizer` (what a signed-in principal may do, decided after identity), and
  `run_id_factory` (UUIDs where the local 12-hex ids would collide). Each
  defaults to today's behaviour, so a deployment that passes none of them gets
  exactly today's app.
- **The running version in the UI header.** Every page shows the same
  `__version__` the build reads, served by `/healthz` — so the label cannot
  drift from the code it labels.
- **The run's own page answers "is this finished enough to send".** It now
  shows the same readiness verdict as the dashboard, computed from the same
  stored dicts, with a link into the review tab when blockers stand in the way.
  A run still in flight has no verdict rather than a made-up one.
- **Warnings and failures travel with the status.** A run that skipped a source
  still finishes `ok`, and now the history row and the run header carry the
  counts — `ok` with a dropped source no longer reads as unqualified success,
  and the header links straight to the tab that lists what happened.
- **The reviewer's own sentence reaches the review tab.** The critic writes one
  line framing the findings (`ReviewReport.summary`); it used to be dropped on
  the floor, and in stub mode it is what says no model ran.
- **Run-view tabs are in the URL.** `#/run/<id>/<tab>` reloads and shares to the
  same tab, which tab is showing survives re-renders, and the strip is a real
  tablist with roving tabindex and arrow keys. Backing between two tabs of one
  run no longer drops the live event stream.
- **Sign-off is a form, not two stacked `prompt()` dialogs.** Those ignored the
  theme, could not be corrected once past the first, and turned "cancel" on the
  note into a submitted decision. The append-only approval trail now renders
  under the header, and actions that hand work to an agent (cancel, resume,
  publish, delete) disable themselves while running so nothing is sent twice.
- **Downloads sit behind one control** instead of four buttons that re-ordered
  on a wrapped row, and the destructive end of the action row stays the end.
- **The roles a run may override are the roles the settings page can
  configure.** Both come from the same list now, derived from the settings
  fields instead of a second hand-written copy — `critic` is offered where it
  was missing, and the phantom `fast` role no agent has ever requested is gone.
  Their wording lives in one file shared by the run form and the settings page.
- **An accessibility pass across the app**: every label is attached to its
  control, the drop zones are buttons (the one control the new-run form cannot
  work without now has a keyboard path), toasts live in one live region instead
  of drawing over each other, the progress log is announced, and
  `prefers-reduced-motion` is honoured.
- **The layout survives a phone.** The header wraps instead of scrolling its
  chrome off a sticky bar, the history list folds behind its heading under
  820px, and the two centred pages stop wearing desktop margins on a narrow
  screen.

#### Hosted — `sourcework-cloud` 0.1.0

- **The hosted service starts as a shell.** A sibling `sourcework-cloud` package
  serves the same web UI over Postgres instead of SQLite — tenant-scoped from
  day one and guarded by an installed authenticator — so the whole product runs
  hosted before any of the identity or tenancy machinery is load-bearing. It is
  web-only by construction: the package declares no console scripts. Its
  `cloud/tests` suite runs against a real Postgres when one is reachable and
  skips loudly when a laptop cannot provide one.
- **The hosted package is versioned at last.** It shipped as the unreleased
  marker `0.0.0`, written out twice; it now carries its own `__version__`
  (`0.1.0`), read at build time the way core's is, and depends on
  `sourcework>=0.3.0` to record the coupling. One release tag still covers both
  distributions — the `Hosted` sections here are what name which one changed.

### Fixed

- **A severity the critic emits can no longer render in the wrong colour.** The
  review tab maps all four severities — a blocker previously read as a nit after
  `blocking` — and tests now tie the front end's maps to `Severity`, the run
  statuses and the readiness states so the vocabularies cannot drift apart again.
- **The claude-code model picker lists the account's models live instead of a
  hand-maintained list.** With `ANTHROPIC_API_KEY` set, the suggestions come
  from Anthropic's models endpoint, so a model released yesterday is offered
  tomorrow; without one (the CLI's stored login), it falls back to the aliases
  that resolve to each tier's latest build plus the current pinned generation.
  The settings profiles, `.env.example` and the litellm role defaults moved
  from the retired 4.x ids to the 5.x ids the live `opencode models` catalogue
  serves. The agy presets keep claude 4.6 ids because that is what agy still
  serves; its picker stays live.

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

[Unreleased]: https://github.com/DavideAresta/sourcework/compare/v0.4.1...HEAD
[0.4.1]: https://github.com/DavideAresta/sourcework/releases/tag/v0.4.1
[0.4.0]: https://github.com/DavideAresta/sourcework/releases/tag/v0.4.0
[0.3.0]: https://github.com/DavideAresta/sourcework/releases/tag/v0.3.0
[0.2.0]: https://github.com/DavideAresta/sourcework/releases/tag/v0.2.0
[0.1.0]: https://github.com/DavideAresta/sourcework/releases/tag/v0.1.0
