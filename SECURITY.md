# Security

## Reporting a vulnerability

Please report privately rather than in a public issue: use GitHub's
[private vulnerability reporting](https://github.com/DavideAresta/sourcework/security/advisories/new),
which opens a draft advisory only you and the maintainer can read. Expect an
acknowledgement within a week.

## What this software is, security-wise

SourceWork is a **local, single-operator tool**. It assumes the person running it
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

The UI therefore binds `127.0.0.1` by default. `--host`/`SOURCEWORK_UI_HOST` will
bind wider and logs a warning when you do. If you need it reachable from another
machine, put a reverse proxy that authenticates in front of it — do not expose
it directly.

Two things reduce what an unauthenticated caller can do even on loopback:

- **Writes require the `X-SourceWork-UI: 1` header.** Not a secret — its only job
  is to be something a cross-site form cannot set, which forces a preflight this
  app does not answer. Without it, any page you visited could start runs on your
  machine with URIs and Confluence publishing targets of its choosing. Reads are
  unaffected; the same-origin policy already covers those.
- **The URI field takes `http(s)` and `confluence://` only.** It used to accept
  `file:///` and bare paths, which made it an arbitrary-file-read: a single
  request returned `.env` — provider keys included — as quotable evidence.
  Local files reach a run through the upload field instead. The CLI still
  accepts local paths, because there the operator is naming a file on their own
  machine.

**Ingestion refuses private network targets.** `http(s)` fetches resolve the
host and reject loopback, link-local, private and reserved addresses, checked
again on every redirect hop — `169.254.169.254` is cloud credentials, and a
public host answering `302 → 127.0.0.1` is the standard way past a check that
only looks at the first URL. `SOURCEWORK_SECURITY__ALLOW_PRIVATE_FETCH=1` exists
for the deployment whose document store really is internal.

### The agent mesh ships with a shared default secret

Every agent declares an `apiKey` scheme, but `SOURCEWORK_SECURITY__ENFORCE`
defaults to `0` and `SOURCEWORK_SECURITY__API_KEY` defaults to the publicly known
`dev-local-shared-secret`. That combination is convenient on a laptop and wrong
anywhere else. Before running the mesh on a shared network:

```bash
SOURCEWORK_SECURITY__ENFORCE=1
SOURCEWORK_SECURITY__API_KEY=$(python -c 'import secrets; print(secrets.token_urlsafe(32))')
```

Enforcing while leaving the default in place now refuses to start. Enforcement
with a secret published in this repository is worse than none: it reads as
authentication in a config review and stops nobody.

The agents bind `0.0.0.0` inside `docker-compose` because they must reach each
other. Do not publish those ports beyond the compose network.

### Model output is untrusted input

The pipeline reads documents, transcripts and images you supply, and a model
reads them too. A source document can contain text addressed at the model
("ignore your instructions and…"). SourceWork limits the damage structurally
rather than by asking the model nicely: requirements must cite evidence ids that
are **validated in code**, an invented id is dropped, and an uncited requirement
is forced to render as `derived`. That bounds fabrication; it is not a defence
against a determined prompt injection. Review PRDs built from sources you do not
trust, and be deliberate about `--publish`, which writes to Confluence.

## Supported versions

The project is pre-1.0. Fixes land on `main`; there are no backported branches.
