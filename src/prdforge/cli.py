"""Command line entry points.

    prdforge-agent serve <name>          run one agent
    prdforge-agent serve-all             run every agent in one process (dev)
    prdforge-agent status                probe the mesh
    prdforge-agent backends              which LLM backends are usable here
    prdforge-agent ui                    the web UI (browser front end)
    prdforge-agent generate ...          drive the orchestrator over A2A
"""

from __future__ import annotations

import argparse
import asyncio
import importlib
import json
import logging
import sys
from pathlib import Path

from prdforge.config import settings

AGENTS = {
    "orchestrator": "prdforge.agents.orchestrator.agent",
    "ingestion": "prdforge.agents.ingestion.agent",
    "vision": "prdforge.agents.vision.agent",
    "transcript": "prdforge.agents.transcript.agent",
    "confluence": "prdforge.agents.confluence_agent.agent",
    "requirements": "prdforge.agents.requirements.agent",
    "writer": "prdforge.agents.writer.agent",
    "critic": "prdforge.agents.critic.agent",
}


def _load(name: str):  # noqa: ANN202
    try:
        return importlib.import_module(AGENTS[name])
    except KeyError:
        raise SystemExit(f"Unknown agent {name!r}. Choose from: {', '.join(AGENTS)}") from None


def cmd_serve(args: argparse.Namespace) -> int:
    from prdforge.a2a_common import build_app, serve

    module = _load(args.name)
    serve(build_app(module.card(), module.executor()), args.port or module.PORT)
    return 0


def cmd_serve_all(args: argparse.Namespace) -> int:
    """Every agent in one process on its own port. Development only."""
    import threading

    import uvicorn

    from prdforge.a2a_common import build_app

    logging.basicConfig(level=settings().log_level, format="%(levelname)-7s %(name)s: %(message)s")
    servers = []
    for name in AGENTS:
        module = _load(name)
        app = build_app(module.card(), module.executor())
        config = uvicorn.Config(app, host="0.0.0.0", port=module.PORT, log_level="warning")  # noqa: S104
        server = uvicorn.Server(config)
        servers.append((name, module.PORT, server))
        threading.Thread(target=server.run, daemon=True).start()

    for name, port, _ in servers:
        print(f"  {name:<14} http://localhost:{port}/.well-known/agent-card.json")
    print("\nCtrl-C to stop.")
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        return 0
    return 0


async def _status() -> int:
    from prdforge.a2a_common import AgentPool

    async with AgentPool() as pool:
        found = await pool.discover()
    for name, url in sorted(pool.registry.items()):
        skills = found.get(name)
        mark = "ok  " if skills else "DOWN"
        print(f"[{mark}] {name:<14} {url:<28} {', '.join(skills or [])}")
    return 0 if len(found) == len(pool.registry) else 1


def cmd_status(args: argparse.Namespace) -> int:
    return asyncio.run(_status())


async def _backends(args: argparse.Namespace) -> int:
    """Report which backends could run here, and optionally prove it."""
    from prdforge.backends import probe, resolve_chain
    from prdforge.llm import LLM

    cfg = settings().llm
    chain = resolve_chain(cfg)

    print(f"active backend : {cfg.active_backend}")
    print(f"failover chain : {' -> '.join(chain) if chain else '(none usable)'}")
    print(f"vision chain   : {' -> '.join(resolve_chain(cfg, needs_vision=True)) or '(none)'}\n")

    for row in probe(cfg):
        mark = "ok  " if row["available"] else "n/a "
        model = row.get("configured_model") or "(backend default)"
        vision = "vision" if row.get("vision") else "text-only"
        print(f"[{mark}] {row['id']:<14} {vision:<10} model={model}")
        models = row.get("models") or []
        if models and args.models:
            for name in models:
                print(f"           {name}")
        if not row["available"] and row.get("detail"):
            print(f"           {row['detail']}")

    if not args.check:
        print("\nAdd --check to send one real prompt through each usable backend.")
        return 0

    # A real call against a real account. Opt-in, because on three of the four
    # backends it spends someone's subscription quota.
    print("\nLive check (one short prompt each):")
    for row in probe(cfg):
        if not row["available"]:
            continue
        backend_id = str(row["id"])
        patched = cfg.model_copy(update={"backend": backend_id, "failover_order": [], "stub": False})
        try:
            answer = await LLM(cfg=patched).text(
                "You are a terse assistant.", "Reply with exactly: PRDFORGE OK"
            )
        except Exception as exc:  # noqa: BLE001 - reporting, not handling
            print(f"[FAIL] {backend_id:<14} {type(exc).__name__}: {str(exc)[:200]}")
            continue
        print(f"[ok  ] {backend_id:<14} {answer.strip()[:80]!r}")
    return 0


