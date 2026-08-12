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
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ValidationError

# Starlette's, NOT fastapi.UploadFile. Reading the form directly (rather than
# declaring it as a parameter) yields Starlette's class, and FastAPI's is a
# *subclass* of it - so an isinstance check against FastAPI's silently drops
# every uploaded file and the run proceeds with no sources.
from starlette.datastructures import UploadFile

from prdforge import readiness
from prdforge.a2a_common import AgentPool
from prdforge.backends import probe
from prdforge.config import LLMOverrides, settings
from prdforge.models import InputRef, PRDBaseline, PRDRequest
from prdforge.ui import env_file
from prdforge.ui.runner import RunManager
from prdforge.ui.store import RunStore, new_run_id

logger = logging.getLogger(__name__)

PORT = 8080
STATIC = Path(__file__).parent / "static"

MAX_UPLOAD_BYTES = 64 * 1024 * 1024
"""Matches the ingest layer's own ceiling - rejecting here gives a clear error
instead of one buried in a failed extraction."""


class UIPaths:
    """Where the UI keeps its state. All of it under one directory."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.uploads = root / "uploads"
        self.db = root / "prdforge-ui.db"
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
    llm: LLMOverrides | None = None


class QuestionAnswer(BaseModel):
    question: str
    answer: str


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


def build_app(workspace: Path | None = None) -> FastAPI:
    paths = UIPaths(workspace or Path(settings().ui_workspace))
    store = RunStore(paths.db)
    manager = RunManager(store)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        orphans = await store.reap_orphans()
        if orphans:
            logger.warning("marked %d interrupted run(s) as failed", orphans)
        yield
        await manager.shutdown()
        store.close()

    app = FastAPI(title="PRD Forge", lifespan=lifespan)

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

    @app.get("/healthz", tags=["ops"])
    async def healthz() -> dict[str, str]:
        return {"status": "ok", "service": "prdforge-ui"}

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
        run_id = new_run_id()
        upload_dir = paths.uploads / run_id
        inputs: list[InputRef] = []

        files = [f for f in form.getlist("files") if isinstance(f, UploadFile)]
        for upload in files:
            inputs.append(await _store_upload(upload, upload_dir))
        for uri in spec.uris:
            if uri.strip():
                inputs.append(InputRef(uri=uri.strip()))
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
        return {**run.as_dict(), "active": manager.is_active(run_id)}

    @app.delete("/api/runs/{run_id}", tags=["runs"])
    async def delete_run(run_id: str) -> dict[str, bool]:
        await manager.cancel(run_id)
        return {"deleted": await store.delete(run_id)}

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

        child_id = new_run_id()
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
                inputs.append(InputRef(uri=uri.strip()))
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

        from prdforge.agents.schemas import PublishRequest

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
            "roles": ["default", "reasoning", "vision", "fast"],
        }

    # -- settings ----------------------------------------------------------

    @app.get("/api/settings", tags=["settings"])
    async def read_settings() -> dict[str, Any]:
        return {
            "path": str(_env_path()),
            "fields": env_file.describe(_env_path()),
            "profiles": env_file.PROFILES,
            "default_profile": env_file.DEFAULT_PROFILE,
        }

    @app.put("/api/settings", tags=["settings"])
    async def write_settings(body: dict[str, str]) -> dict[str, Any]:
        changed = env_file.write(_env_path(), body)
        needs_restart = any(env_file.BY_KEY[k].restart for k in changed if k in env_file.BY_KEY)
        return {
            "changed": changed,
            # Honest rather than convenient: the eight agents read their config
            # once, at start-up. Nothing here reaches a running agent.
            "restart_required": needs_restart,
            "message": (
                f"Saved {len(changed)} setting(s). Restart the mesh for them to take effect."
                if needs_restart
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
    # is a perfectly valid one as far as the browser is concerned.
    name = Path(upload.filename or "upload").name or "upload"
    destination = target_dir / name
    destination.write_bytes(data)
    return InputRef(
        uri=destination.resolve().as_uri(),
        title=name,
        media_type=upload.content_type or None,
    )


def _env_path() -> Path:
    return Path(settings().env_file).expanduser().resolve()


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
container). It stays possible - ``--host``, or ``PRDFORGE_UI_HOST`` - but it is
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
