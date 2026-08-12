# Security

## Reporting a vulnerability

Please report privately rather than in a public issue: open a
[confidential issue](https://gitlab.com/LudusFaber/prd-forge/-/issues/new)
(tick **This issue is confidential**). Expect an acknowledgement within a week.

## What this software is, security-wise

PRD Forge is a **local, single-operator tool**. It assumes the person running it
owns the machine, the documents and the credentials. Nothing in it is built to
survive a hostile user on the same host or network, and it should not be exposed
to one. Three specifics are worth stating plainly.

### The UI has no authentication

There are no accounts, sessions or passwords. Two endpoints matter more than the
rest of the system:

- `GET/POST /api/settings` reads and **rewrites `.env`** — provider API keys and
  the Confluence token included. Secrets are masked on the way out and an
  unchanged mask is ignored on the way in, so opening the page cannot leak a
  token; but anyone who can reach the port can *replace* one.
- `GET /api/runs/...` returns the **full source text** of every ingested
  document, every evidence quote and every PRD. On most installs this is the
  most sensitive data in the tree.

The UI therefore binds `127.0.0.1` by default. `--host`/`PRDFORGE_UI_HOST` will
bind wider and logs a warning when you do. If you need it reachable from another
machine, put a reverse proxy that authenticates in front of it — do not expose
it directly.

### The agent mesh ships with a shared default secret

Every agent declares an `apiKey` scheme, but `PRDFORGE_SECURITY__ENFORCE`
defaults to `0` and `PRDFORGE_SECURITY__API_KEY` defaults to the publicly known
`dev-local-shared-secret`. That combination is convenient on a laptop and wrong
anywhere else. Before running the mesh on a shared network:

```bash
PRDFORGE_SECURITY__ENFORCE=1
PRDFORGE_SECURITY__API_KEY=<something you generated, not this>
```

The agents bind `0.0.0.0` inside `docker-compose` because they must reach each
other. Do not publish those ports beyond the compose network.

### Model output is untrusted input

The pipeline reads documents, transcripts and images you supply, and a model
reads them too. A source document can contain text addressed at the model
("ignore your instructions and…"). PRD Forge limits the damage structurally
rather than by asking the model nicely: requirements must cite evidence ids that
are **validated in code**, an invented id is dropped, and an uncited requirement
is forced to render as `derived`. That bounds fabrication; it is not a defence
against a determined prompt injection. Review PRDs built from sources you do not
trust, and be deliberate about `--publish`, which writes to Confluence.

## Supported versions

The project is pre-1.0. Fixes land on `main`; there are no backported branches.
