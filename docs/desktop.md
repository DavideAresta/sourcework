# SourceWork as a desktop app

A sketch, not a commitment. It describes the smallest thing that is genuinely a
desktop application, what it does when it starts, and how it finds a model to
talk to.

The shape it argues for: **a tray launcher that starts the existing server and
opens the existing browser UI**. No Electron, no webview, no second language.
The front end is already plain ES modules with no build step, and `serve-all`
already runs all eight agents as threads in one process — so the desktop
product is one Python process with an icon, not a container fleet in a Chromium
wrapper.

---

## 1. The shell: why a tray app and not Electron

| | Ships | Buys you | Costs |
|---|---|---|---|
| **Tray launcher** (recommended) | ~300 MB Python, no UI runtime | Real app: double-click, icon, auto-start, clean quit | UI opens in the user's browser, not "in the app" |
| Tauri window | + ~10 MB shell | A window with your name on it | A Rust toolchain and an IPC layer to maintain |
| Electron | + ~150 MB Chromium | Nothing you do not already have | A second browser, a second update channel |

Electron bundles a browser to render HTML the user's browser already renders.
That trade makes sense when you need deep OS integration or an offline
single-file experience; here it buys a title bar.

**Write the tray app in Python, as part of the same package.** `pystray` is
cross-platform and needs Pillow, which is already a dependency (via
`python-pptx`). That means one build artifact and one toolchain — the launcher
is a new CLI entry point, `sourcework app`, and PyInstaller wraps it. A separate
Electron or Tauri project would be a second repository's worth of build
machinery for the same result.

If a window later proves necessary, Tauri points at `http://127.0.0.1:8080` and
nothing in this document changes.

---

## 2. What the tray app does

```
sourcework app
  ├─ acquire single-instance lock        (port 8080 already bound? focus, don't start twice)
  ├─ resolve config dir                  (per-user, not cwd — see §4)
  ├─ first run? → onboarding             (§3)
  ├─ start the mesh (8 agents, threads)  ── existing serve_all
  ├─ start the UI                        ── existing ui.serve
  ├─ wait for /healthz, then open the browser
  └─ tray icon + menu, until quit
```

**The menu is short on purpose:**

```
  ● SourceWork — ready
  ─────────────────────────
  Open SourceWork              ⏎   opens http://127.0.0.1:8080
  Models: gemma-4-12b-it →         submenu, switches the active model
  ─────────────────────────
  Open workspace folder            where runs and uploads live
  View log
  ─────────────────────────
  Restart engine
  Quit SourceWork
```

**The icon carries state, and that matters here more than in most apps.** A run
is minutes, not seconds, and the user will have tabbed away. Four states:
idle, working (a run in flight), attention (a run finished or failed), error
(no model reachable). "Attention" is what makes a long run tolerable — the
alternative is the user re-checking a browser tab.

**Quit must be honest.** A run in flight is a real question: cancel it, or wait?
The store already marks interrupted runs as failed on next start
(`reap_orphans`), so the worst case is recoverable — but the app should ask
rather than discard minutes of GPU time silently.

---

## 3. Finding an inference server

This is the part that decides whether a first-time user succeeds. Probe in
order, take the first that answers:

| Order | Where | How to detect |
|---|---|---|
| 1 | Configured `SOURCEWORK_LLM__API_BASE` | `GET {base}/models` |
| 2 | llama-swap / llama-server on `:8081` | `GET /v1/models` |
| 3 | LM Studio on `:1234` | `GET /v1/models` |
| 4 | Ollama on `:11434` | `GET /api/tags` |
| 5 | A hosted provider | an API key present in config |
| 6 | — | onboarding (below) |

Each probe is a 500 ms timeout against loopback. All of it runs at start-up and
behind **Restart engine**, and the result populates the tray's status line, so
"why is nothing happening" is answered by looking at the icon.

### When nothing is found

Do not show a config file. Offer three doors:

**"I already run one"** — the probe list, re-scanned, with a manual host/port
box. One click, done. This is the path for anyone who already has Ollama or LM
Studio, which is most people who would choose this tool.

**"Set one up for me"** — the guided path, and the one worth building well.
It can be short because the pieces exist:

- `detect_vram_gb()` (in `scripts/llama-models.py`) already reads rocm-smi and
  nvidia-smi; add Apple unified memory.
- `LocalModel.fits()` already decides whether weights plus a KV cache sit on the
  card, and it is what stops the app recommending something that will not load.
