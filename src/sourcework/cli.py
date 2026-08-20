"""Command line entry points.

    sourcework serve <name>          run one agent
    sourcework serve-all             run every agent in one process (dev)
    sourcework status                probe the mesh
    sourcework backends              which LLM backends are usable here
    sourcework ui                    the web UI (browser front end)
    sourcework generate ...          drive the orchestrator over A2A
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import importlib
import json
import logging
import signal
import sys
from pathlib import Path

from sourcework.config import settings

AGENTS = {
    "orchestrator": "sourcework.agents.orchestrator.agent",
    "ingestion": "sourcework.agents.ingestion.agent",
    "vision": "sourcework.agents.vision.agent",
    "transcript": "sourcework.agents.transcript.agent",
    "confluence": "sourcework.agents.confluence_agent.agent",
    "requirements": "sourcework.agents.requirements.agent",
    "writer": "sourcework.agents.writer.agent",
    "critic": "sourcework.agents.critic.agent",
}


def _load(name: str):  # noqa: ANN202
    try:
        return importlib.import_module(AGENTS[name])
    except KeyError:
        raise SystemExit(f"Unknown agent {name!r}. Choose from: {', '.join(AGENTS)}") from None


def cmd_serve(args: argparse.Namespace) -> int:
    from sourcework.a2a_common import build_app, serve

    module = _load(args.name)
    serve(build_app(module.card(), module.executor()), args.port or module.PORT)
    return 0


def cmd_serve_all(args: argparse.Namespace) -> int:
    """Every agent in one process on its own port. Development only."""
    import threading

    import uvicorn

    from sourcework.a2a_common import build_app

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
    from sourcework.a2a_common import AgentPool

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
    from sourcework.backends import probe, resolve_chain
    from sourcework.llm import LLM

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
                "You are a terse assistant.", "Reply with exactly: SOURCEWORK OK"
            )
        except Exception as exc:  # noqa: BLE001 - reporting, not handling
            print(f"[FAIL] {backend_id:<14} {type(exc).__name__}: {str(exc)[:200]}")
            continue
        print(f"[ok  ] {backend_id:<14} {answer.strip()[:80]!r}")
    return 0


def cmd_backends(args: argparse.Namespace) -> int:
    return asyncio.run(_backends(args))


def cmd_doctor(args: argparse.Namespace) -> int:
    """What is configured, what is reachable, and what to do about it."""
    from sourcework import engine, paths

    print(f"config     {paths.env_file()}")
    print(f"workspace  {paths.workspace()}")
    print(f"checkout   {'yes' if paths.is_project_checkout() else 'no (per-user paths)'}\n")

    found = engine.report(timeout=args.timeout)
    print(f"backend    {found['backend']}")

    active = found["engine"]
    configured_base = found["configured_base"]
    if active and active.configured:
        print(f"engine     {active.summary()}  [configured]")
        for model in active.models[:8]:
            print(f"             {model}")
        if len(active.models) > 8:
            print(f"             ... and {len(active.models) - 8} more")
    elif active and configured_base:
        # The case this whole branch exists for. Something answered, but it is
        # not the endpoint the backend will call - and presenting it as "the
        # engine" is how doctor came to report a healthy server while every run
        # failed against a different, dead one. Say which is which, and do not
        # dress a suggestion up as a finding.
        print(f"engine     NOT REACHABLE at {configured_base}  [configured]")
        print(f"           something else is answering: {active.summary()}")
        print(
            f"\n{found['backend']} sends its calls to {configured_base} and will not use\n"
            "the server above. Start yours there, or point the backend at that one."
        )
        print(_how_to_start_one())
        return 1
    elif active:
        print(f"engine     {active.summary()}  [found by probing]")
        for model in active.models[:8]:
            print(f"             {model}")
        if len(active.models) > 8:
            print(f"             ... and {len(active.models) - 8} more")
    elif configured_base:
        print(f"engine     NOT REACHABLE at {configured_base}  [configured]")
        print("\nNothing answered there, and nothing answered on:")
        for url in found["probed"]:
            print(f"  {url}")
        return 1
    elif found["hosted_credentials"]:
        print("engine     none local; a hosted API key is present")
    else:
        print("engine     NONE REACHABLE")
        print("\nNothing answered on:")
        for url in found["probed"]:
            print(f"  {url}")
        print(
            "\nStart one, or point SOURCEWORK_LLM__API_BASE at an OpenAI-compatible\n"
            "server."
        )
        print(_how_to_start_one())
        return 1

    print("\nroles")
    for role, model in found["roles"].items():
        print(f"  {role:<10} {model or '(backend default)'}")
    return 0


def _how_to_start_one() -> str:
    """The next step, which depends on whether llama-swap is actually here.

    Doctor used to end by recommending `scripts/llama-swap.sh` unconditionally.
    On a machine without llama-swap that script's first act is to fail, so the
    advice sent you to a dead end - the same shape of unhelpfulness as a green
    pill on a backend that cannot answer.
    """
    from shutil import which

    if which("llama-swap"):
        return (
            "\nllama-swap is installed. Serve your models with:\n"
            "  scripts/llama-models.py scan   # refresh the generated model list\n"
            "  scripts/llama-swap.sh          # listens on 127.0.0.1:8081"
        )
    return (
        "\nllama-swap is not installed - it is what serves several models behind\n"
        "one endpoint, which is what per-role models need. To fetch it:\n"
        "  sourcework install-llama-swap\n"
        "For a single model instead, MODEL=... scripts/llama-serve.sh"
    )


def installer_version() -> str:
    """The pinned llama-swap version, for the parser's help text."""
    from sourcework.installer import LLAMA_SWAP_VERSION

    return LLAMA_SWAP_VERSION


