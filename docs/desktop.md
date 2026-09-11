# SourceWork as a desktop app

`sourcework app` runs the whole mesh and the web UI in one process. It is still
the whole application; what `desktop/` adds is a **shell** around it: a real
window with SourceWork's name on it, a tray icon, and native notifications —
so the local app is an app, not a Python process you talk to through a browser
tab.

The browser path did not go away. `sourcework app` (or `--browser`),
`sourcework ui`, `docker compose` and the hosted service all still serve the
same UI to a browser. The shell is an outer lifecycle owner; the page inside it
is the ordinary web front end.

---

## 1. What the shell does

```
installer (.deb/.AppImage, .msi/.dmg on those platforms)
└─ SourceWork (Tauri: Rust + the OS webview)
     ├─ tray icon: Show · New PRD · Quit
     ├─ starts, watches and stops the backend
     └─ webview → http://127.0.0.1:<ephemeral port>/?shell=1
                    (the same pages a browser gets)
```

- **It owns the process.** Closing the window hides it to the tray; **Quit**
  asks the backend to stop (`POST /api/shutdown`) and then ends it, so no
  half-dead mesh holds ports.
- **A second launch raises the first window** rather than starting a rival
  backend (Tauri's single-instance plugin).
- **Native notifications** when a run finishes, and only when the window is not
  focused — the page already shows the result, so notifying about it would be
  noise. Inside the shell the page's own browser notifications stand down
  (it sees `?shell=1`); a browser keeps them.
- **The page calls no Tauri APIs.** The shell drives it with host-side
  JavaScript (`location.hash = '#/new'`) and reads runs over the backend's HTTP
  API. That is what keeps the front end binding-free and identical in a browser.

The shell is Rust, but the front end still has **no build step** and no npm:
Tauri's CLI is used as a Rust crate (`cargo tauri`), not the npm package,
because nothing here needs the JavaScript bindings.

## 2. Starting the backend

`sourcework app` is unchanged, but the shell runs it non-interactively:

```
python -m sourcework app --no-browser --port <ephemeral>
```

- **Ephemeral port.** The shell asks the OS for a free port rather than
  fighting over 8080, so the app and a development `serve-all` can coexist.
- **Discovery order** for the interpreter:
  1. `SOURCEWORK_BACKEND_CMD`, if set (the escape hatch);
  2. the Python runtime embedded in the installer (see §2.1);
  3. a checkout's `.venv`, so `cargo tauri dev` runs your working tree;
  4. a `python3`/`python`/`py` that can `import sourcework`;
  5. a `sourcework` console script on `PATH`.
  If none is found, the window says so and names the log, rather than dying
  silently.
- The child inherits the shell's environment, so any `SOURCEWORK_*` you export
  still wins over `.env` (pydantic-settings' order), and its working directory
  decides which `.env` is read — the checkout's when launched from one, the
  per-user file otherwise (see §3).

### 2.1 The embedded runtime

A release installer **carries its own Python**. The build fetches a relocatable
CPython from [python-build-standalone](https://github.com/astral-sh/python-build-standalone)
for the target triple, pip-installs SourceWork into it, and bundles the tree as a
Tauri resource (`desktop/src-tauri/python/`; ~470 MB on disk, compressed into the
installer). The shell resolves `python/bin/python3` (or `python\python.exe`)
under the resource directory and runs that, so no Python is needed on the user's
machine.

The fetch is the release build's job — `scripts/fetch_python.py`, run by the
`Desktop` workflow — never `cargo tauri dev`. A development build carries the
empty `python/` directory (its `.gitkeep`) and falls through to your `.venv`.

## 3. Where config and work live

Per user, not per working directory (`paths.py`, via `platformdirs`), because an
app launched from a menu has no meaningful cwd:

| | Path |
|---|---|
| Linux | `~/.config/SourceWork/`, data in `~/.local/share/SourceWork/` |
| macOS | `~/Library/Application Support/SourceWork/` |
| Windows | `%APPDATA%\SourceWork\` |

A `.env` in the working directory still wins, so a developer checkout and a
launcher-started app coexist on one machine without fighting over the same
database.

## 4. Building it

Prebuilt, unsigned installers for Linux (`.deb`, `.AppImage`), Windows (`.msi`,
`-setup.exe`) and macOS (`.dmg`) are attached to every release by the `Desktop`
workflow (`.github/workflows/desktop.yml`); the README names them. They carry
their own Python runtime. The rest of this section is for building from source.

The shell lives in `desktop/`. Rust only — no Node.

```bash
# once: Rust and the Linux webview/appindicator development libraries
rustup default stable
sudo apt install libwebkit2gtk-4.1-dev build-essential curl wget file \
  libxdo-dev libssl-dev libayatana-appindicator3-dev librsvg2-dev

cd desktop/src-tauri
cargo tauri build          # bundles for the current OS
cargo tauri dev            # a window against your checkout's backend
```

Tauri produces the formats the current platform supports: `deb`/`AppImage` on
Linux, `msi`/`nsis` on Windows, `dmg`/`app` on macOS. The bundles are **not
code-signed** for now, so Windows SmartScreen and macOS Gatekeeper will warn on
first open.

`cargo tauri build` bundles whatever is in `desktop/src-tauri/python/`. To make a
self-contained installer locally, populate it first:

```bash
python scripts/fetch_python.py --dest desktop/src-tauri/python --install .
```

Without that step the bundle carries an empty `python/` and the shell falls back
to the machine's Python — which is exactly what `cargo tauri dev` does, and why
development never needs the fetch.

## 5. Licensing

Tauri, `tauri-plugin-notification`, `tauri-plugin-single-instance` and
`notify-rust` are MIT/Apache-2.0 — compatible with this project's MIT licence
and recorded in `THIRD_PARTY.md`. On Linux the OS webview (WebKitGTK) and the
tray's AppIndicator library are **LGPL system libraries** the shell links
against but does not redistribute; a *bundled* build would make that a relink
obligation, which is why the shell depends on the system's copies.

The installers embed a relocatable **CPython** built by
[python-build-standalone](https://github.com/astral-sh/python-build-standalone)
(PSF-2.0), together with SourceWork's own Python dependencies. That **is**
redistribution, which is why `THIRD_PARTY.md` records those terms, and why a
self-contained build has to reproduce the BSD/Apache notices it carries.

This is the one place the earlier design note changed. That note chose a
browser tab to avoid a shell dependency at all; the trade became worth making
once "the app should be an app" was the goal — a window is what lets the
process have a visible lifecycle, which a browser tab cannot give it.

## 6. Finding an inference server

Unchanged, and still the part that decides whether a first-time user succeeds.
The backend probe (`sourcework.backends.probe`) tries each configured backend in
order and takes the first that answers; `sourcework doctor` prints what it
found.

| Order | Where | How to detect |
|---|---|---|
| 1 | Configured `SOURCEWORK_LLM__API_BASE` | `GET {base}/models` |
| 2 | llama-swap / llama-server on `:8081` | `GET /v1/models` |
| 3 | LM Studio on `:1234` | `GET /v1/models` |
| 4 | Ollama on `:11434` | `GET /api/tags` |
| 5 | A hosted provider | an API key present in config |

Each probe is a 600 ms timeout against loopback, and no attempt is made to
identify a product beyond the port it answers on. Ollama is asked at
`/api/tags` rather than `/v1/models`: it is configured at `/v1` but lists its
models elsewhere, and blindly appending `/v1/models` to a base that already ends
in `/v1` produces a 404 that reads like "nothing is running".

llama.cpp is detected, never bundled. Shipping GPU builds would mean a matrix of
Metal, CUDA, ROCm and Vulkan across three platforms, each with its own driver
failures — and this tool is useful the moment it finds the server you already
run.