def cmd_backends(args: argparse.Namespace) -> int:
    return asyncio.run(_backends(args))


def cmd_ui(args: argparse.Namespace) -> int:
    from prdforge.ui import PORT, serve

    port = args.port or PORT
    print(f"PRD Forge UI  http://localhost:{port}")
    print("Needs the mesh running: prdforge-agent serve-all\n")
    serve(port=port, workspace=Path(args.workspace) if args.workspace else None)
    return 0


async def _generate(args: argparse.Namespace) -> int:
    from prdforge.a2a_common import AgentPool
    from prdforge.models import InputRef, PRDRequest, PRDResult

    inputs = [InputRef(uri=_as_uri(p)) for p in args.input or []]
    for note in args.note or []:
        inputs.append(InputRef(uri="inline:note", text=note, title="Requester note"))

    request = PRDRequest(
        title=args.title,
        inputs=inputs,
        confluence_queries=args.cql or [],
        publish=args.publish,
        confluence_space_key=args.space,
        confluence_parent_id=args.parent,
        template=args.template,
        review_rounds=args.review_rounds,
        extra_instructions=args.instructions,
    )

    async with AgentPool() as pool:
        data = await pool.call("orchestrator", "generate_prd", request)
    result = PRDResult.model_validate(data)

    out = Path(args.out or f"{_slug(args.title)}.md")
    out.write_text(result.markdown, encoding="utf-8")
    out.with_suffix(".json").write_text(result.prd.model_dump_json(indent=2), encoding="utf-8")
    if result.confluence_storage:
        out.with_suffix(".storage.xhtml").write_text(result.confluence_storage, encoding="utf-8")

    print(f"\nWrote {out} (+ .json, .storage.xhtml)")
    print(json.dumps(result.stats, indent=2, default=str))
    if result.published_url:
        print(f"Published: {result.published_url}")
    if result.review:
        print(f"Review verdict: {result.review.verdict} ({len(result.review.findings)} findings)")
    return 0


def cmd_generate(args: argparse.Namespace) -> int:
    return asyncio.run(_generate(args))


def _as_uri(value: str) -> str:
    if "://" in value:
        return value
    return Path(value).expanduser().resolve().as_uri()


def _slug(text: str) -> str:
    return "".join(c if c.isalnum() else "-" for c in text.lower()).strip("-")[:60] or "prd"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="prdforge-agent", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("serve", help="run one agent")
    p.add_argument("name", choices=sorted(AGENTS))
    p.add_argument("--port", type=int)
    p.set_defaults(func=cmd_serve)

    p = sub.add_parser("serve-all", help="run every agent in one process (dev)")
    p.set_defaults(func=cmd_serve_all)

    p = sub.add_parser("status", help="probe the mesh")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("backends", help="which LLM backends are usable here")
    p.add_argument("--models", action="store_true", help="list each backend's selectable models")
    p.add_argument(
        "--check",
        action="store_true",
        help="send one short prompt through each usable backend (spends real quota)",
    )
    p.set_defaults(func=cmd_backends)

    p = sub.add_parser("ui", help="serve the web UI")
    p.add_argument("--port", type=int, help=f"default {8080}")
    p.add_argument("--workspace", help="where run history and uploads live")
    p.set_defaults(func=cmd_ui)

    p = sub.add_parser("generate", help="generate a PRD via the orchestrator")
    p.add_argument("title")
    p.add_argument("-i", "--input", action="append", help="file path or URI; repeatable")
    p.add_argument("-n", "--note", action="append", help="inline requirement note; repeatable")
    p.add_argument("-q", "--cql", action="append", help="Confluence CQL query; repeatable")
    p.add_argument("-o", "--out", help="output markdown path")
    p.add_argument("--publish", action="store_true")
    p.add_argument("--space", help="Confluence space key to publish into")
    p.add_argument("--parent", help="Confluence parent page id")
    p.add_argument("--template", default="standard", choices=["standard", "lean", "technical", "discovery"])
    p.add_argument("--review-rounds", type=int, default=1)
    p.add_argument("--instructions", help="extra steer for the analyst and writer")
    p.set_defaults(func=cmd_generate)

    args = parser.parse_args(argv)
    logging.basicConfig(level=settings().log_level, format="%(levelname)-7s %(name)s: %(message)s")
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