def cmd_install_llama_swap(args: argparse.Namespace) -> int:
    """Fetch llama-swap, verified, after saying what it is about to do.

    Deliberately not silent and deliberately not automatic: this puts a binary
    from the internet on your PATH, and the least it can do is let you read the
    URL and the digest first.
    """
    from shutil import which

    from sourcework import installer

    try:
        plan = installer.plan_for(args.version)
    except installer.InstallError as exc:
        print(exc, file=sys.stderr)
        return 1

    print(plan.describe())
    existing = which("llama-swap")
    if existing and not args.force:
        print(f"\nAlready installed at {existing}. Pass --force to replace it.")
        return 0
    if args.dry_run:
        print("\n--dry-run: nothing was downloaded.")
        return 0

    print()
    try:
        where = installer.install(args.version, force=args.force)
    except installer.InstallError as exc:
        print(exc, file=sys.stderr)
        return 1

    print(f"Installed llama-swap {plan.version} at {where}")
    if not installer.on_path():
        # An install that "worked" and a command still not found is a common
        # enough ending on macOS to be worth saying rather than leaving to be
        # discovered.
        print(f"\n{where.parent} is not on your PATH. Add it:")
        print(f'  export PATH="{where.parent}:$PATH"')
    print("\nNext: scripts/llama-models.py scan, then scripts/llama-swap.sh")
    return 0


def cmd_app(args: argparse.Namespace) -> int:
    from sourcework import desktop

    return desktop.run(port=args.port, open_browser=not args.no_browser)


def cmd_install_entry(args: argparse.Namespace) -> int:
    from sourcework import launcher

    return launcher.remove() if args.remove else launcher.install()


def cmd_ui(args: argparse.Namespace) -> int:
    import os

    from sourcework.ui import DEFAULT_HOST, PORT, serve

    port = args.port or PORT
    host = args.host or os.environ.get("SOURCEWORK_UI_HOST") or DEFAULT_HOST
    print(f"SourceWork UI  http://{'localhost' if host == DEFAULT_HOST else host}:{port}")
    print("Needs the mesh running: sourcework serve-all\n")
    serve(port=port, host=host, workspace=Path(args.workspace) if args.workspace else None)
    return 0


