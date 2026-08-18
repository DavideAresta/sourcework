"""The web UI, as a ninth service.

Deliberately *not* mounted on the orchestrator. The orchestrator is an A2A
agent - one protocol, one contract, drivable by anything that speaks it - and
bolting a browser app onto it would make it two things. This service is just
another A2A client, the same as the CLI, which also means it can be run,
restarted or left out entirely without the mesh noticing.

Uploads are written to the shared workspace and referenced as ``file://`` URIs,
because that is the path the agents already have (compose mounts
``./workspace`` into ingestion, vision and transcript). The alternative -
inlining bytes as base64 in the request - avoids the shared volume but puts a
20 MB PDF through JSON, which is a worse trade for the common case.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    Response,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ValidationError

# Starlette's, NOT fastapi.UploadFile. Reading the form directly (rather than
# declaring it as a parameter) yields Starlette's class, and FastAPI's is a
# *subclass* of it - so an isinstance check against FastAPI's silently drops
# every uploaded file and the run proceeds with no sources.
from starlette.datastructures import UploadFile

from sourcework import __version__, audit, auth, checkpoint, readiness
from sourcework.a2a_common import AgentPool
from sourcework.backends import probe
from sourcework.config import LLMOverrides, settings
from sourcework.models import InputRef, PRDBaseline, PRDRequest
from sourcework.ui import env_file
from sourcework.ui.runner import RunExecutor, RunManager
from sourcework.ui.store import RunStore, Store, new_run_id, now_iso

logger = logging.getLogger(__name__)

PORT = 8080
STATIC = Path(__file__).parent / "static"

MAX_UPLOAD_BYTES = 64 * 1024 * 1024
"""Matches the ingest layer's own ceiling - rejecting here gives a clear error
instead of one buried in a failed extraction."""

Authorizer = Callable[[Request, auth.Principal, str], Awaitable[bool]]
"""The second gate: having a principal is one thing, being *allowed* another.

Core's middleware resolves who is asking and refuses when nobody is. What a
particular installation then lets that person do is not something core can
know - it depends on who the tenants are and which roles mean what. So it is
a supplied policy ``(request, principal, method) -> bool``, called after the
principal is resolved, and a False answer is a 403. The local distribution
passes nothing and the gate is open, exactly matching :class:`NullAuth`'s
"you are the person at this machine"."""


class UIPaths:
    """Where the UI keeps its state. All of it under one directory."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.uploads = root / "uploads"
        self.db = root / "sourcework-ui.db"
        self.uploads.mkdir(parents=True, exist_ok=True)


class NewRun(BaseModel):
    """The run form, as posted alongside any uploaded files."""

    title: str
    notes: list[str] = Field(default_factory=list)
    uris: list[str] = Field(default_factory=list)
    confluence_queries: list[str] = Field(default_factory=list)
    audience: str = "engineering and product"
    template: str = "standard"
    review_rounds: int = 1
    extra_instructions: str | None = None
    publish: bool = False
    confluence_space_key: str | None = None
    confluence_parent_id: str | None = None
    estimate: bool = False
    llm: LLMOverrides | None = None


class QuestionAnswer(BaseModel):
    question: str
    answer: str


class ApprovalUpdate(BaseModel):
    """The sign-off form: a state, who is deciding, and optionally why."""

    state: str  # approved | rejected | draft
    by: str = ""
    note: str = ""


class RefineRun(BaseModel):
    """What the Refine panel posts: answers, additions, and new material."""

    title: str | None = None
    answers: list[QuestionAnswer] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    uris: list[str] = Field(default_factory=list)
    template: str | None = None
    review_rounds: int = 1
    extra_instructions: str | None = None
    llm: LLMOverrides | None = None


_OPEN_PATHS = frozenset({"/healthz", "/", "/favicon.ico"})
"""Reachable without a principal.

Liveness, because a load balancer has no credentials, and the shell, because the
page that would let somebody sign in cannot itself require having signed in.
Everything the shell then fetches is guarded, so an unauthenticated visitor gets
the frame and no data."""


def _bound_beyond_loopback() -> bool:
    """Is this instance reachable from other machines?

    Read from the environment rather than passed in, because `build_app` is
    called from three places and none of them knows the bind address.
    """
    import os

    host = os.environ.get("SOURCEWORK_UI_HOST", "")
    return bool(host) and host not in ("127.0.0.1", "localhost", "::1")


