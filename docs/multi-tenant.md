# Running SourceWork as a multi-tenant service

> **Considered and not taken (August 2026).** SourceWork is a local application
> instead — see [desktop.md](desktop.md). Nearly everything below is a list of
> things to *undo* about the current design: Postgres for SQLite, object storage
> for local disk, a job queue for in-process tasks, identity where there is none.
> Running locally keeps all of those as correct choices, and it makes the
> local-model work the product rather than dead weight, because the documents
> never leave the machine.
>
> Kept because "why isn't this a SaaS?" deserves a real answer, and because the
> schema and phasing here are still right if the decision ever reverses.

A design plan, not a decision. It says what would have to change, in what
order, and which choices are yours rather than the architecture's.

The short version: the pipeline is ready for this and the UI service is not.
Roughly 5,700 lines across `agents/`, `backends/`, `ingest/` and `render/` never
learn that tenants exist. The work is concentrated in `ui/` (1,487 lines),
`config.py`, and one gap in the Confluence agent.

---

## 1. What already works in your favour

**The agents are stateless services with a published contract.** Eight of the
nine processes take a request, do work, and return. They hold no session, no
user, no cursor. That is the expensive property to retrofit and it is already
there.

**Per-run model configuration already travels the whole mesh.** `AgentPool`
attaches `LLMOverrides` to every outbound payload (`a2a_common/client.py`), the
executor reads it back (`_read_overrides`), and `llm_overrides()` applies it as
a context variable for the duration of that request. That is exactly the shape
per-tenant model routing needs, and it exists because per-run overrides were
wanted for a different reason. Tenant A on Claude and tenant B on a local model,
concurrently, in the same worker, needs no new mechanism.

**The evidence model is tenant-neutral.** Evidence ids, citation validation,
`derived`, the traceability matrix — none of it touches identity.

**Config is already an allow-list.** `env_file.FIELDS` enumerates every setting
that may be written, with type, group and help text. Per-tenant settings become
that same list stored per tenant rather than per file. The hard part — deciding
what is safe to expose — is done.

---

## 2. What has to change

### 2.1 Identity and tenancy — the thing that does not exist

There is no user, tenant, session or account anywhere in `src/`, and the `runs`
table has no owner column. `GET /api/runs` returns every run ever created. This
is the blocker, and everything else is downstream of it.

**Recommendation: one Postgres database, `tenant_id` on every row, enforced by
Postgres row-level security rather than by discipline.**

The failure mode of application-level scoping is a single forgotten `WHERE
tenant_id = ?`, and the consequence is one customer reading another's uploaded
documents. That is not a bug you get to fix quietly. With RLS the database
refuses to return the rows even when the query forgets, so a mistake becomes an
empty result instead of a breach:

```sql
ALTER TABLE runs ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON runs
  USING (tenant_id = current_setting('app.tenant_id')::uuid);
```

The application sets `app.tenant_id` once per connection checkout, from the
validated token. Schema-per-tenant and database-per-tenant are the alternatives;
both make migrations and cross-tenant analytics painful, and neither is
justified until a customer contractually demands physical separation.

**Do not build authentication.** Use an OIDC provider (WorkOS, Auth0, Clerk,
Cognito). The UI service validates a JWT and maps it to `(tenant_id, user_id)`.
Everything else reads those two values.

### 2.2 Storage

SQLite on local disk, one connection behind a `threading.Lock` (`ui/store.py`).
Single-process by construction, and on most platforms the filesystem is
ephemeral, so a redeploy discards every run.

Postgres, with the existing shape mostly intact — the current table already
stores `request`, `result` and `events` as JSON blobs, which survives as `jsonb`:

```sql
CREATE TABLE tenants (
    id           uuid PRIMARY KEY,
    name         text NOT NULL,
    created_at   timestamptz NOT NULL DEFAULT now(),
    plan         text NOT NULL DEFAULT 'trial'
);

CREATE TABLE users (
    id           uuid PRIMARY KEY,
    tenant_id    uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    external_id  text NOT NULL,              -- the OIDC subject
    email        citext NOT NULL,
    role         text NOT NULL DEFAULT 'member',
    UNIQUE (external_id)
);

CREATE TABLE runs (
    id           uuid PRIMARY KEY,
    tenant_id    uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    created_by   uuid REFERENCES users(id),
    parent_id    uuid REFERENCES runs(id),   -- refinement chains, as today
    created_at   timestamptz NOT NULL DEFAULT now(),
    finished_at  timestamptz,
    title        text NOT NULL,
    status       text NOT NULL,
    request      jsonb NOT NULL,
    result       jsonb,
    error        text,
    usage        jsonb
);
CREATE INDEX ON runs (tenant_id, created_at DESC);

-- Events leave the run row. They are append-only, they are what the live view
-- streams, and keeping them in a JSON column means rewriting the whole run on
-- every progress message.
CREATE TABLE run_events (
    id           bigserial PRIMARY KEY,
    run_id       uuid NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    tenant_id    uuid NOT NULL,
    at           timestamptz NOT NULL DEFAULT now(),
    kind         text NOT NULL,
    payload      jsonb NOT NULL
);
CREATE INDEX ON run_events (run_id, id);

CREATE TABLE tenant_settings (
    tenant_id    uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    key          text NOT NULL,              -- from env_file.FIELDS
    value        text,
    secret       bytea,                      -- envelope-encrypted, never both
    updated_at   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, key)
);
```

`readiness.chains()` currently reconstructs refinement lineage by walking
`parent_id` in Python over every run. That is fine at laptop scale and is a
recursive CTE here.

### 2.3 Uploads

Uploads are written to `workspace/uploads/<run_id>/` and handed to the agents as
`file://` URIs over a shared read-only mount. In the cloud that means a network
filesystem, which is the expensive way to solve it.

**Object storage with presigned GET URLs.** The UI writes to S3/GCS and passes
the agents a time-limited HTTPS URL.

One interaction worth knowing before you design around it: ingestion now refuses
to fetch private, loopback and link-local addresses, checked on every redirect
hop, because that refusal is what stops SSRF. Presigned URLs on the provider's
public endpoint pass that check. An internal-only object gateway would not, and
"just turn on `ALLOW_PRIVATE_FETCH`" gives back the protection that matters most
in a multi-tenant environment. Prefer the presigned public URL.

### 2.4 Job execution

Runs execute in `asyncio.create_task` after the POST returns (`ui/runner.py`),
and live subscribers are an in-memory dict. Three consequences: a platform that
throttles CPU after the response stalls the run; a restart kills in-flight work;
and with two replicas a browser watching a run usually connects to an instance
that is not executing it.

**Split the API from the worker.**

- API enqueues `(run_id, tenant_id)` and returns. It never executes a run.
- Workers consume the queue, run the pipeline, write events to `run_events`.
- Live updates: workers publish to Redis pub/sub keyed by run; any API replica
  subscribes and streams SSE. This is what makes horizontal scaling work.
- Cancellation becomes a flag the worker checks between agent calls rather than
  `task.cancel()`.
- Concurrency: the semaphore added for local models becomes per-worker
  concurrency plus a per-tenant limit, so one tenant cannot starve the rest.

`reap_orphans()` already marks interrupted runs as failed on start-up — that
honesty carries over, but with a queue most interruptions become a retry
instead.

### 2.5 Configuration and credentials — the biggest decision

The settings page edits `.env` on disk. In a multi-tenant service that concept
does not survive: there is one file and many tenants, and a settings endpoint
that rewrites the process's own environment is a privilege escalation.

Per-tenant settings move into `tenant_settings`, reusing the `FIELDS`
allow-list. The real question is model credentials, and it is a product decision
with security consequences:

| Option | What it means | Cost |
|---|---|---|
| **Platform keys** | You hold provider credentials; tenants never see them. Simplest for the customer, and you carry the spend. | Needs hard per-tenant quotas before launch, or one tenant's 500-page PDF is your bill. |
| **Bring your own key** | Tenant stores their provider key, encrypted. Their spend, their rate limits. | You are now custodian of customer API keys: envelope encryption with KMS, no plaintext in logs, a believable answer to "what happens if you are breached". |

**Recommendation: platform keys with quotas at launch, BYO as an enterprise
option later.** Fewer secrets to hold is a real security posture, and quotas you
need anyway.

Either way, **do not pass tenant credentials through eight services.** Put a
LiteLLM proxy (or equivalent gateway) in front, keyed per tenant; the agents
receive a gateway token scoped to the run and never the underlying key. The
existing `LLMOverrides` payload carries the model choice; the gateway carries
the credential.