- `discover()` already finds GGUFs on disk and pairs vision projectors, so a
  user with models already downloaded skips straight to "use these".
- `llama-server -hf <repo>:<quant>` already downloads, resumes and caches, so
  "fetch a model for me" is an existing command with a progress bar over it.

The recommendation should be a **single suggested model with a reason**, not a
catalogue: *"You have 16 GB of VRAM. Gemma-4-12B fits entirely on the card and
can read images — 7 GB to download."* A dropdown of twenty GGUF quants is how
you lose someone in the first five minutes.

**"Use a hosted API"** — paste a key, pick a provider. Say plainly what changes:
documents leave the machine. That sentence is the product's whole argument and
the one place it must be stated rather than assumed.

### Do not bundle llama.cpp yet

Shipping GPU builds means a matrix of Metal, CUDA, ROCm and Vulkan across three
platforms, each with its own driver failures, and it will eat months before one
user sees a PRD. Detect first, download-on-request second, bundle only if the
support burden of *not* bundling turns out to be worse.

---

## 4. The code changes this needs

Smaller than the shell work, and worth doing first because everything else sits
on it.

**Config and workspace must leave the working directory.** Today
`env_file = ".env"` and `ui_workspace = "workspace"` are relative to wherever
the process started — correct for a checkout, wrong for an app launched from
Finder with `/` as its cwd. Use `platformdirs`:

| | Path |
|---|---|
| macOS | `~/Library/Application Support/SourceWork/` |
| Windows | `%APPDATA%\SourceWork\` |
| Linux | `~/.config/sourcework/`, data in `~/.local/share/sourcework/` |

Keep the current behaviour when a `.env` exists in the working directory, so a
developer checkout and a packaged app can coexist on one machine without
fighting over the same database.

**A first-run state.** Right now, no `.env` means the defaults point at
Anthropic with no key, and the first run fails with a backend error. The app
needs to know it has never been configured and open onboarding instead.

**`sourcework app` as an entry point.** Single-instance lock, tray, lifecycle,
browser open. Everything under it already exists.

**A packaging target.** PyInstaller in `onedir` mode — `onefile` extracts 300 MB
to a temp directory on every launch, which is a two-second pause before the icon
appears and a puzzling disk-usage spike.

---

## 5. Packaging reality

**Size.** ~300 MB of runtime, of which **litellm is 113 MB** — a third of the
payload, carried to normalise across providers a local-first app mostly will not
call. Replacing it with a thin httpx client against OpenAI-compatible endpoints
would cut the installer by roughly 40%, at the cost of the CLI backends and the
hosted-provider breadth. Worth measuring later; not worth doing before the first
build exists.

**Platforms.** macOS arm64 is the priority: unified memory makes a 12B model
comfortable on hardware people already own, which is exactly the configuration
this product is best on. Then Windows x64, then Linux x64.

**Signing is not optional.** Apple notarization (Developer Program, $99/yr) and
Windows Authenticode. Unsigned, macOS refuses to open the app and Windows shows
a SmartScreen warning — for a tool whose pitch is "trust this with confidential
documents", a scary dialog on first launch is fatal to the argument.

**Updates.** A tray app can check a JSON manifest on launch and offer a download
link. Delta updates and background installs are a later problem; being *able to
tell* the user they are three versions behind is most of the value.

---

## 6. Order of work

> **Steps 1–3 are implemented** on the `desktop` branch: per-user paths
> (`sourcework/paths.py`), engine detection (`sourcework/engine.py`, surfaced as
> `sourcework doctor`), and the launcher (`sourcework/desktop.py`, as
> `sourcework app`). Steps 4–6 are not.


1. **Per-user config and workspace paths.** Unblocks everything, testable today,
   no new dependencies beyond `platformdirs`.
2. **Engine detection** as a library function with the probe order above, plus a
   `sourcework doctor` command that prints what it found. Useful on its own, and
   it is the onboarding logic before there is any onboarding UI.
3. **`sourcework app`** — tray, lifecycle, browser open. The app exists here,
   for developers, before any installer does.
4. **First-run onboarding** in the existing web UI. It is a page, not a native
   dialog, and it reuses the settings machinery already written.
5. **PyInstaller build for one platform** (macOS arm64), unsigned, given to five
   people. Everything before this is verifiable; this is where the unknowns are.
6. **Signing, notarization, the other two platforms, update checks.**

Steps 1–3 are days and make the developer experience better regardless of
whether a packaged app ever ships. Step 5 is where the schedule risk lives, and
it is deliberately late so the product is proven before the packaging fight
starts.
