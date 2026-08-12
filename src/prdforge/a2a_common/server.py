"""Boot an agent: FastAPI app + A2A JSON-RPC routes + agent card."""

from __future__ import annotations

import logging

from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import (
    add_a2a_routes_to_fastapi,
    create_agent_card_routes,
    create_jsonrpc_routes,
)
from a2a.server.tasks import InMemoryTaskStore, TaskStore
from a2a.types import AgentCard
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from prdforge.a2a_common.executor import SkillExecutor
from prdforge.config import settings

logger = logging.getLogger(__name__)

RPC_URL = "/"


def build_app(
    card: AgentCard,
    executor: SkillExecutor,
    *,
    task_store: TaskStore | None = None,
) -> FastAPI:
    handler = DefaultRequestHandler(
        agent_executor=executor,
        task_store=task_store or InMemoryTaskStore(),
        agent_card=card,
    )

    app = FastAPI(
        title=card.name,
        description=card.description,
        version=card.version,
    )

    sec = settings().security
    # Refuses to build the app if enforcement is on with the shipped key. Here
    # rather than at the edge of the CLI, so every way of starting an agent -
    # serve, serve-all, compose, an embedded test mesh - inherits the check.
    sec.guard()
    if sec.enforce:

        @app.middleware("http")
        async def _auth(request: Request, call_next):  # type: ignore[no-untyped-def]
            open_paths = ("/.well-known/", "/healthz", "/docs", "/openapi.json")
            unauthenticated = (
                not request.url.path.startswith(open_paths)
                and request.headers.get(sec.header) != sec.api_key
            )
            if unauthenticated:
                return JSONResponse({"error": "unauthorised"}, status_code=401)
            return await call_next(request)

    @app.get("/healthz", tags=["ops"])
    async def healthz() -> dict[str, str]:
        return {"status": "ok", "agent": card.name, "version": card.version}

    add_a2a_routes_to_fastapi(
        app,
        agent_card_routes=create_agent_card_routes(card),
        jsonrpc_routes=create_jsonrpc_routes(handler, RPC_URL),
    )
    return app


def serve(app: FastAPI, port: int, host: str = "0.0.0.0") -> None:  # noqa: S104
    import uvicorn

    logging.basicConfig(
        level=settings().log_level,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    uvicorn.run(app, host=host, port=port, log_level=settings().log_level.lower())