### 2.6 The Confluence gap

Every other per-run setting travels in the request. Confluence does not: the
agent reads `settings().confluence` directly (`agents/confluence_agent/agent.py`),
so the whole deployment shares one site, one account and one token. In a
multi-tenant service each tenant has their own Confluence, and publishing to the
wrong customer's wiki is about the worst failure this product could have.

This needs the same treatment `LLMOverrides` already has — per-request
credentials resolved by the worker from `tenant_settings`, passed in the payload
(or, better, a short-lived token minted per run). It is a small change and it is
load-bearing; do not leave it for later.

---

## 3. Phasing

Each phase ships something and leaves the tree working. Phase 0 is the one that
de-risks the rest.

**Phase 0 — a repository layer, still single-tenant.** Extract every database
access behind a `RunRepository`, and thread a `tenant_id` through it with a
fixed default. No behaviour changes, the existing tests keep passing, and the
diff is mechanical. This is where you find every place that assumes global
state, cheaply, before any of it is load-bearing.

**Phase 1 — Postgres and RLS.** Swap the driver, add the schema above, turn on
row-level security with the default tenant. Still one tenant, now on
infrastructure that supports more. Add a test that a query without the tenant
context returns nothing — that test is the safety net for every later phase.

**Phase 2 — object storage.** Uploads to S3/GCS, presigned URLs to the agents.
Removes the shared-filesystem requirement, which is what unblocks a normal
container platform.

**Phase 3 — queue and workers.** API enqueues, workers execute, Redis pub/sub
for events. After this the service scales horizontally and survives a restart.

**Phase 4 — identity and per-tenant settings.** OIDC, real tenants, settings out
of `.env`, the Confluence gap closed. This is where the product becomes
multi-tenant; everything before it was making that possible.

**Phase 5 — quotas, billing, retention.** Per-tenant token and concurrency
limits from the existing usage ledger, Stripe, a deletion path that actually
removes uploads and evidence, and an export.

Phases 0–3 are infrastructure and could be done by one person with the tests as
a guide. Phase 4 is where product decisions bind. Phase 5 is what makes it
sellable rather than demonstrable.

---

## 4. Buy, don't build

Authentication, billing, the queue, Postgres, object storage, secret management.
None of them differentiate this product, and each is a class of bug you do not
want to own. What differentiates it is the evidence-and-citation model, and that
part is already written.

---

## 5. What will bite

**Customer documents are the most sensitive artifact in the system.** The store
holds full source text of everything uploaded — the `.gitignore` says so today.
Encryption at rest, a real deletion path, and a retention policy are table
stakes the moment someone else's documents are in your database.

**Prompt injection has a bigger blast radius here.** A document is untrusted
input, and in a service it is untrusted input from someone else's customer. The
citation rule bounds fabrication structurally, which is a genuine defence — but
it does not stop a document instructing a model to write something plausible and
wrong, and the review pass now matters more than it did on a laptop.

**Long runs meet HTTP timeouts.** A run is minutes. SSE through a load balancer
needs idle timeouts raised and buffering disabled; several platforms make this
annoying and one or two make it impossible.

**Cost is unbounded per request.** A large PDF pack is a large token bill. The
usage ledger records per-run spend already; enforcement has to come before
launch, not after the first surprising invoice.

**Local models do not come with you.** The llama.cpp path assumes a GPU on the
box. Cloud GPU is expensive and idles badly. Multi-tenant almost certainly means
hosted APIs — which the backend layer supports as a config change, so this is a
cost decision rather than an engineering one.

---

## 6. Decisions needed before Phase 4

1. **Platform keys or bring-your-own?** Changes what you must protect.
2. **Who is a tenant** — a company, or a person? Determines whether you need
   invitations, roles and seat billing, or just an account.
3. **Data retention.** How long do you keep uploaded documents, and can a
   customer demand deletion? Answer before storing anything, not after.
4. **Is Confluence publishing per tenant on day one?** If yes, section 2.6 is
   Phase 4 work rather than Phase 5.
5. **Self-hosting.** The MIT licence means anyone can run this themselves. If
   self-hosting stays first-class, every change above has to keep the
   single-tenant path working — which the phasing does, but it is a constraint
   worth accepting deliberately rather than discovering.
