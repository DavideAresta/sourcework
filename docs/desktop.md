# SourceWork as a desktop app

`sourcework app` runs the whole mesh and the web UI in one process; on Linux,
`sourcework install-desktop-entry` puts it in the application launcher with an
icon. That is the entire desktop story: one Python process you can start from a
menu. No Electron, no webview, no second language, no packaged runtime.

It can be that small because the pieces were already the right shape — the front
end is plain ES modules with no build step, and `serve-all` already runs all
eight agents as threads in one process. This page records what the launcher
does, and why it is not the other things it could have been.

---

## 1. The shell: no browser wrapper, and no tray either

| | Ships | Costs |
|---|---|---|
| **Launcher only** (what exists) | ~300 MB Python, no UI runtime at all | The interface is a browser tab, not a window with your name on it |
| Tauri window | + ~10 MB shell | A Rust toolchain and an IPC layer to maintain |
| Electron | + ~150 MB Chromium | A second browser, bundled to render HTML the first one already renders |

Electron is the heaviest option and buys a title bar. The front end is plain ES
modules with no build step, so there is nothing a renderer process would do that
loopback does not.

**A tray icon was in the first draft and was removed.** Its three jobs are
covered without a GUI toolkit:

| Tray job | What does it instead |
|---|---|
| "Is it running / let me back in" | A second `sourcework app` re-opens the browser rather than starting a rival |
| "My run finished while I was away" | A browser notification from the page — which names the document and says whether it worked, where an icon turning amber says only "something happened" |
| "Quit" | A control in the interface, against an endpoint that exists only in app mode |

The deciding argument was licensing. No permissive cross-platform tray library
exists for Python: `pystray` is LGPL-3.0 and pulls LGPL `python-xlib` on Linux,
and the permissive alternatives are per-platform (`rumps` for macOS,
`infi.systray` for Windows) with nothing for Linux. Importing LGPL is fine;
bundling it in a distributable binary is a relink obligation. For a project
whose whole argument is that you can audit what it does, carrying that for a
coloured dot was the wrong trade.

## 2. What the launcher does

```
sourcework app
  ├─ already answering on 8080? → open the browser, exit            (no rival instance)
  ├─ resolve config dir                                             (per-user, not cwd)
  ├─ start the mesh: 8 agents, threads, loopback only
  ├─ start the UI, handing it a shutdown callback
  ├─ wait for /healthz, print status, open the browser
  └─ block until Quit or Ctrl-C
```

The single-instance check asks `/healthz` for the service name rather than
trying to bind: a second launch should raise what someone already has, and an
unrelated process on 8080 must not be mistaken for us.

Status is printed once, flushed — a launcher's stdout is never a terminal, and
buffered output means silence until exit. After that the interface carries its
own state, which is where there is room to explain it.

**Config and workspace live per user, not per working directory** (`paths.py`,
via `platformdirs`), because an app launched from a menu has no meaningful cwd:

| | Path |
|---|---|
| Linux | `~/.config/sourcework/`, data in `~/.local/share/sourcework/` |
| macOS | `~/Library/Application Support/SourceWork/` |
| Windows | `%APPDATA%\SourceWork\` |

A `.env` in the working directory still wins, so a developer checkout and a
launcher-started app coexist on one machine without fighting over the same
database.

## 3. Finding an inference server

This is the part that decides whether a first-time user succeeds. The
backend probe (`sourcework.backends.probe`) tries each configured backend in
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
