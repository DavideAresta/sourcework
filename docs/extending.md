# Extending SourceWork

Three things can be supplied from outside the repository, without editing it.
Each is an [entry point](https://packaging.python.org/en/latest/specifications/entry-points/)
in a package installed beside SourceWork.

They exist because the alternative is a fork, and a fork of a project that
changes weekly is a fork nobody maintains.

| Group | Supplies | Core ships |
|---|---|---|
| `sourcework.publishers` | somewhere to send a finished PRD | Confluence |
| `sourcework.auth` | who is asking | nobody — a single local operator |
| *(constructor argument)* | where runs are kept | SQLite, one file |

## Publishers

Publishing is an A2A call to an agent that advertises `publish_prd`, so a new
destination is a new agent — the same thing every input modality already is. The
entry point only has to *name* it:

```python
# sourcework_jira/__init__.py
from sourcework.publishers import PublishTarget

TARGET = PublishTarget(id="jira", agent="jira", skill="publish_prd", label="Jira")
```

```toml
[project.entry-points."sourcework.publishers"]
jira = "sourcework_jira:TARGET"
```

Your agent receives a `PublishRequest` carrying the title, the Confluence
storage XHTML, the **markdown**, and a string `options` map from
`PRDRequest.publish_options`. Use the markdown unless you speak Confluence
storage format; both travel so you never have to ask the writer to run again.

A value may also be a list, so one package can front two systems.

**Rules, and why.** A plugin cannot claim an id that core already ships —
shadowing `confluence` would let an installed package silently redirect
published documents, and there is no reading of that which is a feature. A
plugin that fails to import, or returns something that is not a `PublishTarget`,
is logged and skipped: a run that got as far as having a document is not one to
throw away over a destination.

## Authentication

Core binds to loopback and serves one operator, so it ships no sign-in. What it
does own is **enforcement**: one middleware resolves a principal for every
request and refuses when there isn't one. Adding a route cannot open a hole,
which a per-route dependency cannot promise.

```python
from sourcework.auth import Principal

class OidcAuth:
    id = "oidc"

    async def principal(self, request) -> Principal | None:
        token = request.cookies.get("session")
        claims = verify(token)          # yours
        if claims is None:
            return None                 # None means refuse, not "carry on anonymously"
        return Principal(
            id=claims["sub"], name=claims.get("name", ""),
            email=claims.get("email", ""), roles=frozenset(claims.get("roles", [])),
        )

    def challenge(self) -> dict[str, str]:
        return {"Location": "/oidc/login"}   # sent with the 401
```

```toml
[project.entry-points."sourcework.auth"]
oidc = "sourcework_oidc:OidcAuth"
```

`/healthz` and `/` stay open — a load balancer has no credentials, and the page
that would let somebody sign in cannot itself require having signed in.
Everything the shell then fetches is guarded, so an unauthenticated visitor gets
the frame and no data.

**Rules, and why.** Exactly one authenticator may be installed; two raises at
start-up. The unsafe reading of an ambiguous configuration is "carry on
unauthenticated", which is precisely where an open UI is least expected and most
damaging. A plugin that will not load raises for the same reason — the opposite
of the publisher rule, because one of those costs a destination and this one
costs the lock on the door.

`GET /api/me` reports the current principal and which authenticator answered, so
a front end can render a name and a sign-out control when one is installed and
nothing when one is not.

## The run store

`sourcework.ui.store.Store` is a six-method protocol — `save`, `get`, `list`,
`delete`, `reap_orphans`, `close` — taken from what the UI actually calls. Pass
an implementation to `build_app(store=...)`:

```python
from sourcework.ui.app import build_app

app = build_app(store=PostgresStore(dsn))
```

It is a structural protocol, so nothing needs to inherit from anything.

**Retention is an optional seventh method.** If a store defines
`purge_older_than(days) -> list[str]`, the UI calls it at start-up whenever
`SOURCEWORK_RUNS__RETENTION_DAYS` is set, and erases the checkpoints of every id
it returns. It is deliberately off the protocol: a store backed by something
other than a clock may have no notion of "old", and a protocol wider than its
use is a promise every implementation has to keep. A store without it is not
broken — retention is simply announced as unavailable in the log rather than
silently doing nothing.

**Deliberately no notion of an owner.** SQLite with one writer and no user
column is the right shape for a single-user application, and speculatively
adding a tenancy column to core would be carrying a schema nothing reads. An
installation where runs belong to different people brings its own store; the day
core grows a principal on the run record is the day to widen the protocol, not
before.