async def _restart_mesh() -> list[str]:
    """Ask every configured agent to re-exec itself, returning those that heard.

    The agents restart in place (``os.execv``), so there is no supervisor to
    tell - this works identically under ``serve-all``, the desktop app, a plain
    terminal and compose. A peer that does not answer (no mesh running, or the
    UI-only install) is logged and skipped; the caller decides how much of a
    restart counts as one.
    """
    import httpx

    sec = settings().security
    headers = {sec.header: sec.api_key} if sec.enforce else {}
    reached: list[str] = []
    async with httpx.AsyncClient(timeout=2.0, headers=headers) as http:
        for name, url in settings().peers.as_map().items():
            try:
                await http.post(f"{url.rstrip('/')}/api/restart")
                reached.append(name)
            except Exception as exc:  # noqa: BLE001 - a down peer is not a failed save
                logger.warning("could not restart agent %s: %s", name, exc)
    return reached


def build_app(
    workspace: Path | None = None,
    on_shutdown: Callable[[], None] | None = None,
    store: Store | None = None,
    *,
    executor: RunExecutor | None = None,
    settings_backend: env_file.SettingsBackend | None = None,
    authorizer: Authorizer | None = None,
    run_id_factory: Callable[[], str] = new_run_id,
) -> FastAPI:
    """The web app. ``on_shutdown``, when given, exposes a way to stop it.

    Only the desktop launcher passes one. A running-in-a-checkout server or a
    compose deployment has no business offering "quit" over HTTP - there the
    process lifetime belongs to whoever started it, and an endpoint that ends it
    is a denial of service wearing a button.

    ``store`` defaults to the SQLite one, which is the right shape for a
    single-user application: one file, no service, something the operator can
    back up or delete. An installation where runs belong to different people
    needs a different one, and the parameter is what lets it arrive from outside
    rather than by editing this line.

    The four keyword parameters are the seams a hosted deployment supplies
    instead of the single-operator defaults: an :class:`RunExecutor` that runs
    runs somewhere else, a :class:`SettingsBackend` that keeps settings per
    tenant rather than in a shared ``.env``, an :class:`Authorizer` that decides
    what a signed-in principal may do, and a wider run-id factory than the
    local 12-hex one. Each defaults to today's behaviour, and a deployment that
    passes none of them gets exactly today's app.
    """
    paths = UIPaths(workspace or Path(settings().ui_workspace))
    store = store if store is not None else RunStore(paths.db)
    manager = executor if executor is not None else RunManager(store, run_id_factory=run_id_factory)
    settings_backend = (
        settings_backend if settings_backend is not None else env_file.EnvFileBackend(_env_path)
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        orphans = await store.reap_orphans()
        if orphans:
            logger.warning("marked %d interrupted run(s) as failed", orphans)
        retention = settings().runs.retention_days
        # purge_older_than is not on the Store protocol: a protocol wider than
        # its use is a promise every implementation has to keep, and a custom
        # store may not even have a clock-based notion of "old". An absent
        # capability is announced rather than silently skipped.
        purge = getattr(store, "purge_older_than", None)
        if retention > 0:
            if purge is None:
                logger.warning(
                    "retention is configured (%d day(s)) but this store cannot purge", retention
                )
            else:
                purged = await purge(retention)
                # A run is its row *and* its checkpoints, which hold the same
                # source text; deleting one and leaving the other would make
                # the retention setting a half-truth.
                for run_id in purged:
                    checkpoint.discard(run_id)
                # Deleting history is never silent: a reader who expected a run
                # to be there deserves a log line saying where it went.
                if purged:
                    logger.warning(
                        "retention: purged %d run(s) older than %d day(s): %s",
                        len(purged), retention, ", ".join(purged),
                    )
        yield
        await manager.shutdown()
        store.close()

    app = FastAPI(title="SourceWork", lifespan=lifespan)

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception) -> JSONResponse:
        """Return the failure instead of "Internal Server Error".

        This binds to loopback and serves one operator, so hiding the cause buys
        nothing and costs the only person who can fix it the ability to see what
        happened. A bare 500 sent both of us hunting through server logs for a
        message the browser could have shown - and the log is only written when
        the app was started as `sourcework app`, so sometimes it is nowhere at
        all.

        Deliberately not enabled for a wider bind: exception text can carry file
        paths and configuration values, which is fine on your own machine and not
        fine on a shared one.
        """
        logger.exception("unhandled error on %s %s", request.method, request.url.path)
        detail = f"{type(exc).__name__}: {exc}"
        if _bound_beyond_loopback():
            detail = f"{type(exc).__name__} - see the server log"
        return JSONResponse(status_code=500, content={"detail": detail})

    authenticator = auth.build()

    @app.middleware("http")
    async def require_a_principal(request: Request, call_next):  # noqa: ANN001, ANN202
        """Resolve who is asking, and refuse when nobody is.

        The rule lives here rather than on each route on purpose: a per-route
        dependency is one someone forgets to add, and the route they forget it
        on is unauthenticated with nothing to say so. One gate, and adding a
        route cannot open a hole.

        With core's :class:`~sourcework.auth.NullAuth` this never refuses - it
        stamps the local operator on every request and the app behaves exactly
        as it did before. It is the installed authenticator that decides.
        """
        if request.url.path in _OPEN_PATHS or request.url.path.startswith("/static/"):
            # Liveness and the shell that renders the sign-in prompt. A 401 on
            # the page that would let you log in is a locked door with the key
            # behind it.
            return await call_next(request)

        principal = await authenticator.principal(request)
        if principal is None:
            return JSONResponse(
                status_code=401,
                content={"detail": "Not signed in."},
                headers=authenticator.challenge(),
            )
        # Authentication answered "who"; the policy says whether that is
        # someone who may. Local passes no policy and the gate stays open, so
        # this branch only exists for installations that supplied one.
        if authorizer is not None and not await authorizer(request, principal, request.method):
            return JSONResponse(
                status_code=403,
                content={
                    "detail": "Signed in, but not allowed to do that.",
                    "principal": principal.id,
                    "roles": sorted(principal.roles),
                },
            )
        request.state.principal = principal
        return await call_next(request)

    @app.middleware("http")
    async def require_same_origin(request: Request, call_next):  # noqa: ANN001, ANN202
        """Reject cross-site writes.

        The UI has no login, so the browser has no cookie to protect - but it is
        reachable at a known address on the operator's own machine, and that is
        enough. A form on any page they visit can POST to localhost:8080
        cross-origin without a preflight: it cannot read the reply, but the run
        still happens, on attacker-chosen URIs, with attacker-chosen Confluence
        publishing targets.

        The fix is that a cross-origin form *cannot set a header*. Requiring one
        forces a preflight, and this app sends no CORS headers, so the preflight
        fails and the request never arrives. Reads are left alone: the same
        origin policy already stops another site reading them.
        """
        writes = request.method in ("POST", "PUT", "PATCH", "DELETE")
        if writes and request.headers.get("X-SourceWork-UI") != "1":
            return JSONResponse(
                status_code=403,
                content={
                    "detail": "Missing the X-SourceWork-UI header. The browser UI sends it on "
                              "every write; a cross-site form cannot. Add "
                              "`-H 'X-SourceWork-UI: 1'` if you are calling the API directly."
                },
            )
        return await call_next(request)

    # -- pages -------------------------------------------------------------

    @app.get("/", include_in_schema=False)
    async def index() -> HTMLResponse:
        return HTMLResponse((STATIC / "index.html").read_text(encoding="utf-8"))

    @app.get("/settings", include_in_schema=False)
    async def settings_page() -> HTMLResponse:
        return HTMLResponse((STATIC / "settings.html").read_text(encoding="utf-8"))

    @app.get("/dashboard", include_in_schema=False)
    async def dashboard_page() -> HTMLResponse:
        return HTMLResponse((STATIC / "dashboard.html").read_text(encoding="utf-8"))

    @app.get("/api/me", tags=["ops"])
    async def me(request: Request) -> dict[str, Any]:
        """Who this installation thinks you are.

        On core that is always the local operator, which is exactly what makes
        it worth having: the front end can render a name and a sign-out control
        when an authenticator is installed, and nothing when one is not, without
        knowing which case it is in.
        """
        principal = getattr(request.state, "principal", auth.LOCAL)
        return {
            "id": principal.id,
            "name": principal.display,
            "email": principal.email,
            "roles": sorted(principal.roles),
            "authentication": authenticator.id,
        }

    @app.get("/healthz", tags=["ops"])
    async def healthz() -> dict[str, Any]:
        # `shutdown` tells the front end whether to draw a Quit control. The UI
        # is served from the same build in both modes, so it has to ask rather
        # than assume.
        return {
            "status": "ok",
            "service": "sourcework-ui",
            "version": __version__,
            "shutdown": on_shutdown is not None,
        }

    if on_shutdown is not None:

        @app.post("/api/shutdown", tags=["ops"])
        async def shutdown() -> dict[str, bool]:
            # Answer first, stop after: closing the socket before the reply
            # leaves the browser showing a network error for a deliberate quit.
            async def stop() -> None:
                await asyncio.sleep(0.2)
                on_shutdown()

            asyncio.create_task(stop())  # noqa: RUF006 - deliberately unawaited
            return {"stopping": True}

    # -- runs --------------------------------------------------------------

    @app.post("/api/runs", tags=["runs"])
    async def create_run(request: Request) -> dict[str, Any]:
        form = await request.form()
        raw = form.get("request")
        if not isinstance(raw, str):
            raise HTTPException(400, "missing `request` field")
        try:
            spec = NewRun.model_validate(json.loads(raw))
        except (ValidationError, json.JSONDecodeError) as exc:
            raise HTTPException(400, f"malformed request: {exc}") from exc

        # Chosen here, not by the manager, so uploads land in a directory named
        # after the run that will read them.
        run_id = run_id_factory()
        upload_dir = paths.uploads / run_id
        inputs: list[InputRef] = []

        files = [f for f in form.getlist("files") if isinstance(f, UploadFile)]
        for upload in files:
            inputs.append(await _store_upload(upload, upload_dir))
        for uri in spec.uris:
            if uri.strip():
                inputs.append(InputRef(uri=_vetted_uri(uri)))
        for note in spec.notes:
            if note.strip():
                inputs.append(InputRef(uri="inline:note", title="Note", text=note.strip()))

        if not inputs and not spec.confluence_queries:
            raise HTTPException(400, "Give the run something to work from: files, URIs or CQL.")

        prd_request = PRDRequest(
            title=spec.title,
            inputs=inputs,
            confluence_queries=[q for q in spec.confluence_queries if q.strip()],
            audience=spec.audience,
            template=spec.template,
            review_rounds=spec.review_rounds,
            extra_instructions=spec.extra_instructions,
            publish=spec.publish,
            confluence_space_key=spec.confluence_space_key,
            confluence_parent_id=spec.confluence_parent_id,
            estimate=spec.estimate,
            llm=spec.llm,
        )
        run = await manager.start(prd_request, run_id=run_id)
        return {"id": run.id}

    @app.get("/api/runs", tags=["runs"])
    async def list_runs(limit: int = 50) -> list[dict[str, Any]]:
        return [r.summary() for r in await store.list(limit=min(limit, 200))]

    @app.get("/api/dashboard", tags=["runs"])
    async def dashboard(limit: int = 200) -> dict[str, Any]:
        """One row per PRD - not per run - with a verdict on whether it is done.

        Assessing readiness needs the full result (findings, open questions,
        conflicts), which the list view deliberately leaves out. So this reads
        each chain's head in full and nothing else.
        """
        summaries = [r.summary() for r in await store.list(limit=min(limit, 500))]
        groups = readiness.chains(summaries)

        rows: list[dict[str, Any]] = []
        for group in groups:
            head = group["head"]
            assessment = None
            if head.get("status") == "ok":
                full = await store.get(head["id"])
                if full and full.result:
                    assessment = readiness.assess(
                        full.result.get("prd"), full.result.get("review")
                    ).as_dict()
            rows.append({
                "root_id": group["root_id"],
                "versions": group["versions"],
                "in_flight": group["in_flight"],
                "run": head,
                "readiness": assessment,
            })

        def state_of(row: dict[str, Any]) -> str:
            if row["readiness"]:
                return row["readiness"]["state"]
            return "running" if row["in_flight"] else row["run"].get("status", "unknown")

        return {
            "prds": rows,
            "totals": {
                state: sum(1 for r in rows if state_of(r) == state)
                for state in ("ready", "needs_work", "unreviewed", "running", "failed")
            },
        }

    @app.get("/api/runs/{run_id}", tags=["runs"])
    async def get_run(run_id: str) -> dict[str, Any]:
        run = await store.get(run_id)
        if run is None:
            raise HTTPException(404, "no such run")
        return {
            **run.as_dict(),
            "active": manager.is_active(run_id),
            # What a resume would skip. Sent with the run rather than fetched
            # separately so the view can offer resuming only when there is
            # something to resume, and can say what it would keep.
            "resumable": checkpoint.saved_stages(run_id),
            # The same verdict the dashboard shows, computed the same way from
            # the same stored dicts. Without it the run's own page was the one
            # place that could not answer "is this finished enough to send",
            # and a reader had to leave the document to find out.
            "readiness": (
                readiness.assess(run.result.get("prd"), run.result.get("review")).as_dict()
                if run.result else None
            ),
        }

    @app.post("/api/runs/{run_id}/resume", tags=["runs"])
    async def resume_run(run_id: str) -> dict[str, Any]:
        """Continue an interrupted run from the last stage it completed.

        Explicit, never automatic. A run is most often cancelled because the
        configuration was wrong, and silently reusing what that configuration
        produced would hand back a PRD the user had already rejected. Stages
        whose fingerprint no longer matches are recomputed anyway.
        """
        stages = checkpoint.saved_stages(run_id)
        if not stages:
            raise HTTPException(404, "that run saved no stages to resume from")
        run = await manager.resume(run_id)
        if run is None:
            raise HTTPException(409, "that run is already in flight")
        return {"id": run.id, "reusing": stages}

    @app.delete("/api/runs/{run_id}", tags=["runs"])
    async def delete_run(run_id: str) -> dict[str, Any]:
        """Erase a run and say exactly what went.

        The response is the erasure record: what was deleted, and what was
        deliberately left. Uploaded files stay - they live in the shared
        workspace and a later run's checkpoints may still fingerprint them -
        so they are listed, not removed, and the caller hears about it rather
        than being told "deleted" and believing the bytes are gone.
        """
        await manager.cancel(run_id)
        checkpoint.discard(run_id)
        deleted = await store.delete(run_id)
        uploads = paths.uploads / run_id
        left = []
        if uploads.is_dir() and any(uploads.iterdir()):
            left.append(
                f"uploads in {uploads} (kept: the shared workspace is content-addressed "
                "by later runs' checkpoints)"
            )
        return {"deleted": deleted, "run_id": run_id, "left_in_place": left}

    @app.post("/api/runs/{run_id}/approval", tags=["runs"])
    async def set_approval(run_id: str, body: ApprovalUpdate) -> dict[str, Any]:
        """Sign off on a run, or send it back. Recorded, not authenticated.

        Single-operator software: the name is what the operator typed, kept so
        the audit bundle says *who* believed this PRD, not to keep anyone out.
        The history is append-only - a rejected-then-approved run shows both,
        which is exactly the trail an approval is for.
        """
        if body.state not in ("approved", "rejected", "draft"):
            raise HTTPException(400, "state must be approved, rejected or draft")
        run = await store.get(run_id)
        if run is None:
            raise HTTPException(404, "no such run")
        if run.result is None:
            raise HTTPException(409, "only a finished run can be approved")

        entry = {"state": body.state, "by": body.by, "at": now_iso()}
        if body.note:
            entry["note"] = body.note
        history = list((run.approval or {}).get("history") or [])
        history.append(entry)
        run.approval = {"state": body.state, "by": body.by, "at": entry["at"], "history": history}
        # The rendered artifacts follow the decision. The renderers are pure
        # functions of (prd, review), so re-rendering changes exactly one
        # thing: the status line (and the Confluence lozenge it drives). The
        # review has to be handed back in - the pipeline attached it to the
        # shipped artifacts, and re-rendering without it would silently drop
        # the review section from a document at the moment somebody signs it.
        if isinstance(run.result.get("prd"), dict):
            from sourcework.confluence.storage import render_prd
            from sourcework.models import PRDDocument, ReviewReport
            from sourcework.render import to_markdown

            prd = PRDDocument.model_validate(run.result["prd"])
            prd.status = body.state
            stored_review = run.result.get("review")
            review = (
                ReviewReport.model_validate(stored_review)
                if isinstance(stored_review, dict)
                else None
            )
            run.result["prd"] = prd.model_dump(mode="json")
            run.result["markdown"] = to_markdown(prd, review)
            run.result["confluence_storage"] = render_prd(prd, review)
        await store.save(run)
        return run.approval

    @app.get("/api/runs/{run_id}/audit", tags=["runs"])
    async def audit_bundle(run_id: str) -> Response:
        """The run as one downloadable zip: request, result, evidence, sources,
        events, and a manifest whose digests make after-the-fact edits visible."""
        run = await store.get(run_id)
        if run is None:
            raise HTTPException(404, "no such run")

        body = audit.build_bundle(run)
        return Response(
            body,
            media_type="application/zip",
            headers={
                "Content-Disposition":
                    f'attachment; filename="{_slug(run.title)}-{run_id}-audit.zip"'
            },
        )

    @app.post("/api/runs/{run_id}/cancel", tags=["runs"])
    async def cancel_run(run_id: str) -> dict[str, bool]:
        return {"cancelled": await manager.cancel(run_id)}

    @app.get("/api/runs/{run_id}/events", tags=["runs"])
    async def stream_events(run_id: str) -> StreamingResponse:
        run = await store.get(run_id)
        if run is None:
            raise HTTPException(404, "no such run")

        async def events() -> AsyncIterator[str]:
            try:
                async for event in manager.subscribe(run_id):
                    yield f"data: {json.dumps(event)}\n\n"
            except asyncio.CancelledError:  # browser navigated away
                raise
            finally:
                yield "event: end\ndata: {}\n\n"

        return StreamingResponse(
            events(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                # nginx buffers SSE into uselessness without this.
                "X-Accel-Buffering": "no",
            },
        )

    @app.get("/api/runs/{run_id}/artifact/{kind}", tags=["runs"])
    async def artifact(run_id: str, kind: str) -> Response:
        run = await store.get(run_id)
        if run is None or not run.result:
            raise HTTPException(404, "no result for that run")
        slug = _slug(run.title)
        shapes = {
            "md": ("markdown", f"{slug}.md", "text/markdown"),
            "xhtml": ("confluence_storage", f"{slug}.storage.xhtml", "application/xhtml+xml"),
        }
        if kind == "json":
            body = json.dumps(run.result.get("prd"), indent=2)
            return Response(
                body,
                media_type="application/json",
                headers={"Content-Disposition": f'attachment; filename="{slug}.json"'},
            )
        if kind not in shapes:
            raise HTTPException(404, f"unknown artifact {kind!r}")
        key, filename, media = shapes[kind]
        body = run.result.get(key) or ""
        return Response(
            body,
            media_type=media,
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @app.post("/api/runs/{run_id}/refine", tags=["runs"])
    async def refine(run_id: str, request: Request) -> dict[str, Any]:
        """Start a new run that continues an existing PRD.

        A version, not an edit: the parent run is untouched and the child
        records what it was built from. Answers to open questions arrive as
        ordinary inline sources, so they become evidence like everything else
        and the requirements they justify can cite them.
        """
        parent = await store.get(run_id)
        if parent is None or not parent.result:
            raise HTTPException(404, "no finished run to refine")

        form = await request.form()
        raw = form.get("request")
        try:
            spec = RefineRun.model_validate(json.loads(raw) if isinstance(raw, str) else {})
        except (ValidationError, json.JSONDecodeError) as exc:
            raise HTTPException(400, f"malformed request: {exc}") from exc

        child_id = run_id_factory()
        inputs: list[InputRef] = []

        for answer in spec.answers:
            if not answer.answer.strip():
                continue
            # Phrased as a self-contained statement: the extractor sees this
            # text alone, so "yes, in scope" without its question is useless.
            inputs.append(InputRef(
                uri="inline:answer",
                title="Answer to an open question",
                text=(
                    f"Answer to the open question “{answer.question.strip()}”: "
                    f"{answer.answer.strip()}"
                ),
                notes="Decision supplied by the requester while reviewing the previous version.",
            ))
        for note in spec.notes:
            if note.strip():
                inputs.append(InputRef(uri="inline:note", title="Added requirement", text=note.strip()))
        for uri in spec.uris:
            if uri.strip():
                inputs.append(InputRef(uri=_vetted_uri(uri)))
        for upload in [f for f in form.getlist("files") if isinstance(f, UploadFile)]:
            inputs.append(await _store_upload(upload, paths.uploads / child_id))

        if not inputs:
            raise HTTPException(400, "Nothing to add: answer a question, add a note, or attach a file.")

        prd = parent.result.get("prd") or {}
        prd_request = PRDRequest(
            title=spec.title or parent.title,
            inputs=inputs,
            audience=parent.request.get("audience") or "engineering and product",
            template=spec.template or parent.request.get("template") or "standard",
            review_rounds=spec.review_rounds,
            extra_instructions=spec.extra_instructions,
            # The parent's choices carry forward: a refinement of an estimated
            # run stays estimated, or the new requirements would be the only
            # ones without a size.
            estimate=bool(parent.request.get("estimate", False)),
            llm=spec.llm or parent.request.get("llm"),
            baseline=PRDBaseline(
                run_id=parent.id,
                sources=prd.get("sources") or [],
                evidence=prd.get("evidence") or [],
                requirements=prd.get("requirements"),
            ),
        )
        run = await manager.start(prd_request, run_id=child_id)
        return {"id": run.id, "parent": parent.id}

    @app.post("/api/runs/{run_id}/publish", tags=["runs"])
    async def publish(run_id: str, body: dict[str, Any]) -> dict[str, Any]:
        run = await store.get(run_id)
        if run is None or not run.result:
            raise HTTPException(404, "no result for that run")
        storage = run.result.get("confluence_storage")
        if not storage:
            raise HTTPException(400, "this run produced no Confluence storage format")

        from sourcework.agents.schemas import PublishRequest

        async with AgentPool() as pool:
            try:
                result = await pool.call(
                    "confluence",
                    "publish_prd",
                    PublishRequest(
                        title=run.title,
                        storage_xhtml=storage,
                        space_key=body.get("space_key") or None,
                        parent_id=body.get("parent_id") or None,
                    ),
                )
            except Exception as exc:  # noqa: BLE001 - reported, not raised
                raise HTTPException(502, f"publish failed: {exc}") from exc

        run.result["published_url"] = result.get("url")
        await store.save(run)
        return result

    # -- mesh & backends ---------------------------------------------------

    @app.get("/api/mesh", tags=["ops"])
    async def mesh() -> dict[str, Any]:
        async with AgentPool() as pool:
            found = await pool.discover()
            return {
                "agents": found,
                "unreachable": [a for a in pool.registry if a not in found],
                "registry": pool.registry,
            }

    @app.get("/api/backends", tags=["ops"])
    async def backends() -> dict[str, Any]:
        cfg = settings().llm
        return {
            "active": cfg.active_backend,
            "failover_order": cfg.failover_order,
            "backends": probe(cfg),
            # From the settings fields, not a second hand-written list - see
            # env_file.model_roles for what the two drifting apart cost.
            "roles": env_file.model_roles(),
        }

    # -- settings ----------------------------------------------------------

    @app.get("/api/settings", tags=["settings"])
    async def read_settings() -> dict[str, Any]:
        return {
            "path": settings_backend.label,
            "fields": settings_backend.describe(),
            "profiles": settings_backend.profiles_for(),
            "default_profile": settings_backend.default_profile,
        }

    @app.put("/api/settings", tags=["settings"])
    async def write_settings(body: dict[str, str]) -> dict[str, Any]:
        changed = settings_backend.write(body)
        # Restart only applies to a backend that feeds the mesh's start-up
        # settings. One that resolves settings per request (a hosted tenant's
        # own values) has nothing to restart, and restarting every agent on a
        # tenant's save would be taking the whole service down for one of them.
        needs_restart = (
            settings_backend.restartable
            and any(env_file.BY_KEY[k].restart for k in changed if k in env_file.BY_KEY)
        )
        restarted: list[str] = []
        if needs_restart:
            # The agents read their config once, at start-up; a save is only
            # worth something once every peer has re-exec'd itself. Peers that
            # are down are logged and left alone - the save is not a failure,
            # just an unfinished one.
            restarted = await _restart_mesh()
        return {
            "changed": changed,
            "restart_required": needs_restart,
            "message": (
                f"Saved {len(changed)} setting(s). The mesh is restarting "
                "to pick them up - it will be back in a moment."
                if restarted
                else f"Saved {len(changed)} setting(s)."
            )
            if changed
            else "Nothing changed.",
        }

    class _RevalidatingStatic(StaticFiles):
        """Serve the front end with ``no-cache``.

        Not ``no-store``: the files are still cached, the browser just has to
        ask first, and an unchanged file comes back 304. Starlette already sends
        an ETag, so the cost is one conditional request per asset per load.

        This exists because the failure it prevents is expensive and silent. A
        stale ``settings.js`` renders a page that looks plausible and is a
        version behind, so what you are debugging is not what the code says -
        and the browser gives you no hint that is what happened.
        """

        def is_not_modified(self, response_headers, request_headers) -> bool:  # noqa: ANN001
            response_headers.setdefault("cache-control", "no-cache")
            return super().is_not_modified(response_headers, request_headers)

        async def get_response(self, path: str, scope):  # noqa: ANN001, ANN201
            response = await super().get_response(path, scope)
            response.headers.setdefault("cache-control", "no-cache")
            return response

    app.mount("/static", _RevalidatingStatic(directory=STATIC), name="static")

    @app.get("/favicon.ico", include_in_schema=False)
    async def favicon() -> Response:
        icon = STATIC / "favicon.svg"
        if icon.is_file():
            return FileResponse(icon, media_type="image/svg+xml")
        return Response(status_code=204)

    return app


# ---------------------------------------------------------------------------


async def _store_upload(upload: UploadFile, target_dir: Path) -> InputRef:
    data = await upload.read()
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            413, f"{upload.filename} is {len(data) // 1024 // 1024} MB; the limit is 64 MB"
        )
    target_dir.mkdir(parents=True, exist_ok=True)
    # Basename only: a filename is attacker-controlled and "../../etc/passwd"
    # is a perfectly valid one as far as the browser is concerned. `.name`
    # handles that; it does *not* handle a filename of exactly "..", which
    # survives it intact and resolves to the parent directory - not a traversal
    # (writing bytes to a directory just fails) but a 500 where a name would do.
    name = Path(upload.filename or "upload").name.strip()
    if name in ("", ".", ".."):
        name = "upload"
    destination = target_dir / name
    destination.write_bytes(data)
    return InputRef(
        uri=destination.resolve().as_uri(),
        title=name,
        media_type=upload.content_type or None,
    )


