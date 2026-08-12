# Third-party code and tools

SourceWork is MIT licensed (see [LICENSE](LICENSE)). This file records what else
is in the tree or expected on the machine, and under what terms.

## Bundled in this repository

One file, vendored verbatim with its copyright header intact:

| File | Project | Version | Licence |
|---|---|---|---|
| `src/sourcework/ui/static/js/vendor/autocomplete.js` | [autocomplete](https://github.com/denis-taran/autocomplete) by Denis Taran | 10.0.0 | MIT, © 2016 Denis Taran |

It is vendored rather than fetched from a CDN because the UI has to work on a
machine with no internet, and rather than npm-installed because the front end
has no build step. The file carries its own licence header; keep it there.

## Optional: the desktop tray

`pip install sourcework[app]` adds the menu-bar icon, and it is an extra rather
than a dependency for a licensing reason:

| Package | Licence |
|---|---|
| [pystray](https://github.com/moses-palmer/pystray) | LGPL-3.0 |
| python-xlib (pystray's X11 backend, Linux) | LGPL-2.1+ |

Importing an LGPL library from an MIT project is fine. **Bundling one into a
distributable binary is a relink obligation** — users must be able to replace
that component. PyInstaller's `onedir` layout keeps these as separate importable
files, which satisfies it; a single-file build would not, without extra work.

Keeping it optional means the default install is wholly permissive and the
obligation is accepted by whoever packages, rather than inherited by everyone
who installs. `sourcework app` runs without it — no icon, Ctrl-C to stop.

## Python dependencies

Declared in `pyproject.toml`. Every one is permissive and compatible with MIT
redistribution — no GPL, LGPL, AGPL or SSPL anywhere in the resolved tree:

| Licence | Packages |
|---|---|
| MIT | fastapi, pydantic, pydantic-settings, litellm, python-docx, python-pptx, openpyxl, beautifulsoup4, pytest, ruff |
| BSD-3-Clause | uvicorn, httpx, python-dotenv, pypdf, lxml |
| Apache-2.0 | a2a-sdk, tenacity, python-multipart |

Re-check with `pip-licenses` (or the audit in `CONTRIBUTING.md`) before adding a
dependency. A copyleft dependency would change what this project can be.

## External tools, not distributed here

The local-model path drives programs you install yourself. They are never
bundled or redistributed, and SourceWork only speaks HTTP to them:

- **[llama.cpp](https://github.com/ggml-org/llama.cpp)** — MIT. `llama-server`
  provides the OpenAI-compatible endpoint, grammar-constrained JSON, and the
  Hugging Face downloader behind `llama-models.py add`.
- **[llama-swap](https://github.com/mostlygeek/llama-swap)** — MIT. Serves
  several models behind one endpoint so roles can differ.

## Model weights

Not covered by this licence at all. Every model carries its own terms — Gemma,
Qwen, GPT-OSS and the rest each have their own licence and acceptable-use
policy, and some restrict commercial use or redistribution. Check the licence
of any model before you rely on it, and again before you ship what it produced.
