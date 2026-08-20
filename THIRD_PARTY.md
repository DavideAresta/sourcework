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

SourceWork drives programs you install yourself — with one convenience
exception, `sourcework install-llama-swap`, which downloads a pinned llama-swap
release from upstream on request and verifies it. None of them are bundled,
vendored or redistributed, so **none of them affect SourceWork's own licence**.
They matter to you for a different reason, set out under *What actually binds
you* below.

### Inference servers

Spoken to over HTTP, never invoked as code:

- **[llama.cpp](https://github.com/ggml-org/llama.cpp)** — MIT. `llama-server`
  provides the OpenAI-compatible endpoint, grammar-constrained JSON, and the
  Hugging Face downloader behind `llama-models.py add`.
- **[llama-swap](https://github.com/mostlygeek/llama-swap)** — MIT. Serves
  several models behind one endpoint so roles can differ. The one tool
  `sourcework install-llama-swap` will fetch for you: a pinned release, its
  SHA-256 checked against the checksums file published beside it, unpacked into
  `~/.local/bin`. Still not redistributed — the bytes come from the upstream
  release page at the moment you ask, and nothing here fetches them on its own.

### Coding CLIs used as generation backends

Each is launched as a subprocess and handed a prompt on argv or stdin
(`src/sourcework/backends/`). SourceWork ships no part of them and stores no
credential for them — each carries its own sign-in, which is the point: signed
into one of these, the whole pipeline runs on that account with no key plumbed
through this project.

| Backend id | Tool | Vendor | Needs |
|---|---|---|---|
| `claude-code` | [Claude Code](https://claude.com/claude-code) | Anthropic | a Claude account or API credentials |
| `opencode-cli` | [OpenCode](https://opencode.ai) | SST | whatever providers you configured in it |
| `copilot-cli` | [GitHub Copilot CLI](https://github.com/features/copilot) | GitHub / Microsoft | a Copilot subscription |
| `codex-cli` | [Codex CLI](https://openai.com/codex) | OpenAI | a ChatGPT plan or API credentials |
| `agy-cli` | Antigravity CLI (`agy`) | Google | its own sign-in |

Licence terms differ between them and move between releases: some ship an
open-source client in front of a proprietary service, others are proprietary
throughout. **Check the licence of whichever you install** — this table records
what SourceWork talks to, not a legal opinion about any of it.

## What actually binds you

Two obligations that are easy to miss because they are not about *this*
project's licence:

**The tool's own terms govern what you may do with its output.** A coding
subscription is sold for a purpose, and a vendor may restrict automated or
non-interactive use, resale, or using the output to train a competing model.
SourceWork drives these tools non-interactively by design. Read the terms of the
one you use before putting its output in a document you ship to a customer.

**Redistribution obligations start when you package a binary.** Today the
dependency tree is installed by your own `pip`, so nothing here redistributes
anything and MIT asks nothing further. A self-contained build — PyInstaller, a
container image you publish — *is* redistribution, and the permissive licences
in that tree stop being free of paperwork: BSD-3 requires reproducing its
copyright notice in binary form, and Apache-2.0 §4 requires retaining
attribution and passing along any `NOTICE` files. Generate a licence manifest
into the bundle before shipping one.

## Model weights

Not covered by this licence at all. Every model carries its own terms — Gemma,
Qwen, GPT-OSS and the rest each have their own licence and acceptable-use
policy, and some restrict commercial use or redistribution. Check the licence
of any model before you rely on it, and again before you ship what it produced.