def _env_path() -> Path:
    return Path(settings().env_file).expanduser().resolve()


_REMOTE_SCHEMES = ("http://", "https://", "confluence://")


def _vetted_uri(raw: str) -> str:
    """A URI the HTTP API is willing to hand to ingestion.

    ``fetch()`` resolves ``file:///…`` and bare absolute paths, which is right
    for the CLI - an operator naming a file on their own machine - and wrong
    here. This endpoint takes its input from an HTTP request, and a local path
    arriving that way is an arbitrary-file-read primitive: ``.env`` with the
    provider keys in it, ``~/.ssh/id_rsa``, anything the process can open, all
    of it quoted back as evidence through ``/api/runs``.

    Files reach a run through the upload field, which puts them somewhere this
    process chose. Remote schemes are still allowed, and ``fetch()`` refuses the
    private address ranges that would make those a way back in.
    """
    uri = raw.strip()
    if uri.lower().startswith(_REMOTE_SCHEMES):
        return uri
    raise HTTPException(
        400,
        f"Refusing the local path {uri!r}. Attach the file instead - the URI field "
        "reaches http(s) and confluence:// only, because a path sent over HTTP would "
        "let anyone who can reach this port read any file this process can.",
    )


def _slug(text: str) -> str:
    return "".join(c if c.isalnum() else "-" for c in text.lower()).strip("-")[:60] or "prd"


DEFAULT_HOST = "127.0.0.1"
"""Loopback, deliberately.

This UI has no authentication, and two of its endpoints are worth more than the
rest of the system put together: ``/api/settings`` rewrites ``.env`` - including
provider keys and the Confluence token - and ``/api/runs`` hands back the full
source text of everything ever ingested. On ``0.0.0.0`` that is offered to
every machine on the network, with no password, to anyone who guesses the port.

Binding wider is a real thing to want (a workstation under the desk, a
container). It stays possible - ``--host``, or ``SOURCEWORK_UI_HOST`` - but it is
now something an operator chooses, having been told, rather than the default
they inherit. Put it behind something that authenticates before you do.
"""


def serve(port: int = PORT, host: str = DEFAULT_HOST, workspace: Path | None = None) -> None:
    import uvicorn

    logging.basicConfig(
        level=settings().log_level,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    if host not in ("127.0.0.1", "localhost", "::1"):
        logging.getLogger(__name__).warning(
            "The UI is bound to %s and has no authentication: anyone who can reach "
            "port %d can read every ingested document and rewrite your API keys. "
            "Put an authenticating proxy in front of it.",
            host,
            port,
        )
    uvicorn.run(build_app(workspace), host=host, port=port, log_level=settings().log_level.lower())
