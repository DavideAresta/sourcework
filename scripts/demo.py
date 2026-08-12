#!/usr/bin/env python3
"""End-to-end smoke test.

Starts all eight agents in-process, drives the orchestrator over real A2A
JSON-RPC, and writes the resulting PRD to ``out/``. Runs with
``PRDFORGE_LLM__STUB=1`` so it needs no API key - the point is to prove the
wiring, transports, routing, schema validation and rendering all work.

    PRDFORGE_LLM__STUB=1 python scripts/demo.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import threading
import time
from pathlib import Path

os.environ.setdefault("PRDFORGE_LLM__STUB", "1")
os.environ.setdefault("PRDFORGE_LOG_LEVEL", "WARNING")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import uvicorn  # noqa: E402

from prdforge.a2a_common import AgentPool, build_app  # noqa: E402
from prdforge.cli import AGENTS  # noqa: E402
from prdforge.models import InputRef, PRDRequest, PRDResult  # noqa: E402

SAMPLES = ROOT / "examples" / "sample_inputs"
OUT = ROOT / "out"


def start_agents() -> list[int]:
    import importlib

    ports = []
    for name in AGENTS:
        module = importlib.import_module(AGENTS[name])
        app = build_app(module.card(), module.executor())
        server = uvicorn.Server(
            uvicorn.Config(app, host="127.0.0.1", port=module.PORT, log_level="error")
        )
        threading.Thread(target=server.run, daemon=True).start()
        ports.append(module.PORT)
    return ports


async def wait_ready(ports: list[int], timeout: float = 30.0) -> None:
    import httpx

    deadline = time.time() + timeout
    async with httpx.AsyncClient(timeout=2.0) as http:
        while time.time() < deadline:
            try:
                results = await asyncio.gather(
                    *(http.get(f"http://127.0.0.1:{p}/healthz") for p in ports),
                    return_exceptions=True,
                )
                if all(not isinstance(r, Exception) and r.status_code == 200 for r in results):
                    return
            except Exception:  # noqa: BLE001, S110
                pass
            await asyncio.sleep(0.3)
    raise RuntimeError("agents did not come up in time")


async def main() -> int:
    ports = start_agents()
    await wait_ready(ports)

    async with AgentPool() as pool:
        found = await pool.discover()
        print("Mesh:")
        for name, skills in sorted(found.items()):
            print(f"  {name:<14} {', '.join(skills)}")
        print()

        request = PRDRequest(
            title="Self-service invoice reconciliation",
            inputs=[
                InputRef(uri=(SAMPLES / "kickoff.vtt").as_uri(), title="Kickoff meeting"),
                InputRef(uri=(SAMPLES / "requirements.md").as_uri(), title="Draft requirements"),
                InputRef(
                    uri="inline:note",
                    title="Requester note",
                    text="Finance wants this live before the year-end close.",
                ),
            ],
            audience="engineering and finance ops",
            template="standard",
            review_rounds=1,
            publish=False,
        )

        print(f"Generating '{request.title}' ...\n")
        started = time.perf_counter()
        data = await pool.call("orchestrator", "generate_prd", request)
        elapsed = time.perf_counter() - started

    result = PRDResult.model_validate(data)

    OUT.mkdir(exist_ok=True)
    (OUT / "prd.md").write_text(result.markdown, encoding="utf-8")
    (OUT / "prd.json").write_text(result.prd.model_dump_json(indent=2), encoding="utf-8")
    (OUT / "prd.storage.xhtml").write_text(result.confluence_storage or "", encoding="utf-8")

    print(f"Done in {elapsed:.1f}s")
    print(json.dumps(result.stats, indent=2, default=str))
    if result.review:
        print(f"\nReview: {result.review.verdict}, {len(result.review.findings)} finding(s)")
    print(f"\nWrote {OUT}/prd.md, prd.json, prd.storage.xhtml")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