async def _generate(args: argparse.Namespace) -> int:
    from sourcework import checkpoint
    from sourcework.a2a_common import AgentPool
    from sourcework.models import InputRef, PRDRequest, PRDResult, new_id

    inputs = [InputRef(uri=_as_uri(p)) for p in args.input or []]
    for note in args.note or []:
        inputs.append(InputRef(uri="inline:note", text=note, title="Requester note"))

    resuming = args.resume is not None
    if resuming:
        # A bare --resume means the last interrupted run. The browser has a
        # history to point at; a terminal has the run you were just watching.
        run_id = args.resume or next(iter(checkpoint.saved_runs()), None)
        if run_id is None:
            print("Nothing to resume: no run has saved state.", file=sys.stderr)
            return 2
        stages = checkpoint.saved_stages(run_id)
        if not stages:
            print(f"Run {run_id} saved no stages to resume from.", file=sys.stderr)
            return 2
        if await _in_flight(run_id):
            # Asked before anything is spent. Disconnecting a client does not
            # stop a run - Ctrl-C kills this process, not the orchestrator - so
            # the run somebody thinks they interrupted is usually still going.
            # The orchestrator refuses this too; that guard is race-free and
            # this one is early, and the early answer is the useful one.
            print(
                f"Run {run_id} is still going. Interrupting this command did not stop "
                f"it - the orchestrator carries on and will finish on its own. Wait for "
                f"it, or cancel it, then resume.",
                file=sys.stderr,
            )
            return 2
        print(f"Resuming {run_id}, reusing: {', '.join(stages)}")
    else:
        run_id = new_id("run")

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
        estimate=args.estimate,
        run_id=run_id,
        resume=resuming,
    )

    try:
        with _stop_on_sigterm():
            async with AgentPool() as pool:
                data = await pool.call("orchestrator", "generate_prd", request)
    except (KeyboardInterrupt, asyncio.CancelledError):
        # The likeliest interruption of all, and the one that does not arrive as
        # an Exception: somebody watching a slow run and pressing Ctrl-C.
        print("\nInterrupted. Told the orchestrator to stop.", file=sys.stderr)
        _print_resume_hint(run_id, checkpoint, still_running=False)
        return 130
    except Exception as exc:
        # The stages that did finish are on disk, and a terminal has no run
        # history to discover that from - so the failure says so itself, with
        # the command to pick it up. Without this the checkpoint might as well
        # not exist.
        print(f"\nRun failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        _print_resume_hint(run_id, checkpoint, still_running=False)
        return 1

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


@contextlib.contextmanager
def _stop_on_sigterm():  # noqa: ANN202
    """Treat SIGTERM like Ctrl-C, so the run is cancelled rather than orphaned.

    SIGINT already arrives as a cancellation - ``asyncio.run`` turns it into
    one. SIGTERM does not: Python's default handler ends the process outright,
    the coroutine never unwinds, nothing tells the orchestrator to stop, and the
    run carries on billing. That is what a process manager sends - ``timeout``,
    systemd, ``docker stop``, a desktop app being quit - so it is the more
    likely of the two to end a long run on somebody else's machine.
    """
    loop = asyncio.get_event_loop()
    task = asyncio.current_task()
    installed = False
    if task is not None:
        with contextlib.suppress(NotImplementedError, RuntimeError):  # not on Windows
            loop.add_signal_handler(signal.SIGTERM, task.cancel)
            installed = True
    try:
        yield
    finally:
        if installed:
            with contextlib.suppress(NotImplementedError, RuntimeError, ValueError):
                loop.remove_signal_handler(signal.SIGTERM)


async def _in_flight(run_id: str) -> bool:
    """Is the orchestrator already running ``run_id``?

    A failure to ask is not a reason to refuse: an unreachable mesh fails
    informatively a moment later on the real call, and treating "I could not
    check" as "it is running" would block the resume this exists to enable.
    """
    from sourcework.a2a_common import AgentPool

    try:
        async with AgentPool() as pool:
            status = await pool.call("orchestrator", "mesh_status", {})
        return run_id in (status.get("in_flight") or [])
    except Exception:  # noqa: BLE001 - the next call reports it properly
        logging.getLogger(__name__).debug("could not check for in-flight runs", exc_info=True)
        return False


def _print_resume_hint(run_id: str, checkpoint, *, still_running: bool) -> None:  # noqa: ANN001
    """What survived, and how to pick it up.

    ``still_running`` for the case where the run is known to be continuing. A
    cancel that did not land leaves one going, and ``--resume`` checks for that
    before spending anything, so the hint stays correct either way.
    """
    stages = checkpoint.saved_stages(run_id)
    if not stages:
        return
    when = "Once it stops, re-run" if still_running else "Re-run"
    print(
        f"\n{len(stages)} stage(s) survived ({', '.join(stages)}). "
        f"{when} the same command with --resume to continue from there, or "
        f"--resume {run_id} to name it explicitly.",
        file=sys.stderr,
    )


def cmd_generate(args: argparse.Namespace) -> int:
    return asyncio.run(_generate(args))


def _as_uri(value: str) -> str:
    if "://" in value:
        return value
    return Path(value).expanduser().resolve().as_uri()


def _slug(text: str) -> str:
    return "".join(c if c.isalnum() else "-" for c in text.lower()).strip("-")[:60] or "prd"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sourcework", description=__doc__)
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

    p = sub.add_parser("app", help="run as a desktop app (mesh + UI + browser)")
    p.add_argument("--port", type=int)
    p.add_argument("--no-browser", action="store_true", help="do not open a browser")
    p.set_defaults(func=cmd_app)

    p = sub.add_parser("install-desktop-entry",
                       help="add SourceWork to the application launcher (Linux)")
    p.add_argument("--remove", action="store_true", help="take it out again")
    p.set_defaults(func=cmd_install_entry)

    p = sub.add_parser("install-llama-swap",
                       help="download llama-swap (checksum-verified) into ~/.local/bin")
    p.add_argument("--version", default=installer_version(),
                   help=f"release to fetch (default {installer_version()})")
    p.add_argument("--force", action="store_true", help="replace an existing binary")
    p.add_argument("--dry-run", action="store_true",
                   help="print the URL and digest source, download nothing")
    p.set_defaults(func=cmd_install_llama_swap)

    p = sub.add_parser("doctor", help="what is configured and what is reachable")
    p.add_argument("--timeout", type=float, default=0.6, help="per-probe seconds")
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser("ui", help="serve the web UI")
    p.add_argument("--port", type=int, help=f"default {8080}")
    p.add_argument("--host", help="default 127.0.0.1. The UI has no authentication and "
                                  "can rewrite your API keys - bind wider only behind a proxy "
                                  "that authenticates. Also SOURCEWORK_UI_HOST.")
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
    p.add_argument("--estimate", action="store_true",
                   help="ask the analyst for a T-shirt effort estimate (S/M/L/XL) per "
                        "requirement; rendered marked as model inference")
    p.add_argument(
        "--resume", nargs="?", const="", metavar="RUN_ID",
        help="continue an interrupted run instead of starting over, reusing the stages it "
             "finished. Bare --resume takes the most recent. Stages whose inputs have changed "
             "since - a different backend, an edited source - are recomputed either way.",
    )
    p.set_defaults(func=cmd_generate)

    args = parser.parse_args(argv)
    logging.basicConfig(level=settings().log_level, format="%(levelname)-7s %(name)s: %(message)s")
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
